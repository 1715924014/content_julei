import unittest
from pathlib import Path


class CiWorkflowTests(unittest.TestCase):
    def test_github_actions_runs_unit_tests(self):
        workflow = Path(".github/workflows/tests.yml")

        self.assertTrue(workflow.exists())
        content = workflow.read_text(encoding="utf-8")
        self.assertIn("actions/checkout", content)
        self.assertIn("actions/setup-python", content)
        self.assertIn("python -m pip install -r requirements.txt", content)
        self.assertIn("git diff --check", content)
        self.assertIn("python -m unittest discover -s tests", content)


if __name__ == "__main__":
    unittest.main()
