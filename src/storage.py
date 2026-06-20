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

            CREATE INDEX IF NOT EXISTS idx_import_batches_source_status_batch
                ON import_batches(source_name, status, batch_id DESC);
            CREATE INDEX IF NOT EXISTS idx_source_suggestions_created
                ON source_suggestions(created_at, source_suggestion_id);
            CREATE INDEX IF NOT EXISTS idx_suggestion_analysis_batch
                ON suggestion_analysis(batch_id, source_suggestion_id);
            CREATE INDEX IF NOT EXISTS idx_issue_clusters_status_category_owner
                ON issue_clusters(status, primary_category, secondary_category, owner_department);
            CREATE INDEX IF NOT EXISTS idx_cluster_members_source_status
                ON cluster_members(source_suggestion_id, decision_status);
            CREATE INDEX IF NOT EXISTS idx_cluster_members_cluster_status
                ON cluster_members(cluster_id, decision_status);
            CREATE INDEX IF NOT EXISTS idx_review_tasks_status_priority_created
                ON review_tasks(status, priority DESC, created_at, review_task_id);
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

    def get_latest_successful_cursor(self, source_name: str) -> str:
        row = self.connection.execute(
            """
            SELECT cursor_end
            FROM import_batches
            WHERE source_name = ? AND status = ? AND cursor_end IS NOT NULL
            ORDER BY batch_id DESC
            LIMIT 1
            """,
            (source_name, "success"),
        ).fetchone()
        if row is None:
            return ""
        return str(row["cursor_end"] or "")

    def get_import_status_summary(self, source_name: str) -> dict[str, Any]:
        latest_batch = self.connection.execute(
            """
            SELECT batch_id, source_name, status, cursor_start, cursor_end,
                started_at, finished_at, rows_read, rows_created, rows_skipped,
                rows_failed, error_summary
            FROM import_batches
            WHERE source_name = ?
            ORDER BY batch_id DESC
            LIMIT 1
            """,
            (source_name,),
        ).fetchone()
        return {
            "source_name": source_name,
            "latest_successful_cursor": self.get_latest_successful_cursor(source_name),
            "latest_batch": dict(latest_batch) if latest_batch is not None else None,
            "table_counts": {
                table_name: self.count_table(table_name)
                for table_name in sorted(COUNTABLE_TABLES)
            },
        }

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

    def list_pending_review_tasks(self) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            """
            SELECT
                rt.review_task_id,
                rt.source_suggestion_id,
                rt.candidate_cluster_id,
                rt.task_type,
                rt.priority,
                rt.evidence_json,
                rt.status,
                rt.created_at,
                ss.raw_text,
                ss.department,
                ss.job_group,
                ss.work_location,
                ss.scenario,
                ss.owner_department,
                ic.cluster_name AS candidate_cluster_name
            FROM review_tasks rt
            JOIN source_suggestions ss
                ON ss.source_suggestion_id = rt.source_suggestion_id
            LEFT JOIN issue_clusters ic
                ON ic.cluster_id = rt.candidate_cluster_id
            WHERE rt.status = 'pending'
            ORDER BY rt.priority DESC, rt.created_at ASC, rt.review_task_id ASC
            """
        ).fetchall()
        return [dict(row) for row in rows]

    def list_persisted_suggestion_export_rows(self) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            """
            SELECT
                ss.source_suggestion_id,
                ss.source_suggestion_id AS suggestion_id,
                ss.submit_date,
                ss.raw_text,
                ss.department,
                ss.job_group,
                ss.work_location,
                ss.scenario,
                '' AS is_anonymous_for_report,
                ss.status,
                COALESCE(sa.owner_department, ss.owner_department, '') AS owner_department,
                '' AS resolution_note,
                '' AS closed_date,
                COALESCE(sa.primary_category, '') AS primary_category,
                COALESCE(sa.secondary_category, '') AS secondary_category,
                COALESCE(sa.quality_type, '') AS quality_type,
                COALESCE(sa.urgency_level, '') AS urgency_level,
                COALESCE(cm.cluster_id, '') AS cluster_id,
                COALESCE(ic.cluster_name, '') AS cluster_name,
                COALESCE(ic.cluster_summary, '') AS cluster_summary,
                COALESCE(CAST(sa.classification_confidence AS TEXT), '') AS confidence,
                COALESCE(sa.review_required, '') AS review_required,
                '' AS validation_flags
            FROM source_suggestions ss
            LEFT JOIN suggestion_analysis sa
                ON sa.source_suggestion_id = ss.source_suggestion_id
            LEFT JOIN cluster_members cm
                ON cm.source_suggestion_id = ss.source_suggestion_id
                AND cm.decision_status = 'accepted'
            LEFT JOIN issue_clusters ic
                ON ic.cluster_id = cm.cluster_id
            ORDER BY ss.created_at, ss.source_suggestion_id
            """
        ).fetchall()
        return [dict(row) for row in rows]

    def list_persisted_cluster_export_rows(self) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            """
            WITH cluster_sources AS (
                SELECT cluster_id, representative_suggestion_id AS source_suggestion_id
                FROM issue_clusters
                UNION
                SELECT cluster_id, source_suggestion_id
                FROM cluster_members
                WHERE decision_status = 'accepted'
            )
            SELECT
                ic.cluster_id,
                ic.cluster_name,
                ic.cluster_summary,
                ic.primary_category,
                ic.secondary_category,
                COUNT(DISTINCT cs.source_suggestion_id) AS suggestion_count,
                COUNT(DISTINCT NULLIF(ss.department, '')) AS department_count,
                COALESCE(GROUP_CONCAT(DISTINCT NULLIF(ss.department, '')), '') AS departments,
                ic.owner_department,
                CASE MAX(
                    CASE COALESCE(sa.urgency_level, '')
                        WHEN 'high' THEN 3
                        WHEN 'medium' THEN 2
                        WHEN 'low' THEN 1
                        WHEN '高' THEN 3
                        WHEN '中' THEN 2
                        WHEN '低' THEN 1
                        ELSE 0
                    END
                )
                    WHEN 3 THEN 'high'
                    WHEN 2 THEN 'medium'
                    WHEN 1 THEN 'low'
                    ELSE ''
                END AS urgency_level,
                SUM(
                    CASE LOWER(COALESCE(sa.review_required, ''))
                        WHEN 'yes' THEN 1
                        WHEN 'y' THEN 1
                        WHEN 'true' THEN 1
                        ELSE CASE COALESCE(sa.review_required, '')
                            WHEN '是' THEN 1
                            ELSE 0
                        END
                    END
                ) AS review_required_count,
                COALESCE(rep.raw_text, '') AS representative_raw_text
            FROM issue_clusters ic
            LEFT JOIN cluster_sources cs
                ON cs.cluster_id = ic.cluster_id
            LEFT JOIN source_suggestions ss
                ON ss.source_suggestion_id = cs.source_suggestion_id
            LEFT JOIN suggestion_analysis sa
                ON sa.source_suggestion_id = cs.source_suggestion_id
            LEFT JOIN source_suggestions rep
                ON rep.source_suggestion_id = ic.representative_suggestion_id
            WHERE ic.status = 'active'
            GROUP BY
                ic.cluster_id,
                ic.cluster_name,
                ic.cluster_summary,
                ic.primary_category,
                ic.secondary_category,
                ic.owner_department,
                rep.raw_text
            ORDER BY suggestion_count DESC, ic.cluster_id
            """
        ).fetchall()
        return [dict(row) for row in rows]

    def apply_review_task_result(
        self,
        *,
        review_task_id: int,
        review_result: str,
        reviewed_by: str = "",
        target_cluster_id: str | None = None,
    ) -> dict[str, Any]:
        normalized_result = review_result.strip().lower().replace("-", "_")
        aliases = {
            "approved": "approve",
            "accept": "approve",
            "accepted": "approve",
            "rejected": "reject",
            "new": "create_new",
            "new_cluster": "create_new",
            "create_new_cluster": "create_new",
            "reassign": "assign",
            "manual_reassign": "assign",
        }
        normalized_result = aliases.get(normalized_result, normalized_result)
        if normalized_result not in {"approve", "reject", "assign", "create_new"}:
            raise ValueError(f"unsupported review_result: {review_result}")

        task = self.connection.execute(
            """
            SELECT
                rt.review_task_id,
                rt.source_suggestion_id,
                rt.candidate_cluster_id,
                rt.status,
                ss.raw_text,
                ss.scenario,
                ss.owner_department AS source_owner_department,
                sa.normalized_text,
                sa.primary_category,
                sa.secondary_category,
                sa.owner_department AS analysis_owner_department,
                sa.embedding_ref
            FROM review_tasks rt
            JOIN source_suggestions ss
                ON ss.source_suggestion_id = rt.source_suggestion_id
            LEFT JOIN suggestion_analysis sa
                ON sa.source_suggestion_id = rt.source_suggestion_id
            WHERE rt.review_task_id = ?
            """,
            (review_task_id,),
        ).fetchone()
        if task is None:
            raise KeyError(f"unknown review task: {review_task_id}")
        if task["status"] != "pending":
            raise ValueError(f"review task is not pending: {review_task_id}")

        source_suggestion_id = task["source_suggestion_id"]
        candidate_cluster_id = task["candidate_cluster_id"]
        affected_cluster_ids: set[str] = set()
        accepted_cluster_id: str | None = None

        if normalized_result == "approve":
            if not candidate_cluster_id:
                raise ValueError("approve requires candidate_cluster_id")
            self.get_issue_cluster(candidate_cluster_id)
            self._upsert_reviewed_cluster_member(
                cluster_id=candidate_cluster_id,
                source_suggestion_id=source_suggestion_id,
                decision_type="manual_review",
                decision_status="accepted",
                decision_reason="review_approved",
                reviewed_by=reviewed_by,
            )
            accepted_cluster_id = candidate_cluster_id
            affected_cluster_ids.add(candidate_cluster_id)

        if normalized_result == "reject":
            if candidate_cluster_id:
                self._upsert_reviewed_cluster_member(
                    cluster_id=candidate_cluster_id,
                    source_suggestion_id=source_suggestion_id,
                    decision_type="manual_review",
                    decision_status="rejected",
                    decision_reason="review_rejected",
                    reviewed_by=reviewed_by,
                )
                affected_cluster_ids.add(candidate_cluster_id)

        if normalized_result == "assign":
            if not target_cluster_id:
                raise ValueError("assign requires target_cluster_id")
            self.get_issue_cluster(target_cluster_id)
            if candidate_cluster_id and candidate_cluster_id != target_cluster_id:
                self._upsert_reviewed_cluster_member(
                    cluster_id=candidate_cluster_id,
                    source_suggestion_id=source_suggestion_id,
                    decision_type="manual_review",
                    decision_status="rejected",
                    decision_reason="review_reassigned",
                    reviewed_by=reviewed_by,
                )
                affected_cluster_ids.add(candidate_cluster_id)
            self._upsert_reviewed_cluster_member(
                cluster_id=target_cluster_id,
                source_suggestion_id=source_suggestion_id,
                decision_type="manual_reassign",
                decision_status="accepted",
                decision_reason="review_assigned",
                reviewed_by=reviewed_by,
            )
            accepted_cluster_id = target_cluster_id
            affected_cluster_ids.add(target_cluster_id)

        if normalized_result == "create_new":
            if candidate_cluster_id:
                self._upsert_reviewed_cluster_member(
                    cluster_id=candidate_cluster_id,
                    source_suggestion_id=source_suggestion_id,
                    decision_type="manual_review",
                    decision_status="rejected",
                    decision_reason="review_created_new_cluster",
                    reviewed_by=reviewed_by,
                )
                affected_cluster_ids.add(candidate_cluster_id)
            centroid_embedding = self._embedding_from_ref(task["embedding_ref"])
            accepted_cluster_id = self.create_issue_cluster(
                source_suggestion_id=source_suggestion_id,
                normalized_text=task["normalized_text"] or task["raw_text"],
                primary_category=task["primary_category"] or "",
                secondary_category=task["secondary_category"] or "Manual review",
                owner_department=task["analysis_owner_department"] or task["source_owner_department"] or "",
                scenario_key=task["scenario"] or "",
                centroid_embedding=centroid_embedding,
            )
            self._upsert_reviewed_cluster_member(
                cluster_id=accepted_cluster_id,
                source_suggestion_id=source_suggestion_id,
                decision_type="create_new_cluster",
                decision_status="accepted",
                decision_reason="review_created_new_cluster",
                reviewed_by=reviewed_by,
            )
            affected_cluster_ids.add(accepted_cluster_id)

        for cluster_id in affected_cluster_ids:
            self._recalculate_cluster_suggestion_count(cluster_id)

        now = utc_now()
        self.connection.execute(
            """
            UPDATE review_tasks
            SET status = ?, review_result = ?, reviewed_by = ?, reviewed_at = ?
            WHERE review_task_id = ?
            """,
            ("reviewed", normalized_result, reviewed_by, now, review_task_id),
        )
        self.connection.commit()
        return {
            "review_task_id": review_task_id,
            "review_result": normalized_result,
            "cluster_id": accepted_cluster_id,
        }

    def _embedding_from_ref(self, embedding_ref: str | None) -> list[float]:
        try:
            values = json.loads(embedding_ref or "[]")
        except json.JSONDecodeError:
            values = []
        if not isinstance(values, list):
            return []
        return [float(value) for value in values]

    def _upsert_reviewed_cluster_member(
        self,
        *,
        cluster_id: str,
        source_suggestion_id: str,
        decision_type: str,
        decision_status: str,
        decision_reason: str,
        reviewed_by: str,
    ) -> None:
        now = utc_now()
        existing = self.connection.execute(
            """
            SELECT vector_score, keyword_score, final_score
            FROM cluster_members
            WHERE cluster_id = ? AND source_suggestion_id = ?
            """,
            (cluster_id, source_suggestion_id),
        ).fetchone()
        vector_score = float(existing["vector_score"]) if existing is not None else 0.0
        keyword_score = float(existing["keyword_score"]) if existing is not None else 0.0
        final_score = float(existing["final_score"]) if existing is not None else 1.0
        self.connection.execute(
            """
            INSERT INTO cluster_members (
                cluster_id, source_suggestion_id, decision_type, vector_score,
                keyword_score, final_score, decision_status, decision_reason,
                reviewed_by, reviewed_at, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(cluster_id, source_suggestion_id) DO UPDATE SET
                decision_type = excluded.decision_type,
                vector_score = excluded.vector_score,
                keyword_score = excluded.keyword_score,
                final_score = excluded.final_score,
                decision_status = excluded.decision_status,
                decision_reason = excluded.decision_reason,
                reviewed_by = excluded.reviewed_by,
                reviewed_at = excluded.reviewed_at
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
                reviewed_by,
                now,
                now,
            ),
        )

    def _recalculate_cluster_suggestion_count(self, cluster_id: str) -> None:
        now = utc_now()
        row = self.connection.execute(
            """
            SELECT representative_suggestion_id
            FROM issue_clusters
            WHERE cluster_id = ?
            """,
            (cluster_id,),
        ).fetchone()
        if row is None:
            raise KeyError(f"unknown issue cluster: {cluster_id}")
        member_count = self.connection.execute(
            """
            SELECT COUNT(*) AS suggestion_count
            FROM cluster_members
            WHERE cluster_id = ? AND decision_status = 'accepted'
            """,
            (cluster_id,),
        ).fetchone()
        representative_member = self.connection.execute(
            """
            SELECT 1
            FROM cluster_members
            WHERE cluster_id = ?
                AND source_suggestion_id = ?
                AND decision_status = 'accepted'
            """,
            (cluster_id, row["representative_suggestion_id"]),
        ).fetchone()
        suggestion_count = int(member_count["suggestion_count"])
        if representative_member is None:
            suggestion_count += 1
        self.connection.execute(
            """
            UPDATE issue_clusters
            SET suggestion_count = ?, updated_at = ?, last_seen_at = ?
            WHERE cluster_id = ?
            """,
            (suggestion_count, now, now, cluster_id),
        )

    def count_table(self, table_name: str) -> int:
        if table_name not in COUNTABLE_TABLES:
            raise ValueError(f"cannot count unknown table: {table_name}")
        row = self.connection.execute(f"SELECT COUNT(*) AS item_count FROM {table_name}").fetchone()
        return int(row["item_count"])
