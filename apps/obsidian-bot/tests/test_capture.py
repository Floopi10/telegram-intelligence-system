from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from capture import append_line, classify, format_line, remove_line, target_path  # noqa: E402

NOV_4 = datetime(2026, 11, 4, 15, 0)
NOV_5 = datetime(2026, 11, 5, 9, 0)


class ClassifyTests(unittest.TestCase):
    def kind(self, text):
        capture = classify(text)
        return capture.kind if capture else None

    def test_real_diary_lines_go_to_the_diary(self):
        for line in ("100 пользователей на Firsay", "оплатил Google Claude", "записался на вождение",
                     "опять кот потерялся", "обновляю бота Obsidian", "минус 5 акков в твиттере", "+24$ за 1inch"):
            with self.subTest(line=line):
                self.assertEqual(self.kind(line), "log")

    def test_ideas_and_tasks(self):
        self.assertEqual(self.kind("идея: бот для мемов"), "idea")
        self.assertEqual(classify("идея: бот для мемов").text, "бот для мемов")
        self.assertEqual(self.kind("Firsay идея: таблица KOL"), "idea")
        self.assertEqual(self.kind("хочу сделать розыгрыш для токена"), "idea")
        self.assertEqual(self.kind("сделать парсер вакансии для брата"), "task")
        self.assertEqual(self.kind("надо оплатить сервер"), "task")
        self.assertEqual(classify("задача: позвонить маме").text, "позвонить маме")

    def test_links_and_long_texts_stay_notes(self):
        self.assertIsNone(classify("глянь https://x.com/a/status/1"))
        self.assertIsNone(classify(" ".join(["слово"] * 30)))
        self.assertIsNone(classify("строка\nстрока\nстрока\nстрока"))


class WriteTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.vault = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_targets(self):
        self.assertEqual(target_path(self.vault, "log", "x", NOV_4), self.vault / "Месяца/Ноябрь.md")
        self.assertEqual(target_path(self.vault, "task", "x", NOV_4), self.vault / "00 Inbox/Задачи.md")
        self.assertEqual(target_path(self.vault, "idea", "Firsay x", NOV_4), self.vault / "00 Inbox/Идеи.md")
        firsay = self.vault / "02 Projects/Мой AI проект/Разное (мысли, идеи).md"
        firsay.parent.mkdir(parents=True)
        firsay.write_text("x", encoding="utf-8")
        self.assertEqual(target_path(self.vault, "idea", "для Firsay x", NOV_4), firsay)

    def test_line_formats_match_the_vault(self):
        self.assertEqual(format_line("log", "оплатил Claude", NOV_4), "оплатил Claude 04.11")
        self.assertEqual(format_line("log", "оплатил Claude 03.11", NOV_4), "оплатил Claude 03.11")
        self.assertEqual(format_line("idea", "бот", NOV_4), "- бот (04.11)")
        self.assertEqual(format_line("task", "купить прокси", NOV_4), "- [ ] купить прокси ➕ 2026-11-04")

    def test_diary_keeps_days_apart(self):
        path = self.vault / "Месяца/Ноябрь.md"
        path.parent.mkdir(parents=True)
        path.write_text("снег первый 03.11", encoding="utf-8")
        append_line(path, "log", "обновляю бота 04.11", NOV_4)
        append_line(path, "log", "ещё одно 04.11", NOV_4)
        append_line(path, "log", "новый день 05.11", NOV_5)
        self.assertEqual(
            path.read_text(encoding="utf-8"),
            "снег первый 03.11\n\nобновляю бота 04.11\nещё одно 04.11\n\nновый день 05.11\n",
        )

    def test_remove_only_the_bot_line(self):
        path = self.vault / "00 Inbox/Задачи.md"
        append_line(path, "task", "- [ ] a", NOV_4)
        append_line(path, "task", "- [ ] b", NOV_4)
        self.assertTrue(remove_line(path, "- [ ] a"))
        self.assertEqual(path.read_text(encoding="utf-8"), "- [ ] b\n")
        self.assertFalse(remove_line(path, "- [ ] missing"))


class BotCaptureTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        import bot

        self.bot = bot
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.settings = SimpleNamespace(
            allowed_user_ids=[42], vault_path=root, inbox_path=root / "Telegram",
            attachments_folder="attachments", categories=["AI"],
        )

    async def asyncTearDown(self):
        self.temp.cleanup()

    def message(self, text, **forward):
        fields = dict(text=text, caption=None, media_group_id=None, photo=None, video=None, document=None,
                      audio=None, voice=None, video_note=None, from_user=SimpleNamespace(id=42),
                      forward_origin=None, forward_from=None, forward_from_chat=None,
                      forward_sender_name=None, forward_date=None, answer=AsyncMock())
        fields.update(forward)
        return SimpleNamespace(**fields)

    async def send(self, message):
        with patch.object(self.bot, "_process_post", AsyncMock()) as process:
            await self.bot.on_single_message(message, self.settings, None, None, None, None)
        return process

    async def test_own_line_goes_to_the_month_note(self):
        message = self.message("оплатил Claude")
        process = await self.send(message)
        process.assert_not_awaited()
        month = next((self.settings.vault_path / "Месяца").glob("*.md"))
        self.assertIn("оплатил Claude ", month.read_text(encoding="utf-8"))
        self.assertIn("В дневник", message.answer.await_args.args[0])

    async def test_forwarded_text_keeps_the_note_flow(self):
        process = await self.send(self.message("оплатил Claude", forward_date=datetime(2026, 11, 4)))
        process.assert_awaited_once()
        self.assertFalse((self.settings.vault_path / "Месяца").exists())

    async def test_switch_kind_and_undo(self):
        message = self.message("купить прокси")
        await self.send(message)
        tasks = self.settings.vault_path / "00 Inbox/Задачи.md"
        self.assertIn("- [ ] купить прокси", tasks.read_text(encoding="utf-8"))
        token = next(reversed(self.bot._captures))
        callback = SimpleNamespace(data=f"cap:{token}:idea", from_user=SimpleNamespace(id=42),
                                   message=SimpleNamespace(edit_text=AsyncMock(), message_id=1), answer=AsyncMock())
        await self.bot.on_capture_action(callback, self.settings, None, None, None)
        self.assertNotIn("купить прокси", tasks.read_text(encoding="utf-8"))
        ideas = self.settings.vault_path / "00 Inbox/Идеи.md"
        self.assertIn("- купить прокси (", ideas.read_text(encoding="utf-8"))
        callback.data = f"cap:{token}:undo"
        await self.bot.on_capture_action(callback, self.settings, None, None, None)
        self.assertNotIn("купить прокси", ideas.read_text(encoding="utf-8"))

    async def test_stranger_cannot_move_lines(self):
        await self.send(self.message("купить прокси"))
        token = next(reversed(self.bot._captures))
        callback = SimpleNamespace(data=f"cap:{token}:undo", from_user=SimpleNamespace(id=7),
                                   message=SimpleNamespace(edit_text=AsyncMock()), answer=AsyncMock())
        await self.bot.on_capture_action(callback, self.settings, None, None, None)
        tasks = self.settings.vault_path / "00 Inbox/Задачи.md"
        self.assertIn("купить прокси", tasks.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
