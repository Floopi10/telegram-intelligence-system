#!/usr/bin/env python3
"""Find KOL offers in every chat of the parser's Telegram account, closed groups included.

Telegram's global search covers all chats the account is in, so a few keyword searches over
the last N days replace reading every chat's history. Each hit goes through the parser's KOL
filter; the report shows, per chat, how many real offers it had and whether the parser
already reads it. With --add, chats with at least --min-offers offers that the parser does
not read yet are appended to data/kol_chats.csv (read by the monitor for KOL offers only).

Run on the server with the parser stopped (one Telegram session at a time):
  PYTHONPATH=~/telegram-offer-parser ~/telegram-offer-parser/.venv/bin/python scan_kol_offers.py \
      --project-dir ~/telegram-offer-parser [--days 30] [--add --min-offers 1]
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import json
import os
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

KEYWORDS = (
    "kol", "kols", "creator", "creators", "influencer", "influencers", "ambassador", "ambassadors",
    "shillers", "callers", "paid post", "per post",
    "креатор", "креаторы", "креаторов", "блогер", "блогеры", "блогеров", "инфлюенсер", "амбассадор", "колов",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--project-dir", type=Path, required=True)
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--output-dir", type=Path, default=Path.home() / "obsidian-vault/Telegram/.exports/kol-scan")
    parser.add_argument("--add", action="store_true", help="append unread chats with offers to data/kol_chats.csv")
    parser.add_argument("--min-offers", type=int, default=1)
    return parser.parse_args()


def read_ids(path: Path) -> set[int]:
    if not path.exists():
        return set()
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        return {
            int(row["chat_id"]) for row in csv.DictReader(file)
            if row.get("enabled", "").strip().casefold() in {"1", "true", "yes", "да"}
        }


def is_group(chat) -> bool:
    """Groups and supergroups only: global search also returns private dialogs, bots and channels."""
    if chat is None or hasattr(chat, "first_name") or getattr(chat, "bot", False):
        return False
    if getattr(chat, "broadcast", False) and not getattr(chat, "megagroup", False):
        return False
    return True


def message_url(chat_id: int, username: str | None, message_id: int) -> str:
    if username:
        return f"https://t.me/{username}/{message_id}"
    raw = str(chat_id)
    return f"https://t.me/c/{raw[4:]}/{message_id}" if raw.startswith("-100") else ""


async def scan(args: argparse.Namespace) -> int:
    project = args.project_dir.expanduser().resolve()
    os.environ["OFFER_PARSER_HOME"] = str(project)
    from offer_parser.config import Settings
    from offer_parser.kol_offers import detect
    from offer_parser.service import make_client
    from telethon.errors import FloodWaitError
    from telethon.utils import get_display_name

    settings = Settings.load()
    data_dir = settings.knowledge_database_path.parent
    kol_file = Path(os.getenv("KOL_CHATS_FILE", str(data_dir / "kol_chats.csv")))
    monitored = read_ids(settings.chats_file) | read_ids(settings.knowledge_chats_file) | read_ids(kol_file)
    since = datetime.now(timezone.utc) - timedelta(days=args.days)

    client = make_client(settings)
    await client.connect()
    if not await client.is_user_authorized():
        raise RuntimeError("Telegram session is not authorized")
    chats: dict[int, dict] = {}
    offers: dict[int, dict[str, dict]] = defaultdict(dict)
    seen: set[tuple[int, int]] = set()
    checked = 0
    try:
        for keyword in KEYWORDS:
            while True:
                try:
                    async for message in client.iter_messages(None, search=keyword, limit=None):
                        if message.date < since:
                            break
                        key = (message.chat_id, message.id)
                        if key in seen or not message.message:
                            continue
                        seen.add(key)
                        checked += 1
                        offer = detect(message.message)
                        if not offer.is_offer:
                            continue
                        chat = message.chat
                        if not is_group(chat):
                            continue  # private dialogs, bots and channels are not offer groups
                        username = getattr(chat, "username", None)
                        chats.setdefault(message.chat_id, {
                            "chat_id": message.chat_id, "title": get_display_name(chat) if chat else str(message.chat_id),
                            "username": username or "", "private": not username,
                            "monitored": message.chat_id in monitored,
                        })
                        text_key = " ".join(message.message.split())[:200]
                        offers[message.chat_id].setdefault(text_key, {
                            "date": message.date.isoformat(), "url": message_url(message.chat_id, username, message.id),
                            "pay": offer.pay, "niches": list(offer.niches), "text": message.message[:600],
                        })
                    break
                except FloodWaitError as error:
                    print(f"FLOOD_WAIT={error.seconds}s keyword={keyword}", flush=True)
                    await asyncio.sleep(error.seconds + 1)
            print(f"KEYWORD={keyword} checked={checked} chats_with_offers={len(chats)}", flush=True)
    finally:
        await client.disconnect()

    rows = []
    for chat_id, info in chats.items():
        items = sorted(offers[chat_id].values(), key=lambda item: item["date"])
        rows.append({**info, "offers": len(items), "last": items[-1]["date"][:10],
                     "pay": sorted({item["pay"] for item in items if item["pay"]})[:4],
                     "example": items[-1]["text"][:200].replace("\n", " "), "url": items[-1]["url"], "items": items})
    rows.sort(key=lambda row: (row["monitored"], -row["offers"], row["title"]))

    out = args.output_dir.expanduser()
    out.mkdir(parents=True, exist_ok=True)
    (out / "kol-scan.json").write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
    with (out / "kol-scan.csv").open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.writer(file)
        writer.writerow(["chat_id", "title", "link", "private", "monitored", "offers", "last", "pay", "example"])
        for row in rows:
            link = f"https://t.me/{row['username']}" if row["username"] else row["url"]
            writer.writerow([row["chat_id"], row["title"], link, row["private"], row["monitored"],
                             row["offers"], row["last"], "; ".join(row["pay"]), row["example"]])

    new = [row for row in rows if not row["monitored"] and row["offers"] >= args.min_offers]
    print(f"CHECKED={checked} CHATS_WITH_OFFERS={len(rows)} NOT_MONITORED={len(new)} "
          f"PRIVATE_NOT_MONITORED={sum(row['private'] for row in new)}")
    for row in new[:40]:
        print(f"NEW {row['offers']:3} {'(закрытая) ' if row['private'] else ''}{row['title'][:50]}")
    if args.add and new:
        exists = kol_file.exists()
        with kol_file.open("a", encoding="utf-8-sig" if not exists else "utf-8", newline="") as file:
            writer = csv.DictWriter(file, fieldnames=("enabled", "chat_id", "title", "username", "kind"))
            if not exists:
                writer.writeheader()
            for row in new:
                writer.writerow({"enabled": "1", "chat_id": row["chat_id"], "title": row["title"],
                                 "username": row["username"], "kind": "kol"})
        print(f"ADDED={len(new)} FILE={kol_file}")
    print(f"REPORT={out / 'kol-scan.csv'}")
    return 0


def main() -> int:
    return asyncio.run(scan(parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
