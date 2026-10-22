import json
import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from offer_parser.matcher import MatchResult
from offer_parser.matcher import OfferMatcher
from offer_parser.service import (
    effective_knowledge_threshold,
    format_knowledge_notification,
)
from offer_parser.storage import OfferRecord
from offer_parser.twitter_priority import (
    TwitterPrioritySource,
    load_twitter_priority_sources,
)


class TwitterPriorityTest(unittest.TestCase):
    def setUp(self):
        self.p0 = TwitterPrioritySource(-1001, "X FAMILY", "P0", 2)
        self.p1 = TwitterPrioritySource(-1002, "OKANE DAO", "P1", 3)
        project_dir = Path(
            os.environ.get(
                "OFFER_PARSER_HOME",
                Path(__file__).resolve().parent.parent,
            )
        )
        self.matcher = OfferMatcher(project_dir / "knowledge_rules.json")

    def test_loads_five_unique_sources(self):
        path = Path(__file__).resolve().parent.parent / "twitter_priority_sources.json"
        sources = load_twitter_priority_sources(path)
        self.assertEqual(len(sources), 5)
        self.assertEqual(sources[-1000000000001].tier, "P0")
        self.assertEqual(
            sources[-1000000000003].title,
            "Secondary X research chat 2",
        )

    def test_priority_thresholds_apply_to_non_vacancies(self):
        self.assertEqual(effective_knowledge_threshold("Twitter hook", 4, self.p0), 2)
        self.assertEqual(effective_knowledge_threshold("Twitter hook", 4, self.p1), 3)
        self.assertEqual(effective_knowledge_threshold("Twitter hook", 4, None), 4)

    def test_vacancies_keep_global_threshold(self):
        self.assertEqual(
            effective_knowledge_threshold(
                "We are hiring a Content Writer",
                4,
                self.p0,
            ),
            4,
        )

    def test_p0_accepts_two_signal_twitter_candidate(self):
        text = "Twitter creator"
        result = self.matcher.evaluate(text)
        threshold = effective_knowledge_threshold(text, 4, self.p0)
        self.assertEqual(result.score, 2)
        self.assertGreaterEqual(result.score, threshold)

    def test_p1_accepts_analytics_candidate(self):
        text = "Полезная аналитика по росту аккаунта"
        result = self.matcher.evaluate(text)
        threshold = effective_knowledge_threshold(text, 4, self.p1)
        self.assertEqual(result.score, 3)
        self.assertGreaterEqual(result.score, threshold)

    def test_p0_rejects_bare_x_link_signal(self):
        text = "https://x.com/example/status/1"
        result = self.matcher.evaluate(text)
        threshold = effective_knowledge_threshold(text, 4, self.p0)
        self.assertEqual(result.score, 1)
        self.assertLess(result.score, threshold)

    def test_priority_notification_does_not_leak_internal_tier(self):
        record = OfferRecord(
            chat_id=self.p0.chat_id,
            message_id=42,
            chat_title=self.p0.title,
            sender_id=7,
            sender_name="Alice",
            message_date=datetime.now(timezone.utc),
            text="Twitter hook and audience growth",
            score=3,
            matched_rules="twitter (+1), hook (+3)",
            compensation="",
            source_url="https://t.me/c/1/42",
        )
        result = MatchResult(score=3, matched=("twitter (+1)",), compensation=())
        notification = format_knowledge_notification(record, result, self.p0)
        self.assertIn("Тип: Twitter Intelligence", notification)
        self.assertNotIn("P0", notification)

    def test_rejects_duplicate_ids(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "priority.json"
            path.write_text(
                json.dumps(
                    {
                        "sources": [
                            {"chat_id": -1, "title": "A", "tier": "P0", "min_score": 2},
                            {"chat_id": -1, "title": "B", "tier": "P1", "min_score": 3},
                        ]
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaises(ValueError):
                load_twitter_priority_sources(path)


if __name__ == "__main__":
    unittest.main()

