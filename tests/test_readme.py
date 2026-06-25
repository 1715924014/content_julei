import unittest
from pathlib import Path


class ReadmeTests(unittest.TestCase):
    def test_readme_covers_production_incremental_operations(self):
        path = Path("README.md")

        self.assertTrue(path.exists())
        content = path.read_text(encoding="utf-8")
        required_terms = [
            "run_daily_mysql.ps1",
            "每天 7000-10000 条",
            "latest_successful_cursor",
            "failed rows return a non-zero exit code",
            "Manual `import-csv` and `import-mysql`",
            "source_pending_after_batch",
            "recommended_actions",
            "recommended_commands",
            "run_deployment_doctor",
            "daily_lock_present",
            "export-review-tasks",
            "--latest",
            "import-review-results",
            "backup_analysis.ps1",
            "docs/operations.md",
        ]
        for term in required_terms:
            with self.subTest(term=term):
                self.assertIn(term, content)


if __name__ == "__main__":
    unittest.main()
