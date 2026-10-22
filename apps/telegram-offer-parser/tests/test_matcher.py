import json
import tempfile
import unittest
from pathlib import Path

from offer_parser.matcher import OfferMatcher


class OfferMatcherTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        path = Path(self.temp_dir.name) / "rules.json"
        path.write_text(
            json.dumps(
                {
                    "positive": {"5": ["paid collaboration"], "2": ["budget"]},
                    "negative": {"-6": ["position filled"]},
                }
            ),
            encoding="utf-8",
        )
        self.matcher = OfferMatcher(path)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_scores_offer_and_extracts_money(self):
        result = self.matcher.evaluate("Paid collaboration. Budget: 1,500 USDT")
        self.assertEqual(result.score, 7)
        self.assertIn("1,500 USDT", result.compensation)

    def test_negative_rule_reduces_score(self):
        result = self.matcher.evaluate("Paid collaboration, but position filled")
        self.assertEqual(result.score, -1)

    def test_does_not_match_inside_word(self):
        result = self.matcher.evaluate("Our rebudgeting tool")
        self.assertEqual(result.score, 0)

    def test_weak_context_signals_are_capped(self):
        path = Path(self.temp_dir.name) / "weak-rules.json"
        path.write_text(
            json.dumps(
                {
                    "positive": {"1": ["web3", "telegram", "discord", "twitter"]},
                    "negative": {},
                }
            ),
            encoding="utf-8",
        )
        matcher = OfferMatcher(path)
        result = matcher.evaluate("Web3 Telegram Discord Twitter")
        self.assertEqual(result.score, 2)


if __name__ == "__main__":
    unittest.main()
