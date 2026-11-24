"""Writer gigs from X search: "looking for a ghostwriter / reply guy".

The X collector (a logged-in browser profile on its own VM) runs each query on the "Latest"
tab; every post goes through the same gig filter as Telegram messages. Slow on purpose:
one account, a handful of queries, a pause between them.

Env (parser .env; X_COLLECTOR_URL / X_COLLECTOR_TOKEN are the collector's own):
  GIG_X_SEARCH        1 to turn it on (also needs GIG_NOTIFY_TARGET), default 0
  GIG_X_INTERVAL_MIN  minutes between rounds, default 180
  GIG_X_PAUSE_S       seconds between queries, default 45
  GIG_X_QUERIES       own queries separated by "||" instead of the default list
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import urllib.error
import urllib.request
from datetime import datetime, timezone

from .firsay_feed import FeedEvent, _parse_date
from .gig_pipeline import gig_target, process_gig

LOGGER = logging.getLogger(__name__)
X_CHAT_ID = -8_100_000_000_000  # one synthetic "chat" for X posts; the post id is the message id
ROLES_EN = '("ghostwriter" OR "ghost writer" OR "reply guy" OR "reply guys" OR "thread writer")'
ROLES_RU = '("гострайтер" OR "гострайтера" OR "реплай гай" OR "реплай гая" OR "реплай гаев" OR "реплай-гая")'
DEFAULT_QUERIES = (
    f'"looking for" {ROLES_EN} -filter:replies',
    f'("hiring" OR "need a" OR "we need" OR "wanted") {ROLES_EN} -filter:replies',
    '("pay per reply" OR "paid replies" OR "paid reply guys" OR "per reply") -filter:replies',
    f'("ищу" OR "ищем" OR "нужен" OR "нужны" OR "требуется") {ROLES_RU}',
)


def enabled() -> bool:
    return os.getenv("GIG_X_SEARCH", "0").strip() == "1" and bool(gig_target())


def queries() -> tuple[str, ...]:
    own = tuple(q.strip() for q in os.getenv("GIG_X_QUERIES", "").split("||") if q.strip())
    return own or DEFAULT_QUERIES


def _number(name: str, default: int, low: int) -> int:
    try:
        return max(low, int(os.getenv(name, str(default))))
    except ValueError:
        return default


def fetch(base_url: str, token: str, query: str, timeout: int = 150) -> list[dict]:
    request = urllib.request.Request(
        base_url.rstrip("/") + "/search",
        data=json.dumps({"query": query}).encode("utf-8"),
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        payload = json.loads(response.read().decode("utf-8"))
    posts = payload.get("posts") if isinstance(payload, dict) else None
    if not isinstance(posts, list):
        raise RuntimeError(f"X collector search error: {str(payload)[:200]}")
    return posts


def to_event(post: dict) -> tuple[FeedEvent, str, str] | None:
    """(event, source title, url) for one collector post; broken items are skipped."""
    text = str(post.get("text") or "").strip()
    author = str(post.get("author") or "").strip()
    try:
        post_id = int(post.get("id"))
    except (TypeError, ValueError):
        return None
    if not text or not author:
        return None
    published = post.get("published_at")
    event = FeedEvent(
        chat_id=X_CHAT_ID, message_id=post_id, text=text,
        date=_parse_date(published) if published else datetime.now(timezone.utc),
        username=None, sender=author,
    )
    return event, f"X · @{author}", f"https://x.com/{author}/status/{post_id}"


async def search_once(pipeline, client, *, fetch_posts=fetch, pause_s: float = 45.0) -> tuple[int, int]:
    """One round over all queries. Returns (posts seen, gigs found)."""
    seen = found = 0
    for index, query in enumerate(queries()):
        if index:
            await asyncio.sleep(pause_s)
        # The collector drives one browser: wait for the metrics collector's turn.
        async with client._gate:
            posts = await asyncio.to_thread(fetch_posts, client.base_url, client.token, query)
        for post in posts:
            converted = to_event(post)
            if converted is None:
                continue
            event, title, url = converted
            seen += 1
            found += bool(await process_gig(pipeline, event, event.raw_text, title, url=url))
    return seen, found


async def x_gig_search_loop(pipeline, client) -> None:
    interval = _number("GIG_X_INTERVAL_MIN", 180, 30) * 60
    pause = float(_number("GIG_X_PAUSE_S", 45, 5))
    while True:
        try:
            seen, found = await search_once(pipeline, client, pause_s=pause)
            LOGGER.info("X gig search: постов %s, вакансий %s", seen, found)
        except asyncio.CancelledError:
            raise
        except urllib.error.HTTPError as exc:
            LOGGER.warning("X gig search HTTP %s", exc.code)
        except Exception as exc:
            LOGGER.warning("X gig search не удался: %s", type(exc).__name__)
        await asyncio.sleep(interval)
