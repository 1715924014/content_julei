from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Any

from src.vector_index import ClusterVector


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
    "owner_department",
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
                owner_department TEXT,
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
        self._ensure_source_suggestions_owner_department()
        self.connection.commit()

    def _ensure_source_suggestions_owner_department(self) -> None:
        columns = {
            row["name"]
            for row in self.connection.execute("PRAGMA table_info(source_suggestions)").fetchall()
        }
        if "owner_department" not in columns:
            self.connection.execute("ALTER TABLE source_suggestions ADD COLUMN owner_department TEXT")

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
            """
            SELECT submit_date, created_at, raw_text, department, job_group,
                work_location, scenario, status, owner_department
            FROM source_suggestions
            WHERE source_suggestion_id = ?
            """,
            (source_suggestion_id,),
        ).fetchone()

        now = utc_now()
        values = {field: row.get(field) for field in SOURCE_SUGGESTION_FIELDS}
        created_at = values["created_at"] or (existing["created_at"] if existing is not None else now)
        values["created_at"] = created_at

        if existing is not None:
            changed_fields = [
                field
                for field in SOURCE_SUGGESTION_FIELDS
                if field != "source_suggestion_id" and (existing[field] or "") != (values[field] or "")
            ]
            if not changed_fields:
                return False

        if existing is None:
            self.connection.execute(
                """
                INSERT INTO source_suggestions (
                    source_suggestion_id, import_batch_id, submit_date, created_at,
                    raw_text, department, job_group, work_location, scenario, status,
                    owner_department, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    values["owner_department"],
                    now,
                ),
            )
        else:
            self.connection.execute(
                """
                UPDATE source_suggestions
                SET import_batch_id = ?, submit_date = ?, created_at = ?,
                    raw_text = ?, department = ?, job_group = ?, work_location = ?,
                    scenario = ?, status = ?, owner_department = ?, updated_at = ?
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
                    values["owner_department"],
                    now,
                    source_suggestion_id,
                ),
            )
        self.connection.commit()
        return True

    def clear_cluster_members_for_source(self, source_suggestion_id: str) -> None:
        affected_clusters = self.connection.execute(
            """
            SELECT DISTINCT cluster_id
            FROM cluster_members
            WHERE source_suggestion_id = ?
            """,
            (source_suggestion_id,),
        ).fetchall()
        if not affected_clusters:
            return

        now = utc_now()
        self.connection.execute(
            "DELETE FROM cluster_members WHERE source_suggestion_id = ?",
            (source_suggestion_id,),
        )
        for row in affected_clusters:
            self.connection.execute(
                """
                UPDATE issue_clusters
                SET suggestion_count = (
                        SELECT COUNT(*)
                        FROM cluster_members
                        WHERE cluster_id = ? AND decision_status = 'accepted'
                    ),
                    updated_at = ?
                WHERE cluster_id = ?
                """,
                (row["cluster_id"], now, row["cluster_id"]),
            )
        self.connection.commit()

    def clear_review_tasks_for_source(self, source_suggestion_id: str) -> None:
        self.connection.execute(
            """
            DELETE FROM review_tasks
            WHERE source_suggestion_id = ? AND status = ?
            """,
            (source_suggestion_id, "pending"),
        )
        self.connection.commit()

    def upsert_suggestion_analysis(self, row: dict[str, Any]) -> None:
        now = utc_now()
        self.connection.execute(
            """
            INSERT INTO suggestion_analysis (
                source_suggestion_id, batch_id, normalized_text, content_hash,
                primary_category, secondary_category, owner_department,
                quality_type, urgency_level, classification_confidence,
                embedding_status, embedding_model, embedding_ref,
                review_required, analysis_status, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(source_suggestion_id) DO UPDATE SET
                batch_id = excluded.batch_id,
                normalized_text = excluded.normalized_text,
                content_hash = excluded.content_hash,
                primary_category = excluded.primary_category,
                secondary_category = excluded.secondary_category,
                owner_department = excluded.owner_department,
                quality_type = excluded.quality_type,
                urgency_level = excluded.urgency_level,
                classification_confidence = excluded.classification_confidence,
                embedding_status = excluded.embedding_status,
                embedding_model = excluded.embedding_model,
                embedding_ref = excluded.embedding_ref,
                review_required = excluded.review_required,
                analysis_status = excluded.analysis_status,
                updated_at = excluded.updated_at
            """,
            (
                row["source_suggestion_id"],
                row["batch_id"],
                row["normalized_text"],
                row["content_hash"],
                row["primary_category"],
                row["secondary_category"],
                row["owner_department"],
                row["quality_type"],
                row["urgency_level"],
                row["classification_confidence"],
                row["embedding_status"],
                row.get("embedding_model"),
                row.get("embedding_ref"),
                row["review_required"],
                row["analysis_status"],
                now,
                now,
            ),
        )
        self.connection.commit()

    def list_active_cluster_vectors(self) -> list[ClusterVector]:
        rows = self.connection.execute(
            """
            SELECT cluster_id, cluster_summary, centroid_embedding_ref,
                primary_category, secondary_category, owner_department, status
            FROM issue_clusters
            WHERE status = ?
            ORDER BY cluster_id
            """,
            ("active",),
        ).fetchall()
        clusters: list[ClusterVector] = []
        for row in rows:
            try:
                vector = json.loads(row["centroid_embedding_ref"] or "[]")
            except json.JSONDecodeError:
                vector = []
            if not isinstance(vector, list):
                vector = []
            clusters.append(
                ClusterVector(
                    cluster_id=row["cluster_id"],
                    text=row["cluster_summary"],
                    vector=[float(value) for value in vector],
                    primary_category=row["primary_category"],
                    secondary_category=row["secondary_category"],
                    owner_department=row["owner_department"],
                    active=row["status"] == "active",
                )
            )
        return clusters

    def get_issue_cluster(self, cluster_id: str) -> sqlite3.Row:
        row = self.connection.execute(
            "SELECT * FROM issue_clusters WHERE cluster_id = ?",
            (cluster_id,),
        ).fetchone()
        if row is None:
            raise KeyError(f"unknown issue cluster: {cluster_id}")
        return row

    def create_issue_cluster(
        self,
        *,
        source_suggestion_id: str,
        normalized_text: str,
        primary_category: str,
        secondary_category: str,
        owner_department: str,
        scenario_key: str,
        centroid_embedding: list[float],
    ) -> str:
        now = utc_now()
        row = self.connection.execute(
            """
            SELECT cluster_id
            FROM issue_clusters
            WHERE cluster_id LIKE 'CL%'
            ORDER BY cluster_id DESC
            LIMIT 1
            """
        ).fetchone()
        next_number = int(row["cluster_id"][2:]) + 1 if row is not None else 1
        cluster_id = f"CL{next_number:06d}"
        cluster_name = secondary_category or primary_category
        cluster_summary = normalized_text
        self.connection.execute(
            """
            INSERT INTO issue_clusters (
                cluster_id, cluster_name, cluster_summary, primary_category,
                secondary_category, owner_department, scenario_key, status,
                suggestion_count, representative_suggestion_id,
                centroid_embedding_ref, last_seen_at, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                cluster_id,
                cluster_name,
                cluster_summary,
                primary_category,
                secondary_category,
                owner_department,
                scenario_key,
                "active",
                1,
                source_suggestion_id,
                json.dumps(centroid_embedding),
                now,
                now,
                now,
            ),
        )
        self.connection.commit()
        return cluster_id

    def add_cluster_member(
        self,
        *,
        cluster_id: str,
        source_suggestion_id: str,
        decision_type: str,
        vector_score: float,
        keyword_score: float,
        final_score: float,
        decision_status: str,
        decision_reason: str,
    ) -> None:
        now = utc_now()
        existing_member = self.connection.execute(
            """
            SELECT 1
            FROM cluster_members
            WHERE cluster_id = ? AND source_suggestion_id = ?
            """,
            (cluster_id, source_suggestion_id),
        ).fetchone()
        self.connection.execute(
            """
            INSERT INTO cluster_members (
                cluster_id, source_suggestion_id, decision_type, vector_score,
                keyword_score, final_score, decision_status, decision_reason,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(cluster_id, source_suggestion_id) DO UPDATE SET
                decision_type = excluded.decision_type,
                vector_score = excluded.vector_score,
                keyword_score = excluded.keyword_score,
                final_score = excluded.final_score,
                decision_status = excluded.decision_status,
                decision_reason = excluded.decision_reason
            """,
            (
                cluster_id,
                source_suggestion_id,
                decision_type,
                vector_score,
                keyword_score,
                final_score,
                decision_status,
                decision_reason,
                now,
            ),
        )
        if existing_member is None and decision_status == "accepted" and decision_type != "create_new_cluster":
            self.connection.execute(
                """
                UPDATE issue_clusters
                SET suggestion_count = suggestion_count + 1,
                    last_seen_at = ?,
                    updated_at = ?
                WHERE cluster_id = ?
                """,
                (now, now, cluster_id),
            )
        self.connection.commit()

    def create_review_task(
        self,
        *,
        source_suggestion_id: str,
        candidate_cluster_id: str | None,
        task_type: str,
        priority: int,
        evidence: dict[str, Any],
    ) -> None:
        now = utc_now()
        self.connection.execute(
            """
            INSERT INTO review_tasks (
                source_suggestion_id, candidate_cluster_id, task_type,
                priority, evidence_json, status, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(source_suggestion_id, candidate_cluster_id, task_type)
            DO UPDATE SET
                priority = excluded.priority,
                evidence_json = excluded.evidence_json,
                status = excluded.status
            """,
            (
                source_suggestion_id,
                candidate_cluster_id,
                task_type,
                priority,
                json.dumps(evidence, sort_keys=True),
                "pending",
                now,
            ),
        )
        self.connection.commit()

    def count_table(self, table_name: str) -> int:
        if table_name not in COUNTABLE_TABLES:
            raise ValueError(f"cannot count unknown table: {table_name}")
        row = self.connection.execute(f"SELECT COUNT(*) AS item_count FROM {table_name}").fetchone()
        return int(row["item_count"])
