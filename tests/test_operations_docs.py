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
            "rows_failed",
            "latest_successful_cursor",
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
