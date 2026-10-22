import unittest
from datetime import datetime, timezone
from pathlib import Path

from offer_parser.matcher import OfferMatcher
from offer_parser.service import format_knowledge_notification
from offer_parser.storage import OfferRecord


ROOT = Path(__file__).resolve().parent.parent


class KnowledgePipelineTest(unittest.TestCase):
    def setUp(self):
        self.matcher = OfferMatcher(ROOT / "knowledge_rules.json")

    def test_accepts_useful_twitter_breakdown(self):
        result = self.matcher.evaluate(
            "Twitter post breakdown: hook, impressions and audience growth case study"
        )
        self.assertGreaterEqual(result.score, 4)

    def test_rejects_offer_noise(self):
        result = self.matcher.evaluate(
            "Looking for KOLs for paid collaboration. DM me and fill this form."
        )
        self.assertLess(result.score, 4)

    def test_accepts_english_bd_vacancy(self):
        result = self.matcher.evaluate(
            "We are hiring a Business Development Manager for a Web3 startup."
        )
        self.assertGreaterEqual(result.score, 4)

    def test_accepts_russian_community_vacancy(self):
        result = self.matcher.evaluate(
            "Вакансия: ищем комьюнити-менеджера для криптопроекта."
        )
        self.assertGreaterEqual(result.score, 4)

    def test_accepts_related_marketing_vacancy(self):
        result = self.matcher.evaluate(
            "Open position: Growth Marketer for a crypto product."
        )
        self.assertGreaterEqual(result.score, 4)

    def test_rejects_unrelated_developer_vacancy(self):
        result = self.matcher.evaluate(
            "We are hiring a senior backend developer for a Web3 startup."
        )
        self.assertLess(result.score, 4)

    def test_rejects_unrelated_trader_vacancy(self):
        result = self.matcher.evaluate(
            "Вакансия: трейдер криптовалют, опыт от двух лет."
        )
        self.assertLess(result.score, 4)

    def test_rejects_content_writer_vacancy(self):
        result = self.matcher.evaluate(
            "We are hiring a Content Writer and Copywriter for a Web3 startup."
        )
        self.assertLess(result.score, 4)

    def test_rejects_smm_vacancy(self):
        result = self.matcher.evaluate(
            "Вакансия: ищем SMM-менеджера для криптопроекта."
        )
        self.assertLess(result.score, 4)

    def test_rejects_research_and_analyst_vacancy(self):
        result = self.matcher.evaluate(
            "Open position: Research Analyst for a crypto product."
        )
        self.assertLess(result.score, 4)

    def test_rejects_graphic_designer_vacancy(self):
        result = self.matcher.evaluate(
            "We are hiring a Graphic and Creative Designer for Web3."
        )
        self.assertLess(result.score, 4)

    def test_accepts_russian_post_and_video_evaluation(self):
        self.assertGreaterEqual(
            self.matcher.evaluate("Оценка поста и полезный разбор видео").score,
            4,
        )

    def test_notification_is_bounded_and_keeps_provenance(self):
        result = self.matcher.evaluate("Twitter algorithm case study")
        record = OfferRecord(
            chat_id=-100123,
            message_id=42,
            chat_title="Research",
            sender_id=7,
            sender_name="Alice",
            message_date=datetime.now(timezone.utc),
            text="x" * 5000,
            score=result.score,
            matched_rules=", ".join(result.matched),
            compensation="",
            source_url="https://t.me/c/123/42",
        )
        notification = format_knowledge_notification(record, result)
        self.assertLessEqual(len(notification), 3900)
        self.assertIn("Research", notification)
        self.assertIn("https://t.me/c/123/42", notification)
        self.assertTrue(notification.endswith("…"))

    def test_vacancy_notification_has_explicit_type(self):
        result = self.matcher.evaluate(
            "Crypto jobs: we are hiring a Business Development Manager"
        )
        record = OfferRecord(
            chat_id=-100123,
            message_id=43,
            chat_title="Jobs",
            sender_id=7,
            sender_name="Alice",
            message_date=datetime.now(timezone.utc),
            text="Crypto jobs: we are hiring a Business Development Manager",
            score=result.score,
            matched_rules=", ".join(result.matched),
            compensation="",
            source_url="https://t.me/c/123/43",
        )
        notification = format_knowledge_notification(record, result)
        self.assertIn("Тип: Вакансия", notification)


if __name__ == "__main__":
    unittest.main()
