from __future__ import annotations

import asyncio
import logging

from telethon.errors import FloodWaitError

from .storage import Storage


LOGGER = logging.getLogger(__name__)
MAX_ATTEMPTS = 20
MAX_BACKOFF_SECONDS = 3600


def backoff_seconds(attempts: int) -> int:
    return min(30 * 2 ** min(attempts, 10), MAX_BACKOFF_SECONDS)


async def drain_outbox(client, storage: Storage, *, limit: int = 20) -> int:
    sent = 0
    for row in storage.outbox_due(limit):
        try:
            await client.send_message(row["target"], row["body"], link_preview=False)
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
