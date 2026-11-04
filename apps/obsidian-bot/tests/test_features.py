from __future__ import annotations

import asyncio
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from related import find_related, index_notes, rebuild_links, read_note_ref  # noqa: E402
from ui import saved_card, x_metrics_line  # noqa: E402
from vault import Vault, _format_frontmatter  # noqa: E402
from x_enrich import XCollector, XPost, enriched_text, first_status_url, parse_count  # noqa: E402


def vault_settings(root: Path) -> SimpleNamespace:
    inbox = root / "Telegram"
    return SimpleNamespace(
        vault_path=root, inbox_path=inbox, attachments_path=inbox / "attachments",
        attachments_folder="attachments", category_path=lambda name: inbox / name,
    )


def write_note(path: Path, title: str, tags: list[str], category: str = "AI") -> None:
    from datetime import datetime

    path.parent.mkdir(parents=True, exist_ok=True)
    head = _format_frontmatter(
        title=title, created=datetime(2026, 11, 1), category=category, tags=tags,
        source=None, chat=None, author=None, summary="s",
    )
    path.write_text(f"{head}\n\n# {title}\n\nbody\n", encoding="utf-8")


class XEnrichTests(unittest.TestCase):
    def test_status_url_is_canonical(self):
        self.assertEqual(
            first_status_url("смотри https://twitter.com/Some_User/status/123?s=20 круто"),
            "https://x.com/Some_User/status/123",
        )
        self.assertIsNone(first_status_url("https://x.com/i/status/123"))
        self.assertIsNone(first_status_url("https://x.com/some_user"))

    def test_counts_from_collector_labels(self):
        self.assertEqual(parse_count("98\n Views"), 98)
        self.assertEqual(parse_count("1,132"), 1132)
        self.assertEqual(parse_count("12.5K"), 12500)
        self.assertIsNone(parse_count("Reply"))
        self.assertEqual(parse_count("1 Bookmark"), 1)
        self.assertEqual(parse_count("5 Bookmarks. Bookmark"), 5)
        self.assertEqual(parse_count("12.5K Likes"), 12500)

    def test_enriched_text_uses_lines_the_note_builder_parses(self):
        post = XPost("https://x.com/a/status/1", "a", "Hook line", "2026-11-01T10:00:00Z",
                     {"views": 12000, "likes": 300, "reposts": None, "replies": 4, "bookmarks": 50})
        text = enriched_text("https://x.com/a/status/1", post)
        self.assertIn("Текст поста:\nHook line", text)
        self.assertIn("Просмотры: 12000", text)
        self.assertNotIn("Репосты", text)
        from llm import LLM

        self.assertIn("- Просмотры: 12000", LLM._post_metrics_block(text))

    def test_collector_without_config_is_disabled(self):
        collector = XCollector("")
        self.assertFalse(collector.enabled)
        self.assertIsNone(asyncio.run(collector.fetch("https://x.com/a/status/1")))

    def test_collector_failure_returns_none(self):
        with tempfile.TemporaryDirectory() as directory:
            env = Path(directory) / "x.env"
            env.write_text("X_COLLECTOR_URL=http://127.0.0.1:9\nX_COLLECTOR_TOKEN=t\n", encoding="utf-8")
            collector = XCollector(str(env), timeout=1)
            with patch.object(collector, "_request", side_effect=OSError("down")):
                self.assertIsNone(asyncio.run(collector.fetch("https://x.com/a/status/1")))
            with patch.object(collector, "_request", return_value={"text": "Post", "views": "1.2K", "author": "A"}):
                post = asyncio.run(collector.fetch("https://x.com/a/status/1"))
            self.assertEqual((post.text, post.metrics["views"], post.author), ("Post", 1200, "A"))


