from __future__ import annotations

import importlib.util
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from offer_parser.firsay_feed import handle_item, to_event
from offer_parser.kol_offers import detect, format_offer
from offer_parser.kol_pipeline import notified_today, process_kol
from offer_parser.storage import Storage

# The owner's own examples of offers they want (2026-11-16).
OFFERS = [
    "Hello KOLs!\n\nWe're looking for project profile accounts like @Crevia_AI (http://x.com/crevia_ai) for "
    "partnership posts.\n\nwe will pay you for paid posting, if interested please DMs.",
    "Hey guys, been a while!\n\nThere's a small gig, and the pay is currently 30$ per KOL\n\nOne original post from "
    "KOLs and engagement on all posts per week.\n\nIf you're interested, DM me, and I'll add you to the group.\n\n"
    "I'm actually looking for people to push the project.\n\nJust 20 KOLs needed.",
    "мне нужны ИИ креаторы\n\nкак можно больше \n\nс ценой в лс @ol_onX",
    "Gm, i need Stock/ market / Finance creators \n\nStrictly just that, drop your links with the niche Ty\n\n"
    "Recommending just a few",
    "I need AI creator profiles, not web3 or CT\n\nNote : strictly AI \n\nGot a paid offer for anyone, who is or know someone",
]

NOT_OFFERS = [
    # service seller spam seen in the Firsay feed
    "Hi Project owners, are you here so you can see it? 20 Telegram Channels + 15 X Accounts  Target: $800K–$1M "
    "Market Cap  Reach investors across the USA, UK, Canada, UAE, Europe & more.",
    "I'm a KOL with 50k followers, open for paid collabs, DM me for rates",
    "We offer KOL marketing packages for your project, 100+ influencers",
    # mutual follows and guides from the owner's groups
    "просто кидайте ссылки на свои твиттер аккаунты и подписывайтесь друг на друга",
    "Тебе не нужен сразу огромный аккаунт и куча подписчиков. Нужен нормальный старт и немного терпения.",
    "Connect your wallet to claim your airdrop, looking for KOLs to share it",
    "gm everyone, how is the market today",
]


class DetectTest(unittest.TestCase):
    def test_owner_examples_are_offers(self):
        for text in OFFERS:
            self.assertTrue(detect(text).is_offer, text[:40])

    def test_sellers_spam_and_guides_are_not(self):
        for text in NOT_OFFERS:
            self.assertFalse(detect(text).is_offer, text[:40])

    def test_long_guide_with_prices_is_not_an_offer(self):
        guide = ("Нужен прокси, я всегда использую ProxyLine, прайс всего 1.7$ в месяц. " * 20) + "нужны аккаунты"
        self.assertEqual(detect(guide).reason, "long_text")

    def test_fields(self):
        offer = detect(OFFERS[1])
        self.assertEqual(offer.pay, "30$ per KOL")
        self.assertEqual(detect(OFFERS[2]).contacts, ("@ol_onX",))
        self.assertIn("AI", detect(OFFERS[4]).niches)
        self.assertIn("Финансы", detect(OFFERS[3]).niches)

    def test_message_format(self):
        text = format_offer(detect(OFFERS[2]), text=OFFERS[2], chat_title="Jobs", url="https://t.me/jobs/1")
        self.assertTrue(text.startswith("💸 ИЩУТ KOL · AI"))
        self.assertIn("Контакт: @ol_onX", text)
        self.assertIn("Ссылка: https://t.me/jobs/1", text)


class PipelineTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.storage = Storage(Path(self.temp.name) / "k.db")
        self.pipeline = SimpleNamespace(
            knowledge_storage=self.storage, storage=self.storage, knowledge_by_id={},
            knowledge_matcher=object(), settings=SimpleNamespace(notify_target="me"),
        )

        async def guarded(handler, storage, stream, event, text):
            await handler(event, text)

        self.pipeline._guarded = guarded

    async def asyncTearDown(self):
        self.storage.close()
        self.temp.cleanup()

    def event(self, message_id: int, chat_id: int = -1001234567890, username: str | None = "kolgigs"):
        return SimpleNamespace(chat_id=chat_id, id=message_id, date=datetime.now(timezone.utc), sender=None,
                               get_chat=AsyncMock(return_value=SimpleNamespace(username=username)))

    def outbox(self):
        return [row[0] for row in self.storage.connection.execute("SELECT body FROM outbox ORDER BY rowid")]

    async def test_offer_is_saved_and_sent_once(self):
        self.assertTrue(await process_kol(self.pipeline, self.event(1), OFFERS[0], "KOL Gigs"))
        self.assertTrue(await process_kol(self.pipeline, self.event(2, chat_id=-1009), OFFERS[0], "Other"))
        self.assertEqual(len(self.outbox()), 1)
        self.assertIn("Ссылка: https://t.me/kolgigs/1", self.outbox()[0])
        kind = self.storage.connection.execute("SELECT item_kind FROM offers").fetchone()[0]
        self.assertEqual(kind, "offer")
        self.assertEqual(notified_today(self.storage), 1)

    async def test_private_chat_link_and_non_offer(self):
        await process_kol(self.pipeline, self.event(5, username=None), OFFERS[2], "Closed group")
        self.assertIn("https://t.me/c/1234567890/5", self.outbox()[0])
        self.assertFalse(await process_kol(self.pipeline, self.event(6), NOT_OFFERS[0], "Spam"))

    async def test_daily_cap(self):
        from unittest.mock import patch

        with patch.dict("os.environ", {"KOL_NOTIFY_LIMIT": "1"}):
            await process_kol(self.pipeline, self.event(1), OFFERS[0], "A")
            await process_kol(self.pipeline, self.event(2), OFFERS[3], "A")
        self.assertEqual(len(self.outbox()), 1)
        reasons = [r[0] for r in self.storage.connection.execute(
            "SELECT reason FROM audit_decisions WHERE stream='kol_offer' ORDER BY id")]
        self.assertEqual(reasons, ["notified", "radar_only"])

    async def test_firsay_feed_takes_only_kol_offers(self):
        item = {"url": "https://t.me/kolhub/77", "text": OFFERS[4], "source": {"title": "KOL Hub"},
                "publishedAt": "2026-11-16T10:00:00Z", "sender": "boss"}
        event, title = to_event(item)
        await handle_item(self.pipeline, event, title, 10)
        spam, spam_title = to_event({**item, "url": "https://t.me/kolhub/78", "text": NOT_OFFERS[0]})
        await handle_item(self.pipeline, spam, spam_title, 10)
        bodies = self.outbox()
        self.assertEqual(len(bodies), 1)
        self.assertIn("Чат: Firsay · KOL Hub", bodies[0])
        self.assertIn("Контакт: @boss", bodies[0])


@unittest.skipUnless(importlib.util.find_spec("telethon"), "telethon is not installed")
class OwnChatTest(unittest.IsolatedAsyncioTestCase):
    async def test_kol_offer_in_own_chat_is_not_sent_twice(self):
        from offer_parser.service import MessagePipeline

        matcher = SimpleNamespace(evaluate=lambda text: SimpleNamespace(score=9, matched=("looking for kol",), compensation=()))
        with tempfile.TemporaryDirectory() as directory:
            offers, knowledge = Storage(Path(directory) / "o.db"), Storage(Path(directory) / "k.db")
            pipeline = MessagePipeline(
                settings=SimpleNamespace(min_score=5, notify_target="me", knowledge_target="bot", knowledge_min_score=4,
                                         knowledge_balance_enabled=False, knowledge_daily_limit=15,
                                         twitter_daily_limit=5, twitter_mechanics_daily_limit=2),
                selected_by_id={-100: "KOL chat"}, matcher=matcher, storage=offers, knowledge_by_id={},
                knowledge_matcher=matcher, knowledge_storage=knowledge, twitter_priority_by_id={},
                twitter_ai=None, twitter_strength=None,
            )
            event = SimpleNamespace(
                raw_text=OFFERS[0], chat_id=-100, id=3, date=datetime.now(timezone.utc), sender=None,
                get_sender=AsyncMock(return_value=SimpleNamespace(id=7, first_name="A", last_name=None)),
                get_chat=AsyncMock(return_value=SimpleNamespace(username="kolchat")),
            )
            await pipeline.handle(event)
            sent = [r[0] for r in knowledge.connection.execute("SELECT body FROM outbox")]
            old = offers.connection.execute("SELECT COUNT(*) FROM outbox").fetchone()[0]
            offers.close()
            knowledge.close()
        self.assertEqual(len(sent), 1)
        self.assertTrue(sent[0].startswith("💸 ИЩУТ KOL"))
        self.assertEqual(old, 0)


if __name__ == "__main__":
    unittest.main()
