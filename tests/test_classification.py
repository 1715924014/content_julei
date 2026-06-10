import unittest

from src.classification import classify_suggestion, detect_quality_type, detect_urgency


class ClassificationTests(unittest.TestCase):
    def test_classifies_canteen_feedback_with_existing_owner_and_confidence(self):
        primary, secondary, owner, confidence = classify_suggestion(
            "夜班下班食堂没有热饭吃，希望加热饭菜",
            "食堂",
        )

        self.assertEqual(primary, "后勤保障")
        self.assertEqual(secondary, "食堂饭菜")
        self.assertEqual(owner, "后勤部")
        self.assertGreaterEqual(confidence, 0.6)

    def test_classifies_safety_feedback_as_high_urgency(self):
        primary, secondary, owner, confidence = classify_suggestion(
            "车间粉尘太大，口罩不够用，有安全隐患",
            "安全",
        )
        urgency = detect_urgency("车间粉尘太大，口罩不够用，有安全隐患", primary)

        self.assertEqual(primary, "安全生产")
        self.assertEqual(secondary, "劳保用品")
        self.assertEqual(owner, "安全环保部")
        self.assertGreaterEqual(confidence, 0.6)
        self.assertEqual(urgency, "高")

    def test_short_feedback_with_short_text_flag_is_information_insufficient(self):
        quality = detect_quality_type("没有", ["文本过短"])

        self.assertEqual(quality, "信息不足")


if __name__ == "__main__":
    unittest.main()
