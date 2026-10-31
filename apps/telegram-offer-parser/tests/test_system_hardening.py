from __future__ import annotations

import json
import tempfile
import unittest
import urllib.error
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from telethon.errors import FloodWaitError

from offer_parser.dedupe import build_signature, similarity
from offer_parser.digest import (
    NICHE_LIMITS,
    build_daily_digest,
    digest_window_start,
    refresh_radar_metrics,
    select_twitter_radar,
    send_daily_digest_once,
)
from offer_parser.knowledge_balance import classify_niches
from offer_parser.outbox import MAX_ATTEMPTS, drain_outbox
from offer_parser.service import MessagePipeline
from offer_parser.storage import OfferRecord, Storage
from offer_parser.twitter_ai import (
    TwitterAIClient,
    TwitterAIRateLimited,
    TwitterAnalysis,
    XPreview,
    ai_budget_block,
    first_status_url,
)
from offer_parser.twitter_evidence import extract_telegram_performance_evidence
from offer_parser.twitter_niches import NICHE_ORDER, classify_twitter_niche, is_sport
from offer_parser.twitter_priority import TwitterPrioritySource
from offer_parser.twitter_strength import decide_strength, parse_metrics, parse_number


NOW = datetime(2026, 10, 25, 18, 0, tzinfo=timezone.utc)
URL = "https://x.com/author/status/1"


def radar_row(topic: str, **extra) -> dict:
    return {
        "canonical_url": f"https://x.com/a/status/{abs(hash(topic))}",
        "content_score": 8,
        "views": 20_000,
        "likes": 100,
        "reposts": 10,
        "bookmarks": 10,
        "analysis_json": json.dumps({"topic": topic, "hook": "hook"}),
        **extra,
    }


def metrics(views: int = 50_000, published_hours_ago: float = 24, author: str = "author"):
    published = NOW - timedelta(hours=published_hours_ago)
    return parse_metrics(URL, {
        "author": author, "published_at": published.isoformat(), "collected_at": NOW.isoformat(),
        "views": str(views), "likes": "500", "reposts": "50", "replies": "10", "bookmarks": "20",
    })


def analysis(keep: bool = True, score: int = 8, topic: str = "Claude AI agents") -> TwitterAnalysis:
    return TwitterAnalysis(
        keep=keep, score=score, topic=topic, hook="hook", analysis_ru="разбор",
        key_points=("мысль",), metrics_summary="", performance_evidence="none",
        risk_flags=(), reason_ru="причина",
    )


class RadarSelectionTests(unittest.TestCase):
    def test_every_niche_has_a_limit(self):
        self.assertEqual(set(NICHE_ORDER) - set(NICHE_LIMITS), set())

    def test_specific_niche_does_not_crash_digest(self):
        for topic, niche in (
            ("Mac mini M4 local server", "Mac mini"),
            ("geometry theorem proof", "Математика"),
            ("Claude workflow in Obsidian vault", "Claude + Obsidian"),
        ):
            with self.subTest(niche=niche):
                selected = select_twitter_radar([radar_row(topic)], 50)
                self.assertEqual([item["niche"] for item in selected], [niche])

    def test_stored_niche_keeps_reference_account_classification(self):
        row = radar_row("example post")
        row["analysis_json"] = json.dumps({"topic": "example post", "hook": "h", "niche": "Роботы"})
        self.assertEqual(select_twitter_radar([row], 50)[0]["niche"], "Роботы")

    def test_reference_account_used_when_url_is_passed(self):
        row = radar_row("example post", canonical_url="https://x.com/framexin/status/1")
        self.assertEqual(select_twitter_radar([row], 50)[0]["niche"], "Девушки и lifestyle")

    def test_inconsistent_metrics_are_not_a_winner(self):
        row = radar_row("Claude AI agents", likes=50_000)
        self.assertEqual(select_twitter_radar([row], 50), [])


