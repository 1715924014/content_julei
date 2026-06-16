import csv
import sqlite3
import tempfile
import unittest
from pathlib import Path

from src.batch import run_csv_import_batch, run_rows_import_batch
from src.classification import CATEGORY_RULES
from src.domain import INPUT_FIELDS
from src.storage import Storage


class CsvImportBatchTests(unittest.TestCase):
    def make_storage(self):
        storage = Storage(sqlite3.connect(":memory:"))
        storage.initialize_schema()
        return storage

    def write_csv(self, directory: str, rows: list[dict[str, str]] | None = None) -> Path:
        input_path = Path(directory) / "suggestions.csv"
        if rows is None:
            rows = [
                {
                    "suggestion_id": "S001",
                    "submit_date": "2026-06-01",
                    "raw_text": "Night shift canteen meals are cold and need reheating",
                    "department": "Production",
                    "job_group": "Line worker",
                    "work_location": "Plant A",
                    "scenario": "Canteen",
                    "status": "new",
                }
            ]
        with input_path.open("w", encoding="utf-8-sig", newline="") as file:
            writer = csv.DictWriter(file, fieldnames=INPUT_FIELDS)
            writer.writeheader()
            for row in rows:
                csv_row = {field: "" for field in INPUT_FIELDS}
                csv_row.update(row)
                writer.writerow(csv_row)
        return input_path

    def test_run_csv_import_batch_is_idempotent_for_existing_suggestion(self):
        storage = self.make_storage()
        with tempfile.TemporaryDirectory() as directory:
            input_path = self.write_csv(directory)

            first = run_csv_import_batch(storage, input_path)
            self.assertEqual(storage.count_table("source_suggestions"), 1)
            self.assertEqual(storage.count_table("suggestion_analysis"), 1)

            second = run_csv_import_batch(storage, input_path)
            analysis_batch_id = storage.connection.execute(
                """
                SELECT batch_id
                FROM suggestion_analysis
                WHERE source_suggestion_id = ?
                """,
                ("S001",),
            ).fetchone()["batch_id"]

        self.assertEqual(first.rows_read, 1)
        self.assertEqual(first.rows_created, 1)
        self.assertEqual(first.rows_skipped, 0)
        self.assertEqual(first.rows_failed, 0)
        self.assertEqual(second.rows_read, 1)
        self.assertEqual(second.rows_created, 0)
        self.assertEqual(second.rows_skipped, 1)
        self.assertEqual(second.rows_failed, 0)
        self.assertEqual(analysis_batch_id, first.batch_id)
        self.assertEqual(storage.count_table("source_suggestions"), 1)
        self.assertEqual(storage.count_table("suggestion_analysis"), 1)

    def test_reimport_refreshes_analysis_when_reporting_fields_change(self):
        storage = self.make_storage()
        with tempfile.TemporaryDirectory() as directory:
            input_path = self.write_csv(directory)
            first = run_csv_import_batch(storage, input_path)

            changed_path = self.write_csv(
                directory,
                [
                    {
                        "suggestion_id": "S001",
                        "submit_date": "2026-06-01",
                        "raw_text": "Night shift canteen meals are cold and need reheating",
                        "department": "Operations",
                        "job_group": "Line worker",
                        "work_location": "Plant A",
                        "scenario": "Night canteen",
                        "status": "new",
                    }
                ],
            )
            second = run_csv_import_batch(storage, changed_path)
            source = storage.connection.execute(
                """
                SELECT department, scenario
                FROM source_suggestions
                WHERE source_suggestion_id = ?
                """,
                ("S001",),
            ).fetchone()
            analysis_batch_id = storage.connection.execute(
                """
                SELECT batch_id
                FROM suggestion_analysis
                WHERE source_suggestion_id = ?
                """,
                ("S001",),
            ).fetchone()["batch_id"]

        self.assertEqual(first.rows_created, 1)
        self.assertEqual(second.rows_created, 1)
        self.assertEqual(second.rows_skipped, 0)
        self.assertEqual(source["department"], "Operations")
        self.assertEqual(source["scenario"], "Night canteen")
        self.assertEqual(analysis_batch_id, second.batch_id)

    def test_reimport_refreshes_analysis_when_owner_department_changes(self):
        storage = self.make_storage()
        with tempfile.TemporaryDirectory() as directory:
            input_path = self.write_csv(
                directory,
                [
                    {
                        "suggestion_id": "S001",
                        "submit_date": "2026-06-01",
                        "raw_text": "Night shift canteen meals are cold and need reheating",
                        "department": "Production",
                        "job_group": "Line worker",
                        "work_location": "Plant A",
                        "scenario": "Canteen",
                        "status": "new",
                        "owner_department": "Facilities",
                    }
                ],
            )
            first = run_csv_import_batch(storage, input_path)

            changed_path = self.write_csv(
                directory,
                [
                    {
                        "suggestion_id": "S001",
                        "submit_date": "2026-06-01",
                        "raw_text": "Night shift canteen meals are cold and need reheating",
                        "department": "Production",
                        "job_group": "Line worker",
                        "work_location": "Plant A",
                        "scenario": "Canteen",
                        "status": "new",
                        "owner_department": "Operations Excellence",
                    }
                ],
            )
            second = run_csv_import_batch(storage, changed_path)
            analysis = storage.connection.execute(
                """
                SELECT batch_id, owner_department
                FROM suggestion_analysis
                WHERE source_suggestion_id = ?
                """,
                ("S001",),
            ).fetchone()

        self.assertEqual(first.rows_created, 1)
        self.assertEqual(second.rows_created, 1)
        self.assertEqual(second.rows_skipped, 0)
        self.assertEqual(analysis["batch_id"], second.batch_id)
        self.assertEqual(analysis["owner_department"], "Operations Excellence")

    def test_first_import_creates_issue_cluster_and_member(self):
        storage = self.make_storage()
        with tempfile.TemporaryDirectory() as directory:
            input_path = self.write_csv(directory)

            result = run_csv_import_batch(storage, input_path)
            cluster = storage.connection.execute("SELECT * FROM issue_clusters").fetchone()
            member = storage.connection.execute("SELECT * FROM cluster_members").fetchone()

        self.assertEqual(result.rows_failed, 0)
        self.assertEqual(storage.count_table("issue_clusters"), 1)
        self.assertEqual(storage.count_table("cluster_members"), 1)
        self.assertEqual(cluster["representative_suggestion_id"], "S001")
        self.assertEqual(cluster["suggestion_count"], 1)
        self.assertEqual(member["source_suggestion_id"], "S001")
        self.assertEqual(member["decision_type"], "create_new_cluster")
        self.assertEqual(member["decision_status"], "accepted")

    def test_similar_second_suggestion_records_candidate_cluster_decision(self):
        storage = self.make_storage()
        with tempfile.TemporaryDirectory() as directory:
            first_path = self.write_csv(directory)
            run_csv_import_batch(storage, first_path)

            second_path = self.write_csv(
                directory,
                [
                    {
                        "suggestion_id": "S002",
                        "submit_date": "2026-06-02",
                        "raw_text": "Night shift canteen meals are cold and need reheating",
                        "department": "Production",
                        "job_group": "Line worker",
                        "work_location": "Plant A",
                        "scenario": "Canteen",
                        "status": "new",
                    }
                ],
            )
            run_csv_import_batch(storage, second_path)
            member = storage.connection.execute(
                """
                SELECT *
                FROM cluster_members
                WHERE source_suggestion_id = ?
                """,
                ("S002",),
            ).fetchone()

        self.assertEqual(storage.count_table("issue_clusters"), 1)
        self.assertIsNotNone(member)
        self.assertEqual(member["cluster_id"], "CL000001")
        self.assertIn(member["decision_type"], {"auto_merge", "manual_review"})
        self.assertIn(member["decision_status"], {"accepted", "pending"})
        self.assertGreaterEqual(member["final_score"], 0.72)

    def test_different_category_creates_separate_cluster(self):
        storage = self.make_storage()
        logistics_keyword = str(CATEGORY_RULES[0]["keywords"][0])
        equipment_keyword = str(CATEGORY_RULES[2]["keywords"][0])
        with tempfile.TemporaryDirectory() as directory:
            input_path = self.write_csv(
                directory,
                [
                    {
                        "suggestion_id": "S001",
                        "submit_date": "2026-06-01",
                        "raw_text": f"{logistics_keyword} night shift meals need reheating",
                        "department": "Production",
                        "job_group": "Line worker",
                        "work_location": "Plant A",
                        "scenario": "Canteen",
                        "status": "new",
                    },
                    {
                        "suggestion_id": "S002",
                        "submit_date": "2026-06-02",
                        "raw_text": f"{equipment_keyword} keeps failing and needs urgent repair",
                        "department": "Production",
                        "job_group": "Line worker",
                        "work_location": "Plant A",
                        "scenario": "Equipment",
                        "status": "new",
                    },
                ],
            )

            run_csv_import_batch(storage, input_path)
            clusters = storage.connection.execute(
                """
                SELECT cluster_id, representative_suggestion_id
                FROM issue_clusters
                ORDER BY cluster_id
                """
            ).fetchall()
            categories = storage.connection.execute(
                """
                SELECT primary_category
                FROM suggestion_analysis
                ORDER BY source_suggestion_id
                """
            ).fetchall()

        self.assertEqual(storage.count_table("issue_clusters"), 2)
        self.assertEqual(storage.count_table("cluster_members"), 2)
        self.assertEqual([cluster["representative_suggestion_id"] for cluster in clusters], ["S001", "S002"])
        self.assertNotEqual(categories[0]["primary_category"], categories[1]["primary_category"])

    def test_changed_source_suggestion_keeps_single_cluster_membership(self):
        storage = self.make_storage()
        logistics_keyword = str(CATEGORY_RULES[0]["keywords"][0])
        equipment_keyword = str(CATEGORY_RULES[2]["keywords"][0])
        with tempfile.TemporaryDirectory() as directory:
            input_path = self.write_csv(
                directory,
                [
                    {
                        "suggestion_id": "S001",
                        "submit_date": "2026-06-01",
                        "raw_text": f"{logistics_keyword} night shift meals need reheating",
                        "department": "Production",
                        "job_group": "Line worker",
                        "work_location": "Plant A",
                        "scenario": "Canteen",
                        "status": "new",
                    }
                ],
            )
            run_csv_import_batch(storage, input_path)

            changed_path = self.write_csv(
                directory,
                [
                    {
                        "suggestion_id": "S001",
                        "submit_date": "2026-06-01",
                        "raw_text": f"{equipment_keyword} keeps failing and needs urgent repair",
                        "department": "Production",
                        "job_group": "Line worker",
                        "work_location": "Plant A",
                        "scenario": "Equipment",
                        "status": "new",
                    }
                ],
            )
            second = run_csv_import_batch(storage, changed_path)
            members = storage.connection.execute(
                """
                SELECT cluster_id, source_suggestion_id
                FROM cluster_members
                WHERE source_suggestion_id = ?
                ORDER BY cluster_id
                """,
                ("S001",),
            ).fetchall()
            clusters = storage.connection.execute(
                """
                SELECT cluster_id, suggestion_count
                FROM issue_clusters
                ORDER BY cluster_id
                """
            ).fetchall()

        self.assertEqual(second.rows_created, 1)
        self.assertEqual(len(members), 1)
        self.assertEqual(members[0]["source_suggestion_id"], "S001")
        self.assertEqual(sum(cluster["suggestion_count"] for cluster in clusters), 1)

    def test_run_rows_import_batch_accepts_mysql_mapped_rows(self):
        storage = self.make_storage()
        rows = [
            {
                "suggestion_id": "M001",
                "submit_date": "2026-06-16",
                "raw_text": "夜班食堂没有热饭",
                "department": "生产一部",
                "job_group": "一线",
                "work_location": "A厂区",
                "scenario": "食堂",
                "is_anonymous_for_report": "是",
                "status": "待识别",
                "owner_department": "",
                "resolution_note": "",
                "closed_date": "",
            }
        ]

        result = run_rows_import_batch(storage, rows, source_name="mysql", cursor_start="100")
        batch = storage.get_import_batch(result.batch_id)

        self.assertEqual(result.rows_created, 1)
        self.assertEqual(batch["source_name"], "mysql")
        self.assertEqual(batch["cursor_start"], "100")
        self.assertEqual(storage.count_table("source_suggestions"), 1)
        self.assertEqual(storage.count_table("issue_clusters"), 1)

    def test_run_rows_import_batch_persists_source_cursor_end(self):
        storage = self.make_storage()
        rows = [
            {
                "suggestion_id": "M001",
                "_source_cursor": "105",
                "submit_date": "2026-06-16",
                "raw_text": "夜班食堂没有热饭",
                "department": "生产一部",
                "job_group": "一线",
                "work_location": "A厂区",
                "scenario": "食堂",
                "is_anonymous_for_report": "是",
                "status": "待识别",
                "owner_department": "",
                "resolution_note": "",
                "closed_date": "",
            },
            {
                "suggestion_id": "M002",
                "_source_cursor": "110",
                "submit_date": "2026-06-16",
                "raw_text": "宿舍卫生需要加强",
                "department": "生产二部",
                "job_group": "一线",
                "work_location": "B厂区",
                "scenario": "宿舍",
                "is_anonymous_for_report": "是",
                "status": "待识别",
                "owner_department": "",
                "resolution_note": "",
                "closed_date": "",
            },
        ]

        result = run_rows_import_batch(
            storage,
            rows,
            source_name="mysql",
            cursor_start="100",
            cursor_field="_source_cursor",
        )
        batch = storage.get_import_batch(result.batch_id)

        self.assertEqual(batch["cursor_end"], "110")

    def test_empty_rows_import_batch_keeps_cursor_end_at_start(self):
        storage = self.make_storage()

        result = run_rows_import_batch(
            storage,
            [],
            source_name="mysql",
            cursor_start="100",
            cursor_field="_source_cursor",
        )
        batch = storage.get_import_batch(result.batch_id)

        self.assertEqual(batch["rows_read"], 0)
        self.assertEqual(batch["cursor_end"], "100")


if __name__ == "__main__":
    unittest.main()
