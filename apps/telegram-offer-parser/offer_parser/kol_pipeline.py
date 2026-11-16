"""Route KOL offers ("ищут KOL") to Saved Messages and the Radar "Офферы" tab.

Used for every message from the owner's chats and for the Firsay feed. A detected offer is
kept once (the same ask is often posted in many groups) and sent at once; a daily cap
(KOL_NOTIFY_LIMIT, default 40) only protects against a flood.
"""
from __future__ import annotations

import logging
import os
from datetime import timezone

from .kol_offers import detect, format_offer
from .storage import OfferRecord

LOGGER = logging.getLogger(__name__)
STREAM = "kol_offer"


def notify_limit() -> int:
    try:
        return max(0, int(os.getenv("KOL_NOTIFY_LIMIT", "40")))
    except ValueError:
        return 40


def notified_today(storage) -> int:
    return int(storage.connection.execute(
        """SELECT COUNT(*) FROM audit_decisions WHERE stream = ? AND reason = 'notified'
           AND created_at >= datetime('now', '+3 hours', 'start of day', '-3 hours')""",
        (STREAM,),
    ).fetchone()[0])


def message_url(chat_id: int, username: str | None, message_id: int) -> str | None:
    if username:
        return f"https://t.me/{username}/{message_id}"
    raw = str(chat_id)
    return f"https://t.me/c/{raw[4:]}/{message_id}" if raw.startswith("-100") else None


async def process_kol(pipeline, event, text: str, title: str) -> bool:
    """Return True when the message is a KOL offer (sent or already known)."""
    offer = detect(text)
    if not offer.is_offer:
        return False
    storage = pipeline.knowledge_storage or pipeline.storage
    chat = await event.get_chat()
    url = message_url(event.chat_id, getattr(chat, "username", None), event.id)
    # Firsay feed events carry a username string; Telethon events carry a User (or None).
    sender = getattr(event, "sender", None)
    author = sender if isinstance(sender, str) else str(getattr(sender, "username", "") or "")
    record = OfferRecord(
        chat_id=event.chat_id, message_id=event.id, chat_title=title, sender_id=None,
        sender_name=f"@{author}" if author else "неизвестно",
        message_date=event.date.astimezone(timezone.utc), text=text, score=offer.score,
        matched_rules="kol: " + ", ".join(offer.niches or ("any",)), compensation=offer.pay, source_url=url,
    )
    saved, reason = storage.save_unique(record, "offer")
    notify = saved and notified_today(storage) < notify_limit()
    outcome = "notified" if notify else "radar_only" if saved else reason
    storage.record_decision(
        chat_id=event.chat_id, message_id=event.id, chat_title=title, stream=STREAM,
        decision="accepted" if saved else "duplicate", reason=outcome, score=offer.score, text=text,
    )
    if notify:
        storage.enqueue_message(
            dedupe_key=f"kol:{event.chat_id}:{event.id}",
            target=pipeline.settings.notify_target,
            body=format_offer(offer, text=text, chat_title=title, url=url, author=author),
        )
        LOGGER.info("KOL-оффер отправлен: %s/%s", event.chat_id, event.id)
    return True
