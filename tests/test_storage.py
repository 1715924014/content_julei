import sqlite3
import unittest

from src.storage import Storage


class StorageTests(unittest.TestCase):
    def make_storage(self):
        storage = Storage(sqlite3.connect(":memory:"))
        storage.initialize_schema()
        return storage

    def test_import_batch_lifecycle_stores_success_status_and_cursor_end(self):
        storage = self.make_storage()

        batch_id = storage.start_import_batch("suggestions.csv", cursor_start="10")
        storage.finish_import_batch(batch_id, status="success", cursor_end="25")

        batch = storage.get_import_batch(batch_id)
        self.assertIsNotNone(batch)
        self.assertEqual(batch["source_name"], "suggestions.csv")
        self.assertEqual(batch["cursor_start"], "10")
        self.assertEqual(batch["status"], "success")
        self.assertEqual(batch["cursor_end"], "25")
        self.assertIsNotNone(batch["started_at"])
        self.assertIsNotNone(batch["finished_at"])

    def test_source_suggestion_upsert_is_idempotent_for_identical_rows(self):
        storage = self.make_storage()
        row = {
            "suggestion_id": "S001",
            "submit_date": "2026-06-01",
            "raw_text": "Need hotter canteen meals at night",
            "department": "Production",
            "scenario": "Canteen",
            "status": "new",
        }

        self.assertTrue(storage.upsert_source_suggestion(row))
        self.assertFalse(storage.upsert_source_suggestion(dict(row)))
        self.assertEqual(storage.count_table("source_suggestions"), 1)


if __name__ == "__main__":
    unittest.main()
