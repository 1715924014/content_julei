import unittest
from pathlib import Path


class OperationsDocsTests(unittest.TestCase):
    def test_operations_runbook_covers_daily_operation_and_recovery(self):
        path = Path("docs/operations.md")

        self.assertTrue(path.exists())
        content = path.read_text(encoding="utf-8")
        required_terms = [
            "run-daily-mysql",
            "status --db",
            "MINI_PROGRAM_DB_PASSWORD",
            "--cursor",
            "logs",
            "LogRetentionDays",
            "MaxDurationSeconds must be zero or positive",
            "MinThroughputRowsPerSecond must be zero or positive",
            "Limit must be a positive integer",
            "zero or positive",
            "log cleanup failed",
            "daily-mysql-*.json",
            "rows_failed",
            "error_type",
            "lock_path",
            "lock_started_at",
            "stale_lock_started_at",
            "limit_reached",
            "source_backlog_remaining",
            "warnings",
            "latest_batch_still_running",
            "latest_batch_reached_daily_limit",
            "latest_batch_limit_reached",
            "latest_batch_duration_seconds",
            "latest_batch_rows_per_second",
            "source_pending_after_batch",
            "source_pending_error_summary",
            "source_pending_count_unavailable",
            "inspect_source_pending_count",
            "latest_batch_throughput_below_minimum",
            "latest_batch_below_min_throughput",
            "recommended_actions",
            "run_additional_import_or_increase_limit",
            "optimize_import_throughput",
            "review_runtime_capacity",
            "export_import_failures_and_repair_rows",
            "--min-throughput-rows-per-second",
            "latest_batch_duration_exceeded",
            "latest_batch_exceeded_max_duration",
            "--daily-limit",
            "--max-duration-seconds",
            "unsafe SQL identifier",
            "positive integer",
            "password environment variable",
            "connect timeout",
            "latest_successful_cursor",
            "health_summary_error_type",
            "analysis.db-wal",
            "analysis.db-shm",
            "During restore",
            "Windows 任务计划程序",
        ]
        for term in required_terms:
            with self.subTest(term=term):
                self.assertIn(term, content)


if __name__ == "__main__":
    unittest.main()
