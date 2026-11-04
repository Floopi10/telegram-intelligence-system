from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tags import clean_tag, clean_tags  # noqa: E402
from vault import Vault  # noqa: E402


class TagCleanupTest(unittest.TestCase):
    def test_drops_the_broken_tags_from_a_real_card(self):
        tags = ["harness-архитектура", "grokbot", "агентная_автоматизация",
                "skills-based_automation", "аттитюль_вместо_задачи"]
        self.assertEqual(clean_tags(tags), ["grokbot", "агентная_автоматизация"])

    def test_normalizes_case_hash_and_spaces(self):
        self.assertEqual(clean_tag("#AI Agents"), "ai_agents")

    def test_drops_long_and_odd_tags(self):
        self.assertEqual(clean_tag("a" * 25), "")
        self.assertEqual(clean_tag("c++"), "")
        self.assertEqual(clean_tag("   "), "")

    def test_keeps_system_tags_and_dedupes(self):
        self.assertEqual(
            clean_tags(["telegram", "parser"], ["Telegram", "telegram-source"]),
            ["telegram", "parser", "telegram-source"],
        )

    def test_vault_merge_uses_the_cleanup(self):
        self.assertEqual(Vault._merge_tags(["ai"], ["harness-архитектура", "AI"]), ["ai"])


if __name__ == "__main__":
    unittest.main()
