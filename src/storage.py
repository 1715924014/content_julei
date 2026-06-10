from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from typing import Any


SOURCE_SUGGESTION_FIELDS = [
    "suggestion_id",
    "submit_date",
    "raw_text",
    "department",
    "job_group",
    "work_location",
    "scenario",
    "is_anonymous_for_report",
    "status",
    "owner_department",
    "resolution_note",
    "closed_date",
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
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS source_suggestions (
                suggestion_id TEXT PRIMARY KEY,
                import_batch_id INTEGER,
                submit_date TEXT,
                raw_text TEXT,
                department TEXT,
                job_group TEXT,
                work_location TEXT,
                scenario TEXT,
                is_anonymous_for_report TEXT,
                status TEXT,
                owner_department TEXT,
                resolution_note TEXT,
                closed_date TEXT,
                source_payload TEXT NOT NULL,
                row_hash TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY (import_batch_id) REFERENCES import_batches(batch_id)
            );

            CREATE TABLE IF NOT EXISTS suggestion_analysis (
                suggestion_id TEXT PRIMARY KEY,
                primary_category TEXT,
                secondary_category TEXT,
                quality_type TEXT,
                urgency_level TEXT,
                confidence REAL,
                review_required TEXT,
                validation_flags TEXT,
                analyzed_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY (suggestion_id) REFERENCES source_suggestions(suggestion_id)
            );

            CREATE TABLE IF NOT EXISTS issue_clusters (
                cluster_id TEXT PRIMARY KEY,
                cluster_name TEXT,
                cluster_summary TEXT,
                primary_category TEXT,
                secondary_category TEXT,
                owner_department TEXT,
                urgency_level TEXT,
                review_required_count INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS cluster_members (
                cluster_id TEXT NOT NULL,
                suggestion_id TEXT NOT NULL,
                member_order INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                PRIMARY KEY (cluster_id, suggestion_id),
                FOREIGN KEY (cluster_id) REFERENCES issue_clusters(cluster_id),
                FOREIGN KEY (suggestion_id) REFERENCES source_suggestions(suggestion_id)
            );

            CREATE TABLE IF NOT EXISTS review_tasks (
                task_id INTEGER PRIMARY KEY AUTOINCREMENT,
                suggestion_id TEXT,
                cluster_id TEXT,
                task_type TEXT NOT NULL,
                status TEXT NOT NULL,
                assigned_to TEXT,
                due_date TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY (suggestion_id) REFERENCES source_suggestions(suggestion_id),
                FOREIGN KEY (cluster_id) REFERENCES issue_clusters(cluster_id)
            );

            CREATE TABLE IF NOT EXISTS action_items (
                action_id TEXT PRIMARY KEY,
                cluster_id TEXT,
                action_title TEXT NOT NULL,
                owner_department TEXT,
                urgency_level TEXT,
                status TEXT NOT NULL,
                next_step TEXT,
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

    def finish_import_batch(self, batch_id: int, status: str, cursor_end: str | None = None) -> None:
        now = utc_now()
        self.connection.execute(
            """
            UPDATE import_batches
            SET status = ?, cursor_end = ?, finished_at = ?, updated_at = ?
            WHERE batch_id = ?
            """,
            (status, cursor_end, now, now, batch_id),
        )
        self.connection.commit()

    def get_import_batch(self, batch_id: int) -> sqlite3.Row | None:
        return self.connection.execute(
            "SELECT * FROM import_batches WHERE batch_id = ?",
            (batch_id,),
        ).fetchone()

    def upsert_source_suggestion(self, row: dict[str, Any], import_batch_id: int | None = None) -> bool:
        suggestion_id = str(row.get("suggestion_id", "")).strip()
        if not suggestion_id:
            raise ValueError("source suggestion requires suggestion_id")

        payload = json.dumps(row, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        row_hash = hashlib.sha256(payload.encode("utf-8")).hexdigest()
        existing = self.connection.execute(
            "SELECT row_hash FROM source_suggestions WHERE suggestion_id = ?",
            (suggestion_id,),
        ).fetchone()
        if existing is not None and existing["row_hash"] == row_hash:
            return False

        now = utc_now()
        values = {field: row.get(field) for field in SOURCE_SUGGESTION_FIELDS}
        if existing is None:
            self.connection.execute(
                """
                INSERT INTO source_suggestions (
                    suggestion_id, import_batch_id, submit_date, raw_text, department,
                    job_group, work_location, scenario, is_anonymous_for_report, status,
                    owner_department, resolution_note, closed_date, source_payload,
                    row_hash, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    suggestion_id,
                    import_batch_id,
                    values["submit_date"],
                    values["raw_text"],
                    values["department"],
                    values["job_group"],
                    values["work_location"],
                    values["scenario"],
                    values["is_anonymous_for_report"],
                    values["status"],
                    values["owner_department"],
                    values["resolution_note"],
                    values["closed_date"],
                    payload,
                    row_hash,
                    now,
                    now,
                ),
            )
        else:
            self.connection.execute(
                """
                UPDATE source_suggestions
                SET import_batch_id = ?, submit_date = ?, raw_text = ?, department = ?,
                    job_group = ?, work_location = ?, scenario = ?,
                    is_anonymous_for_report = ?, status = ?, owner_department = ?,
                    resolution_note = ?, closed_date = ?, source_payload = ?,
                    row_hash = ?, updated_at = ?
                WHERE suggestion_id = ?
                """,
                (
                    import_batch_id,
                    values["submit_date"],
                    values["raw_text"],
                    values["department"],
                    values["job_group"],
                    values["work_location"],
                    values["scenario"],
                    values["is_anonymous_for_report"],
                    values["status"],
                    values["owner_department"],
                    values["resolution_note"],
                    values["closed_date"],
                    payload,
                    row_hash,
                    now,
                    suggestion_id,
                ),
            )
        self.connection.commit()
        return True

    def count_table(self, table_name: str) -> int:
        if table_name not in COUNTABLE_TABLES:
            raise ValueError(f"cannot count unknown table: {table_name}")
        row = self.connection.execute(f"SELECT COUNT(*) AS item_count FROM {table_name}").fetchone()
        return int(row["item_count"])
