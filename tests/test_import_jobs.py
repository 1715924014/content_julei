import json
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import Mock, patch

from src.import_jobs import import_mysql_batch, run_daily_mysql_job
from src.storage import Storage, connect_analysis_db


class ImportJobTests(unittest.TestCase):
    def test_import_mysql_batch_rejects_non_positive_limit_before_connecting(self):
        with patch("src.import_jobs.load_app_config") as load_config, patch("src.import_jobs.connect_mysql") as connect_mysql:
            with self.assertRaisesRegex(ValueError, "limit"):
                import_mysql_batch(
                    config_path=Path("config/mysql.json"),
                    db_path=Path("data/analysis.db"),
                    limit=0,
                )

        load_config.assert_not_called()
        connect_mysql.assert_not_called()

    def test_daily_mysql_job_writes_failure_log_for_non_positive_limit(self):
        with tempfile.TemporaryDirectory() as directory:
            exit_code = run_daily_mysql_job(
                config_path=Path("config/mysql.json"),
                db_path=Path("data/analysis.db"),
                log_dir=Path(directory),
                limit=0,
                cursor_override=None,
            )
            logs = list(Path(directory).glob("daily-mysql-*.json"))
            payload = json.loads(logs[0].read_text(encoding="utf-8"))

        self.assertEqual(exit_code, 1)
        self.assertEqual(payload["status"], "failed")
        self.assertIn("limit", payload["error_summary"])

    def test_daily_mysql_job_refuses_to_run_when_lock_exists(self):
        batch = Mock(
            batch_id=9,
            rows_read=1,
            rows_created=1,
            rows_skipped=0,
            rows_failed=0,
            cursor_start="100",
            cursor_end="101",
            error_summary="",
        )
        with tempfile.TemporaryDirectory() as directory, patch(
            "src.import_jobs.import_mysql_batch",
            return_value=batch,
        ) as import_batch:
            lock_path = Path(directory) / "daily-mysql.lock"
            lock_path.write_text("2999-01-01T01:02:03+00:00", encoding="utf-8")

            exit_code = run_daily_mysql_job(
                config_path=Path("config/mysql.json"),
                db_path=Path("data/analysis.db"),
                log_dir=Path(directory),
                limit=1000,
                cursor_override=None,
            )
            logs = list(Path(directory).glob("daily-mysql-*.json"))
            payload = json.loads(logs[0].read_text(encoding="utf-8"))
            lock_exists_after_run = lock_path.exists()

        self.assertEqual(exit_code, 1)
        self.assertEqual(len(logs), 1)
        self.assertEqual(payload["status"], "failed")
        self.assertEqual(payload["error"], "another daily MySQL job is already running")
        self.assertEqual(payload["lock_path"], str(lock_path))
        self.assertEqual(payload["lock_started_at"], "2999-01-01T01:02:03+00:00")
        self.assertTrue(lock_exists_after_run)
        import_batch.assert_not_called()

    def test_daily_mysql_job_replaces_stale_lock_and_runs(self):
        batch = Mock(
            batch_id=10,
            rows_read=1,
            rows_created=1,
            rows_skipped=0,
            rows_failed=0,
            cursor_start="100",
            cursor_end="101",
            error_summary="",
        )
        with tempfile.TemporaryDirectory() as directory, patch(
            "src.import_jobs.import_mysql_batch",
            return_value=batch,
        ) as import_batch:
            lock_path = Path(directory) / "daily-mysql.lock"
            lock_path.write_text("2000-01-01T00:00:00+00:00", encoding="utf-8")

            exit_code = run_daily_mysql_job(
                config_path=Path("config/mysql.json"),
                db_path=Path("data/analysis.db"),
                log_dir=Path(directory),
                limit=1000,
                cursor_override=None,
            )
            logs = list(Path(directory).glob("daily-mysql-*.json"))
            payload = json.loads(logs[0].read_text(encoding="utf-8"))
            lock_exists_after_run = lock_path.exists()

        self.assertEqual(exit_code, 0)
        self.assertEqual(payload["status"], "success")
        self.assertTrue(payload["stale_lock_replaced"])
        self.assertEqual(payload["stale_lock_started_at"], "2000-01-01T00:00:00+00:00")
        self.assertFalse(lock_exists_after_run)
        import_batch.assert_called_once()

    def test_daily_mysql_job_log_includes_import_health_summary(self):
        with tempfile.TemporaryDirectory() as directory:
            db_path = Path(directory) / "analysis.db"

            def write_success_batch(**_kwargs):
                with closing(connect_analysis_db(db_path)) as connection:
                    storage = Storage(connection)
                    storage.initialize_schema()
                    batch_id = storage.start_import_batch("mysql", cursor_start="100")
                    storage.finish_import_batch(
                        batch_id,
                        "125",
                        rows_read=10,
                        rows_created=10,
                        rows_skipped=0,
                        rows_failed=0,
                    )
                return Mock(
                    batch_id=batch_id,
                    rows_read=10,
                    rows_created=10,
                    rows_skipped=0,
                    rows_failed=0,
                    cursor_start="100",
                    cursor_end="125",
                    error_summary="",
                )

            with patch("src.import_jobs.import_mysql_batch", side_effect=write_success_batch):
                exit_code = run_daily_mysql_job(
                    config_path=Path("config/mysql.json"),
                    db_path=db_path,
                    log_dir=Path(directory),
                    limit=1000,
                    cursor_override=None,
                )
                logs = list(Path(directory).glob("daily-mysql-*.json"))
                payload = json.loads(logs[0].read_text(encoding="utf-8"))

        self.assertEqual(exit_code, 0)
        self.assertEqual(payload["health"], {"status": "ok", "reasons": []})
        self.assertEqual(payload["pending_review_tasks"], 0)
        self.assertEqual(payload["latest_successful_cursor"], "125")

    def test_daily_mysql_job_logs_health_summary_error_type(self):
        batch = Mock(
            batch_id=12,
            rows_read=10,
            rows_created=10,
            rows_skipped=0,
            rows_failed=0,
            cursor_start="100",
            cursor_end="110",
            error_summary="",
        )
        with tempfile.TemporaryDirectory() as directory, patch(
            "src.import_jobs.import_mysql_batch",
            return_value=batch,
        ), patch(
            "src.import_jobs.connect_analysis_db",
            side_effect=RuntimeError("summary db unavailable"),
        ):
            exit_code = run_daily_mysql_job(
                config_path=Path("config/mysql.json"),
                db_path=Path("data/analysis.db"),
                log_dir=Path(directory),
                limit=1000,
                cursor_override=None,
            )
            logs = list(Path(directory).glob("daily-mysql-*.json"))
            payload = json.loads(logs[0].read_text(encoding="utf-8"))

        self.assertEqual(exit_code, 0)
        self.assertEqual(payload["status"], "success")
        self.assertEqual(payload["health_summary_error"], "summary db unavailable")
        self.assertEqual(payload["health_summary_error_type"], "RuntimeError")

    def test_daily_mysql_job_writes_success_log(self):
        batch = Mock(
            batch_id=7,
            rows_read=10,
            rows_created=8,
            rows_skipped=2,
            rows_failed=0,
            cursor_start="100",
            cursor_end="125",
            error_summary="",
        )
        with tempfile.TemporaryDirectory() as directory, patch(
            "src.import_jobs.import_mysql_batch",
            return_value=batch,
        ) as import_batch:
            exit_code = run_daily_mysql_job(
                config_path=Path("config/mysql.json"),
                db_path=Path("data/analysis.db"),
                log_dir=Path(directory),
                limit=1000,
                cursor_override=None,
            )
            logs = list(Path(directory).glob("daily-mysql-*.json"))
            payload = json.loads(logs[0].read_text(encoding="utf-8"))

        self.assertEqual(exit_code, 0)
        self.assertEqual(len(logs), 1)
        self.assertEqual(payload["status"], "success")
        self.assertEqual(payload["batch_id"], 7)
        self.assertEqual(payload["rows_read"], 10)
        self.assertEqual(payload["rows_created"], 8)
        self.assertEqual(payload["rows_skipped"], 2)
        self.assertEqual(payload["rows_failed"], 0)
        self.assertIn("duration_seconds", payload)
        self.assertGreaterEqual(payload["duration_seconds"], 0)
        self.assertEqual(payload["cursor_start"], "100")
        self.assertEqual(payload["cursor_end"], "125")
        self.assertEqual(payload["error_summary"], "")
        self.assertFalse(payload["limit_reached"])
        import_batch.assert_called_once_with(
            config_path=Path("config/mysql.json"),
            db_path=Path("data/analysis.db"),
            cursor_override=None,
            limit=1000,
        )

    def test_daily_mysql_job_marks_limit_reached_when_batch_reads_full_limit(self):
        batch = Mock(
            batch_id=11,
            rows_read=1000,
            rows_created=1000,
            rows_skipped=0,
            rows_failed=0,
            cursor_start="100",
            cursor_end="1100",
            error_summary="",
        )
        with tempfile.TemporaryDirectory() as directory, patch(
            "src.import_jobs.import_mysql_batch",
            return_value=batch,
        ):
            exit_code = run_daily_mysql_job(
                config_path=Path("config/mysql.json"),
                db_path=Path("data/analysis.db"),
                log_dir=Path(directory),
                limit=1000,
                cursor_override=None,
            )
            logs = list(Path(directory).glob("daily-mysql-*.json"))
            payload = json.loads(logs[0].read_text(encoding="utf-8"))

        self.assertEqual(exit_code, 0)
        self.assertTrue(payload["limit_reached"])
        self.assertIn("limit_reached", payload["warnings"])

    def test_daily_mysql_job_returns_error_code_for_partial_batch(self):
        batch = Mock(
            batch_id=8,
            rows_read=10,
            rows_created=7,
            rows_skipped=1,
            rows_failed=2,
            cursor_start="100",
            cursor_end="125",
            error_summary="2 rows missing raw_text",
        )
        with tempfile.TemporaryDirectory() as directory, patch(
            "src.import_jobs.import_mysql_batch",
            return_value=batch,
        ):
            exit_code = run_daily_mysql_job(
                config_path=Path("config/mysql.json"),
                db_path=Path("data/analysis.db"),
                log_dir=Path(directory),
                limit=1000,
                cursor_override=None,
            )
            logs = list(Path(directory).glob("daily-mysql-*.json"))
            payload = json.loads(logs[0].read_text(encoding="utf-8"))

        self.assertEqual(exit_code, 1)
        self.assertEqual(len(logs), 1)
        self.assertEqual(payload["status"], "partial")
        self.assertEqual(payload["batch_id"], 8)
        self.assertEqual(payload["rows_failed"], 2)
        self.assertEqual(payload["error_summary"], "2 rows missing raw_text")

    def test_daily_mysql_job_writes_failure_log_and_returns_error_code(self):
        with tempfile.TemporaryDirectory() as directory, patch(
            "src.import_jobs.import_mysql_batch",
            side_effect=RuntimeError("database unavailable"),
        ):
            exit_code = run_daily_mysql_job(
                config_path=Path("config/mysql.json"),
                db_path=Path("data/analysis.db"),
                log_dir=Path(directory),
                limit=None,
                cursor_override="100",
            )
            logs = list(Path(directory).glob("daily-mysql-*.json"))
            payload = json.loads(logs[0].read_text(encoding="utf-8"))

        self.assertEqual(exit_code, 1)
        self.assertEqual(len(logs), 1)
        self.assertEqual(payload["status"], "failed")
        self.assertEqual(payload["error"], "database unavailable")
        self.assertEqual(payload["error_summary"], "database unavailable")
        self.assertEqual(payload["error_type"], "RuntimeError")
        self.assertEqual(payload["cursor_override"], "100")
        self.assertIn("duration_seconds", payload)
        self.assertGreaterEqual(payload["duration_seconds"], 0)


if __name__ == "__main__":
    unittest.main()
