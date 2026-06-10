import unittest

from src.domain import Suggestion
from src.text_processing import content_hash, normalize_text, text_features, validate_suggestion


class TextProcessingTests(unittest.TestCase):
    def test_normalize_text_removes_whitespace_and_lowercases(self):
        self.assertEqual(normalize_text("  Ab C\nD\t "), "abcd")

    def test_content_hash_is_stable_for_equivalent_spacing(self):
        self.assertEqual(content_hash("食堂 热饭"), content_hash(" 食堂\n热饭 "))

    def test_text_features_includes_known_keywords_and_ngrams(self):
        features = text_features("食堂 热饭")

        self.assertIn("食堂", features)
        self.assertIn("热饭", features)
        self.assertIn("堂热", features)
        self.assertIn("堂热饭", features)

    def test_text_features_includes_custom_keywords(self):
        features = text_features("Alpha beta gamma", ["alpha", "delta"])

        self.assertIn("alpha", features)
        self.assertNotIn("delta", features)
        self.assertIn("al", features)
        self.assertIn("alp", features)

    def test_validate_suggestion_preserves_raw_text_and_detects_duplicates(self):
        seen_hashes: set[str] = set()
        original_text = " 食堂 热饭 "
        first = Suggestion({"suggestion_id": "S001", "raw_text": original_text})
        duplicate = Suggestion({"suggestion_id": "S002", "raw_text": "食堂热饭"})

        first_flags = validate_suggestion(first, seen_hashes)
        duplicate_flags = validate_suggestion(duplicate, seen_hashes)

        self.assertEqual(first.raw_text, original_text)
        self.assertEqual(first_flags, [])
        self.assertIn("疑似重复", duplicate_flags)


if __name__ == "__main__":
    unittest.main()