class ClassificationTests(unittest.TestCase):
    def test_sport_filter_ignores_lookalike_words(self):
        for text in ("AI voice: голос в нейросети", "головоломка про алгоритмы", "barcelona web3 conference"):
            with self.subTest(text=text):
                self.assertFalse(is_sport(text))
        for text in ("Гол на 90 минуте", "Итоги матча", "NBA finals", "FC Barcelona signing"):
            with self.subTest(text=text):
                self.assertTrue(is_sport(text))

    def test_handle_in_url_does_not_trigger_sport(self):
        self.assertFalse(is_sport("https://x.com/nba/status/1"))

    def test_substrings_do_not_create_false_niches(self):
        self.assertNotEqual(classify_twitter_niche("community manager tips"), "Игры")
        self.assertNotEqual(classify_twitter_niche("mainstream audience growth"), "Игры")
        self.assertIsNone(classify_twitter_niche("biotechnology breakthrough"))
        self.assertIsNone(classify_twitter_niche("кодекс поведения"))

    def test_marketing_is_not_classified_as_finance(self):
        self.assertNotIn("Финансы", classify_niches("marketing funnel for new brands"))
        self.assertIn("Финансы", classify_niches("prediction market and the crypto market"))
        self.assertNotIn("Контент", classify_niches("постоянно поставка оборудования"))


class EvidenceAndMetricsTests(unittest.TestCase):
    def test_grouped_numbers_are_not_truncated(self):
        self.assertEqual(extract_telegram_performance_evidence("1 000 просмотров").claimed_views, 1000)
        self.assertEqual(extract_telegram_performance_evidence("1,000 views").claimed_views, 1000)
        self.assertEqual(extract_telegram_performance_evidence("1,5к просмотров").claimed_views, 1500)
        self.assertEqual(extract_telegram_performance_evidence("+10 500 подписчиков").followers_gained, 10500)

    def test_parse_number_formats(self):
        self.assertEqual(parse_number("1,234 Likes"), 1234)
        self.assertEqual(parse_number("1.2K"), 1200)
        self.assertEqual(parse_number("12\xa0345 views"), 12345)
        self.assertIsNone(parse_number("Reply"))

    def test_limit_still_reports_percentiles(self):
        rows = [{"views_per_hour": float(index), "signal_rate": 0.001} for index in range(30)]
        decision = decide_strength(
            content_score=9, metrics=metrics(), author_rows=[], cohort_rows=rows,
            kept_today=5, mechanics_today=0, daily_limit=5, mechanics_limit=2,
        )
        self.assertFalse(decision.keep)
        self.assertEqual(decision.baseline_size, 30)
        self.assertEqual(decision.velocity_percentile, 1.0)


class DedupeTests(unittest.TestCase):
    def test_shared_channel_link_is_not_duplicate(self):
        left = build_signature("Текст один про рынок https://t.me/mychannel")
        right = build_signature("Совсем другой текст о роботах https://t.me/mychannel")
        self.assertEqual(similarity(left, json.dumps(right.tokens), json.dumps(right.urls)), 0.0)

    def test_shared_post_link_is_duplicate(self):
        left = build_signature("Один https://t.me/mychannel/15")
        right = build_signature("Другой https://t.me/mychannel/15")
        self.assertEqual(similarity(left, json.dumps(right.tokens), json.dumps(right.urls)), 1.0)


class StorageCase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.storage = Storage(Path(self.temp.name) / "knowledge.db")

    async def asyncTearDown(self):
        self.storage.close()
        self.temp.cleanup()


class FakeClient:
    def __init__(self, fail_on: set[int] | None = None):
        self.fail_on = fail_on or set()
        self.calls = 0
        self.messages: list[str] = []

    async def send_message(self, target, message, **kwargs):
        self.calls += 1
        if self.calls in self.fail_on:
            raise ConnectionError("temporary")
        self.messages.append(message)


