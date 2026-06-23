import io
import csv
import json
import sqlite3
import tempfile
import unittest
from contextlib import closing, redirect_stdout
from pathlib import Path
from unittest.mock import Mock, patch

from src.suggestion_pipeline import INPUT_FIELDS, analyze_rows
from src.storage import Storage, connect_analysis_db


def row(suggestion_id, raw_text, department="生产一部", scenario="食堂"):
    data = {field: "" for field in INPUT_FIELDS}
    data.update(
        {
            "suggestion_id": suggestion_id,
            "submit_date": "2026-06-01",
            "raw_text": raw_text,
            "department": department,
            "job_group": "生产一线",
            "work_location": "A厂区",
            "scenario": scenario,
            "is_anonymous_for_report": "是",
            "status": "待识别",
        }
    )
    return data


class SuggestionPipelineTests(unittest.TestCase):
    def test_keeps_raw_text_and_clusters_similar_canteen_feedback(self):
        rows = [
            row("S001", "夜班下班太晚食堂没热饭吃"),
            row("S002", "晚上食堂饭菜都是凉的，希望加热"),
            row("S003", "夜班没热饭，干完活冷饭吃了不舒服"),
        ]

        suggestions, clusters = analyze_rows(rows)

        self.assertEqual(suggestions[0].raw_text, "夜班下班太晚食堂没热饭吃")
        self.assertEqual({item.analysis["secondary_category"] for item in suggestions}, {"食堂饭菜"})
        self.assertEqual(len(clusters), 1)
        self.assertEqual(suggestions[0].analysis["review_required"], "是")

    def test_does_not_merge_distinct_hygiene_and_safety_issues(self):
        rows = [
            row("S001", "宿舍厕所味道大，卫生没人管", "后勤部", "宿舍"),
            row("S002", "车间粉尘太大，口罩不够用", "生产一部", "安全"),
        ]

        suggestions, clusters = analyze_rows(rows)

        categories = {item.analysis["secondary_category"] for item in suggestions}
        self.assertEqual(categories, {"宿舍卫生", "劳保用品"})
        self.assertEqual(len(clusters), 2)

    def test_marks_short_or_empty_feedback_for_review_without_dropping_it(self):
        suggestions, clusters = analyze_rows([row("S001", "没有", "行政部", "其他")])

        self.assertEqual(len(suggestions), 1)
        self.assertEqual(len(clusters), 1)
        self.assertEqual(suggestions[0].analysis["quality_type"], "信息不足")
        self.assertEqual(suggestions[0].analysis["review_required"], "是")

    def test_detects_duplicate_feedback(self):
        rows = [
            row("S001", "保安岗亭空调坏了很久没人修", "安保部", "设备"),
            row("S002", "保安岗亭空调坏了很久没人修", "安保部", "设备"),
        ]

        suggestions, _ = analyze_rows(rows)

        self.assertEqual(suggestions[1].analysis["quality_type"], "重复问题")
        self.assertIn("疑似重复", suggestions[1].analysis["validation_flags"])

    def test_import_mysql_delegates_to_import_job(self):
        batch_result = Mock(batch_id=1, rows_read=0, rows_created=0, rows_skipped=0, rows_failed=0)

        with patch("src.suggestion_pipeline.import_mysql_batch", return_value=batch_result) as import_batch:
            from src.suggestion_pipeline import main

            exit_code = main(
                [
                    "import-mysql",
                    "--config",
                    "config.json",
                    "--db",
                    "analysis.db",
                    "--cursor",
                    "250",
                    "--limit",
                    "1000",
                ]
            )

        self.assertEqual(exit_code, 0)
        import_batch.assert_called_once_with(
            config_path=Path("config.json"),
            db_path=Path("analysis.db"),
            cursor_override="250",
            limit=1000,
        )

    def test_import_mysql_rejects_non_positive_limit(self):
        with patch("src.suggestion_pipeline.import_mysql_batch") as import_batch:
            from src.suggestion_pipeline import main

            with self.assertRaises(SystemExit):
                main(
                    [
                        "import-mysql",
                        "--config",
                        "config.json",
                        "--db",
                        "analysis.db",
                        "--limit",
                        "0",
                    ]
                )

        import_batch.assert_not_called()

    def test_run_daily_mysql_rejects_non_positive_limit(self):
        with patch("src.suggestion_pipeline.run_daily_mysql_job") as run_job:
            from src.suggestion_pipeline import main

            with self.assertRaises(SystemExit):
                main(
                    [
                        "run-daily-mysql",
                        "--config",
                        "config.json",
                        "--db",
                        "analysis.db",
                        "--log-dir",
                        "logs",
                        "--limit",
                        "0",
                    ]
                )

        run_job.assert_not_called()

    def test_run_daily_mysql_delegates_to_daily_job(self):
        with patch("src.suggestion_pipeline.run_daily_mysql_job", return_value=1) as run_job:
            from src.suggestion_pipeline import main

            exit_code = main(
                [
                    "run-daily-mysql",
                    "--config",
                    "config.json",
                    "--db",
                    "analysis.db",
                    "--log-dir",
                    "logs",
                ]
            )

        self.assertEqual(exit_code, 1)
        run_job.assert_called_once_with(
            config_path=Path("config.json"),
            db_path=Path("analysis.db"),
            log_dir=Path("logs"),
            cursor_override=None,
            limit=None,
        )

    def test_status_outputs_import_summary_as_json(self):
        with tempfile.TemporaryDirectory() as directory:
            db_path = Path(directory) / "analysis.db"
            with closing(sqlite3.connect(db_path)) as connection:
                storage = Storage(connection)
                storage.initialize_schema()
                batch_id = storage.start_import_batch("mysql", cursor_start="0")
                storage.finish_import_batch(
                    batch_id,
                    "100",
                    rows_read=10,
                    rows_created=10,
                    rows_skipped=0,
                    rows_failed=0,
                )

            from src.suggestion_pipeline import main

            output = io.StringIO()
            with redirect_stdout(output):
                exit_code = main(["status", "--db", str(db_path), "--source", "mysql"])

        payload = json.loads(output.getvalue())
        self.assertEqual(exit_code, 0)
        self.assertEqual(payload["source_name"], "mysql")
        self.assertEqual(payload["latest_successful_cursor"], "100")
        self.assertEqual(payload["latest_batch"]["rows_read"], 10)
        self.assertEqual(payload["health"], {"status": "ok", "reasons": []})
        self.assertEqual(payload["pending_review_tasks"], 0)

    def test_status_can_fail_when_health_is_unhealthy(self):
        with tempfile.TemporaryDirectory() as directory:
            db_path = Path(directory) / "analysis.db"
            with closing(connect_analysis_db(db_path)) as connection:
                storage = Storage(connection)
                storage.initialize_schema()
                batch_id = storage.start_import_batch("mysql", cursor_start="0")
                storage.finish_import_batch(
                    batch_id,
                    "100",
                    rows_read=10,
                    rows_created=9,
                    rows_skipped=0,
                    rows_failed=1,
                    error_summary="1 row missing raw_text",
                )

            from src.suggestion_pipeline import main

            normal_output = io.StringIO()
            with redirect_stdout(normal_output):
                normal_exit_code = main(["status", "--db", str(db_path), "--source", "mysql"])

            failing_output = io.StringIO()
            with redirect_stdout(failing_output):
                failing_exit_code = main(
                    ["status", "--db", str(db_path), "--source", "mysql", "--fail-on-unhealthy"]
                )

        normal_payload = json.loads(normal_output.getvalue())
        failing_payload = json.loads(failing_output.getvalue())
        self.assertEqual(normal_exit_code, 0)
        self.assertEqual(failing_exit_code, 1)
        self.assertEqual(normal_payload["health"]["status"], "attention")
        self.assertEqual(failing_payload["health"]["status"], "attention")

    def test_status_daily_limit_marks_full_latest_batch_unhealthy(self):
        with tempfile.TemporaryDirectory() as directory:
            db_path = Path(directory) / "analysis.db"
            with closing(connect_analysis_db(db_path)) as connection:
                storage = Storage(connection)
                storage.initialize_schema()
                batch_id = storage.start_import_batch("mysql", cursor_start="0")
                storage.finish_import_batch(
                    batch_id,
                    "10000",
                    rows_read=10000,
                    rows_created=10000,
                    rows_skipped=0,
                    rows_failed=0,
                )

            from src.suggestion_pipeline import main

            output = io.StringIO()
            with redirect_stdout(output):
                exit_code = main(
                    [
                        "status",
                        "--db",
                        str(db_path),
                        "--source",
                        "mysql",
                        "--daily-limit",
                        "10000",
                        "--fail-on-unhealthy",
                    ]
                )

        payload = json.loads(output.getvalue())
        self.assertEqual(exit_code, 1)
        self.assertTrue(payload["latest_batch_limit_reached"])
        self.assertEqual(payload["health"]["status"], "warning")
        self.assertIn("latest_batch_reached_daily_limit", payload["health"]["reasons"])

    def test_status_max_duration_marks_slow_latest_batch_unhealthy(self):
        with tempfile.TemporaryDirectory() as directory:
            db_path = Path(directory) / "analysis.db"
            with closing(connect_analysis_db(db_path)) as connection:
                storage = Storage(connection)
                storage.initialize_schema()
                batch_id = storage.start_import_batch("mysql", cursor_start="0")
                storage.finish_import_batch(
                    batch_id,
                    "100",
                    rows_read=100,
                    rows_created=100,
                    rows_skipped=0,
                    rows_failed=0,
                )
                connection.execute(
                    """
                    UPDATE import_batches
                    SET started_at = ?, finished_at = ?
                    WHERE batch_id = ?
                    """,
                    ("2026-06-23T00:00:00+00:00", "2026-06-23T00:45:00+00:00", batch_id),
                )
                connection.commit()

            from src.suggestion_pipeline import main

            output = io.StringIO()
            with redirect_stdout(output):
                exit_code = main(
                    [
                        "status",
                        "--db",
                        str(db_path),
                        "--source",
                        "mysql",
                        "--max-duration-seconds",
                        "1800",
                        "--fail-on-unhealthy",
                    ]
                )

        payload = json.loads(output.getvalue())
        self.assertEqual(exit_code, 1)
        self.assertEqual(payload["latest_batch_duration_seconds"], 2700)
        self.assertTrue(payload["latest_batch_duration_exceeded"])
        self.assertEqual(payload["health"]["status"], "warning")
        self.assertIn("latest_batch_exceeded_max_duration", payload["health"]["reasons"])

    def test_export_db_results_writes_persisted_reports(self):
        with tempfile.TemporaryDirectory() as directory:
            db_path = Path(directory) / "analysis.db"
            output_dir = Path(directory) / "reports"
            with closing(sqlite3.connect(db_path)) as connection:
                storage = Storage(connection)
                storage.initialize_schema()
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
                storage.upsert_action_item_for_cluster(cluster_id)

            from src.suggestion_pipeline import main

            exit_code = main(["export-db-results", "--db", str(db_path), "--output-dir", str(output_dir)])

            self.assertEqual(exit_code, 0)
            for filename in ["suggestions_analyzed.csv", "clusters.csv", "action_items.csv", "weekly_report.md"]:
                self.assertTrue((output_dir / filename).exists(), filename)
            with (output_dir / "suggestions_analyzed.csv").open("r", encoding="utf-8-sig", newline="") as file:
                rows = list(csv.DictReader(file))
            self.assertEqual(rows[0]["source_suggestion_id"], "S001")
            self.assertEqual(rows[0]["cluster_id"], cluster_id)
            with (output_dir / "clusters.csv").open("r", encoding="utf-8-sig", newline="") as file:
                clusters = list(csv.DictReader(file))
            self.assertEqual(clusters[0]["suggestion_count"], "1")
            with (output_dir / "action_items.csv").open("r", encoding="utf-8-sig", newline="") as file:
                actions = list(csv.DictReader(file))
            self.assertEqual(actions[0]["cluster_id"], cluster_id)
            self.assertEqual(actions[0]["status"], "watchlist")
            self.assertIn("Persisted Analysis Report", (output_dir / "weekly_report.md").read_text(encoding="utf-8-sig"))

    def test_export_review_tasks_writes_pending_tasks_to_csv(self):
        with tempfile.TemporaryDirectory() as directory:
            db_path = Path(directory) / "analysis.db"
            output_path = Path(directory) / "review_tasks.csv"
            with closing(sqlite3.connect(db_path)) as connection:
                storage = Storage(connection)
                storage.initialize_schema()
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
                storage.create_review_task(
                    source_suggestion_id="S001",
                    candidate_cluster_id=None,
                    task_type="manual_cluster_review",
                    priority=90,
                    evidence={"reason": "low confidence"},
                )

            from src.suggestion_pipeline import main

            exit_code = main(["export-review-tasks", "--db", str(db_path), "--output", str(output_path)])

            self.assertEqual(exit_code, 0)
            with output_path.open("r", encoding="utf-8-sig", newline="") as file:
                rows = list(csv.DictReader(file))
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["source_suggestion_id"], "S001")
            self.assertEqual(rows[0]["raw_text"], "Night shift canteen meals are cold")
            self.assertEqual(rows[0]["priority"], "90")
            self.assertEqual(rows[0]["candidate_cluster_name"], "")
            self.assertEqual(rows[0]["review_result"], "")
            self.assertEqual(rows[0]["target_cluster_id"], "")
            self.assertEqual(rows[0]["reviewed_by"], "")

    def test_export_import_failures_writes_failed_rows_to_csv(self):
        with tempfile.TemporaryDirectory() as directory:
            db_path = Path(directory) / "analysis.db"
            output_path = Path(directory) / "import_failures.csv"
            with closing(sqlite3.connect(db_path)) as connection:
                storage = Storage(connection)
                storage.initialize_schema()
                batch_id = storage.start_import_batch(
                    source_name="mysql",
                    cursor_start="100",
                )
                storage.record_import_failure(
                    batch_id=batch_id,
                    source_suggestion_id="S002",
                    source_cursor="102",
                    row_number=2,
                    error_message="missing raw_text",
                    raw_row={"id": 102, "department": "Production"},
                )

            from src.suggestion_pipeline import main

            exit_code = main(
                [
                    "export-import-failures",
                    "--db",
                    str(db_path),
                    "--batch-id",
                    str(batch_id),
                    "--output",
                    str(output_path),
                ]
            )

            self.assertEqual(exit_code, 0)
            with output_path.open("r", encoding="utf-8-sig", newline="") as file:
                rows = list(csv.DictReader(file))
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["batch_id"], str(batch_id))
            self.assertEqual(rows[0]["source_suggestion_id"], "S002")
            self.assertEqual(rows[0]["source_cursor"], "102")
            self.assertEqual(rows[0]["row_number"], "2")
            self.assertEqual(rows[0]["error_message"], "missing raw_text")
            self.assertEqual(rows[0]["raw_row_json"], '{"department": "Production", "id": 102}')

    def test_import_review_results_applies_csv_decisions(self):
        with tempfile.TemporaryDirectory() as directory:
            db_path = Path(directory) / "analysis.db"
            review_path = Path(directory) / "review_results.csv"
            with closing(sqlite3.connect(db_path)) as connection:
                storage = Storage(connection)
                storage.initialize_schema()
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
            with review_path.open("w", encoding="utf-8-sig", newline="") as file:
                writer = csv.DictWriter(file, fieldnames=["review_task_id", "review_result", "reviewed_by"])
                writer.writeheader()
                writer.writerow(
                    {
                        "review_task_id": str(review_task_id),
                        "review_result": "approve",
                        "reviewed_by": "ops-user",
                    }
                )

            from src.suggestion_pipeline import main

            output = io.StringIO()
            with redirect_stdout(output):
                exit_code = main(["import-review-results", "--db", str(db_path), "--input", str(review_path)])

            with closing(sqlite3.connect(db_path)) as connection:
                connection.row_factory = sqlite3.Row
                member = connection.execute(
                    """
                    SELECT decision_status, reviewed_by
                    FROM cluster_members
                    WHERE cluster_id = ? AND source_suggestion_id = ?
                    """,
                    (cluster_id, "S002"),
                ).fetchone()
            self.assertEqual(exit_code, 0)
            self.assertIn("applied=1", output.getvalue())
            self.assertEqual(member["decision_status"], "accepted")
            self.assertEqual(member["reviewed_by"], "ops-user")

    def test_doctor_passes_optional_backup_root(self):
        with patch(
            "src.suggestion_pipeline.run_doctor_checks",
            return_value={
                "status": "success",
                "checks": {"backup_root_writable": True},
                "issues": [],
            },
        ) as doctor:
            from src.suggestion_pipeline import main

            output = io.StringIO()
            with redirect_stdout(output):
                exit_code = main(
                    [
                        "doctor",
                        "--config",
                        "config.json",
                        "--db",
                        "analysis.db",
                        "--backup-root",
                        "backups",
                    ]
                )

        payload = json.loads(output.getvalue())
        self.assertEqual(exit_code, 0)
        self.assertTrue(payload["checks"]["backup_root_writable"])
        doctor.assert_called_once_with(
            config_path=Path("config.json"),
            db_path=Path("analysis.db"),
            backup_root=Path("backups"),
        )

    def test_doctor_outputs_report_and_returns_failure_for_failed_checks(self):
        with patch(
            "src.suggestion_pipeline.run_doctor_checks",
            return_value={
                "status": "failed",
                "checks": {"config_loaded": True, "password_env_present": False},
                "issues": ["MINI_PROGRAM_DB_PASSWORD is missing"],
            },
        ) as doctor:
            from src.suggestion_pipeline import main

            output = io.StringIO()
            with redirect_stdout(output):
                exit_code = main(["doctor", "--config", "config.json", "--db", "analysis.db"])

        payload = json.loads(output.getvalue())
        self.assertEqual(exit_code, 1)
        self.assertEqual(payload["status"], "failed")
        doctor.assert_called_once_with(config_path=Path("config.json"), db_path=Path("analysis.db"), backup_root=None)


if __name__ == "__main__":
    unittest.main()
