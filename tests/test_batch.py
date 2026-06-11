import csv
import sqlite3
import tempfile
import unittest
from pathlib import Path

from src.batch import run_csv_import_batch
from src.domain import INPUT_FIELDS
from src.storage import Storage


class CsvImportBatchTests(unittest.TestCase):
    def make_storage(self):
        storage = Storage(sqlite3.connect(":memory:"))
        storage.initialize_schema()
        return storage

    def write_csv(self, directory: str) -> Path:
        input_path = Path(directory) / "suggestions.csv"
        row = {field: "" for field in INPUT_FIELDS}
        row.update(
            {
                "suggestion_id": "S001",
                "submit_date": "2026-06-01",
                "raw_text": "Night shift canteen meals are cold and need reheating",
                "department": "Production",
                "job_group": "Line worker",
                "work_location": "Plant A",
                "scenario": "Canteen",
                "status": "new",
            }
        )
        with input_path.open("w", encoding="utf-8-sig", newline="") as file:
            writer = csv.DictWriter(file, fieldnames=INPUT_FIELDS)
            writer.writeheader()
            writer.writerow(row)
        return input_path

    def test_run_csv_import_batch_is_idempotent_for_existing_suggestion(self):
        storage = self.make_storage()
        with tempfile.TemporaryDirectory() as directory:
            input_path = self.write_csv(directory)

            first = run_csv_import_batch(storage, input_path)
            self.assertEqual(storage.count_table("source_suggestions"), 1)
            self.assertEqual(storage.count_table("suggestion_analysis"), 1)

            second = run_csv_import_batch(storage, input_path)

        self.assertEqual(first.rows_read, 1)
        self.assertEqual(first.rows_created, 1)
        self.assertEqual(first.rows_skipped, 0)
        self.assertEqual(first.rows_failed, 0)
        self.assertEqual(second.rows_read, 1)
        self.assertEqual(second.rows_created, 0)
        self.assertEqual(second.rows_skipped, 1)
        self.assertEqual(second.rows_failed, 0)
        self.assertEqual(storage.count_table("source_suggestions"), 1)
        self.assertEqual(storage.count_table("suggestion_analysis"), 1)


if __name__ == "__main__":
    unittest.main()
