import unittest

from src.config import MySQLSourceConfig
from src.mysql_source import build_incremental_query, map_mysql_row


class MySQLSourceTests(unittest.TestCase):
    def make_config(self):
        return MySQLSourceConfig(
            host="127.0.0.1",
            port=3306,
            database="mini_program",
            user="report_user",
            password_env="MINI_PROGRAM_DB_PASSWORD",
            table="employee_suggestions",
            cursor_field="id",
            field_mapping={
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
        )

    def test_build_incremental_query_uses_cursor_and_limit(self):
        query, params = build_incremental_query(self.make_config(), cursor_value="100", limit=500)

        self.assertIn("FROM `employee_suggestions`", query)
        self.assertIn("WHERE `id` > %s", query)
        self.assertIn("ORDER BY `id` ASC", query)
        self.assertIn("LIMIT %s", query)
        self.assertEqual(params, ["100", 500])

    def test_map_mysql_row_outputs_pipeline_input_fields(self):
        mapped = map_mysql_row(
            {
                "id": 101,
                "created_at": "2026-06-16",
                "content": "夜班食堂没有热饭",
                "department_name": "生产一部",
                "job_group": "一线",
                "work_location": "A厂区",
                "scenario": "食堂",
                "anonymous_flag": "是",
                "status": "待识别",
                "owner_department": "",
                "resolution_note": "",
                "closed_at": "",
            },
            self.make_config(),
        )

        self.assertEqual(mapped["suggestion_id"], "101")
        self.assertEqual(mapped["raw_text"], "夜班食堂没有热饭")
        self.assertEqual(mapped["department"], "生产一部")


if __name__ == "__main__":
    unittest.main()
