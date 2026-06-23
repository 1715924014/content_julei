import unittest
from pathlib import Path


class DeploymentScriptTests(unittest.TestCase):
    def test_windows_daily_mysql_script_invokes_daily_job_with_strict_failure_handling(self):
        script_path = Path("scripts/run_daily_mysql.ps1")

        self.assertTrue(script_path.exists())
        content = script_path.read_text(encoding="utf-8")
        self.assertIn("$ErrorActionPreference = \"Stop\"", content)
        self.assertIn("doctor", content)
        self.assertIn("run-daily-mysql", content)
        self.assertIn("exit $LASTEXITCODE", content)
        self.assertIn("MINI_PROGRAM_DB_PASSWORD", content)
        self.assertIn("$BackupRoot", content)
        self.assertIn("--backup-root", content)
        self.assertIn("$MaxDurationSeconds", content)
        self.assertIn("--max-duration-seconds", content)
        self.assertIn("$MinThroughputRowsPerSecond", content)
        self.assertIn("--min-throughput-rows-per-second", content)
        self.assertIn("$LogRetentionDays", content)
        self.assertIn("daily-mysql-*.json", content)
        self.assertIn("Remove-Item", content)
        self.assertLess(content.index("doctor"), content.index("run-daily-mysql"))

    def test_backup_script_copies_database_and_logs(self):
        script_path = Path("scripts/backup_analysis.ps1")

        self.assertTrue(script_path.exists())
        content = script_path.read_text(encoding="utf-8")
        self.assertIn("$ErrorActionPreference = \"Stop\"", content)
        self.assertIn("analysis.db", content)
        self.assertIn("Copy-Item", content)
        self.assertIn("logs", content)
        self.assertIn("-wal", content)
        self.assertIn("-shm", content)
        self.assertIn("RetentionDays", content)
        self.assertIn("Remove-Item", content)
        self.assertIn("exit 0", content)


if __name__ == "__main__":
    unittest.main()
