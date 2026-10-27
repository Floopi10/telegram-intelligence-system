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
from offer_parser.digest_guard import (
    is_digest_worthy_knowledge,
    is_knowledge_noise,
    split_digest_rows,
)
from offer_parser.storage import Storage
from offer_parser.vacancy_filter import evaluate_vacancy, is_vacancy_text


def candidate(index: int, *, topic: str = "AI agents", hook: str = "Useful AI workflow") -> dict:
    return {
        "canonical_url": f"https://x.com/user/status/{index}",
        "content_score": 7,
        "views": 1000 + index,
        "likes": 30,
        "reposts": 5,
        "bookmarks": 8,
        "views_per_hour": 250,
        "velocity_percentile": 0.90,
        "signal_percentile": 0.80,
        "signal_rate": 0.05,
        "strength_decision": "keep",
        "strength_kind": "подтверждённый победитель",
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

    def test_campaign_welcome_and_personal_banter_are_rejected(self):
        campaign = "ULANBIT COMMUNITY GROWTH CAMPAIGN. Total Rewards: 300 USDT. Winners announced soon."
        welcome = "Welcome @Vemalana to Korvex Creator Hub where opportunities become partnerships."
        banter = "БРО, ТЫ САМ ПОПРОСИЛ. Ты настолько ебанулся на маркетинге, что оптимизируешь всё подряд."
        self.assertTrue(is_knowledge_noise(campaign))
        self.assertTrue(is_knowledge_noise(welcome))
        self.assertTrue(is_knowledge_noise(banter))

    def test_current_digest_promos_and_solicitations_are_rejected(self):
        trivia = "🧠 Trivia— Crypto Scams & Transaction Security. Think you can spot a crypto scam?"
        solicitation = (
            "Hello legends! Looking for projects interested in cross-partnerships. "
            "If any project BD is here, feel free to DM me."
        )
        campaign = (
            "🧪 TestDrive × Sidefleet. For this campaign: try Sidefleet and "
            "quote-post Mark's launch tweet."
        )
        self.assertTrue(is_knowledge_noise(trivia))
        self.assertTrue(is_knowledge_noise(solicitation))
        self.assertTrue(is_knowledge_noise(campaign))

    def test_low_score_digest_item_requires_trusted_source(self):
        generic = {
            "text": "Several LLMs on one machine with Claude Code.",
            "score": 2,
            "chat_title": "ClawEng",
            "item_kind": "knowledge",
        }
        alpha = {
            "text": "Early BTC mint https://x.com/bitcointapes",
            "score": 1,
            "chat_title": "Alpha Professor",
            "item_kind": "knowledge",
        }
        self.assertFalse(is_digest_worthy_knowledge(generic))
        self.assertTrue(is_digest_worthy_knowledge(alpha))

    def test_unpaid_moderator_vacancy_never_leaks_into_knowledge(self):
        text = (
            "TouchGrass Community Moderator Apply: DM Rachit with your experience. "
            "TouchGrass is looking for 2–3 Community Moderators to join the team."
        )
        self.assertTrue(is_vacancy_text(text))
        vacancies, knowledge, rejected = split_digest_rows([
            {"text": text, "score": 4, "chat_title": "Build3", "item_kind": "knowledge"},
        ])
        self.assertEqual(vacancies, [])
        self.assertEqual(knowledge, [])
        self.assertEqual(len(rejected), 1)

    def test_radar_uses_rejected_ai_pool_but_blocks_noise(self):
        rows = [candidate(index) for index in range(35)]
        rows.append(candidate(100, topic="Bayern match", hook="3 xG football prediction"))
        rows.append(candidate(101, topic="AI", hook="We are hiring, apply now"))
        selected = select_twitter_radar(rows, 50)
        self.assertEqual(len(selected), 9)
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
