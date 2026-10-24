#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import zipfile
from datetime import datetime, timezone
from pathlib import Path


def safe_name(value: str) -> str:
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", value).strip(" .")
    return cleaned or "telegram-chat"


def message_link(chat_id: int, message_id: int) -> str | None:
    value = str(chat_id)
    if value.startswith("-100"):
        return f"https://t.me/c/{value[4:]}/{message_id}"
    return None


def reaction_summary(reactions: object | None) -> list[dict[str, object]]:
    result: list[dict[str, object]] = []
    for item in getattr(reactions, "results", None) or ():
        reaction = getattr(item, "reaction", None)
        label = (
            getattr(reaction, "emoticon", None)
            or getattr(reaction, "document_id", None)
            or type(reaction).__name__
        )
        result.append({"reaction": str(label), "count": int(getattr(item, "count", 0))})
    return result


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Export complete Telegram chat history")
    parser.add_argument("--chat-id", type=int, required=True)
    parser.add_argument("--chat-title", required=True)
    parser.add_argument("--project-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, default=Path.home() / "exports")
    return parser.parse_args()


async def export_history(args: argparse.Namespace) -> int:
    project_dir = args.project_dir.expanduser().resolve()
    os.environ["OFFER_PARSER_HOME"] = str(project_dir)

    from offer_parser.config import Settings
    from offer_parser.service import make_client
    from telethon.utils import get_display_name

    settings = Settings.load()
    client = make_client(settings)
    await client.connect()
    try:
        if not await client.is_user_authorized():
            raise RuntimeError("Telegram session is not authorized")

        entity = await client.get_entity(args.chat_id)
        actual_title = get_display_name(entity) or args.chat_title
        folder = args.output_dir.expanduser().resolve() / safe_name(args.chat_title)
        folder.mkdir(parents=True, exist_ok=True)
        stem = safe_name(f"{args.chat_title} — all messages")
        markdown_path = folder / f"{stem}.md"
        jsonl_path = folder / f"{stem}.jsonl"
        markdown_tmp = markdown_path.with_suffix(".md.part")
        jsonl_tmp = jsonl_path.with_suffix(".jsonl.part")

        total = 0
        with (
            markdown_tmp.open("w", encoding="utf-8", newline="\n") as markdown,
            jsonl_tmp.open("w", encoding="utf-8", newline="\n") as jsonl,
        ):
            markdown.write(f"# {actual_title} — все сообщения\n\n")
            markdown.write(f"Chat ID: `{args.chat_id}`\n\n")

            async for message in client.iter_messages(entity, limit=None, reverse=True):
                sender = getattr(message, "sender", None)
                sender_id = getattr(message, "sender_id", None)
                sender_name = get_display_name(sender) if sender is not None else None
                username = getattr(sender, "username", None) if sender is not None else None
                date = message.date.astimezone(timezone.utc).isoformat()
                text = message.message or ""
                link = message_link(args.chat_id, message.id)
                media_type = type(message.media).__name__ if message.media else None
                reply_to = getattr(message, "reply_to_msg_id", None)
                replies = getattr(getattr(message, "replies", None), "replies", None)
                forwarded_from = getattr(getattr(message, "fwd_from", None), "from_name", None)

                record = {
                    "chat_id": args.chat_id,
                    "chat_title": actual_title,
                    "message_id": message.id,
                    "date_utc": date,
                    "edit_date_utc": (
                        message.edit_date.astimezone(timezone.utc).isoformat()
                        if message.edit_date else None
                    ),
                    "sender_id": sender_id,
                    "sender_name": sender_name,
                    "username": username,
                    "text": text,
                    "reply_to_message_id": reply_to,
                    "grouped_id": getattr(message, "grouped_id", None),
                    "forwarded_from": forwarded_from,
                    "views": getattr(message, "views", None),
                    "forwards": getattr(message, "forwards", None),
                    "replies": replies,
                    "reactions": reaction_summary(getattr(message, "reactions", None)),
                    "media_type": media_type,
                    "telegram_url": link,
                }
                jsonl.write(json.dumps(record, ensure_ascii=False) + "\n")

                author = sender_name or (f"@{username}" if username else None) or str(sender_id or "unknown")
                markdown.write(f"## {date} · {author} · #{message.id}\n\n")
                if link:
                    markdown.write(f"[Открыть сообщение]({link})\n\n")
                if reply_to:
                    markdown.write(f"Ответ на сообщение: `{reply_to}`\n\n")
                if media_type:
                    markdown.write(f"Медиа: `{media_type}`\n\n")
                markdown.write((text or "_[сообщение без текста]_") + "\n\n---\n\n")

                total += 1
                if total % 100 == 0:
                    markdown.flush()
                    jsonl.flush()
                    print(f"EXPORTED={total}", flush=True)

        markdown_tmp.replace(markdown_path)
        jsonl_tmp.replace(jsonl_path)
        archive_path = folder / f"{stem}.zip"
        with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.write(markdown_path, markdown_path.name)
            archive.write(jsonl_path, jsonl_path.name)

        print(f"EXPORT_COMPLETE={total}")
        print(f"MARKDOWN={markdown_path}")
        print(f"JSONL={jsonl_path}")
        print(f"ARCHIVE={archive_path}")
        return 0
    finally:
        await client.disconnect()


def main() -> int:
    return asyncio.run(export_history(parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
