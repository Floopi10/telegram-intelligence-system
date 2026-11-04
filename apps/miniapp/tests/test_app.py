from __future__ import annotations

import hashlib
import hmac
import json
import os
import sys
import time
import unittest
from pathlib import Path
from urllib.parse import urlencode

ROOT = Path(__file__).resolve().parents[3]
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "test-token")
os.environ.setdefault("PARSER_PROJECT", str(ROOT / "apps/telegram-offer-parser"))
os.environ["ALLOWED_USER_IDS"] = "42"
sys.path.insert(0, str(ROOT / "apps/miniapp"))

import app  # noqa: E402


def signed(user_id: int, auth_date: int | None = None) -> str:
    values = {"auth_date": str(auth_date or int(time.time())), "user": json.dumps({"id": user_id})}
    data_check = "\n".join(f"{key}={values[key]}" for key in sorted(values))
    secret = hmac.new(b"WebAppData", b"test-token", hashlib.sha256).digest()
    values["hash"] = hmac.new(secret, data_check.encode(), hashlib.sha256).hexdigest()
    return urlencode(values)


def twitter_row(analysis: dict, url: str = "https://x.com/framexin/status/1") -> dict:
    return {
        "canonical_url": url, "author": "framexin", "chat_title": "CelebeX", "content_score": 9,
        "collected_at": "2026-11-01T10:00:00+00:00", "published_at": "2026-11-01T08:00:00+00:00",
        "views": 1200, "likes": 80, "reposts": 9, "replies": 3, "bookmarks": 20,
        "views_per_hour": 600.0, "strength_kind": "ранний кандидат",
        "strength_reason": "скорость в верхних 10%", "analysis_json": json.dumps(analysis),
    }


class AuthTests(unittest.TestCase):
    def test_valid_signature_for_allowed_user(self):
        self.assertEqual(app.validate_init_data(signed(42))["id"], 42)

    def test_other_user_and_tampering_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "forbidden_user"):
            app.validate_init_data(signed(7))
        with self.assertRaisesRegex(ValueError, "invalid_hash"):
            app.validate_init_data(signed(42).replace("hash=", "hash=0"))

    def test_expired_signature_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "expired"):
            app.validate_init_data(signed(42, auth_date=int(time.time()) - 10 * 86_400))


class TwitterItemTests(unittest.TestCase):
    def test_uses_stored_niche_hook_and_review(self):
        item = app._twitter_item(twitter_row({
            "niche": "Роботы", "hook": "Robot bee with Claude", "topic": "robots",
            "analysis_ru": "Разбор поста", "key_points": ["Первое", " ", "Второе"],
        }))
        self.assertEqual(item["niche"], "Роботы")
        self.assertEqual(item["title"], "Robot bee with Claude")
        self.assertEqual(item["full_text"], "Разбор поста")
        self.assertEqual(item["points"], ["Первое", "Второе"])
        self.assertEqual(item["metrics"]["views"], 1200)

    def test_rejected_niche_label_falls_back_to_classifier(self):
        item = app._twitter_item(twitter_row({"niche": "Вне ниш — отклонено", "hook": "example post"}))
        self.assertEqual(item["niche"], "Девушки и lifestyle")

    def test_malformed_analysis_does_not_crash(self):
        row = twitter_row({})
        row["analysis_json"] = "{broken"
        item = app._twitter_item(row)
        self.assertEqual(item["points"], [])
        self.assertTrue(item["title"])


class HiddenTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        import tempfile
        from datetime import datetime, timezone

        from aiohttp.test_utils import TestClient, TestServer

        sys.path.insert(0, os.environ["PARSER_PROJECT"])
        from offer_parser.storage import OfferRecord, Storage

        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.patches = [("STATE_DB", root / "state" / "state.db"), ("DB_PATH", root / "knowledge.db")]
        self.saved = {name: getattr(app, name) for name, _ in self.patches}
        for name, value in self.patches:
            setattr(app, name, value)
        storage = Storage(app.DB_PATH)
        text = (
            "Разбор токеномики нового протокола: команда держит 38 процентов, "
            "ончейн-данные показывают вывод ликвидности фондами на биржи."
        )
        for message_id in (1, 2):
            storage.save(OfferRecord(
                chat_id=-100, message_id=message_id, chat_title="Alpha", sender_id=1, sender_name="A",
                message_date=datetime.now(timezone.utc), text=text + f" #{message_id}", score=9,
                matched_rules="", compensation="", source_url=None,
            ), kind="knowledge")
        storage.close()
        self.client = TestClient(TestServer(app.create_app()))
        await self.client.start_server()

    async def asyncTearDown(self):
        await self.client.close()
        for name, value in self.saved.items():
            setattr(app, name, value)
        self.temp.cleanup()

    async def post(self, body, user_id: int = 42):
        return await self.client.post("/api/hide", json=body, headers={"X-Telegram-Init-Data": signed(user_id)})

    def knowledge_ids(self) -> list[str]:
        return [item["id"] for item in app.load_dashboard()["knowledge"]]

    async def test_hide_and_restore(self):
        self.assertEqual(sorted(self.knowledge_ids()), ["tg:-100:1", "tg:-100:2"])
        response = await self.post({"id": "tg:-100:1"})
        self.assertEqual(response.status, 200)
        self.assertEqual(self.knowledge_ids(), ["tg:-100:2"])
        self.assertEqual(app.load_dashboard()["hidden_count"], 1)
        await self.post({"id": "tg:-100:1", "hidden": False})
        self.assertEqual(len(self.knowledge_ids()), 2)

    async def test_rejects_strangers_and_bad_ids(self):
        self.assertEqual((await self.post({"id": "tg:-100:1"}, user_id=7)).status, 401)
        self.assertEqual((await self.post({"id": "../../etc"})).status, 400)
        self.assertEqual((await self.post(["tg:-100:1"])).status, 400)
        self.assertEqual(len(self.knowledge_ids()), 2)

    def test_radar_uses_the_digest_metric_gate(self):
        from datetime import datetime, timezone
        from types import SimpleNamespace

        from offer_parser.storage import Storage

        storage = Storage(app.DB_PATH)
        now = datetime.now(timezone.utc)
        for author, views, likes, bookmarks, kind in (
            ("tiny", 9, 1, 1_000_000_000, "полезная механика"),
            ("viral", 20_000, 900, 300, "подтверждённый победитель"),
        ):
            storage.save_twitter_strength(
                canonical_url=f"https://x.com/{author}/status/1", author=author, chat_id=-1, message_id=1,
                chat_title="X", content_score=9,
                metrics=SimpleNamespace(published_at=now, collected_at=now, age_bucket="6_to_30h", age_hours=10,
                    views=views, likes=likes, reposts=5, replies=2, bookmarks=bookmarks,
                    views_per_hour=views / 10, signal_rate=0.05),
                strength=SimpleNamespace(kind=kind, reason_ru="r", baseline_scope="cohort", baseline_size=30,
                    velocity_percentile=0.9, signal_percentile=0.9),
                decision="keep", analysis={"hook": author, "niche": "AI"},
            )
        storage.close()
        twitter = app.load_dashboard()["twitter"]
        self.assertEqual([(item["author"], item["tier"]) for item in twitter], [("viral", "winner")])

    def test_x_ids_are_accepted(self):
        self.assertTrue(app.ITEM_ID_RE.fullmatch("x:https://x.com/Some_User/status/2116898898019020879"))
        self.assertFalse(app.ITEM_ID_RE.fullmatch("x:https://evil.com/a/status/1"))


if __name__ == "__main__":
    unittest.main()
