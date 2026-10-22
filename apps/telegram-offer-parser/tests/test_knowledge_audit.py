import unittest
from pathlib import Path

from offer_parser.knowledge_audit import _topics, render_summary


class KnowledgeAuditTest(unittest.TestCase):
    def test_topics_count_each_chat_once(self):
        rows = [
            {"status": "ok", "excerpt": "Twitter analytics analytics growth"},
            {"status": "ok", "excerpt": "Twitter content growth"},
            {"status": "error", "excerpt": "ignored topic"},
        ]
        topics = dict(_topics(rows))
        self.assertEqual(topics["twitter"], 2)
        self.assertEqual(topics["growth"], 2)
        self.assertEqual(topics["analytics"], 1)
        self.assertNotIn("ignored", topics)

    def test_summary_contains_scores_topics_and_candidate(self):
        rows = [
            {
                "status": "ok",
                "score": "6",
                "message_date": "2026-10-16T10:00:00+00:00",
                "matched_rules": "post breakdown, twitter",
                "excerpt": "Twitter post breakdown with analytics",
                "chat_title": "Content Lab",
                "source_url": "https://t.me/example/42",
            },
            {
                "status": "no_text",
                "score": "0",
                "message_date": "",
                "matched_rules": "",
                "excerpt": "",
                "chat_title": "Media only",
                "source_url": "",
            },
        ]
        summary = render_summary(rows, Path("audit.csv"))
        self.assertIn("Проверено чатов: **2**", summary)
        self.assertIn("score 6: 1", summary)
        self.assertIn("post breakdown: 1", summary)
        self.assertIn("Content Lab", summary)
        self.assertIn("https://t.me/example/42", summary)


if __name__ == "__main__":
    unittest.main()