class DigestWindowTests(StorageCase):
    def record(self, when: datetime) -> OfferRecord:
        return OfferRecord(
            chat_id=-1, message_id=int(when.timestamp()), chat_title="Alpha Professor",
            sender_id=1, sender_name="A", message_date=when,
            text="Late evening research about web3 protocols", score=8,
            matched_rules="", compensation="", source_url="https://t.me/c/1/1",
        )

    async def test_item_after_previous_digest_reaches_next_digest(self):
        previous = datetime(2026, 10, 24, 18, 0, tzinfo=timezone.utc)
        self.storage.mark_digest_sent("2026-10-24", previous)
        self.storage.save(self.record(previous + timedelta(hours=2)), kind="knowledge")
        self.assertEqual(digest_window_start(self.storage, NOW), previous)
        self.assertIn("Late evening research", build_daily_digest(self.storage, NOW))

    async def test_long_downtime_is_capped(self):
        self.storage.mark_digest_sent("2026-10-01", datetime(2026, 10, 1, tzinfo=timezone.utc))
        self.assertEqual(digest_window_start(self.storage, NOW), NOW - timedelta(hours=72))

    async def test_first_digest_starts_at_moscow_midnight(self):
        self.assertEqual(
            digest_window_start(self.storage, NOW),
            datetime(2026, 10, 24, 21, 0, tzinfo=timezone.utc),
        )

    async def test_retry_does_not_repeat_delivered_parts(self):
        self.storage.mark_digest_part_sent("2026-10-25", 0)
        client = FakeClient()
        self.assertTrue(await send_daily_digest_once(client, self.storage, target="me", now_utc=NOW))
        self.assertEqual(client.messages, [])
        self.assertTrue(self.storage.digest_was_sent("2026-10-25"))

    async def test_digest_stays_within_telegram_limit_when_full(self):
        for index in range(5):
            record = self.record(datetime.now(timezone.utc))
            self.storage.save(
                OfferRecord(**{**record.__dict__, "message_id": index, "text": "web3 research " * 40}),
                kind="knowledge",
            )
        rendered = build_daily_digest(self.storage)
        self.assertLessEqual(len(rendered), 3900)
        self.assertIn("Фильтр за сутки", rendered)


class RefreshTests(StorageCase):
    def seed(self, *, ai_keep: bool = True) -> None:
        self.storage.save_twitter_analysis(
            canonical_url=URL, chat_id=-1, message_id=1, chat_title="X", decision="reject",
            ai_score=8, topic="AI", risk_flags=(),
            analysis={"ai_keep": ai_keep, "topic": "Claude AI agents", "hook": "h"},
        )

    def start(self) -> datetime:
        return datetime.now(timezone.utc) - timedelta(days=1)

    async def test_missing_metrics_after_collector_failure_are_recovered(self):
        self.seed()
        collector = SimpleNamespace(collect=AsyncMock(return_value=metrics()))
        count = await refresh_radar_metrics(self.storage, collector, self.start(), now_utc=NOW)
        self.assertEqual(count, 1)
        row = self.storage.digest_items(self.start())["twitter_candidates"][0]
        self.assertEqual(row["views"], 50_000)

    async def test_stale_metrics_are_updated_without_demotion(self):
        self.seed()
        first = decide_strength(
            content_score=9, metrics=metrics(200, 1), author_rows=[], cohort_rows=[],
            kept_today=0, mechanics_today=0, daily_limit=5, mechanics_limit=2,
        )
        self.assertTrue(first.keep)
        old = metrics(200, 1)
        stale = SimpleNamespace(**{**old.__dict__, "collected_at": NOW - timedelta(hours=5)})
        self.storage.save_twitter_strength(
            canonical_url=URL, author="author", chat_id=-1, message_id=1, chat_title="X",
            content_score=8, metrics=stale, strength=first, decision="keep", analysis={},
        )
        collector = SimpleNamespace(collect=AsyncMock(return_value=metrics()))
        await refresh_radar_metrics(self.storage, collector, self.start(), now_utc=NOW)
        row = self.storage.digest_items(self.start())["twitter_candidates"][0]
        self.assertEqual(row["views"], 50_000)
        self.assertEqual(row["strength_decision"], "keep")

    async def test_recent_snapshot_is_not_collected_again(self):
        self.seed()
        self.storage.save_twitter_strength(
            canonical_url=URL, author="author", chat_id=-1, message_id=1, chat_title="X",
            content_score=8, metrics=metrics(), strength=SimpleNamespace(
                kind="watch", reason_ru="r", baseline_scope="none", baseline_size=0,
                velocity_percentile=None, signal_percentile=None,
            ), decision="watch", analysis={},
        )
        collector = SimpleNamespace(collect=AsyncMock())
        await refresh_radar_metrics(self.storage, collector, self.start(), now_utc=NOW)
        collector.collect.assert_not_awaited()

    async def test_ai_rejected_posts_are_not_collected(self):
        self.seed(ai_keep=False)
        collector = SimpleNamespace(collect=AsyncMock())
        await refresh_radar_metrics(self.storage, collector, self.start(), now_utc=NOW)
        collector.collect.assert_not_awaited()

    async def test_collector_failure_does_not_block_digest(self):
        self.seed()
        collector = SimpleNamespace(collect=AsyncMock(side_effect=ConnectionError("down")))
        client = FakeClient()
        with patch("offer_parser.digest.asyncio.sleep", AsyncMock()):
            sent = await send_daily_digest_once(
                client, self.storage, target="me", now_utc=datetime.now(timezone.utc), collector=collector,
            )
        self.assertTrue(sent)
        self.assertEqual(len(client.messages), 1)


