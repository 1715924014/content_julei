import sqlite3
import tempfile
import unittest
import json
from contextlib import closing
from pathlib import Path

from src.storage import Storage, connect_analysis_db


class StorageConnectionTests(unittest.TestCase):
    def test_connect_analysis_db_enables_wal_and_busy_timeout(self):
        with tempfile.TemporaryDirectory() as directory:
            db_path = Path(directory) / "analysis.db"
            with closing(connect_analysis_db(db_path)) as connection:
                storage = Storage(connection)
                storage.initialize_schema()
                busy_timeout = connection.execute("PRAGMA busy_timeout").fetchone()[0]
                journal_mode = connection.execute("PRAGMA journal_mode").fetchone()[0]

        self.assertGreaterEqual(busy_timeout, 30000)
        self.assertEqual(journal_mode.lower(), "wal")


class StorageTests(unittest.TestCase):
    def make_storage(self):
        storage = Storage(sqlite3.connect(":memory:"))
        storage.initialize_schema()
        return storage

    def test_initialize_schema_creates_query_indexes_for_incremental_growth(self):
        storage = self.make_storage()

        expected_indexes = {
            "idx_import_batches_source_status_batch",
            "idx_source_suggestions_created",
            "idx_suggestion_analysis_batch",
            "idx_suggestion_analysis_content_hash",
            "idx_issue_clusters_status_category_owner",
            "idx_cluster_members_source_status",
            "idx_cluster_members_cluster_status",
            "idx_review_tasks_status_priority_created",
        }
        actual_indexes = {
            row["name"]
            for row in storage.connection.execute(
                """
                SELECT name
                FROM sqlite_master
                WHERE type = 'index'
                """
            ).fetchall()
        }

        self.assertTrue(expected_indexes.issubset(actual_indexes))

    def test_deferred_commits_roll_back_uncommitted_writes_on_error(self):
        storage = self.make_storage()

        with self.assertRaises(RuntimeError):
            with storage.defer_commits():
                storage.start_import_batch("mysql", cursor_start="100")
                raise RuntimeError("stop batch")

        self.assertEqual(storage.count_table("import_batches"), 0)

    def test_record_import_failure_persists_row_context(self):
        storage = self.make_storage()
        batch_id = storage.start_import_batch("mysql", cursor_start="100")

        storage.record_import_failure(
            batch_id=batch_id,
            source_suggestion_id="M001",
            source_cursor="105",
            row_number=3,
            error_message="missing raw_text",
            raw_row={"suggestion_id": "M001", "raw_text": ""},
        )
        failures = storage.list_import_failures(batch_id)

        self.assertEqual(len(failures), 1)
        self.assertEqual(failures[0]["source_suggestion_id"], "M001")
        self.assertEqual(failures[0]["source_cursor"], "105")
        self.assertEqual(failures[0]["row_number"], 3)
        self.assertEqual(failures[0]["error_message"], "missing raw_text")
        self.assertIn('"raw_text": ""', failures[0]["raw_row_json"])

    def test_get_latest_failure_batch_id_returns_most_recent_batch_with_failures(self):
        storage = self.make_storage()
        first = storage.start_import_batch("mysql", cursor_start="100")
        storage.record_import_failure(
            batch_id=first,
            source_suggestion_id="M001",
            source_cursor="101",
            row_number=1,
            error_message="missing raw_text",
            raw_row={"id": 101},
        )
        empty = storage.start_import_batch("mysql", cursor_start="101")
        storage.finish_import_batch(
            empty,
            "102",
            rows_read=1,
            rows_created=1,
            rows_skipped=0,
            rows_failed=0,
        )
        latest = storage.start_import_batch("mysql", cursor_start="102")
        storage.record_import_failure(
            batch_id=latest,
            source_suggestion_id="M003",
            source_cursor="103",
            row_number=1,
            error_message="invalid id",
            raw_row={"id": 103},
        )
        other_source = storage.start_import_batch("csv", cursor_start="0")
        storage.record_import_failure(
            batch_id=other_source,
            source_suggestion_id="C001",
            source_cursor="1",
            row_number=1,
            error_message="csv error",
            raw_row={"id": 1},
        )

        self.assertEqual(storage.get_latest_failure_batch_id("mysql"), latest)
        self.assertEqual(storage.get_latest_failure_batch_id("csv"), other_source)
        self.assertIsNone(storage.get_latest_failure_batch_id("missing"))

    def test_import_batch_lifecycle_stores_success_status_cursor_and_counts(self):
        storage = self.make_storage()

        batch_id = storage.start_import_batch("suggestions.csv", cursor_start="10")
        storage.finish_import_batch(
            batch_id,
            "25",
            rows_read=12,
            rows_created=8,
            rows_skipped=4,
            rows_failed=0,
        )

        batch = storage.get_import_batch(batch_id)
        self.assertEqual(batch["source_name"], "suggestions.csv")
        self.assertEqual(batch["cursor_start"], "10")
        self.assertEqual(batch["status"], "success")
        self.assertEqual(batch["cursor_end"], "25")
        self.assertEqual(batch["rows_read"], 12)
        self.assertEqual(batch["rows_created"], 8)
        self.assertEqual(batch["rows_skipped"], 4)
        self.assertEqual(batch["rows_failed"], 0)
        self.assertIsNone(batch["error_summary"])
        self.assertIsNotNone(batch["started_at"])
        self.assertIsNotNone(batch["finished_at"])

    def test_import_batch_finish_records_partial_status_when_rows_fail(self):
        storage = self.make_storage()

        batch_id = storage.start_import_batch("suggestions.csv", cursor_start="25")
        storage.finish_import_batch(
            batch_id,
            "40",
            rows_read=15,
            rows_created=10,
            rows_skipped=2,
            rows_failed=3,
            error_summary="3 rows missing raw_text",
        )

        batch = storage.get_import_batch(batch_id)
        self.assertEqual(batch["status"], "partial")
        self.assertEqual(batch["rows_failed"], 3)
        self.assertEqual(batch["error_summary"], "3 rows missing raw_text")

    def test_get_import_batch_raises_for_unknown_batch(self):
        storage = self.make_storage()

        with self.assertRaises(KeyError):
            storage.get_import_batch(999)

    def test_latest_successful_cursor_uses_most_recent_success_for_source(self):
        storage = self.make_storage()

        first = storage.start_import_batch("mysql", cursor_start="0")
        storage.finish_import_batch(
            first,
            "100",
            rows_read=10,
            rows_created=10,
            rows_skipped=0,
            rows_failed=0,
        )
        partial = storage.start_import_batch("mysql", cursor_start="100")
        storage.finish_import_batch(
            partial,
            "200",
            rows_read=10,
            rows_created=9,
            rows_skipped=0,
            rows_failed=1,
        )
        other_source = storage.start_import_batch("csv", cursor_start="0")
        storage.finish_import_batch(
            other_source,
            "999",
            rows_read=1,
            rows_created=1,
            rows_skipped=0,
            rows_failed=0,
        )

        self.assertEqual(storage.get_latest_successful_cursor("mysql"), "100")
        self.assertEqual(storage.get_latest_successful_cursor("missing"), "")

    def test_import_status_summary_reports_latest_batch_cursor_and_counts(self):
        storage = self.make_storage()
        first = storage.start_import_batch("mysql", cursor_start="0")
        storage.finish_import_batch(
            first,
            "100",
            rows_read=10,
            rows_created=10,
            rows_skipped=0,
            rows_failed=0,
        )
        second = storage.start_import_batch("mysql", cursor_start="100")
        storage.finish_import_batch(
            second,
            "125",
            rows_read=25,
            rows_created=20,
            rows_skipped=4,
            rows_failed=1,
            error_summary="1 row missing raw_text",
        )
        storage.upsert_source_suggestion(
            {
                "source_suggestion_id": "M001",
                "submit_date": "2026-06-16",
                "created_at": "2026-06-16",
                "raw_text": "夜班食堂没有热饭",
                "department": "生产一部",
                "job_group": "一线",
                "work_location": "A厂区",
                "scenario": "食堂",
                "status": "待识别",
                "owner_department": "",
            }
        )

        summary = storage.get_import_status_summary("mysql")

        self.assertEqual(summary["source_name"], "mysql")
        self.assertEqual(summary["latest_batch"]["batch_id"], second)
        self.assertEqual(summary["latest_batch"]["status"], "partial")
        self.assertEqual(summary["latest_batch"]["cursor_start"], "100")
        self.assertEqual(summary["latest_batch"]["cursor_end"], "125")
        self.assertEqual(summary["latest_successful_cursor"], "100")
        self.assertEqual(summary["table_counts"]["import_batches"], 2)
        self.assertEqual(summary["table_counts"]["source_suggestions"], 1)
        self.assertEqual(summary["health"]["status"], "attention")
        self.assertIn("latest_batch_has_failed_rows", summary["health"]["reasons"])

    def test_import_status_summary_warns_when_latest_batch_is_running(self):
        storage = self.make_storage()
        storage.start_import_batch("mysql", cursor_start="100")

        summary = storage.get_import_status_summary("mysql")

        self.assertEqual(summary["health"]["status"], "warning")
        self.assertIn("latest_batch_still_running", summary["health"]["reasons"])

    def test_import_status_summary_warns_when_review_tasks_are_pending(self):
        storage = self.make_storage()
        batch_id = storage.start_import_batch("mysql", cursor_start="0")
        storage.finish_import_batch(
            batch_id,
            "100",
            rows_read=10,
            rows_created=10,
            rows_skipped=0,
            rows_failed=0,
        )
        storage.upsert_source_suggestion(
            {
                "source_suggestion_id": "M002",
                "submit_date": "2026-06-16",
                "created_at": "2026-06-16",
                "raw_text": "Need clearer shift handover",
                "department": "Production",
                "job_group": "Operator",
                "work_location": "Plant A",
                "scenario": "Shift",
                "status": "new",
                "owner_department": "Production",
            }
        )
        storage.create_review_task(
            source_suggestion_id="M002",
            candidate_cluster_id=None,
            task_type="manual_cluster_review",
            priority=90,
            evidence={"reason": "low confidence"},
        )

        summary = storage.get_import_status_summary("mysql")

        self.assertEqual(summary["pending_review_tasks"], 1)
        self.assertEqual(summary["health"]["status"], "warning")
        self.assertIn("pending_review_tasks", summary["health"]["reasons"])

    def test_import_status_summary_reports_ok_health_when_import_is_clean(self):
        storage = self.make_storage()
        batch_id = storage.start_import_batch("mysql", cursor_start="0")
        storage.finish_import_batch(
            batch_id,
            "100",
            rows_read=10,
            rows_created=10,
            rows_skipped=0,
            rows_failed=0,
        )

        summary = storage.get_import_status_summary("mysql")

        self.assertEqual(summary["pending_review_tasks"], 0)
        self.assertEqual(summary["health"], {"status": "ok", "reasons": []})

    def test_import_status_summary_warns_when_latest_batch_reaches_daily_limit(self):
        storage = self.make_storage()
        batch_id = storage.start_import_batch("mysql", cursor_start="0")
        storage.finish_import_batch(
            batch_id,
            "10000",
            rows_read=10000,
            rows_created=10000,
            rows_skipped=0,
            rows_failed=0,
        )

        summary = storage.get_import_status_summary("mysql", daily_limit=10000)

        self.assertTrue(summary["latest_batch_limit_reached"])
        self.assertEqual(summary["health"]["status"], "warning")
        self.assertIn("latest_batch_reached_daily_limit", summary["health"]["reasons"])
        self.assertIn("run_additional_import_or_increase_limit", summary["recommended_actions"])

    def test_import_status_summary_warns_when_latest_batch_exceeds_duration_threshold(self):
        storage = self.make_storage()
        batch_id = storage.start_import_batch("mysql", cursor_start="0")
        storage.finish_import_batch(
            batch_id,
            "100",
            rows_read=100,
            rows_created=100,
            rows_skipped=0,
            rows_failed=0,
        )
        storage.connection.execute(
            """
            UPDATE import_batches
            SET started_at = ?, finished_at = ?
            WHERE batch_id = ?
            """,
            ("2026-06-23T00:00:00+00:00", "2026-06-23T00:30:00+00:00", batch_id),
        )
        storage.connection.commit()

        summary = storage.get_import_status_summary("mysql", max_duration_seconds=1200)

        self.assertEqual(summary["latest_batch_duration_seconds"], 1800)
        self.assertTrue(summary["latest_batch_duration_exceeded"])
        self.assertEqual(summary["health"]["status"], "warning")
        self.assertIn("latest_batch_exceeded_max_duration", summary["health"]["reasons"])

    def test_import_status_summary_reports_latest_batch_throughput(self):
        storage = self.make_storage()
        batch_id = storage.start_import_batch("mysql", cursor_start="0")
        storage.finish_import_batch(
            batch_id,
            "600",
            rows_read=600,
            rows_created=600,
            rows_skipped=0,
            rows_failed=0,
        )
        storage.connection.execute(
            """
            UPDATE import_batches
            SET started_at = ?, finished_at = ?
            WHERE batch_id = ?
            """,
            ("2026-06-23T00:00:00+00:00", "2026-06-23T00:10:00+00:00", batch_id),
        )
        storage.connection.commit()

        summary = storage.get_import_status_summary("mysql")

        self.assertEqual(summary["latest_batch_duration_seconds"], 600)
        self.assertEqual(summary["latest_batch_rows_per_second"], 1.0)

    def test_import_status_summary_warns_when_throughput_is_below_threshold(self):
        storage = self.make_storage()
        batch_id = storage.start_import_batch("mysql", cursor_start="0")
        storage.finish_import_batch(
            batch_id,
            "600",
            rows_read=600,
            rows_created=600,
            rows_skipped=0,
            rows_failed=0,
        )
        storage.connection.execute(
            """
            UPDATE import_batches
            SET started_at = ?, finished_at = ?
            WHERE batch_id = ?
            """,
            ("2026-06-23T00:00:00+00:00", "2026-06-23T00:10:00+00:00", batch_id),
        )
        storage.connection.commit()

        summary = storage.get_import_status_summary("mysql", min_throughput_rows_per_second=2.0)

        self.assertEqual(summary["latest_batch_rows_per_second"], 1.0)
        self.assertTrue(summary["latest_batch_throughput_below_minimum"])
        self.assertEqual(summary["health"]["status"], "warning")
        self.assertIn("latest_batch_below_min_throughput", summary["health"]["reasons"])

    def test_source_suggestion_upsert_is_idempotent_for_same_classification_fields(self):
        storage = self.make_storage()
        row = {
            "source_suggestion_id": "S001",
            "submit_date": "2026-06-01",
            "raw_text": "Need hotter canteen meals at night",
            "department": "Production",
            "scenario": "Canteen",
            "status": "new",
        }

        self.assertTrue(storage.upsert_source_suggestion(row))
        self.assertFalse(storage.upsert_source_suggestion(dict(row)))
        self.assertEqual(storage.count_table("source_suggestions"), 1)

        changed_status = dict(row, status="triaged")
        self.assertTrue(storage.upsert_source_suggestion(changed_status))
        self.assertEqual(storage.count_table("source_suggestions"), 1)

    def test_source_suggestion_upsert_detects_reporting_field_changes(self):
        storage = self.make_storage()
        row = {
            "source_suggestion_id": "S001",
            "submit_date": "2026-06-01",
            "created_at": "2026-06-01",
            "raw_text": "Need hotter canteen meals at night",
            "department": "Production",
            "job_group": "Line worker",
            "work_location": "Plant A",
            "scenario": "Canteen",
            "status": "new",
            "owner_department": "Facilities",
        }

        self.assertTrue(storage.upsert_source_suggestion(row))
        self.assertTrue(storage.upsert_source_suggestion(dict(row, department="Operations")))
        changed_scenario = dict(row, scenario="Night canteen")
        self.assertTrue(storage.upsert_source_suggestion(changed_scenario))
        self.assertTrue(
            storage.upsert_source_suggestion(
                dict(changed_scenario, owner_department="Operations Excellence")
            )
        )
        stored = storage.connection.execute(
            """
            SELECT department, scenario, owner_department
            FROM source_suggestions
            WHERE source_suggestion_id = ?
            """,
            ("S001",),
        ).fetchone()

        self.assertEqual(stored["department"], "Production")
        self.assertEqual(stored["scenario"], "Night canteen")
        self.assertEqual(stored["owner_department"], "Operations Excellence")

    def test_list_pending_review_tasks_includes_source_and_candidate_cluster_context(self):
        storage = self.make_storage()
        storage.upsert_source_suggestion(
            {
                "source_suggestion_id": "S001",
                "submit_date": "2026-06-01",
                "created_at": "2026-06-01",
                "raw_text": "Night shift canteen meals are cold",
                "department": "Production",
                "job_group": "Operator",
                "work_location": "Plant A",
                "scenario": "Canteen",
                "status": "new",
                "owner_department": "Facilities",
            }
        )
        cluster_id = storage.create_issue_cluster(
            source_suggestion_id="S001",
            normalized_text="Night shift employees report cold meals.",
            primary_category="Logistics",
            secondary_category="Night canteen hot meals",
            owner_department="Facilities",
            scenario_key="canteen",
            centroid_embedding=[0.1, 0.2, 0.3],
        )
        storage.create_review_task(
            source_suggestion_id="S001",
            candidate_cluster_id=cluster_id,
            task_type="manual_cluster_review",
            priority=80,
            evidence={"final_score": 0.72, "reason": "borderline vector match"},
        )

        tasks = storage.list_pending_review_tasks()

        self.assertEqual(len(tasks), 1)
        task = tasks[0]
        self.assertEqual(task["source_suggestion_id"], "S001")
        self.assertEqual(task["candidate_cluster_id"], cluster_id)
        self.assertEqual(task["candidate_cluster_name"], "Night canteen hot meals")
        self.assertEqual(task["raw_text"], "Night shift canteen meals are cold")
        self.assertEqual(task["department"], "Production")
        self.assertEqual(task["scenario"], "Canteen")
        self.assertEqual(json.loads(task["evidence_json"])["final_score"], 0.72)

    def test_list_persisted_export_rows_include_analysis_and_cluster_summaries(self):
        storage = self.make_storage()
        batch_id = storage.start_import_batch("mysql", cursor_start="0")
        storage.upsert_source_suggestion(
            {
                "source_suggestion_id": "S001",
                "submit_date": "2026-06-01",
                "created_at": "2026-06-01",
                "raw_text": "Night shift canteen meals are cold",
                "department": "Production",
                "job_group": "Operator",
                "work_location": "Plant A",
                "scenario": "Canteen",
                "status": "new",
                "owner_department": "Facilities",
            },
            import_batch_id=batch_id,
        )
        storage.upsert_suggestion_analysis(
            {
                "source_suggestion_id": "S001",
                "batch_id": batch_id,
                "normalized_text": "night shift canteen meals are cold",
                "content_hash": "hash-001",
                "primary_category": "Logistics",
                "secondary_category": "Canteen",
                "owner_department": "Facilities",
                "quality_type": "normal",
                "urgency_level": "medium",
                "classification_confidence": 0.82,
                "embedding_status": "ready",
                "embedding_model": "test",
                "embedding_ref": json.dumps([0.1, 0.2]),
                "review_required": "no",
                "analysis_status": "analyzed",
            }
        )
        cluster_id = storage.create_issue_cluster(
            source_suggestion_id="S001",
            normalized_text="night shift canteen meals are cold",
            primary_category="Logistics",
            secondary_category="Canteen",
            owner_department="Facilities",
            scenario_key="Canteen",
            centroid_embedding=[0.1, 0.2],
        )
        storage.add_cluster_member(
            cluster_id=cluster_id,
            source_suggestion_id="S001",
            decision_type="create_new_cluster",
            vector_score=1.0,
            keyword_score=1.0,
            final_score=1.0,
            decision_status="accepted",
            decision_reason="new_cluster",
        )

        suggestion_rows = storage.list_persisted_suggestion_export_rows()
        cluster_rows = storage.list_persisted_cluster_export_rows()

        self.assertEqual(suggestion_rows[0]["source_suggestion_id"], "S001")
        self.assertEqual(suggestion_rows[0]["raw_text"], "Night shift canteen meals are cold")
        self.assertEqual(suggestion_rows[0]["primary_category"], "Logistics")
        self.assertEqual(suggestion_rows[0]["cluster_id"], cluster_id)
        self.assertEqual(suggestion_rows[0]["cluster_name"], "Canteen")
        self.assertEqual(cluster_rows[0]["cluster_id"], cluster_id)
        self.assertEqual(cluster_rows[0]["suggestion_count"], 1)
        self.assertEqual(cluster_rows[0]["departments"], "Production")

    def test_upsert_action_item_for_cluster_creates_and_refreshes_persisted_todo(self):
        storage = self.make_storage()
        storage.upsert_source_suggestion(
            {
                "source_suggestion_id": "S001",
                "submit_date": "2026-06-01",
                "created_at": "2026-06-01",
                "raw_text": "Night shift canteen meals are cold",
                "department": "Production",
                "scenario": "Canteen",
                "owner_department": "Facilities",
            }
        )
        cluster_id = storage.create_issue_cluster(
            source_suggestion_id="S001",
            normalized_text="Night shift canteen meals are cold",
            primary_category="Logistics",
            secondary_category="Canteen",
            owner_department="Facilities",
            scenario_key="Canteen",
            centroid_embedding=[0.1, 0.2],
        )

        storage.upsert_action_item_for_cluster(cluster_id)
        first = storage.connection.execute("SELECT * FROM action_items WHERE cluster_id = ?", (cluster_id,)).fetchone()
        storage.connection.execute(
            """
            UPDATE issue_clusters
            SET suggestion_count = 3,
                cluster_name = 'Night shift canteen meals'
            WHERE cluster_id = ?
            """,
            (cluster_id,),
        )
        storage.connection.commit()
        storage.upsert_action_item_for_cluster(cluster_id)
        refreshed = storage.connection.execute("SELECT * FROM action_items WHERE cluster_id = ?", (cluster_id,)).fetchone()

        self.assertEqual(first["action_id"], f"A-{cluster_id}")
        self.assertEqual(first["status"], "watchlist")
        self.assertEqual(refreshed["action_title"], "Night shift canteen meals")
        self.assertEqual(refreshed["suggestion_count"], 3)
        self.assertEqual(refreshed["status"], "pending_dispatch")
        self.assertEqual(refreshed["first_seen_at"], first["first_seen_at"])
        self.assertGreaterEqual(refreshed["last_seen_at"], first["last_seen_at"])

    def test_list_active_cluster_vectors_filters_by_category_and_owner(self):
        storage = self.make_storage()
        matching = storage.create_issue_cluster(
            source_suggestion_id="S001",
            normalized_text="night canteen meals are cold",
            primary_category="Logistics",
            secondary_category="Canteen",
            owner_department="Facilities",
            scenario_key="Canteen",
            centroid_embedding=[0.1, 0.2],
        )
        storage.create_issue_cluster(
            source_suggestion_id="S002",
            normalized_text="workshop masks are insufficient",
            primary_category="Safety",
            secondary_category="Labor protection",
            owner_department="Safety",
            scenario_key="Safety",
            centroid_embedding=[0.3, 0.4],
        )

        clusters = storage.list_active_cluster_vectors(
            primary_category="Logistics",
            secondary_category="Canteen",
            owner_department="Facilities",
        )

        self.assertEqual([cluster.cluster_id for cluster in clusters], [matching])

    def test_apply_review_task_result_approves_pending_cluster_member_and_updates_count(self):
        storage = self.make_storage()
        storage.upsert_source_suggestion(
            {
                "source_suggestion_id": "S001",
                "submit_date": "2026-06-01",
                "created_at": "2026-06-01",
                "raw_text": "Night shift canteen meals are cold",
                "department": "Production",
                "job_group": "Operator",
                "work_location": "Plant A",
                "scenario": "Canteen",
                "status": "new",
                "owner_department": "Facilities",
            }
        )
        storage.upsert_source_suggestion(
            {
                "source_suggestion_id": "S002",
                "submit_date": "2026-06-02",
                "created_at": "2026-06-02",
                "raw_text": "Night shift needs hot meals",
                "department": "Production",
                "job_group": "Operator",
                "work_location": "Plant A",
                "scenario": "Canteen",
                "status": "new",
                "owner_department": "Facilities",
            }
        )
        cluster_id = storage.create_issue_cluster(
            source_suggestion_id="S001",
            normalized_text="Night shift canteen meals are cold",
            primary_category="Logistics",
            secondary_category="Canteen",
            owner_department="Facilities",
            scenario_key="canteen",
            centroid_embedding=[0.1, 0.2, 0.3],
        )
        storage.add_cluster_member(
            cluster_id=cluster_id,
            source_suggestion_id="S002",
            decision_type="manual_review",
            vector_score=0.74,
            keyword_score=0.5,
            final_score=0.72,
            decision_status="pending",
            decision_reason="score_above_manual_review_threshold",
        )
        storage.create_review_task(
            source_suggestion_id="S002",
            candidate_cluster_id=cluster_id,
            task_type="cluster_match",
            priority=1,
            evidence={"final_score": 0.72},
        )
        review_task_id = storage.list_pending_review_tasks()[0]["review_task_id"]

        storage.apply_review_task_result(
            review_task_id=review_task_id,
            review_result="approve",
            reviewed_by="ops-user",
        )

        member = storage.connection.execute(
            """
            SELECT decision_status, reviewed_by, reviewed_at
            FROM cluster_members
            WHERE cluster_id = ? AND source_suggestion_id = ?
            """,
            (cluster_id, "S002"),
        ).fetchone()
        task = storage.connection.execute("SELECT status, review_result FROM review_tasks").fetchone()
        cluster = storage.get_issue_cluster(cluster_id)
        self.assertEqual(member["decision_status"], "accepted")
        self.assertEqual(member["reviewed_by"], "ops-user")
        self.assertIsNotNone(member["reviewed_at"])
        self.assertEqual(task["status"], "reviewed")
        self.assertEqual(task["review_result"], "approve")
        self.assertEqual(cluster["suggestion_count"], 2)

    def test_apply_review_task_result_rejects_candidate_cluster_member(self):
        storage = self.make_storage()
        storage.upsert_source_suggestion(
            {
                "source_suggestion_id": "S001",
                "submit_date": "2026-06-01",
                "created_at": "2026-06-01",
                "raw_text": "Dorm toilet smells bad",
                "department": "Production",
                "scenario": "Dorm",
            }
        )
        storage.upsert_source_suggestion(
            {
                "source_suggestion_id": "S002",
                "submit_date": "2026-06-02",
                "created_at": "2026-06-02",
                "raw_text": "Workshop dust masks are not enough",
                "department": "Production",
                "scenario": "Safety",
            }
        )
        cluster_id = storage.create_issue_cluster(
            source_suggestion_id="S001",
            normalized_text="Dorm toilet smells bad",
            primary_category="Logistics",
            secondary_category="Dorm hygiene",
            owner_department="Facilities",
            scenario_key="dorm",
            centroid_embedding=[0.1, 0.2],
        )
        storage.add_cluster_member(
            cluster_id=cluster_id,
            source_suggestion_id="S002",
            decision_type="manual_review",
            vector_score=0.73,
            keyword_score=0.2,
            final_score=0.72,
            decision_status="pending",
            decision_reason="score_above_manual_review_threshold",
        )
        storage.create_review_task(
            source_suggestion_id="S002",
            candidate_cluster_id=cluster_id,
            task_type="cluster_match",
            priority=1,
            evidence={"final_score": 0.72},
        )
        review_task_id = storage.list_pending_review_tasks()[0]["review_task_id"]

        storage.apply_review_task_result(
            review_task_id=review_task_id,
            review_result="reject",
            reviewed_by="ops-user",
        )

        member = storage.connection.execute("SELECT decision_status FROM cluster_members WHERE source_suggestion_id = ?", ("S002",)).fetchone()
        task = storage.connection.execute("SELECT status, review_result FROM review_tasks").fetchone()
        cluster = storage.get_issue_cluster(cluster_id)
        self.assertEqual(member["decision_status"], "rejected")
        self.assertEqual(task["status"], "reviewed")
        self.assertEqual(task["review_result"], "reject")
        self.assertEqual(cluster["suggestion_count"], 1)

    def test_apply_review_task_result_assigns_to_target_cluster(self):
        storage = self.make_storage()
        for suggestion_id, raw_text, scenario in [
            ("S001", "Dorm toilet smells bad", "Dorm"),
            ("S002", "Workshop dust masks are not enough", "Safety"),
            ("S003", "Masks are insufficient near dusty line", "Safety"),
        ]:
            storage.upsert_source_suggestion(
                {
                    "source_suggestion_id": suggestion_id,
                    "submit_date": "2026-06-01",
                    "created_at": "2026-06-01",
                    "raw_text": raw_text,
                    "department": "Production",
                    "scenario": scenario,
                }
            )
        candidate_cluster_id = storage.create_issue_cluster(
            source_suggestion_id="S001",
            normalized_text="Dorm toilet smells bad",
            primary_category="Logistics",
            secondary_category="Dorm hygiene",
            owner_department="Facilities",
            scenario_key="dorm",
            centroid_embedding=[0.1, 0.2],
        )
        target_cluster_id = storage.create_issue_cluster(
            source_suggestion_id="S003",
            normalized_text="Masks are insufficient near dusty line",
            primary_category="Safety",
            secondary_category="Labor protection",
            owner_department="Safety",
            scenario_key="safety",
            centroid_embedding=[0.3, 0.4],
        )
        storage.add_cluster_member(
            cluster_id=candidate_cluster_id,
            source_suggestion_id="S002",
            decision_type="manual_review",
            vector_score=0.73,
            keyword_score=0.2,
            final_score=0.72,
            decision_status="pending",
            decision_reason="score_above_manual_review_threshold",
        )
        storage.create_review_task(
            source_suggestion_id="S002",
            candidate_cluster_id=candidate_cluster_id,
            task_type="cluster_match",
            priority=1,
            evidence={"final_score": 0.72},
        )
        review_task_id = storage.list_pending_review_tasks()[0]["review_task_id"]

        storage.apply_review_task_result(
            review_task_id=review_task_id,
            review_result="assign",
            reviewed_by="ops-user",
            target_cluster_id=target_cluster_id,
        )

        candidate_member = storage.connection.execute(
            "SELECT decision_status FROM cluster_members WHERE cluster_id = ? AND source_suggestion_id = ?",
            (candidate_cluster_id, "S002"),
        ).fetchone()
        target_member = storage.connection.execute(
            "SELECT decision_status, decision_type FROM cluster_members WHERE cluster_id = ? AND source_suggestion_id = ?",
            (target_cluster_id, "S002"),
        ).fetchone()
        self.assertEqual(candidate_member["decision_status"], "rejected")
        self.assertEqual(target_member["decision_status"], "accepted")
        self.assertEqual(target_member["decision_type"], "manual_reassign")
        self.assertEqual(storage.get_issue_cluster(candidate_cluster_id)["suggestion_count"], 1)
        self.assertEqual(storage.get_issue_cluster(target_cluster_id)["suggestion_count"], 2)

    def test_apply_review_task_result_creates_new_cluster_from_reviewed_source(self):
        storage = self.make_storage()
        batch_id = storage.start_import_batch("mysql", cursor_start="0")
        storage.upsert_source_suggestion(
            {
                "source_suggestion_id": "S001",
                "submit_date": "2026-06-01",
                "created_at": "2026-06-01",
                "raw_text": "Workshop dust masks are not enough",
                "department": "Production",
                "job_group": "Operator",
                "work_location": "Plant A",
                "scenario": "Safety",
                "status": "new",
                "owner_department": "Safety",
            },
            import_batch_id=batch_id,
        )
        storage.upsert_suggestion_analysis(
            {
                "source_suggestion_id": "S001",
                "batch_id": batch_id,
                "normalized_text": "workshop dust masks are not enough",
                "content_hash": "hash-001",
                "primary_category": "Safety",
                "secondary_category": "Labor protection",
                "owner_department": "Safety",
                "quality_type": "normal",
                "urgency_level": "medium",
                "classification_confidence": 0.8,
                "embedding_status": "ready",
                "embedding_model": "test",
                "embedding_ref": json.dumps([0.3, 0.4]),
                "review_required": "yes",
                "analysis_status": "analyzed",
            }
        )
        storage.create_review_task(
            source_suggestion_id="S001",
            candidate_cluster_id=None,
            task_type="cluster_match",
            priority=1,
            evidence={"reason": "no suitable candidate"},
        )
        review_task_id = storage.list_pending_review_tasks()[0]["review_task_id"]

        result = storage.apply_review_task_result(
            review_task_id=review_task_id,
            review_result="create_new",
            reviewed_by="ops-user",
        )

        member = storage.connection.execute(
            "SELECT cluster_id, decision_status, decision_type FROM cluster_members WHERE source_suggestion_id = ?",
            ("S001",),
        ).fetchone()
        cluster = storage.get_issue_cluster(result["cluster_id"])
        self.assertEqual(member["cluster_id"], result["cluster_id"])
        self.assertEqual(member["decision_status"], "accepted")
        self.assertEqual(member["decision_type"], "create_new_cluster")
        self.assertEqual(cluster["secondary_category"], "Labor protection")
        self.assertEqual(cluster["suggestion_count"], 1)


if __name__ == "__main__":
    unittest.main()
