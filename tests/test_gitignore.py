import unittest
from pathlib import Path


class GitIgnoreTests(unittest.TestCase):
    def test_runtime_outputs_and_databases_are_ignored(self):
        content = Path(".gitignore").read_text(encoding="utf-8")

        for pattern in ["data/", "logs/", "backups/", "*.db", "*.sqlite", "*.sqlite3"]:
            with self.subTest(pattern=pattern):
                self.assertIn(pattern, content)

    def test_private_config_files_are_ignored(self):
        content = Path(".gitignore").read_text(encoding="utf-8")

        for pattern in ["config/*.prod.json", "config/*.local.json"]:
            with self.subTest(pattern=pattern):
                self.assertIn(pattern, content)


if __name__ == "__main__":
    unittest.main()