class OutboxTests(StorageCase):
    def enqueue(self, key: str = "k") -> None:
        self.storage.enqueue_message(dedupe_key=key, target="me", body=f"body {key}")

    async def asyncSetUp(self):
        await super().asyncSetUp()
        patcher = patch("offer_parser.outbox.asyncio.sleep", AsyncMock())
        patcher.start()
        self.addCleanup(patcher.stop)

    async def test_same_key_is_queued_once(self):
        self.enqueue()
        self.enqueue()
        client = FakeClient()
        self.assertEqual(await drain_outbox(client, self.storage), 1)
        self.assertEqual(client.messages, ["body k"])
        self.assertEqual(await drain_outbox(client, self.storage), 0)

    async def test_failed_message_stays_queued_and_others_continue(self):
        self.enqueue("a")
        self.enqueue("b")
        client = FakeClient(fail_on={1})
        self.assertEqual(await drain_outbox(client, self.storage), 1)
        self.assertEqual(client.messages, ["body b"])
        row = self.storage.connection.execute("SELECT * FROM outbox WHERE dedupe_key='a'").fetchone()
        self.assertEqual((row["status"], row["attempts"]), ("pending", 1))
        self.storage.connection.execute("UPDATE outbox SET next_attempt_at = datetime('now', '-1 second')")
        self.assertEqual(await drain_outbox(client, self.storage), 1)
        self.assertIn("body a", client.messages)

    async def test_flood_wait_defers_without_using_attempts(self):
        self.enqueue()
        client = SimpleNamespace(send_message=AsyncMock(side_effect=FloodWaitError(None, capture=120)))
        self.assertEqual(await drain_outbox(client, self.storage), 0)
        row = self.storage.connection.execute("SELECT * FROM outbox").fetchone()
        self.assertEqual(row["attempts"], 0)
        self.assertEqual(self.storage.outbox_due(), [])

    async def test_permanent_failure_stops_after_max_attempts(self):
        self.enqueue()
        self.storage.connection.execute("UPDATE outbox SET attempts = ?", (MAX_ATTEMPTS - 1,))
        await drain_outbox(SimpleNamespace(send_message=AsyncMock(side_effect=ValueError("bad peer"))), self.storage)
        row = self.storage.connection.execute("SELECT * FROM outbox").fetchone()
        self.assertEqual(row["status"], "failed")


