"""Read-only X search in the collector's logged-in Chromium profile (its cookies).

Opens the "Latest" tab of x.com/search for one query, scrolls a few screens and returns
the posts it saw. Nothing is posted, liked or followed: the caller passes the same request
guard the collector uses for post pages.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from urllib.parse import quote

STATUS_RE = re.compile(r"^/([A-Za-z0-9_]{1,15})/status/(\d+)")
MAX_QUERY_CHARS = 400
MAX_LIMIT = 60


class LoginRequired(RuntimeError):
    """X sent the profile to a login page or to its access check."""


def read_articles(page) -> list[dict[str, object]]:
    """Posts rendered right now. One DOM round trip: the timeline re-renders while scrolling."""
    return page.evaluate(
        """() => [...document.querySelectorAll('article[data-testid="tweet"]')].map((article) => {
            const time = article.querySelector('a[href*="/status/"] time');
            const text = article.querySelector('[data-testid="tweetText"]');
            return {
                href: time ? time.closest('a').getAttribute('href') : '',
                published_at: time ? time.getAttribute('datetime') : null,
                text: text ? text.innerText : '',
            };
        })"""
    )


def to_post(raw: dict[str, object]) -> dict[str, object] | None:
    match = STATUS_RE.match(str(raw.get("href") or ""))
    text = str(raw.get("text") or "").strip()
    if not match or not text:
        return None
    return {
        "id": match.group(2),
        "author": match.group(1),
        "url": f"https://x.com/{match.group(1)}/status/{match.group(2)}",
        "text": text,
        "published_at": raw.get("published_at"),
    }


def search_page(page, query: str, *, limit: int = 30, scrolls: int = 4) -> dict[str, object]:
    query = " ".join(str(query or "").split())
    if not query or len(query) > MAX_QUERY_CHARS:
        raise ValueError("Search query is empty or too long")
    limit = max(1, min(MAX_LIMIT, int(limit)))
    page.goto(f"https://x.com/search?q={quote(query)}&src=typed_query&f=live",
              wait_until="domcontentloaded", timeout=60_000)
    posts: dict[str, dict[str, object]] = {}
    try:
        page.locator('article[data-testid="tweet"], [data-testid="emptyState"]').first.wait_for(timeout=45_000)
    except Exception:
        if "/login" in page.url or "/account/access" in page.url or "/i/flow" in page.url:
            raise LoginRequired("X profile needs login or an access check") from None
        raise
    for _ in range(max(1, scrolls)):
        for raw in read_articles(page):
            post = to_post(raw)
            if post is not None:
                posts.setdefault(str(post["id"]), post)
        if len(posts) >= limit:
            break
        page.mouse.wheel(0, 2400)
        page.wait_for_timeout(1500)
    return {
        "collected_at": datetime.now(timezone.utc).isoformat(),
        "query": query,
        "posts": list(posts.values())[:limit],
    }
