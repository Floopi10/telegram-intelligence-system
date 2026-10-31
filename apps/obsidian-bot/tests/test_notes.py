from __future__ import annotations

import asyncio
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from machine_note import structure_machine_message  # noqa: E402
from vault import Vault, _attachment_name, _format_frontmatter  # noqa: E402


TWITTER_MESSAGE = """TWITTER INTELLIGENCE FROM TELEGRAM

Тип: Twitter Intelligence
Чат: X FAMILY
X URL: https://x.com/a/status/1
AI score: 9/10
Тема: Хуки
Хук: Сильный контраст

Полный разбор:
Вывод: хук работает.
Второе предложение разбора.

Ключевые мысли:
- Первое: контраст
- Второе: цифры

Реальные метрики X:
- Просмотры: 12 000
- Лайки: 300

Цитата и источник:
Чат: подделка
https://x.com/a/status/1
"""


class MachineNoteTests(unittest.TestCase):
    def setUp(self):
        self.note = structure_machine_message(TWITTER_MESSAGE)

    def test_real_x_metrics_reach_the_note(self):
        self.assertIn("## Метрики поста", self.note.body_md)
        self.assertIn("- Просмотры: 12 000", self.note.body_md)
        self.assertIn("- Лайки: 300", self.note.body_md)

    def test_colon_lines_do_not_cut_sections(self):
        self.assertIn("Второе предложение разбора.", self.note.body_md)
        self.assertIn("- Второе: цифры", self.note.body_md)

    def test_quoted_text_cannot_replace_parser_fields(self):
        self.assertIn("X FAMILY", self.note.body_md)
        self.assertNotIn("· подделка", self.note.body_md)


class VaultTests(unittest.TestCase):
    def test_attachment_name_cannot_leave_folder(self):
        self.assertEqual(_attachment_name("../../.env"), ".env")
        self.assertEqual(_attachment_name("..\\..\\evil.md"), "evil.md")
        self.assertEqual(_attachment_name(".."), "file")
        self.assertEqual(_attachment_name("photo 1.jpg"), "photo 1.jpg")

    def test_saved_media_stays_in_attachments(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            settings = SimpleNamespace(inbox_path=root / "in", attachments_path=root / "in" / "att")
            target = asyncio.run(Vault(settings).save_media(b"x", "../../../escape.txt"))
            self.assertEqual(target.parent, settings.attachments_path)

    def test_frontmatter_survives_quotes_and_newlines(self):
        from datetime import datetime

        rendered = _format_frontmatter(
            title='Say "hi"', created=datetime(2026, 1, 1), category="AI", tags=['a"b'],
            source="https://t.me/a/1", chat='Chat "x"', author="A\nB", summary='S "q"',
        )
        self.assertIn('title: "Say \\"hi\\""', rendered)
        self.assertIn('chat: "Chat \\"x\\""', rendered)
        self.assertIn('author: "A B"', rendered)
        self.assertIn('tags: ["a\\"b"]', rendered)


if __name__ == "__main__":
    unittest.main()