def settings(**overrides) -> SimpleNamespace:
    return SimpleNamespace(**{
        "min_score": 5, "notify_target": "me", "knowledge_target": "bot", "knowledge_min_score": 4,
        "knowledge_balance_enabled": False, "knowledge_daily_limit": 15,
        "twitter_daily_limit": 5, "twitter_mechanics_daily_limit": 2, **overrides,
    })


def event(text: str, *, message_id: int = 1, webpage=None) -> SimpleNamespace:
    media = SimpleNamespace(webpage=webpage) if webpage else None
    return SimpleNamespace(
        raw_text=text, chat_id=-100, id=message_id, date=NOW,
        message=SimpleNamespace(raw_text=text, media=media),
        get_sender=AsyncMock(return_value=SimpleNamespace(id=7, first_name="A", last_name=None)),
        get_chat=AsyncMock(return_value=SimpleNamespace(username="chat")),
    )


WEBPAGE = SimpleNamespace(title="Post", description="Claude AI agents workflow")


class PipelineTests(StorageCase):
    def pipeline(self, *, ai=None, collector=None, **setting_overrides) -> MessagePipeline:
        matcher = SimpleNamespace(evaluate=lambda text: SimpleNamespace(score=8, matched=("a (+8)",), compensation=()))
        return MessagePipeline(
            settings=settings(**setting_overrides),
            selected_by_id={},
            matcher=matcher,
            storage=self.storage,
            knowledge_by_id={-100: "Chat"},
            knowledge_matcher=matcher,
            knowledge_storage=self.storage,
            twitter_priority_by_id={-100: TwitterPrioritySource(-100, "Chat", "P0", 2)},
            twitter_ai=ai,
            twitter_strength=collector,
        )

    def statuses(self) -> list[tuple[str, str]]:
        rows = self.storage.connection.execute("SELECT decision, reason FROM audit_decisions").fetchall()
        return [(row["decision"], row["reason"]) for row in rows]

    async def test_kept_post_is_queued_and_saved_before_delivery(self):
        ai = SimpleNamespace(analyze=AsyncMock(return_value=analysis(score=9)))
        collector = SimpleNamespace(collect=AsyncMock(return_value=metrics()))
        await self.pipeline(ai=ai, collector=collector).handle(event(URL, webpage=WEBPAGE))
        due = self.storage.outbox_due()
        self.assertEqual(len(due), 1)
        self.assertEqual(due[0]["target"], "bot")
        self.assertTrue(self.storage.twitter_url_seen(URL))

    async def test_ai_failure_is_audited_and_url_can_retry(self):
        ai = SimpleNamespace(analyze=AsyncMock(side_effect=RuntimeError("down")))
        await self.pipeline(ai=ai).handle(event(URL, webpage=WEBPAGE))
        self.assertEqual(self.statuses(), [("error", "ai_error:RuntimeError")])
        self.assertFalse(self.storage.twitter_url_seen(URL))

    async def test_collector_failure_keeps_candidate_for_refresh(self):
        ai = SimpleNamespace(analyze=AsyncMock(return_value=analysis()))
        collector = SimpleNamespace(collect=AsyncMock(side_effect=ConnectionError("down")))
        await self.pipeline(ai=ai, collector=collector).handle(event(URL, webpage=WEBPAGE))
        self.assertEqual(self.statuses(), [("rejected", "collector_error:ConnectionError")])
        since = datetime.now(timezone.utc) - timedelta(days=1)
        self.assertEqual(len(self.storage.radar_refresh_candidates(since, 10)), 1)

    async def test_off_niche_post_is_not_labelled_sport(self):
        ai = SimpleNamespace(analyze=AsyncMock(return_value=analysis(topic="comedy moment")))
        webpage = SimpleNamespace(title="Post", description="funny moment")
        await self.pipeline(ai=ai).handle(event(URL, webpage=webpage))
        self.assertEqual(self.statuses(), [("rejected", "no_niche")])

    async def test_reference_account_reaches_stored_niche(self):
        url = "https://x.com/framexin/status/5"
        ai = SimpleNamespace(analyze=AsyncMock(return_value=analysis(topic="example")))
        webpage = SimpleNamespace(title="Post", description="short text")
        await self.pipeline(ai=ai).handle(event(url, webpage=webpage))
        row = self.storage.connection.execute("SELECT analysis_json FROM twitter_intelligence").fetchone()
        self.assertEqual(json.loads(row[0])["niche"], "Девушки и lifestyle")

    async def test_post_without_preview_uses_collector_text_once(self):
        collected = metrics()
        collected.raw["text"] = "Claude AI agents workflow"
        ai = SimpleNamespace(analyze=AsyncMock(return_value=analysis(score=9)))
        collector = SimpleNamespace(collect=AsyncMock(return_value=collected))
        await self.pipeline(ai=ai, collector=collector).handle(event(URL))
        self.assertEqual(collector.collect.await_count, 1)
        preview: XPreview = ai.analyze.await_args.args[0]
        self.assertEqual(preview.description, "Claude AI agents workflow")

    async def test_same_url_in_two_concurrent_messages_is_analysed_once(self):
        import asyncio

        release = asyncio.Event()

        async def slow(*args):
            await release.wait()
            return analysis(keep=False)

        ai = SimpleNamespace(analyze=AsyncMock(side_effect=slow))
        pipeline = self.pipeline(ai=ai)
        first = asyncio.create_task(pipeline.handle(event(URL, message_id=1, webpage=WEBPAGE)))
        await asyncio.sleep(0)
        await pipeline.handle(event(URL, message_id=2, webpage=WEBPAGE))
        release.set()
        await first
        self.assertEqual(ai.analyze.await_count, 1)
        self.assertIn(("duplicate", "canonical_url"), self.statuses())

    async def test_knowledge_is_saved_and_queued(self):
        pipeline = self.pipeline(ai=None)
        await pipeline.handle(event("Подробный разбор протокола и его токеномики"))
        self.assertEqual(len(self.storage.outbox_due()), 1)

    async def test_unexpected_error_is_audited_not_raised(self):
        pipeline = self.pipeline(ai=None)
        broken = event("Подробный разбор протокола и его токеномики")
        broken.get_sender = AsyncMock(side_effect=ValueError("boom"))
        await pipeline.handle(broken)
        self.assertEqual(self.statuses(), [("error", "ValueError")])


