from __future__ import annotations

import importlib.util
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from offer_parser.storage import Storage

TOOL = Path(__file__).resolve().parents[3] / "tools" / "scan_kol_offers.py"
spec = importlib.util.spec_from_file_location("scan_kol_offers", TOOL)
scan_tool = importlib.util.module_from_spec(spec)
spec.loader.exec_module(scan_tool)

JOB_AD = (
    "📢 Community Manager, Korea\n🏢 Aptos Foundation\n📍 Remote / Worldwide\n💼 Job type: Full Time\n"
    "Aptos Foundation is looking for an exceptional Community Manager, Korea to join their fast-growing Web3 team."
)
KOL_ASK = "Gm guys Need x Kols 18 send me links 3$ Each post"


class ScanToolTest(unittest.TestCase):
    def test_read_ids_and_links(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "c.csv"
            path.write_text("enabled,chat_id,title,username,kind\n1,-1001,A,a,kol\n0,-1002,B,,kol\n", encoding="utf-8")
            self.assertEqual(scan_tool.read_ids(path), {-1001})
            self.assertEqual(scan_tool.read_ids(Path(directory) / "missing.csv"), set())
        self.assertEqual(scan_tool.message_url(-1009, None, 5), "https://t.me/c/9/5")
        self.assertEqual(scan_tool.message_url(-1009, "kolhub", 5), "https://t.me/kolhub/5")


@unittest.skipUnless(importlib.util.find_spec("telethon"), "telethon is not installed")
class MonitorRoutingTest(unittest.IsolatedAsyncioTestCase):
    def pipeline(self, offers: Storage, knowledge: Storage, **kwargs):
        from offer_parser.service import MessagePipeline

        matcher = SimpleNamespace(evaluate=lambda text: SimpleNamespace(score=9, matched=("community manager",), compensation=()))
        return MessagePipeline(
            settings=SimpleNamespace(min_score=5, notify_target="me", knowledge_target="bot", knowledge_min_score=4,
                                     knowledge_balance_enabled=False, knowledge_daily_limit=15,
                                     twitter_daily_limit=5, twitter_mechanics_daily_limit=2),
            matcher=matcher, storage=offers, knowledge_matcher=matcher, knowledge_storage=knowledge,
            twitter_priority_by_id={}, twitter_ai=None, twitter_strength=None, **kwargs,
        )

    def event(self, chat_id: int, text: str):
        return SimpleNamespace(
            raw_text=text, chat_id=chat_id, id=7, date=datetime.now(timezone.utc), sender=None,
            get_sender=AsyncMock(return_value=SimpleNamespace(id=1, first_name="A", last_name=None)),
            get_chat=AsyncMock(return_value=SimpleNamespace(username="jobs")),
        )

    async def run_case(self, chat_kind: str, text: str, legacy: str = "0"):
        with tempfile.TemporaryDirectory() as directory, patch.dict("os.environ", {"LEGACY_OFFERS": legacy}):
            offers, knowledge = Storage(Path(directory) / "o.db"), Storage(Path(directory) / "k.db")
            ids = {"selected_by_id": {}, "knowledge_by_id": {}, "kol_by_id": {}}
            ids[chat_kind] = {-100: "Chat"}
            pipeline = self.pipeline(offers, knowledge, **ids)
            await pipeline.handle(self.event(-100, text))
            sent = [row[0] for row in knowledge.connection.execute("SELECT body FROM outbox")]
            old = [row[0] for row in offers.connection.execute("SELECT body FROM outbox")]
            kinds = [row[0] for row in knowledge.connection.execute("SELECT item_kind FROM offers")]
            offers.close()
            knowledge.close()
        return sent, old, kinds

    async def test_job_ad_is_not_sent_as_an_offer_any_more(self):
        sent, old, _ = await self.run_case("selected_by_id", JOB_AD)
        self.assertEqual((sent, old), ([], []))

    async def test_legacy_switch_brings_old_offers_back(self):
        _, old, _ = await self.run_case("selected_by_id", JOB_AD, legacy="1")
        self.assertEqual(len(old), 1)

    async def test_kol_only_chat_sends_kol_offers_and_nothing_else(self):
        sent, old, kinds = await self.run_case("kol_by_id", KOL_ASK)
        self.assertEqual(len(sent), 1)
        self.assertTrue(sent[0].startswith("💸 ИЩУТ KOL"))
        self.assertEqual((old, kinds), ([], ["offer"]))
        sent, _, kinds = await self.run_case("kol_by_id", JOB_AD)
        self.assertEqual((sent, kinds), ([], []))


if __name__ == "__main__":
    unittest.main()
