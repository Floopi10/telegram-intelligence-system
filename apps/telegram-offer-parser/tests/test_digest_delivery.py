from __future__ import annotations

import asyncio
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch

from offer_parser.digest import daily_digest_loop, send_daily_digest_once
from offer_parser.storage import Storage


NOW = datetime(2026, 10, 25, 18, 0, tzinfo=timezone.utc)


class FakeClient:
    def __init__(self, *, fail: bool = False):
        self.fail = fail
        self.messages: list[str] = []

    async def send_message(self, target, message, **kwargs):
        if self.fail:
            raise ConnectionError("temporary Telegram failure")
        self.messages.append(message)


class DigestDeliveryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.storage = Storage(Path(self.temp.name) / "knowledge.db")

    async def asyncTearDown(self):
        self.storage.close()
        self.temp.cleanup()

    async def test_success_marks_day_only_after_send(self):
        client = FakeClient()
        sent = await send_daily_digest_once(
            client,
            self.storage,
            target="me",
            now_utc=NOW,
        )
        self.assertTrue(sent)
        self.assertEqual(len(client.messages), 1)
        self.assertTrue(self.storage.digest_was_sent("2026-10-25"))

    async def test_failure_is_not_marked_as_sent(self):
        with self.assertRaises(ConnectionError):
            await send_daily_digest_once(
                FakeClient(fail=True),
                self.storage,
                target="me",
                now_utc=NOW,
            )
        self.assertFalse(self.storage.digest_was_sent("2026-10-25"))

    async def test_duplicate_is_skipped_unless_forced(self):
        self.storage.mark_digest_sent("2026-10-25")
        client = FakeClient()
        self.assertFalse(await send_daily_digest_once(
            client, self.storage, target="me", now_utc=NOW,
        ))
        self.assertTrue(await send_daily_digest_once(
            client, self.storage, target="me", now_utc=NOW, force=True,
        ))
        self.assertEqual(len(client.messages), 1)

    async def test_loop_retries_after_temporary_delivery_failure(self):
        delivery = AsyncMock(side_effect=[ConnectionError("temporary"), asyncio.CancelledError()])
        sleeper = AsyncMock()
        with patch("offer_parser.digest.send_daily_digest_once", delivery), patch(
            "offer_parser.digest.asyncio.sleep", sleeper
        ):
            with self.assertRaises(asyncio.CancelledError):
                await daily_digest_loop(
                    FakeClient(), self.storage, target="me", hour_msk=0
                )
        self.assertEqual(delivery.await_count, 2)
        sleeper.assert_any_await(300)


if __name__ == "__main__":
    unittest.main()
