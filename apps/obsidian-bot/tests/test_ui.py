from __future__ import annotations

import os
import sys
import tempfile
import time
import unittest
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ui import (  # noqa: E402
    collect_note_stats,
    help_text,
    progress_text,
    saved_card,
    stats_text,
)


class SavedCardTests(unittest.TestCase):
    def card(self, **overrides) -> str:
        values = dict(
            title="Hook <test> (Хук)", summary="Кратко & ясно", category="AI",
            tags=["ai", "telegram", "ai", "long tag"], media_count=2,
            note_path=Path("/vault/Telegram/AI/Hook.md"), inbox_path=Path("/vault/Telegram"),
            route="ai",
        )
        values.update(overrides)
        return saved_card(**values)

    def test_escapes_html_and_dedupes_tags(self):
        card = self.card()
        self.assertIn("Hook &lt;test&gt;", card)
        self.assertIn("Кратко &amp; ясно", card)
        self.assertEqual(card.count("#ai"), 1)
        self.assertIn("#long_tag", card)

    def test_shows_path_relative_to_inbox(self):
        self.assertIn("<code>AI/Hook.md</code>", self.card())
        self.assertNotIn("/vault/", self.card())

    def test_route_labels(self):
        self.assertIn("без AI", self.card(route="machine"))
        self.assertIn("Сохранено без разбора", self.card(route="fallback"))
        self.assertIn("📎 2", self.card())
        self.assertNotIn("📎", self.card(media_count=0))

    def test_many_tags_are_collapsed(self):
        card = self.card(tags=[f"t{index}" for index in range(12)])
        self.assertIn("+4", card)

    def test_empty_summary_has_no_quote(self):
        self.assertNotIn("blockquote", self.card(summary="  "))


class TextTests(unittest.TestCase):
    def test_progress_counts_words_and_files(self):
        self.assertIn("3 слова · 1 вложение", progress_text("one two three", 1))
        self.assertIn("пустое сообщение", progress_text("", 0))

    def test_help_lists_categories_with_icons(self):
        text = help_text(["AI", "Неизвестная"])
        self.assertIn("🧠 AI", text)
        self.assertIn("🗂 Неизвестная", text)


class StatsTests(unittest.TestCase):
    def test_counts_notes_and_skips_attachments(self):
        with tempfile.TemporaryDirectory() as directory:
            inbox = Path(directory)
            for relative in ("AI/a.md", "AI/b.md", "Web3/c.md", "attachments/x.md", "root.md"):
                path = inbox / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("x", encoding="utf-8")
            old = inbox / "Web3/c.md"
            stamp = time.time() - 10 * 86_400
            os.utime(old, (stamp, stamp))
            stats = collect_note_stats(inbox, "attachments", datetime.now())
            self.assertEqual((stats.total, stats.week), (4, 3))
            self.assertEqual(stats.top_week[0], ("AI", 2))
            rendered = stats_text(stats)
            self.assertIn("7 дней: <b>3</b>", rendered)

    def test_missing_inbox_is_empty(self):
        stats = collect_note_stats(Path("/nonexistent/inbox"), "attachments", datetime.now())
        self.assertEqual(stats.total, 0)
        self.assertIn("За неделю заметок нет", stats_text(stats))


if __name__ == "__main__":
    unittest.main()