class RelatedTests(unittest.TestCase):
    def test_finds_notes_by_tags_and_title_words(self):
        with tempfile.TemporaryDirectory() as directory:
            settings = vault_settings(Path(directory))
            write_note(settings.inbox_path / "AI/Claude agents review.md", "Claude agents review (Ревью)", ["claude", "agents"])
            write_note(settings.inbox_path / "AI/Cooking.md", "Cooking pasta (Паста)", ["food"])
            write_note(settings.inbox_path / "attachments/skip.md", "Claude agents", ["claude", "agents"])
            notes = index_notes(settings.inbox_path, "attachments")
            self.assertEqual(len(notes), 2)
            self.assertEqual(find_related("Claude agents for PR", ["claude", "telegram"], notes), ["Claude agents review"])
            self.assertEqual(find_related("Pasta night", ["ai", "telegram"], notes), [])

    def test_generic_tags_alone_do_not_link(self):
        ref = SimpleNamespace(stem="X", tags=frozenset(), words=frozenset({"growth"}))
        self.assertEqual(find_related("growth", ["telegram", "ai"], [ref]), [])

    def test_quoted_title_and_tags_are_read(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "n.md"
            write_note(path, 'Say "hello" world', ["Hooks", "telegram"])
            ref = read_note_ref(path)
            self.assertEqual(ref.tags, frozenset({"hooks"}))
            self.assertIn("hello", ref.words)

    def test_rebuild_keeps_only_existing_links(self):
        body = "## Суть\ntext\n\n## Связи\n- [[Real Note]]\n- [[Made Up]]\n\n## Цитата и источник\nq"
        result = rebuild_links(body, ["Related One"], {"real note"})
        self.assertIn("## Цитата и источник\nq", result)
        self.assertTrue(result.endswith("## Связи\n- [[Related One]]\n- [[Real Note]]"))
        self.assertNotIn("Made Up", result)
        self.assertEqual(rebuild_links("## Суть\nx\n\n## Связи\n- [[Gone]]", [], set()), "## Суть\nx")


class VaultActionTests(unittest.TestCase):
    def test_move_updates_category_and_keeps_attachment_links(self):
        with tempfile.TemporaryDirectory() as directory:
            settings = vault_settings(Path(directory))
            vault = Vault(settings)
            note = settings.inbox_path / "AI/Note.md"
            write_note(note, "Note", ["x"], category="AI")
            note.write_text(note.read_text(encoding="utf-8") + "![[../attachments/p.jpg]]\n", encoding="utf-8")
            moved = vault.move_note(note, "Web3")
            self.assertFalse(note.exists())
            self.assertEqual(moved, settings.inbox_path / "Web3/Note.md")
            text = moved.read_text(encoding="utf-8")
            self.assertIn('category: "Web3"', text)
            self.assertIn("![[../attachments/p.jpg]]", text)

    def test_trash_moves_into_obsidian_trash(self):
        with tempfile.TemporaryDirectory() as directory:
            settings = vault_settings(Path(directory))
            vault = Vault(settings)
            note = settings.inbox_path / "AI/Note.md"
            write_note(note, "Note", [])
            trashed = vault.trash_note(note)
            self.assertEqual(trashed, Path(directory) / ".trash/Note.md")
            self.assertTrue(trashed.exists())


class CardTests(unittest.TestCase):
    def test_x_metrics_line(self):
        self.assertEqual(x_metrics_line({"views": 12000, "likes": 300, "reposts": None}), "𝕏 👁 12 000 · ♥ 300")
        self.assertEqual(x_metrics_line(None), "")
        card = saved_card(
            title="T", summary="", category="AI", tags=[], media_count=0,
            note_path=Path("/v/T/AI/T.md"), inbox_path=Path("/v/T"), route="ai",
            x_metrics={"views": 5},
        )
        self.assertIn("𝕏 👁 5", card)


class CardActionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        import bot

        self.bot = bot
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.vault_settings = vault_settings(root)
        self.vault = Vault(self.vault_settings)
        self.note = self.vault_settings.inbox_path / "AI/Note.md"
        write_note(self.note, "Note", [])
        self.settings = SimpleNamespace(
            allowed_user_ids=[42], categories=["AI", "Web3", "Другое"], inbox_path=self.vault_settings.inbox_path,
        )
        self.card = bot.CardState(self.note, "Note", "s", "AI", [], 0, "ai", None)
        self.token = bot._remember_card(self.card)

    async def asyncTearDown(self):
        self.temp.cleanup()

    def callback(self, data: str, user_id: int = 42):
        message = SimpleNamespace(edit_text=AsyncMock())
        return SimpleNamespace(data=data, from_user=SimpleNamespace(id=user_id), message=message, answer=AsyncMock())

    async def test_move_to_category(self):
        callback = self.callback(f"mv:{self.token}:1")
        await self.bot.on_card_action(callback, self.settings, self.vault)
        moved = self.vault_settings.inbox_path / "Web3/Note.md"
        self.assertTrue(moved.exists())
        self.assertEqual(self.bot._cards[self.token].category, "Web3")
        self.assertIn("Web3", callback.message.edit_text.await_args.args[0])

    async def test_delete_needs_confirmation(self):
        await self.bot.on_card_action(self.callback(f"del:{self.token}"), self.settings, self.vault)
        self.assertTrue(self.note.exists())
        await self.bot.on_card_action(self.callback(f"rm:{self.token}"), self.settings, self.vault)
        self.assertFalse(self.note.exists())
        self.assertNotIn(self.token, self.bot._cards)

    async def test_stranger_and_stale_buttons_do_nothing(self):
        stranger = self.callback(f"rm:{self.token}", user_id=7)
        await self.bot.on_card_action(stranger, self.settings, self.vault)
        self.assertTrue(self.note.exists())
        stale = self.callback("rm:deadbeef00")
        await self.bot.on_card_action(stale, self.settings, self.vault)
        self.assertTrue(stale.answer.await_args.kwargs.get("show_alert"))
        self.assertTrue(self.note.exists())


if __name__ == "__main__":
    unittest.main()
