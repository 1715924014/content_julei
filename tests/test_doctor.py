import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.doctor import run_doctor_checks


class DoctorTests(unittest.TestCase):
    def write_config(self, directory: str) -> Path:
        path = Path(directory) / "mysql.json"
        path.write_text(
            json.dumps(
                {
                    "mysql_source": {
                        "host": "127.0.0.1",
                        "port": 3306,
                        "database": "mini_program",
                        "user": "report_user",
                        "password_env": "MINI_PROGRAM_DB_PASSWORD",
                        "table": "employee_suggestions",
                        "cursor_field": "id",
                        "field_mapping": {
                            "suggestion_id": "id",
                            "submit_date": "created_at",
                            "raw_text": "content",
                        },
                    }
                }
            ),
            encoding="utf-8",
        )
        return path

    def test_doctor_checks_config_password_env_and_database(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(
            "os.environ",
            {"MINI_PROGRAM_DB_PASSWORD": "secret"},
        ):
            config_path = self.write_config(directory)
            db_path = Path(directory) / "analysis.db"

            report = run_doctor_checks(config_path=config_path, db_path=db_path)

        self.assertEqual(report["status"], "success")
        self.assertTrue(report["checks"]["config_loaded"])
        self.assertTrue(report["checks"]["password_env_present"])
        self.assertTrue(report["checks"]["database_initialized"])

    def test_doctor_fails_when_password_env_is_missing(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict("os.environ", {}, clear=True):
            config_path = self.write_config(directory)
            db_path = Path(directory) / "analysis.db"

            report = run_doctor_checks(config_path=config_path, db_path=db_path)

        self.assertEqual(report["status"], "failed")
        self.assertTrue(report["checks"]["config_loaded"])
        self.assertFalse(report["checks"]["password_env_present"])
        self.assertTrue(any("MINI_PROGRAM_DB_PASSWORD" in issue for issue in report["issues"]))


if __name__ == "__main__":
    unittest.main()
