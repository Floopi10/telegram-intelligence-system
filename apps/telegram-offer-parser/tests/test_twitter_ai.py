import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from offer_parser.storage import Storage
from offer_parser.twitter_ai import TwitterAIClient, XPreview, canonical_status_url, format_twitter_intelligence


class TwitterAITest(unittest.TestCase):
    def test_canonical_status_url_removes_query(self):
        self.assertEqual(canonical_status_url("https://twitter.com/User/status/123?s=20"), "https://x.com/User/status/123")
        self.assertIsNone(canonical_status_url("https://x.com/profile"))

    def _client(self, payload):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        env = Path(temp.name) / ".env"
        env.write_text("OPENROUTER_API_KEY=test\nOPENROUTER_MODEL=test-model\n")
        client = TwitterAIClient(env, min_score=7)
        return client, patch.object(client, "_request", return_value=payload)

    @staticmethod
    def _payload(**overrides):
        base = {"keep": True, "score": 8, "topic": "AI/Creative Coding", "hook": "contrast", "analysis_ru": "Полноценный разбор механики поста.", "key_points": ["Первая мысль", "Вторая мысль", "Третья мысль"], "metrics_summary": "", "performance_evidence": "none", "risk_flags": [], "reason_ru": "полезно"}
        base.update(overrides)
        return base

    def test_hard_rejects_engagement_bait(self):
        client, mocked = self._client(self._payload(score=9, risk_flags=["engagement_bait"]))
        with mocked:
            result = asyncio.run(client.analyze(XPreview("https://x.com/a/status/1", "t", "d")))
        self.assertFalse(result.keep)

    def test_accepts_clean_relevant_post_and_new_format(self):
        client, mocked = self._client(self._payload())
        with mocked:
            result = asyncio.run(client.analyze(XPreview("https://x.com/a/status/3", "title", "tweet text")))
        self.assertTrue(result.keep)
        rendered = format_twitter_intelligence("X FAMILY", "P0", XPreview("https://x.com/a/status/3", "title", "tweet text"), result)
        self.assertIn("AI score: 8/10", rendered)
        self.assertIn("Полный разбор:", rendered)
        self.assertIn("Ключевые мысли:", rendered)
        self.assertIn("Цитата и источник:", rendered)
        for forbidden in ("Приоритет:", "Как применить:", "Риски:", "Заголовок X:", "Подтверждение метрик:"):
            self.assertNotIn(forbidden, rendered)
        self.assertNotIn("Метрики:", rendered)

    def test_metrics_render_only_when_present(self):
        client, mocked = self._client(self._payload(metrics_summary="120K views, 4K likes", performance_evidence="self_reported"))
        with mocked:
            result = asyncio.run(client.analyze(XPreview("https://x.com/a/status/4", "t", "d")))
        rendered = format_twitter_intelligence("X FAMILY", "P0", XPreview("https://x.com/a/status/4", "t", "d"), result)
        self.assertIn("Метрики:", rendered)
        self.assertIn("указаны автором поста", rendered)

    def test_url_deduplication_is_persistent(self):
        with tempfile.TemporaryDirectory() as directory:
            storage = Storage(Path(directory) / "knowledge.db")
            url = "https://x.com/a/status/3"
            self.assertFalse(storage.twitter_url_seen(url))
            self.assertTrue(storage.save_twitter_analysis(canonical_url=url, chat_id=-1, message_id=1, chat_title="A", decision="keep", ai_score=8, topic="ai_tools", risk_flags=(), analysis={"keep": True}))
            self.assertTrue(storage.twitter_url_seen(url))
            self.assertFalse(storage.save_twitter_analysis(canonical_url=url, chat_id=-2, message_id=2, chat_title="B", decision="keep", ai_score=9, topic="content", risk_flags=(), analysis={"keep": True}))
            storage.close()


if __name__ == "__main__":
    unittest.main()
