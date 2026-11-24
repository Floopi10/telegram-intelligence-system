from __future__ import annotations

import asyncio
import json
import logging
import os
import urllib.parse
import urllib.error
import urllib.request

from telethon.errors import FloodWaitError

from .storage import Storage


LOGGER = logging.getLogger(__name__)
MAX_ATTEMPTS = 20
MAX_BACKOFF_SECONDS = 3600


def backoff_seconds(attempts: int) -> int:
    return min(30 * 2 ** min(attempts, 10), MAX_BACKOFF_SECONDS)


BOT_PREFIX = "bot:"
BOT_MAX_CHARS = 4096


def send_via_bot(chat_id: str, body: str, token: str | None = None) -> None:
    """Send through the Bot API: for a reader who is not the owner (target "bot:<chat_id>")."""
    token = token or os.getenv("GIG_BOT_TOKEN", "").strip()
    if not token:
        raise RuntimeError("GIG_BOT_TOKEN не задан")
    data = urllib.parse.urlencode({
        "chat_id": chat_id, "text": body[:BOT_MAX_CHARS], "disable_web_page_preview": "true",
    }).encode("utf-8")
    request = urllib.request.Request(f"https://api.telegram.org/bot{token}/sendMessage", data=data)
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        # The URL holds the token: report the status only.
        raise RuntimeError(f"Bot API HTTP {exc.code}") from None
    if not payload.get("ok"):
        raise RuntimeError(f"Bot API: {payload.get('description', 'error')}")


async def send(client, target: str, body: str) -> None:
    if target.startswith(BOT_PREFIX):
        await asyncio.to_thread(send_via_bot, target[len(BOT_PREFIX):].strip(), body)
    else:
        await client.send_message(target, body, link_preview=False)


async def drain_outbox(client, storage: Storage, *, limit: int = 20) -> int:
    sent = 0
    for row in storage.outbox_due(limit):
        try:
            await send(client, row["target"], row["body"])
        except FloodWaitError as exc:
            storage.outbox_defer(
                row["id"], delay_seconds=exc.seconds + 1,
                error=f"FloodWait:{exc.seconds}", count_attempt=False,
            )
            LOGGER.warning("Отправка отложена: Telegram просит ждать %s с", exc.seconds)
            return sent
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            attempts = int(row["attempts"]) + 1
            give_up = attempts >= MAX_ATTEMPTS
            storage.outbox_defer(
                row["id"], delay_seconds=backoff_seconds(attempts),
                error=f"{type(exc).__name__}: {exc}", give_up=give_up,
            )
            log = LOGGER.error if give_up else LOGGER.warning
            log("Сообщение %s не отправлено, попытка %s: %s", row["id"], attempts, exc)
            continue
        storage.outbox_mark_sent(row["id"])
        sent += 1
        await asyncio.sleep(1)
    return sent


async def outbox_loop(client, storages: list[Storage], *, poll_seconds: float = 5.0) -> None:
    while True:
        for storage in storages:
            try:
                await drain_outbox(client, storage)
            except asyncio.CancelledError:
                raise
            except Exception:
                LOGGER.exception("Ошибка очереди отправки")
        await asyncio.sleep(poll_seconds)
