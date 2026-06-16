import unittest
from pathlib import Path


class DeploymentScriptTests(unittest.TestCase):
    def test_windows_daily_mysql_script_invokes_daily_job_with_strict_failure_handling(self):
        script_path = Path("scripts/run_daily_mysql.ps1")

        self.assertTrue(script_path.exists())
        content = script_path.read_text(encoding="utf-8")
        self.assertIn("$ErrorActionPreference = \"Stop\"", content)
        self.assertIn("run-daily-mysql", content)
        self.assertIn("exit $LASTEXITCODE", content)
        self.assertIn("MINI_PROGRAM_DB_PASSWORD", content)


if __name__ == "__main__":
    unittest.main()
