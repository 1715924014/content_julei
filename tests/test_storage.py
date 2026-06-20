import sqlite3
import unittest
import json

from src.storage import Storage


class StorageTests(unittest.TestCase):
    def make_storage(self):
        storage = Storage(sqlite3.connect(":memory:"))
        storage.initialize_schema()
        return storage

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
