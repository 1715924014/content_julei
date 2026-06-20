import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from src.import_jobs import run_daily_mysql_job


class ImportJobTests(unittest.TestCase):
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
        import_batch.assert_called_once_with(
            config_path=Path("config/mysql.json"),
            db_path=Path("data/analysis.db"),
            cursor_override=None,
            limit=1000,
        )

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
        self.assertEqual(payload["cursor_override"], "100")
        self.assertIn("duration_seconds", payload)
        self.assertGreaterEqual(payload["duration_seconds"], 0)


if __name__ == "__main__":
    unittest.main()
