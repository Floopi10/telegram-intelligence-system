from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from offer_parser.firsay_feed import (
    FeedConfig, SYNTHETIC_CHAT_BASE, handle_item, offers_notified_today, pull_once, read_cursor, to_event,
)
from offer_parser.storage import Storage

TEXT = "We are hiring a BD manager for a Solana launchpad, remote, paid in USDT, DM to apply now."


def item(url: str, text: str = TEXT, title: str = "Web3 Jobs") -> dict:
    return {"url": url, "text": text, "publishedAt": "2026-11-05T10:00:00.000Z",
            "categories": ["job"], "source": {"username": "web3jobs", "title": title}}


def config(directory: str) -> FeedConfig:
    return FeedConfig(url="https://firsay.test/api?route=intel-export", token="t" * 40,
                      interval_s=3600, max_pages=3, cursor_path=Path(directory) / "cursor.txt")


def match(score: int):
    return SimpleNamespace(evaluate=lambda text: SimpleNamespace(
        score=score, matched=("looking for kol",), compensation=("$500",)))


class FakePipeline:
    def __init__(self, storage=None, offer_score: int = 0):
        self.knowledge_by_id: dict[int, str] = {}
        self.knowledge_matcher = object()
        self.knowledge_storage = storage or object()
        self.matcher = match(offer_score)
        self.settings = SimpleNamespace(min_score=5, notify_target="me")
        self.seen: list[tuple[int, int, str]] = []

    async def _handle_knowledge(self, event, text):
        chat = await event.get_chat()
        self.seen.append((event.chat_id, event.id, chat.username))

    async def _guarded(self, handler, storage, stream, event, text):
        await handler(event, text)


class ToEventTest(unittest.TestCase):
    def test_public_link_keeps_username_and_message_id(self):
        event, title = to_event(item("https://t.me/web3jobs/42"))
        self.assertEqual((event.id, event._chat.username), (42, "web3jobs"))
        self.assertLess(event.chat_id, SYNTHETIC_CHAT_BASE)
        self.assertEqual(title, "Firsay · Web3 Jobs")
        self.assertEqual(event.chat_id, to_event(item("https://t.me/Web3Jobs/1"))[0].chat_id)

    def test_private_link_maps_to_the_real_chat_id(self):
        event, _ = to_event(item("https://t.me/c/1234567890/7"))
        self.assertEqual((event.chat_id, event.id, event._chat.username), (-1001234567890, 7, None))

    def test_items_without_a_telegram_link_or_text_are_skipped(self):
        self.assertIsNone(to_event(item("https://example.com/a/1")))
        self.assertIsNone(to_event(item("https://t.me/web3jobs/1", text="  ")))


class ConfigTest(unittest.TestCase):
    def test_off_without_token(self):
        with patch.dict("os.environ", {"FIRSAY_FEED_TOKEN": ""}):
            self.assertIsNone(FeedConfig.from_env(Path(".")))

    def test_defaults(self):
        with patch.dict("os.environ", {"FIRSAY_FEED_TOKEN": "x" * 40}, clear=False):
            cfg = FeedConfig.from_env(Path("data"))
        self.assertEqual((cfg.interval_s, cfg.max_pages), (3600, 5))
        self.assertIn("route=intel-export", cfg.url)


class PullTest(unittest.IsolatedAsyncioTestCase):
    async def test_pages_until_done_and_saves_the_cursor(self):
        pages = [
            {"items": [item("https://t.me/web3jobs/1"), item("https://example.com/x")], "next": "c1", "more": True},
            {"items": [item("https://t.me/c/99/2", title="Private")], "next": "c2", "more": False},
        ]
        calls = []

        def fetch(cfg, after):
            calls.append(after)
            return pages[len(calls) - 1]

        with tempfile.TemporaryDirectory() as directory:
            cfg = config(directory)
            pipeline = FakePipeline()
            self.assertEqual(await pull_once(pipeline, cfg, fetch), 2)
            self.assertEqual(calls, ["", "c1"])
            self.assertEqual(read_cursor(cfg.cursor_path), "c2")
            self.assertEqual([row[1] for row in pipeline.seen], [1, 2])
            self.assertIn("Firsay · Private", pipeline.knowledge_by_id.values())

    async def test_a_failed_page_keeps_the_old_cursor(self):
        def fetch(cfg, after):
            raise TimeoutError

        with tempfile.TemporaryDirectory() as directory:
            cfg = config(directory)
            cfg.cursor_path.write_text("old", encoding="utf-8")
            with self.assertRaises(TimeoutError):
                await pull_once(FakePipeline(), cfg, fetch)
            self.assertEqual(read_cursor(cfg.cursor_path), "old")

    async def test_stops_at_max_pages(self):
        def fetch(cfg, after):
            return {"items": [], "next": after + "x", "more": True}

        with tempfile.TemporaryDirectory() as directory:
            cfg = config(directory)
            await pull_once(FakePipeline(), cfg, fetch)
            self.assertEqual(read_cursor(cfg.cursor_path), "xxx")


class OfferTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.storage = Storage(Path(self.temp.name) / "knowledge.db")

    async def asyncTearDown(self):
        self.storage.close()
        self.temp.cleanup()

    def outbox(self) -> list[str]:
        return [row[0] for row in self.storage.connection.execute("SELECT body FROM outbox ORDER BY rowid")]

    async def send(self, pipeline, number: int, text: str | None = None, limit: int = 2):
        event, title = to_event(item(f"https://t.me/web3jobs/{number}",
                                     text=text or f"Looking for KOL number {number}, paid $500 per post, DM us."))
        await handle_item(pipeline, event, title, limit)

    async def test_offers_are_kept_and_only_the_first_ones_notified(self):
        pipeline = FakePipeline(self.storage, offer_score=8)
        for number in range(1, 4):
            await self.send(pipeline, number, text=f"Paid KOL campaign {number} for a new L2: $500 per post, DM. Unique {number * 7919}")
        rows = self.storage.connection.execute(
            "SELECT item_kind, source_url, chat_title FROM offers ORDER BY message_id").fetchall()
        self.assertEqual([tuple(row) for row in rows][0], ("offer", "https://t.me/web3jobs/1", "Firsay · Web3 Jobs"))
        self.assertEqual(len(rows), 3)
        self.assertEqual(offers_notified_today(self.storage), 2)
        bodies = self.outbox()
        self.assertEqual(len(bodies), 2)
        self.assertIn("НАЙДЕН ОФФЕР (FIRSAY)", bodies[0])
        self.assertIn("https://t.me/web3jobs/1", bodies[0])

    async def test_duplicate_offer_is_not_notified_again(self):
        pipeline = FakePipeline(self.storage, offer_score=8)
        await self.send(pipeline, 1)
        await self.send(pipeline, 2)  # same text
        self.assertEqual(len(self.outbox()), 1)

    async def test_low_score_goes_to_knowledge(self):
        pipeline = FakePipeline(self.storage, offer_score=1)
        await self.send(pipeline, 5)
        self.assertEqual(pipeline.seen, [(to_event(item("https://t.me/web3jobs/5"))[0].chat_id, 5, "web3jobs")])
        self.assertEqual(self.outbox(), [])


@unittest.skipUnless(importlib.util.find_spec("telethon"), "telethon is not installed")
class RealPipelineTest(unittest.IsolatedAsyncioTestCase):
    async def test_feed_message_lands_in_the_knowledge_store_with_its_link(self):
        from offer_parser.service import MessagePipeline

        matcher = SimpleNamespace(evaluate=lambda text: SimpleNamespace(score=8, matched=(), compensation=()))
        with tempfile.TemporaryDirectory() as directory:
            storage = Storage(Path(directory) / "knowledge.db")
            pipeline = MessagePipeline(
                settings=SimpleNamespace(min_score=5, notify_target="me", knowledge_target="bot",
                                         knowledge_min_score=4, knowledge_balance_enabled=False,
                                         knowledge_daily_limit=15, twitter_daily_limit=5,
                                         twitter_mechanics_daily_limit=2),
                selected_by_id={}, matcher=matcher, storage=storage, knowledge_by_id={},
                knowledge_matcher=matcher, knowledge_storage=storage, twitter_priority_by_id={},
                twitter_ai=None, twitter_strength=None,
            )
            text = "Grant program for Web3 builders: up to $50,000 for open-source tooling, apply by Friday."

            def fetch(cfg, after):
                return {"items": [item("https://t.me/web3grants/77", text=text, title="Web3 Grants")],
                        "next": "n", "more": False}

            await pull_once(pipeline, config(directory), fetch)
            row = storage.connection.execute("SELECT chat_title, source_url FROM offers").fetchone()
            storage.close()
        self.assertEqual(tuple(row), ("Firsay · Web3 Grants", "https://t.me/web3grants/77"))


if __name__ == "__main__":
    unittest.main()
