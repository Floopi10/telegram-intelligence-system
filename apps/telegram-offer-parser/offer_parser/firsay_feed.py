"""Hourly pull of classified Telegram messages from Firsay into the knowledge stream.

Firsay already reads thousands of Telegram groups and tags useful messages (jobs, grants,
partnerships, KOL campaigns...). Its read-only feed (route=intel-export, own token) is
fetched here and every message goes through the same filters as messages the parser reads
itself, so Radar and the digest show them with the source "Firsay · <group>".

Messages that pass the offer rules (paid collabs, KOL and ambassador hiring...) are kept as
item_kind "offer" for the Radar "Офферы" tab; the first FIRSAY_OFFER_NOTIFY_LIMIT of them a
day (MSK) are also sent to Telegram. Everything else goes to the knowledge stream.

Env (from the parser .env): FIRSAY_FEED_TOKEN (required to turn it on), FIRSAY_FEED_URL,
FIRSAY_FEED_INTERVAL_MIN (default 60), FIRSAY_FEED_MAX_PAGES (default 5 per run),
FIRSAY_OFFER_NOTIFY_LIMIT (default 10 a day).
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import urllib.error
import urllib.parse
import urllib.request
import zlib
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from .storage import OfferRecord

LOGGER = logging.getLogger(__name__)

DEFAULT_URL = "https://firsay.io/api/telegram-sync?route=intel-export"
PAGE_LIMIT = 100
SYNTHETIC_CHAT_BASE = -8_000_000_000_000
TME_RE = re.compile(r"^https://t\.me/(?:(c)/(\d+)|([A-Za-z0-9_]{4,32}))/(\d+)")


@dataclass(frozen=True)
class FeedConfig:
    url: str
    token: str
    interval_s: int
    max_pages: int
    cursor_path: Path
    notify_limit: int = 10

    @classmethod
    def from_env(cls, data_dir: Path) -> "FeedConfig | None":
        token = os.getenv("FIRSAY_FEED_TOKEN", "").strip()
        if not token:
            return None
        return cls(
            url=os.getenv("FIRSAY_FEED_URL", DEFAULT_URL).strip() or DEFAULT_URL,
            token=token,
            interval_s=max(15, int(os.getenv("FIRSAY_FEED_INTERVAL_MIN", "60"))) * 60,
            max_pages=max(1, min(20, int(os.getenv("FIRSAY_FEED_MAX_PAGES", "5")))),
            cursor_path=data_dir / "firsay_feed_cursor.txt",
            notify_limit=max(0, int(os.getenv("FIRSAY_OFFER_NOTIFY_LIMIT", "10"))),
        )


class FeedEvent:
    """The slice of a Telethon NewMessage event that MessagePipeline uses."""

    def __init__(
        self, *, chat_id: int, message_id: int, text: str, date: datetime, username: str | None, sender: str = "",
    ) -> None:
        self.chat_id = chat_id
        self.sender = sender
        self.id = message_id
        self.raw_text = text
        self.date = date
        self._chat = SimpleNamespace(username=username)

    async def get_sender(self):
        return None

    async def get_chat(self):
        return self._chat


def _parse_date(value: object) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return datetime.now(timezone.utc)


def to_event(item: dict) -> tuple[FeedEvent, str] | None:
    """Turn one feed item into (event, chat title). Items without a t.me link are skipped."""
    match = TME_RE.match(str(item.get("url") or ""))
    text = str(item.get("text") or "").strip()
    if not match or not text:
        return None
    private_id, username, message_id = match.group(2), match.group(3), int(match.group(4))
    source = item.get("source") or {}
    if private_id:
        chat_id, username = int(f"-100{private_id}"), None
    else:
        chat_id = SYNTHETIC_CHAT_BASE - (zlib.crc32(username.casefold().encode()) & 0x7FFFFFFF)
    title = str(source.get("title") or source.get("username") or username or "Telegram").strip()
    event = FeedEvent(
        chat_id=chat_id, message_id=message_id, text=text,
        date=_parse_date(item.get("publishedAt")), username=username,
        sender=str(item.get("sender") or "").strip(),
    )
    return event, f"Firsay · {title}"[:120]


def fetch_page(config: FeedConfig, after: str) -> dict:
    parts = urllib.parse.urlsplit(config.url)
    query = dict(urllib.parse.parse_qsl(parts.query))
    query["limit"] = str(PAGE_LIMIT)
    if after:
        query["after"] = after
    url = urllib.parse.urlunsplit(parts._replace(query=urllib.parse.urlencode(query)))
    request = urllib.request.Request(
        url, headers={"Authorization": f"Bearer {config.token}", "User-Agent": "telegram-intelligence-parser"},
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.loads(response.read().decode("utf-8"))


def read_cursor(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def write_cursor(path: Path, cursor: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(cursor, encoding="utf-8")
    temporary.replace(path)


OFFER_STREAM = "firsay_offer"


def offer_notification(offer: OfferRecord, result) -> str:
    """Same layout as the parser's own offer messages, marked as coming from Firsay."""
    text = offer.text if len(offer.text) <= 2600 else offer.text[:2600] + "…"
    return (
        f"НАЙДЕН ОФФЕР (FIRSAY) · SCORE {offer.score}\n\n"
        f"Чат: {offer.chat_title}\n"
        f"Автор: {offer.sender_name}\n"
        f"Оплата: {', '.join(result.compensation) or 'не найдено'}\n"
        f"Сигналы: {', '.join(result.matched[:8])}\n\n"
        f"{text}\n\n"
        f"Источник: {offer.source_url or 'прямой ссылки нет'}"
    )


