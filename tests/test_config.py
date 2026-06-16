import json
import tempfile
import unittest
from pathlib import Path

from src.config import load_app_config


class ConfigTests(unittest.TestCase):
    def test_loads_mysql_source_config_with_field_mapping(self):
        payload = {
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
                    "department": "department_name",
                    "job_group": "job_group",
                    "work_location": "work_location",
                    "scenario": "scenario",
                    "is_anonymous_for_report": "anonymous_flag",
                    "status": "status",
                    "owner_department": "owner_department",
                    "resolution_note": "resolution_note",
                    "closed_date": "closed_at",
                },
            }
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            path.write_text(json.dumps(payload), encoding="utf-8")

            config = load_app_config(path)

        self.assertEqual(config.mysql_source.host, "127.0.0.1")
        self.assertEqual(config.mysql_source.table, "employee_suggestions")
        self.assertEqual(config.mysql_source.field_mapping["raw_text"], "content")


if __name__ == "__main__":
    unittest.main()
