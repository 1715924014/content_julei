from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import Any


SOURCE_SUGGESTION_FIELDS = [
    "source_suggestion_id",
    "submit_date",
    "created_at",
    "raw_text",
    "department",
    "job_group",
    "work_location",
    "scenario",
    "status",
]

COUNTABLE_TABLES = {
    "source_suggestions",
    "import_batches",
    "suggestion_analysis",
    "issue_clusters",
    "cluster_members",
    "review_tasks",
    "action_items",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


class Storage:
    def __init__(self, connection: sqlite3.Connection):
        self.connection = connection
        self.connection.row_factory = sqlite3.Row

    def initialize_schema(self) -> None:
        self.connection.executescript(
            """
            PRAGMA foreign_keys = ON;

            CREATE TABLE IF NOT EXISTS import_batches (
                batch_id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_name TEXT NOT NULL,
                status TEXT NOT NULL,
                cursor_start TEXT,
                cursor_end TEXT,
                started_at TEXT NOT NULL,
                finished_at TEXT,
                rows_read INTEGER NOT NULL DEFAULT 0,
                rows_created INTEGER NOT NULL DEFAULT 0,
                rows_skipped INTEGER NOT NULL DEFAULT 0,
                rows_failed INTEGER NOT NULL DEFAULT 0,
                error_summary TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS source_suggestions (
                source_suggestion_id TEXT PRIMARY KEY,
                import_batch_id INTEGER,
                submit_date TEXT,
                created_at TEXT NOT NULL,
                raw_text TEXT NOT NULL,
                department TEXT,
                job_group TEXT,
                work_location TEXT,
                scenario TEXT,
                status TEXT,
                updated_at TEXT NOT NULL,
                FOREIGN KEY (import_batch_id) REFERENCES import_batches(batch_id)
            );

            CREATE TABLE IF NOT EXISTS suggestion_analysis (
                analysis_id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_suggestion_id TEXT NOT NULL UNIQUE,
                batch_id INTEGER NOT NULL,
                normalized_text TEXT NOT NULL,
                content_hash TEXT NOT NULL,
                primary_category TEXT NOT NULL,
                secondary_category TEXT NOT NULL,
                owner_department TEXT NOT NULL,
                quality_type TEXT NOT NULL,
                urgency_level TEXT NOT NULL,
                classification_confidence REAL NOT NULL,
                embedding_status TEXT NOT NULL,
                embedding_model TEXT,
                embedding_ref TEXT,
                review_required TEXT NOT NULL,
                analysis_status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY (source_suggestion_id) REFERENCES source_suggestions(source_suggestion_id),
                FOREIGN KEY (batch_id) REFERENCES import_batches(batch_id)
            );

            CREATE TABLE IF NOT EXISTS issue_clusters (
                cluster_id TEXT PRIMARY KEY,
                cluster_name TEXT NOT NULL,
                cluster_summary TEXT NOT NULL,
                primary_category TEXT NOT NULL,
                secondary_category TEXT NOT NULL,
                owner_department TEXT NOT NULL,
                scenario_key TEXT,
                status TEXT NOT NULL,
                suggestion_count INTEGER NOT NULL,
                representative_suggestion_id TEXT NOT NULL,
                centroid_embedding_ref TEXT,
                last_seen_at TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS cluster_members (
                cluster_member_id INTEGER PRIMARY KEY AUTOINCREMENT,
                cluster_id TEXT NOT NULL,
                source_suggestion_id TEXT NOT NULL,
                decision_type TEXT NOT NULL,
                vector_score REAL NOT NULL,
                keyword_score REAL NOT NULL,
                final_score REAL NOT NULL,
                decision_status TEXT NOT NULL,
                decision_reason TEXT NOT NULL,
                reviewed_by TEXT,
                reviewed_at TEXT,
                created_at TEXT NOT NULL,
                UNIQUE(cluster_id, source_suggestion_id),
                FOREIGN KEY (cluster_id) REFERENCES issue_clusters(cluster_id),
                FOREIGN KEY (source_suggestion_id) REFERENCES source_suggestions(source_suggestion_id)
            );

            CREATE TABLE IF NOT EXISTS review_tasks (
                review_task_id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_suggestion_id TEXT NOT NULL,
                candidate_cluster_id TEXT,
                task_type TEXT NOT NULL,
                priority INTEGER NOT NULL,
                evidence_json TEXT NOT NULL,
                status TEXT NOT NULL,
                review_result TEXT,
                reviewed_by TEXT,
                reviewed_at TEXT,
                created_at TEXT NOT NULL,
                UNIQUE(source_suggestion_id, candidate_cluster_id, task_type),
                FOREIGN KEY (source_suggestion_id) REFERENCES source_suggestions(source_suggestion_id),
                FOREIGN KEY (candidate_cluster_id) REFERENCES issue_clusters(cluster_id)
            );

            CREATE TABLE IF NOT EXISTS action_items (
                action_id TEXT PRIMARY KEY,
                cluster_id TEXT NOT NULL UNIQUE,
                action_title TEXT NOT NULL,
                owner_department TEXT NOT NULL,
                urgency_level TEXT NOT NULL,
                status TEXT NOT NULL,
                suggestion_count INTEGER NOT NULL,
                first_seen_at TEXT NOT NULL,
                last_seen_at TEXT NOT NULL,
                next_step TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY (cluster_id) REFERENCES issue_clusters(cluster_id)
            );
            """
        )
        self.connection.commit()

    def start_import_batch(self, source_name: str, cursor_start: str | None = None, status: str = "running") -> int:
        now = utc_now()
        cursor = self.connection.execute(
            """
            INSERT INTO import_batches (
                source_name, status, cursor_start, started_at, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (source_name, status, cursor_start, now, now, now),
        )
        self.connection.commit()
        return int(cursor.lastrowid)

    def finish_import_batch(
        self,
        batch_id: int,
        cursor_end: str,
        *,
        rows_read: int,
        rows_created: int,
        rows_skipped: int,
        rows_failed: int,
        error_summary: str | None = None,
    ) -> None:
        now = utc_now()
        status = "success" if rows_failed == 0 else "partial"
        self.connection.execute(
            """
            UPDATE import_batches
            SET status = ?, cursor_end = ?, finished_at = ?, rows_read = ?,
                rows_created = ?, rows_skipped = ?, rows_failed = ?,
                error_summary = ?, updated_at = ?
            WHERE batch_id = ?
            """,
            (
                status,
                cursor_end,
                now,
                rows_read,
                rows_created,
                rows_skipped,
                rows_failed,
                error_summary,
                now,
                batch_id,
            ),
        )
        self.connection.commit()

    def get_import_batch(self, batch_id: int) -> sqlite3.Row:
        row = self.connection.execute(
            "SELECT * FROM import_batches WHERE batch_id = ?",
            (batch_id,),
        ).fetchone()
        if row is None:
            raise KeyError(f"unknown import batch: {batch_id}")
        return row

    def upsert_source_suggestion(self, row: dict[str, Any], import_batch_id: int | None = None) -> bool:
        source_suggestion_id = str(row.get("source_suggestion_id", "")).strip()
        if not source_suggestion_id:
            raise ValueError("source suggestion requires source_suggestion_id")

        existing = self.connection.execute(
            "SELECT raw_text, status FROM source_suggestions WHERE source_suggestion_id = ?",
            (source_suggestion_id,),
        ).fetchone()
        raw_text = row["raw_text"]
        status = row.get("status", "")
        if existing is not None and existing["raw_text"] == raw_text and existing["status"] == status:
            return False

        now = utc_now()
        values = {field: row.get(field) for field in SOURCE_SUGGESTION_FIELDS}
        created_at = values["created_at"] or now
        if existing is None:
            self.connection.execute(
                """
                INSERT INTO source_suggestions (
                    source_suggestion_id, import_batch_id, submit_date, created_at,
                    raw_text, department, job_group, work_location, scenario, status,
                    updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    source_suggestion_id,
                    import_batch_id,
                    values["submit_date"],
                    created_at,
                    values["raw_text"],
                    values["department"],
                    values["job_group"],
                    values["work_location"],
                    values["scenario"],
                    values["status"],
                    now,
                ),
            )
        else:
            self.connection.execute(
                """
                UPDATE source_suggestions
                SET import_batch_id = ?, submit_date = ?, created_at = ?,
                    raw_text = ?, department = ?, job_group = ?, work_location = ?,
                    scenario = ?, status = ?, updated_at = ?
                WHERE source_suggestion_id = ?
                """,
                (
                    import_batch_id,
                    values["submit_date"],
                    created_at,
                    values["raw_text"],
                    values["department"],
                    values["job_group"],
                    values["work_location"],
                    values["scenario"],
                    values["status"],
                    now,
                    source_suggestion_id,
                ),
            )
        self.connection.commit()
        return True

    def count_table(self, table_name: str) -> int:
        if table_name not in COUNTABLE_TABLES:
            raise ValueError(f"cannot count unknown table: {table_name}")
        row = self.connection.execute(f"SELECT COUNT(*) AS item_count FROM {table_name}").fetchone()
        return int(row["item_count"])
