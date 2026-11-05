from __future__ import annotations

import importlib.util
import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

from offer_parser.health_report import BAD, OK, WARN, build_report, check_collector, check_services
from offer_parser.storage import OfferRecord, Storage

NOW = datetime.now(timezone.utc)


def record(message_id: int, chat_id: int = -100) -> OfferRecord:
    return OfferRecord(
        chat_id=chat_id, message_id=message_id, chat_title="Jobs", sender_id=1, sender_name="A",
        message_date=NOW, text=f"Paid KOL campaign number {message_id}", score=8,
        matched_rules="looking for kol", compensation="$500", source_url=f"https://t.me/jobs/{message_id}",
    )


def metrics_db(path: Path, *, failures: int, hours_ago: float | None) -> Path:
    with closing(sqlite3.connect(path)) as db, db:
        db.execute("CREATE TABLE snapshots (url TEXT, collected_at TEXT)")
        db.execute("CREATE TABLE collector_health (id INTEGER PRIMARY KEY, consecutive_failures INTEGER, alerted INTEGER, last_error TEXT, updated_at TEXT)")
        db.execute("INSERT INTO collector_health VALUES (1, ?, 0, 'HTTPError: 502', NULL)", (failures,))
        if hours_ago is not None:
            db.execute("INSERT INTO snapshots VALUES ('u', ?)", ((NOW - timedelta(hours=hours_ago)).isoformat(),))
    return path


def services(state: str = "active"):
    return lambda *args, **kwargs: SimpleNamespace(stdout=state + "\n")