class AIBudgetTests(unittest.TestCase):
    def block(self, calls, hour, priority=False):
        return ai_budget_block(
            calls_today=calls, budget=680,
            now_utc=datetime(2026, 11, 1, hour, 0, tzinfo=timezone.utc), priority_source=priority,
        )

    def test_quota_is_spread_across_the_day(self):
        self.assertIsNone(self.block(0, 0))
        self.assertEqual(self.block(100, 1), "ai_pacing")
        self.assertIsNone(self.block(300, 12))
        self.assertEqual(self.block(400, 12), "ai_pacing")

    def test_priority_source_ignores_pacing_but_not_the_cap(self):
        self.assertIsNone(self.block(400, 12, priority=True))
        self.assertEqual(self.block(680, 23, priority=True), "ai_budget")

    def test_rate_limit_is_not_retried(self):
        with tempfile.TemporaryDirectory() as directory:
            env = Path(directory) / ".env"
            env.write_text("OPENROUTER_API_KEY=test\n", encoding="utf-8")
            client = TwitterAIClient(env, 7)
            error = urllib.error.HTTPError("https://x", 429, "Too Many", {}, None)
            with patch("offer_parser.twitter_ai.urllib.request.urlopen", side_effect=error) as call:
                with self.assertRaises(TwitterAIRateLimited):
                    client._request(XPreview(URL, "t", "d"))
            self.assertEqual(call.call_count, 1)