def offers_notified_today(storage) -> int:
    return int(storage.connection.execute(
        """SELECT COUNT(*) FROM audit_decisions
        WHERE stream = ? AND reason = 'notified'
          AND created_at >= datetime('now', '+3 hours', 'start of day', '-3 hours')""",
        (OFFER_STREAM,),
    ).fetchone()[0])


async def handle_offer(pipeline, event, title: str, result, notify_limit: int) -> str:
    """Keep a Firsay message that passed the offer rules; notify only the first ones of the day."""
    storage = pipeline.knowledge_storage
    chat = await event.get_chat()
    username = getattr(chat, "username", None)
    raw = str(event.chat_id)
    record = OfferRecord(
        chat_id=event.chat_id, message_id=event.id, chat_title=title, sender_id=None,
        sender_name=f"@{event.sender}" if event.sender else "неизвестно",
        message_date=event.date.astimezone(timezone.utc), text=event.raw_text, score=result.score,
        matched_rules=", ".join(result.matched), compensation=", ".join(result.compensation),
        source_url=f"https://t.me/{username}/{event.id}" if username
        else f"https://t.me/c/{raw[4:]}/{event.id}" if raw.startswith("-100") else None,
    )
    saved, reason = storage.save_unique(record, "offer")
    notify = saved and offers_notified_today(storage) < notify_limit
    outcome = "notified" if notify else "radar_only" if saved else reason
    storage.record_decision(
        chat_id=event.chat_id, message_id=event.id, chat_title=title, stream=OFFER_STREAM,
        decision="accepted" if saved else "duplicate", reason=outcome, score=result.score, text=event.raw_text,
    )
    if notify:
        storage.enqueue_message(
            dedupe_key=f"firsay:{event.chat_id}:{event.id}",
            target=pipeline.settings.notify_target,
            body=offer_notification(record, result),
        )
    return outcome


async def handle_item(pipeline, event, title: str, notify_limit: int) -> None:
    result = pipeline.matcher.evaluate(event.raw_text)
    if result.score >= pipeline.settings.min_score:
        await pipeline._guarded(
            lambda ev, _text: handle_offer(pipeline, ev, title, result, notify_limit),
            pipeline.knowledge_storage, OFFER_STREAM, event, event.raw_text,
        )
        return
    await pipeline._guarded(
        pipeline._handle_knowledge, pipeline.knowledge_storage, "knowledge", event, event.raw_text,
    )


async def pull_once(pipeline, config: FeedConfig, fetch=fetch_page) -> int:
    """One run: up to max_pages pages. The cursor moves only after a page is handled."""
    if pipeline.knowledge_matcher is None or pipeline.knowledge_storage is None:
        return 0
    handled = 0
    cursor = read_cursor(config.cursor_path)
    for _ in range(config.max_pages):
        page = await asyncio.to_thread(fetch, config, cursor)
        for item in page.get("items") or []:
            converted = to_event(item)
            if converted is None:
                continue
            event, title = converted
            pipeline.knowledge_by_id.setdefault(event.chat_id, title)
            await handle_item(pipeline, event, title, config.notify_limit)
            handled += 1
        cursor = str(page.get("next") or cursor)
        write_cursor(config.cursor_path, cursor)
        if not page.get("more"):
            break
    return handled


async def firsay_feed_loop(pipeline, config: FeedConfig) -> None:
    while True:
        try:
            handled = await pull_once(pipeline, config)
            LOGGER.info("Firsay: обработано сообщений %s", handled)
        except asyncio.CancelledError:
            raise
        except urllib.error.HTTPError as exc:
            LOGGER.warning("Firsay feed HTTP %s", exc.code)
        except (urllib.error.URLError, TimeoutError, ValueError, OSError) as exc:
            LOGGER.warning("Firsay feed недоступен: %s", type(exc).__name__)
        await asyncio.sleep(config.interval_s)