class CollectorTest(unittest.TestCase):
    def test_states(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.assertEqual(check_collector(metrics_db(root / "a.db", failures=0, hours_ago=1), NOW).status, OK)
            self.assertEqual(check_collector(metrics_db(root / "b.db", failures=0, hours_ago=12), NOW).status, WARN)
            blocked = check_collector(metrics_db(root / "c.db", failures=4, hours_ago=1), NOW)
            self.assertEqual(blocked.status, BAD)
            self.assertIn("проверку", blocked.detail)
            self.assertEqual(check_collector(root / "missing.db", NOW).status, WARN)

    def test_old_enricher_without_health_table(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "m.db"
            with closing(sqlite3.connect(path)) as db, db:
                db.execute("CREATE TABLE snapshots (url TEXT, collected_at TEXT)")
                db.execute("INSERT INTO snapshots VALUES ('u', ?)", (NOW.isoformat(),))
            self.assertEqual(check_collector(path, NOW).status, OK)


class ServicesTest(unittest.TestCase):
    def test_down_service_is_named(self):
        check = check_services(services("inactive"))
        self.assertEqual(check.status, BAD)
        self.assertIn("obsidian-telegram-bot (inactive)", check.detail)
        self.assertEqual(check_services(services()).status, OK)

    def test_no_systemd(self):
        def missing(*args, **kwargs):
            raise FileNotFoundError

        self.assertEqual(check_services(missing).status, WARN)


class ReportTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.knowledge_path, self.offers_path = root / "knowledge.db", root / "offers.db"
        self.knowledge, self.offers = Storage(self.knowledge_path), Storage(self.offers_path)
        self.metrics = metrics_db(root / "x_metrics.db", failures=0, hours_ago=1)

    def tearDown(self):
        self.knowledge.close()
        self.offers.close()
        self.temp.cleanup()

    def report(self, **overrides) -> str:
        options = dict(knowledge_db=self.knowledge_path, offers_db=self.offers_path, metrics_db=self.metrics,
                       firsay_enabled=True, digest_hour=21, runner=services())
        options.update(overrides)
        return build_report(**options)

    def test_quiet_day_lists_every_problem(self):
        text = self.report()
        self.assertIn("🩺 Здоровье системы", text)
        self.assertIn("❌ Парсер: за сутки ни одного решения", text)
        self.assertIn("❌ Дайджест", text)
        self.assertIn("⚠️ Firsay", text)
        self.assertIn("✅ Коллектор X", text)
        self.assertIn("✅ Сервисы", text)

    def test_healthy_day(self):
        self.knowledge.record_decision(chat_id=-1, message_id=1, chat_title="Alpha", stream="knowledge",
                                       decision="accepted", reason="niche:AI", score=7, text="t")
        self.knowledge.record_decision(chat_id=-2, message_id=2, chat_title="Firsay · Jobs", stream="firsay_offer",
                                       decision="accepted", reason="notified", score=8, text="t")
        self.offers.save(record(1), kind="knowledge")
        now_msk = NOW.astimezone(timezone(timedelta(hours=3)))
        day = now_msk.date() if now_msk.hour >= 21 else now_msk.date() - timedelta(days=1)
        self.knowledge.connection.execute("INSERT INTO digest_runs (digest_day) VALUES (?)", (day.isoformat(),))
        self.knowledge.connection.commit()
        text = self.report()
        self.assertIn("✅ Парсер: разобрано 1, принято 1, офферов 1", text)
        self.assertIn("✅ Firsay: сообщений 1, офферов 1, в Telegram 1", text)
        self.assertIn("✅ Дайджест", text)
        self.assertIn("✅ Отправка: очередь пустая", text)

    def test_failed_delivery_and_firsay_off(self):
        self.knowledge.enqueue_message(dedupe_key="x", target="me", body="b")
        self.knowledge.connection.execute("UPDATE outbox SET status = 'failed'")
        self.knowledge.connection.commit()
        text = self.report(firsay_enabled=False)
        self.assertIn("❌ Отправка: не доставлено 1", text)
        self.assertIn("✅ Firsay: подхват выключен", text)


class CopyOffersTest(unittest.TestCase):
    def test_recent_offers_are_copied_once(self):
        with tempfile.TemporaryDirectory() as directory:
            source, target = Storage(Path(directory) / "o.db"), Storage(Path(directory) / "k.db")
            source.save(record(1))
            source.save(record(2))
            target.save(record(2), kind="vacancy")  # already in Radar under another kind
            self.assertEqual(source.copy_recent_offers(target), 1)
            self.assertEqual(source.copy_recent_offers(target), 0)
            kinds = dict(target.connection.execute("SELECT message_id, item_kind FROM offers").fetchall())
            source.close()
            target.close()
        self.assertEqual(kinds, {1: "offer", 2: "vacancy"})


@unittest.skipUnless(importlib.util.find_spec("telethon"), "telethon is not installed")
class OwnOfferToRadarTest(unittest.IsolatedAsyncioTestCase):
    async def test_offer_from_own_chat_is_copied_for_radar(self):
        from unittest.mock import AsyncMock

        from offer_parser.service import MessagePipeline

        matcher = SimpleNamespace(evaluate=lambda text: SimpleNamespace(score=8, matched=("looking for kol",), compensation=("$500",)))
        with tempfile.TemporaryDirectory() as directory:
            offers, knowledge = Storage(Path(directory) / "o.db"), Storage(Path(directory) / "k.db")
            pipeline = MessagePipeline(
                settings=SimpleNamespace(min_score=5, notify_target="me", knowledge_target="bot", knowledge_min_score=4,
                                         knowledge_balance_enabled=False, knowledge_daily_limit=15,
                                         twitter_daily_limit=5, twitter_mechanics_daily_limit=2),
                selected_by_id={-100: "Jobs"}, matcher=matcher, storage=offers, knowledge_by_id={},
                knowledge_matcher=matcher, knowledge_storage=knowledge, twitter_priority_by_id={},
                twitter_ai=None, twitter_strength=None,
            )
            event = SimpleNamespace(
                raw_text="Looking for KOL, paid $500 per post", chat_id=-100, id=9, date=NOW,
                get_sender=AsyncMock(return_value=SimpleNamespace(id=7, first_name="A", last_name=None)),
                get_chat=AsyncMock(return_value=SimpleNamespace(username="jobs")),
            )
            await pipeline.handle(event)
            row = knowledge.connection.execute("SELECT item_kind, source_url FROM offers").fetchone()
            offers.close()
            knowledge.close()
        self.assertEqual(tuple(row), ("offer", "https://t.me/jobs/9"))


if __name__ == "__main__":
    unittest.main()
