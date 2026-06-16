import unittest
from unittest.mock import Mock, patch

from src.suggestion_pipeline import INPUT_FIELDS, analyze_rows


def row(suggestion_id, raw_text, department="生产一部", scenario="食堂"):
    data = {field: "" for field in INPUT_FIELDS}
    data.update(
        {
            "suggestion_id": suggestion_id,
            "submit_date": "2026-06-01",
            "raw_text": raw_text,
            "department": department,
            "job_group": "生产一线",
            "work_location": "A厂区",
            "scenario": scenario,
            "is_anonymous_for_report": "是",
            "status": "待识别",
        }
    )
    return data


class SuggestionPipelineTests(unittest.TestCase):
    def test_keeps_raw_text_and_clusters_similar_canteen_feedback(self):
        rows = [
            row("S001", "夜班下班太晚食堂没热饭吃"),
            row("S002", "晚上食堂饭菜都是凉的，希望加热"),
            row("S003", "夜班没热饭，干完活冷饭吃了不舒服"),
        ]

        suggestions, clusters = analyze_rows(rows)

        self.assertEqual(suggestions[0].raw_text, "夜班下班太晚食堂没热饭吃")
        self.assertEqual({item.analysis["secondary_category"] for item in suggestions}, {"食堂饭菜"})
        self.assertEqual(len(clusters), 1)
        self.assertEqual(suggestions[0].analysis["review_required"], "是")

    def test_does_not_merge_distinct_hygiene_and_safety_issues(self):
        rows = [
            row("S001", "宿舍厕所味道大，卫生没人管", "后勤部", "宿舍"),
            row("S002", "车间粉尘太大，口罩不够用", "生产一部", "安全"),
        ]

        suggestions, clusters = analyze_rows(rows)

        categories = {item.analysis["secondary_category"] for item in suggestions}
        self.assertEqual(categories, {"宿舍卫生", "劳保用品"})
        self.assertEqual(len(clusters), 2)

    def test_marks_short_or_empty_feedback_for_review_without_dropping_it(self):
        suggestions, clusters = analyze_rows([row("S001", "没有", "行政部", "其他")])

        self.assertEqual(len(suggestions), 1)
        self.assertEqual(len(clusters), 1)
        self.assertEqual(suggestions[0].analysis["quality_type"], "信息不足")
        self.assertEqual(suggestions[0].analysis["review_required"], "是")

    def test_detects_duplicate_feedback(self):
        rows = [
            row("S001", "保安岗亭空调坏了很久没人修", "安保部", "设备"),
            row("S002", "保安岗亭空调坏了很久没人修", "安保部", "设备"),
        ]

        suggestions, _ = analyze_rows(rows)

        self.assertEqual(suggestions[1].analysis["quality_type"], "重复问题")
        self.assertIn("疑似重复", suggestions[1].analysis["validation_flags"])

    def test_import_mysql_uses_latest_successful_cursor_when_not_provided(self):
        config = Mock()
        config.mysql_source = Mock()
        source_connection = Mock()
        source_connection.__enter__ = Mock(return_value=source_connection)
        source_connection.__exit__ = Mock(return_value=None)

        storage = Mock()
        storage.get_latest_successful_cursor.return_value = "250"
        batch_result = Mock(batch_id=1, rows_read=0, rows_created=0, rows_skipped=0, rows_failed=0)

        with patch("src.suggestion_pipeline.load_app_config", return_value=config), patch(
            "src.suggestion_pipeline.connect_mysql", return_value=source_connection
        ), patch("src.suggestion_pipeline.fetch_incremental_rows", return_value=[] ) as fetch_rows, patch(
            "src.suggestion_pipeline.Storage", return_value=storage
        ), patch("src.suggestion_pipeline.run_rows_import_batch", return_value=batch_result) as run_batch, patch(
            "src.suggestion_pipeline.sqlite3.connect"
        ):
            from src.suggestion_pipeline import main

            exit_code = main(["import-mysql", "--config", "config.json", "--db", "analysis.db"])

        self.assertEqual(exit_code, 0)
        fetch_rows.assert_called_once_with(
            source_connection,
            config.mysql_source,
            cursor_value="250",
            limit=None,
        )
        run_batch.assert_called_once_with(
            storage,
            [],
            source_name="mysql",
            cursor_start="250",
            cursor_field="_source_cursor",
        )


if __name__ == "__main__":
    unittest.main()