class AIFilterTests(StorageCase):
    def pipeline(self, ai, chat_title: str = "Chat") -> MessagePipeline:
        matcher = SimpleNamespace(evaluate=lambda text: SimpleNamespace(score=8, matched=(), compensation=()))
        return MessagePipeline(
            settings=settings(),
            selected_by_id={},
            matcher=matcher,
            storage=self.storage,
            knowledge_by_id={-100: chat_title},
            knowledge_matcher=matcher,
            knowledge_storage=self.storage,
            twitter_priority_by_id={-100: TwitterPrioritySource(-100, chat_title, "P0", 2)},
            twitter_ai=ai,
            twitter_strength=None,
        )

    def statuses(self) -> list[tuple[str, str]]:
        rows = self.storage.connection.execute("SELECT decision, reason FROM audit_decisions").fetchall()
        return [(row["decision"], row["reason"]) for row in rows]

    def seed_author(self, author: str, amount: int) -> None:
        for index in range(amount):
            self.storage.save_twitter_analysis(
                canonical_url=f"https://x.com/{author}/status/{100 + index}", chat_id=-1,
                message_id=100 + index, chat_title="X", decision="reject", ai_score=3,
                topic="t", risk_flags=(), analysis={},
            )

    def ai(self) -> SimpleNamespace:
        return SimpleNamespace(analyze=AsyncMock(return_value=analysis(keep=False)))

    async def test_engagement_noise_spends_no_quota_and_url_stays_retryable(self):
        ai = self.ai()
        await self.pipeline(ai).handle(event(f"лайк комм {URL}", webpage=WEBPAGE))
        ai.analyze.assert_not_awaited()
        self.assertEqual(self.statuses(), [("ignored", "engagement_noise")])
        self.assertFalse(self.storage.twitter_url_seen(URL))

    async def test_third_post_of_author_is_skipped(self):
        self.seed_author("Author", 2)
        ai = self.ai()
        await self.pipeline(ai).handle(event(URL, webpage=WEBPAGE))
        ai.analyze.assert_not_awaited()
        self.assertEqual(self.statuses(), [("ignored", "author_daily_limit")])

    async def test_second_post_of_author_is_analysed(self):
        self.seed_author("author", 1)
        ai = self.ai()
        await self.pipeline(ai).handle(event(URL, webpage=WEBPAGE))
        ai.analyze.assert_awaited_once()

    async def test_reference_account_has_no_author_limit(self):
        self.seed_author("framexin", 5)
        ai = self.ai()
        await self.pipeline(ai).handle(event("https://x.com/framexin/status/5", webpage=WEBPAGE))
        ai.analyze.assert_awaited_once()

    async def test_priority_chat_has_no_filters(self):
        self.seed_author("author", 5)
        ai = self.ai()
        await self.pipeline(ai, "X FAMILY").handle(event(f"лайк комм {URL}", webpage=WEBPAGE))
        ai.analyze.assert_awaited_once()

    async def test_rate_limit_pauses_further_requests(self):
        ai = SimpleNamespace(analyze=AsyncMock(side_effect=TwitterAIRateLimited("429")))
        pipeline = self.pipeline(ai)
        await pipeline.handle(event(URL, message_id=1, webpage=WEBPAGE))
        await pipeline.handle(event("https://x.com/other/status/2", message_id=2, webpage=WEBPAGE))
        self.assertEqual(ai.analyze.await_count, 1)
        self.assertIn(("error", "ai_rate_limited"), self.statuses())
        self.assertIn(("ignored", "ai_cooldown"), self.statuses())
        self.assertFalse(self.storage.twitter_url_seen(URL))


class StatusUrlTests(unittest.TestCase):
    def test_first_status_url_skips_profiles(self):
        self.assertEqual(
            first_status_url("see https://x.com/a and https://twitter.com/b/status/9?s=20"),
            "https://x.com/b/status/9",
        )


if __name__ == "__main__":
    unittest.main()
