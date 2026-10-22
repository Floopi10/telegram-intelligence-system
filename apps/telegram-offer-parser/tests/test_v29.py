import json
import sqlite3
import sys
import tempfile
import types
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "payload"
sys.path.insert(0, str(ROOT))

dedupe = types.ModuleType("offer_parser.dedupe")
dedupe.build_signature = lambda text: None
dedupe.similarity = lambda *args: 0.0
sys.modules.setdefault("offer_parser.dedupe", dedupe)

from offer_parser.digest import build_daily_digest, select_twitter_radar
from offer_parser.digest_guard import is_knowledge_noise, split_digest_rows
from offer_parser.storage import Storage
from offer_parser.vacancy_filter import evaluate_vacancy


def candidate(index: int, *, topic: str = "AI agents", hook: str = "Useful AI workflow") -> dict:
    return {
        "canonical_url": f"https://x.com/user/status/{index}",
        "content_score": 7,
        "views": None,
        "velocity_percentile": None,
        "signal_percentile": None,
        "signal_rate": None,
        "analysis_json": json.dumps({
            "ai_keep": False,
            "score": 7,
            "topic": topic,
            "hook": hook,
            "analysis_ru": "Практическая механика",
            "niche": "AI",
        }),
    }


class QualityV29Tests(unittest.TestCase):
    def test_public_sector_sales_vacancy_is_rejected(self):
        text = (
            "Cohere is hiring a remote VP of Sales Public Sector. "
            "Business development, paid salary $560,000, remote global."
        )
        decision = evaluate_vacancy(text)
        self.assertFalse(decision.accepted)
        self.assertEqual(decision.reason, "excluded_role")

    def test_knowledge_spam_and_fragment_are_rejected(self):
        spam = "12 AMBASSADOR PROGRAM Making $200 to $1000 monthly. Join our community."
        fragment = "alright thanks Bella, can you please explain because I am confused"
        useful = "Meta AI product reached 10.2 million downloads and grew faster than the launch cohort."
        self.assertTrue(is_knowledge_noise(spam))
        self.assertTrue(is_knowledge_noise(fragment))
        self.assertFalse(is_knowledge_noise(useful))
        _, knowledge, rejected = split_digest_rows([
            {"text": spam, "item_kind": "knowledge"},
            {"text": useful, "item_kind": "knowledge"},
        ])
        self.assertEqual([row["text"] for row in knowledge], [useful])
        self.assertEqual(rejected, [])

    def test_radar_uses_rejected_ai_pool_but_blocks_noise(self):
        rows = [candidate(index) for index in range(35)]
        rows.append(candidate(100, topic="Bayern match", hook="3 xG football prediction"))
        rows.append(candidate(101, topic="AI", hook="We are hiring, apply now"))
        selected = select_twitter_radar(rows, 50)
        self.assertEqual(len(selected), 35)
        self.assertTrue(all(item["analysis"]["ai_keep"] is False for item in selected))
        urls = {item["canonical_url"] for item in selected}
        self.assertNotIn("https://x.com/user/status/100", urls)
        self.assertNotIn("https://x.com/user/status/101", urls)

    def test_storage_candidate_pool_reads_twitter_intelligence(self):
        with tempfile.TemporaryDirectory() as directory:
            storage = Storage(Path(directory) / "knowledge.db")
            storage.connection.execute(
                """INSERT INTO twitter_intelligence
                   (canonical_url,chat_id,message_id,chat_title,decision,ai_score,topic,risk_flags,analysis_json,created_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?)""",
                (
                    "https://x.com/a/status/1", -1, 1, "Chat", "reject", 7,
                    "AI", "", candidate(1)["analysis_json"], "2026-10-22 10:00:00",
                ),
            )
            storage.connection.commit()
            data = storage.digest_items(datetime(2026, 10, 22, tzinfo=timezone.utc))
            self.assertEqual(len(data["twitter_candidates"]), 1)
            self.assertEqual(data["twitter_candidates"][0]["content_score"], 7)
            storage.close()


if __name__ == "__main__":
    unittest.main()
