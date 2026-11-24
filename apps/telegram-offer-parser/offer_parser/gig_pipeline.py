"""Route writer gigs ("ищут reply guy / гострайтера") to one reader.

The reader is a friend of the owner, so nothing here touches Saved Messages, Radar or the
digest: a gig is kept under its own item_kind "gig" and sent to GIG_NOTIFY_TARGET. The
stream is off until that variable is set.

Env (parser .env):
  GIG_NOTIFY_TARGET  "@username" / chat id (sent from the parser's Telegram account) or
                     "bot:<chat_id>" (sent by the bot whose token is GIG_BOT_TOKEN; the reader
                     must press Start in that bot once); several readers go through a comma
  GIG_BOT_TOKEN      Bot API token, only for "bot:" targets
  GIG_NOTIFY_LIMIT   messages a day (MSK), default 30
  GIG_MAX_AGE_DAYS   older messages are skipped (search and feeds return old posts), default 14
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone

from .gig_offers import detect, format_gig
from .kol_pipeline import message_url
from .storage import OfferRecord

LOGGER = logging.getLogger(__name__)
STREAM = "gig"
KIND = "gig"


def gig_target() -> str:
    return os.getenv("GIG_NOTIFY_TARGET", "").strip()


def _int_env(name: str, default: int) -> int:
    try:
        return max(0, int(os.getenv(name, str(default))))
    except ValueError:
        return default


def notified_today(storage) -> int:
    return int(storage.connection.execute(
        """SELECT COUNT(*) FROM audit_decisions WHERE stream = ? AND reason = 'notified'
           AND created_at >= datetime('now', '+3 hours', 'start of day', '-3 hours')""",
        (STREAM,),
    ).fetchone()[0])


async def process_gig(pipeline, event, text: str, title: str, *, url: str | None = None) -> bool:
    """Return True when the message is a writer gig (sent, kept or already known)."""
    target = gig_target()
    if not target:
        return False
    gig = detect(text)
    if not gig.is_gig:
        return False
    storage = pipeline.knowledge_storage or pipeline.storage
    date = event.date.astimezone(timezone.utc)
    if datetime.now(timezone.utc) - date > timedelta(days=_int_env("GIG_MAX_AGE_DAYS", 14)):
        return False
    if url is None:
        chat = await event.get_chat()
        url = message_url(event.chat_id, getattr(chat, "username", None), event.id)
    # Feed events carry a username string; Telethon events carry a User (or None).
    sender = getattr(event, "sender", None)
    author = sender if isinstance(sender, str) else str(getattr(sender, "username", "") or "")
    record = OfferRecord(
        chat_id=event.chat_id, message_id=event.id, chat_title=title, sender_id=None,
        sender_name=f"@{author}" if author else "неизвестно", message_date=date, text=text, score=gig.score,
        matched_rules="gig: " + ", ".join(gig.roles), compensation=gig.pay, source_url=url,
    )
    saved, reason = storage.save_unique(record, KIND)
    notify = saved and notified_today(storage) < _int_env("GIG_NOTIFY_LIMIT", 30)
    outcome = "notified" if notify else "over_limit" if saved else reason
    storage.record_decision(
        chat_id=event.chat_id, message_id=event.id, chat_title=title, stream=STREAM,
        decision="accepted" if saved else "duplicate", reason=outcome, score=gig.score, text=text,
    )
    if notify:
        body = format_gig(gig, text=text, source=title, url=url, author=author)
        # Several readers: "bot:1,bot:2". The daily limit counts gigs, not copies.
        for reader in dict.fromkeys(part.strip() for part in target.split(",") if part.strip()):
            storage.enqueue_message(dedupe_key=f"gig:{event.chat_id}:{event.id}:{reader}", target=reader, body=body)
        LOGGER.info("Gig отправлен: %s/%s", event.chat_id, event.id)
    return True


def main() -> int:
    """`python -m offer_parser.gig_pipeline`: who pressed Start in the gig bot; `--test <chat_id>` sends a test."""
    import json
    import sys
    import urllib.request

    from dotenv import load_dotenv

    from .config import PROJECT_DIR
    from .outbox import send_via_bot

    load_dotenv(PROJECT_DIR / ".env")
    token = os.getenv("GIG_BOT_TOKEN", "").strip()
    if not token:
        print("GIG_BOT_TOKEN не задан в .env")
        return 2
    if len(sys.argv) == 3 and sys.argv[1] == "--test":
        send_via_bot(sys.argv[2], "✍️ Тест: сюда будут приходить вакансии reply guy / гострайтер.", token)
        print("Отправлено")
        return 0
    try:
        with urllib.request.urlopen(f"https://api.telegram.org/bot{token}/getUpdates", timeout=30) as response:
            updates = json.loads(response.read().decode("utf-8")).get("result", [])
    except OSError as exc:
        # The URL holds the token: never print it.
        print(f"Bot API недоступен: {type(exc).__name__} {getattr(exc, 'code', '')}")
        return 1
    chats = {}
    for update in updates:
        chat = (update.get("message") or {}).get("chat") or {}
        if chat.get("id"):
            chats[chat["id"]] = chat.get("username") or chat.get("first_name") or ""
    if not chats:
        print("Никто ещё не нажал Start в боте (или обновления старше суток).")
    for chat_id, name in chats.items():
        print(f"GIG_NOTIFY_TARGET=bot:{chat_id}   # {name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
