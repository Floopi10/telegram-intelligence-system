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


if __name__ == "__main__":
    unittest.main()
