from __future__ import annotations

import importlib.util
import sqlite3
import sys
import tempfile
import unittest
import urllib.error
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

TOOL = Path(__file__).resolve().parent.parent / "tools" / "x_metrics_enricher.py"
spec = importlib.util.spec_from_file_location("x_metrics_enricher", TOOL)
enricher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(enricher)

NOW = datetime(2026, 11, 5, 12, 0, tzinfo=timezone.utc)
FRESH_URL = "https://x.com/shirosignal/status/2118034011188514880"  # 2026-11-04 17:33 UTC


def snapshot(collected: datetime, published: datetime | None = None) -> dict:
    return {"collected_at": collected.isoformat(), "published_at": published.isoformat() if published else None}


class StatusTimeTest(unittest.TestCase):
    def test_decodes_the_post_time_from_the_status_id(self):
        posted = enricher.status_time(FRESH_URL)
        self.assertEqual((posted.year, posted.month, posted.day, posted.hour), (2026, 11, 4, 17))

    def test_unknown_for_tiny_or_missing_ids(self):
        self.assertIsNone(enricher.status_time("https://x.com/a/status/12"))
        self.assertIsNone(enricher.status_time("https://example.com"))


class DueTest(unittest.TestCase):
    def test_fresh_post_every_two_hours(self):
        posted = NOW - timedelta(hours=20)
        self.assertTrue(enricher.is_due(None, NOW, posted))
        self.assertFalse(enricher.is_due(snapshot(NOW - timedelta(hours=1), posted), NOW, posted))
        self.assertTrue(enricher.is_due(snapshot(NOW - timedelta(hours=3), posted), NOW, posted))

    def test_week_old_post_every_twelve_hours(self):
        posted = NOW - timedelta(days=5)
        self.assertFalse(enricher.is_due(snapshot(NOW - timedelta(hours=6), posted), NOW, posted))
        self.assertTrue(enricher.is_due(snapshot(NOW - timedelta(hours=13), posted), NOW, posted))

    def test_old_post_gets_one_snapshot_then_never(self):
        posted = NOW - timedelta(days=40)
        self.assertTrue(enricher.is_due(None, NOW, posted))
        self.assertFalse(enricher.is_due(snapshot(NOW - timedelta(days=30), posted), NOW, posted))

    def test_snapshot_publish_time_wins_over_the_guess(self):
        old = NOW - timedelta(days=40)
        self.assertFalse(enricher.is_due(snapshot(NOW - timedelta(days=9), old), NOW, NOW))

    def test_fresh_posts_sort_before_old_ones(self):
        keys = [
            enricher.priority(NOW - timedelta(days=30), NOW, 9e9),
            enricher.priority(NOW - timedelta(hours=30), NOW, 1),
            enricher.priority(None, NOW, 5),
            enricher.priority(NOW - timedelta(hours=2), NOW, 0),
        ]
        ordered = sorted(range(4), key=lambda i: keys[i])
        self.assertEqual(ordered[:2], [3, 1])


class HealthTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = enricher.open_db(Path(self.tmp.name) / "m.db")

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def health(self):
        self.db.row_factory = sqlite3.Row
        return self.db.execute("SELECT * FROM collector_health WHERE id = 1").fetchone()

    def test_alerts_once_after_three_failures_and_once_on_recovery(self):
        target = ("token", "1")
        with patch.object(enricher, "send_alert", return_value=True) as send:
            for _ in range(5):
                enricher.record_health(self.db, target, False, "TimeoutError: timed out")
            self.assertEqual(send.call_count, 1)
            self.assertIn("3 сбора подряд", send.call_args.args[1])
            self.assertEqual(self.health()["consecutive_failures"], 5)
            enricher.record_health(self.db, target, True)
            self.assertEqual(send.call_count, 2)
            self.assertEqual(send.call_args.args[1], enricher.RECOVERY_TEXT)
            self.assertEqual((self.health()["consecutive_failures"], self.health()["alerted"]), (0, 0))

    def test_retries_the_alert_while_telegram_is_unreachable(self):
        with patch.object(enricher, "send_alert", return_value=False) as send:
            for _ in range(4):
                enricher.record_health(self.db, ("t", "1"), False, "x")
        self.assertEqual(send.call_count, 2)
        self.assertEqual(self.health()["alerted"], 0)

    def test_success_without_alert_sends_nothing(self):
        with patch.object(enricher, "send_alert") as send:
            enricher.record_health(self.db, ("t", "1"), False, "x")
            enricher.record_health(self.db, ("t", "1"), True)
        send.assert_not_called()

    def test_collector_failures_are_told_apart_from_bad_input(self):
        bad_gateway = urllib.error.HTTPError("u", 504, "timeout", {}, None)
        bad_request = urllib.error.HTTPError("u", 400, "bad", {}, None)
        self.assertTrue(enricher.is_collector_failure(bad_gateway))
        self.assertFalse(enricher.is_collector_failure(bad_request))
        self.assertTrue(enricher.is_collector_failure(urllib.error.URLError("refused")))
        self.assertTrue(enricher.is_collector_failure(TimeoutError()))
        self.assertFalse(enricher.is_collector_failure(PermissionError()))


class AlertTargetTest(unittest.TestCase):
    def test_reads_token_and_first_user(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = Path(tmp) / ".env"
            env.write_text("TELEGRAM_BOT_TOKEN=abc\nALLOWED_USER_IDS=11, 22\n", encoding="utf-8")
            self.assertEqual(enricher.load_alert_target(env), ("abc", "11"))
            self.assertIsNone(enricher.load_alert_target(Path(tmp) / "missing.env"))
        self.assertIsNone(enricher.load_alert_target(None))


class RunTest(unittest.TestCase):
    def test_stops_after_the_first_collector_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            vault = root / "vault"
            vault.mkdir()
            for index in range(3):
                (vault / f"n{index}.md").write_text(f"# n\n{FRESH_URL[:-1]}{index}\n", encoding="utf-8")
            config = root / "x.env"
            config.write_text("X_COLLECTOR_URL=http://127.0.0.1:9\nX_COLLECTOR_TOKEN=t\n", encoding="utf-8")
            argv = ["x", "--vault", str(vault), "--db", str(root / "m.db"), "--config", str(config), "--limit", "3"]
            failure = urllib.error.HTTPError("u", 504, "X page timed out", {}, None)
            with patch.object(sys, "argv", argv), \
                    patch.object(enricher, "request_metrics", side_effect=failure) as collect, \
                    patch.object(enricher, "send_alert", return_value=False):
                self.assertEqual(enricher.main(), 1)
            self.assertEqual(collect.call_count, 1)

    def test_updates_the_note_and_resets_failures(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            vault = root / "vault"
            vault.mkdir()
            note = vault / "n.md"
            note.write_text(f"# n\n{FRESH_URL}\n", encoding="utf-8")
            config = root / "x.env"
            config.write_text("X_COLLECTOR_URL=http://127.0.0.1:9\nX_COLLECTOR_TOKEN=t\n", encoding="utf-8")
            db_path = root / "m.db"
            db = enricher.open_db(db_path)
            db.execute("UPDATE collector_health SET consecutive_failures = 2")
            db.commit()
            db.close()
            raw = {"views": "864", "likes": "32", "reposts": "3", "replies": "1", "bookmarks": "1 Bookmark",
                   "collected_at": NOW.isoformat(), "published_at": "2026-11-04T17:33:54Z"}
            argv = ["x", "--vault", str(vault), "--db", str(db_path), "--config", str(config)]
            with patch.object(sys, "argv", argv), patch.object(enricher, "request_metrics", return_value=raw):
                self.assertEqual(enricher.main(), 0)
            text = note.read_text(encoding="utf-8")
            self.assertIn("Просмотры: **864**", text)
            self.assertIn("Закладки: **1**", text)
            db = sqlite3.connect(db_path)
            self.assertEqual(db.execute("SELECT consecutive_failures FROM collector_health").fetchone()[0], 0)
            db.close()


if __name__ == "__main__":
    unittest.main()
