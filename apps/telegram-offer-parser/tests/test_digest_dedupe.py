import csv
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from offer_parser.dedupe import build_signature, similarity
from offer_parser.digest import build_daily_digest
from offer_parser.sources import add_knowledge_source
from offer_parser.storage import OfferRecord, Storage


class DigestDedupeTest(unittest.TestCase):
    def record(self, message_id, text, score=8):
        return OfferRecord(
            chat_id=-1001, message_id=message_id, chat_title="Alpha Professor",
            sender_id=1, sender_name="Alice", message_date=datetime.now(timezone.utc),
            text=text, score=score, matched_rules="web3", compensation="",
            source_url=f"https://t.me/c/1/{message_id}",
        )

    def test_exact_and_semantic_duplicates(self):
        with tempfile.TemporaryDirectory() as directory:
            storage = Storage(Path(directory) / "knowledge.db")
            first = self.record(1, "Paid remote global Business Development Manager for Web3 protocol with partnerships and outreach experience")
            second = self.record(2, "Web3 protocol seeks a paid global remote Business Development Manager with outreach and partnerships experience")
            self.assertEqual(storage.save_unique(first, "vacancy"), (True, "accepted"))
            saved, reason = storage.save_unique(second, "vacancy", threshold=0.60)
            self.assertFalse(saved)
            self.assertEqual(reason, "semantic_duplicate")
            self.assertEqual(storage.save_unique(self.record(3, first.text), "vacancy"), (False, "exact_duplicate"))
            storage.close()

    def test_shared_url_is_duplicate(self):
        left = build_signature("Useful analysis https://x.com/a/status/1")
        right = build_signature("Completely different words https://x.com/a/status/1")
        import json
        self.assertEqual(similarity(left, json.dumps(right.tokens), json.dumps(right.urls)), 1.0)

    def test_digest_contains_three_sections_and_saved_messages_content(self):
        with tempfile.TemporaryDirectory() as directory:
            storage = Storage(Path(directory) / "knowledge.db")
            storage.save(self.record(1, "Paid remote global BD role", 9), kind="vacancy")
            storage.save(self.record(2, "Web3 protocol analysis", 7), kind="knowledge")
            storage.save_twitter_analysis(
                canonical_url="https://x.com/a/status/2", chat_id=-1, message_id=3,
                chat_title="X FAMILY", decision="keep", ai_score=9, topic="growth",
                risk_flags=(), analysis={
                    "topic": "viral content marketing",
                    "hook": "Strong contrast hook for audience growth",
                },
            )
            now = datetime.now(timezone.utc)
            storage.save_twitter_strength(
                canonical_url="https://x.com/a/status/2",
                author="a",
                chat_id=-1,
                message_id=3,
                chat_title="X FAMILY",
                content_score=9,
                metrics=SimpleNamespace(
                    published_at=now,
                    collected_at=now,
                    age_bucket="under_6h",
                    age_hours=1.0,
                    views=100_000,
                    likes=1000,
                    reposts=200,
                    replies=50,
                    bookmarks=100,
                    views_per_hour=100_000.0,
                    signal_rate=0.03,
                ),
                strength=SimpleNamespace(
                    kind="подтверждённый победитель",
                    reason_ru="absolute viral",
                    baseline_scope="cohort",
                    baseline_size=20,
                    velocity_percentile=0.99,
                    signal_percentile=0.95,
                ),
                decision="keep",
                analysis={"hook": "Strong contrast hook for audience growth"},
            )
            message = build_daily_digest(storage)
            self.assertIn("💼 Вакансии", message)
            self.assertIn("🐦 Twitter-находки", message)
            self.assertIn("🧠 Web3 и knowledge", message)
            self.assertIn("https://x.com/a/status/2", message)
            self.assertLessEqual(len(message), 3900)
            self.assertFalse(storage.digest_was_sent("2099-01-01"))
            storage.mark_digest_sent("2099-01-01")
            self.assertTrue(storage.digest_was_sent("2099-01-01"))
            storage.close()

    def test_add_alpha_professor_to_knowledge_csv(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            chats = root / "chats.csv"
            knowledge = root / "knowledge_chats.csv"
            with chats.open("w", encoding="utf-8-sig", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=["enabled", "chat_id", "title", "username", "kind"])
                writer.writeheader()
                writer.writerow({"enabled": "0", "chat_id": "-10055", "title": "Alpha Professor", "username": "", "kind": "group"})
            chat_id, title = add_knowledge_source(chats, knowledge, "alpha professor")
            self.assertEqual(chat_id, -10055)
            self.assertEqual(title, "Alpha Professor")
            with knowledge.open(encoding="utf-8-sig", newline="") as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual(rows[0]["enabled"], "1")


if __name__ == "__main__":
    unittest.main()
