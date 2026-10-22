#!/usr/bin/env python3
"""Read-only HTTP wrapper around an authenticated Chromium profile for X posts."""

from __future__ import annotations

import hmac
import json
import os
import re
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright


TOKEN = os.environ["X_COLLECTOR_TOKEN"]
BIND = os.environ.get("X_COLLECTOR_BIND", "127.0.0.1")
PORT = int(os.environ.get("X_COLLECTOR_PORT", "8765"))
PROFILE = Path(os.environ.get("X_COLLECTOR_PROFILE", "./chrome-profile")).resolve()
CHROME = os.environ.get("X_COLLECTOR_CHROME", "/usr/bin/google-chrome")
LOCK = threading.Lock()
URL_PATTERN = re.compile(
    r"^https://(?:www\.)?(?:x\.com|twitter\.com)/([A-Za-z0-9_]{1,15})/status/(\d+)"
)
BLOCKED = (
    "CreateTweet", "DeleteTweet", "FavoriteTweet", "UnfavoriteTweet",
    "CreateRetweet", "DeleteRetweet", "CreateBookmark", "DeleteBookmark",
    "CreateFriendship", "DestroyFriendship", "SendMessage",
)


def first_text(locator) -> str | None:
    if not locator.count():
        return None
    value = locator.first.inner_text(timeout=5_000).strip()
    return value or None


def collect(url: str) -> dict[str, object]:
    match = URL_PATTERN.match(url)
    if not match:
        raise ValueError("Only direct x.com/twitter.com status URLs are supported")
    canonical = f"https://x.com/{match.group(1)}/status/{match.group(2)}"
    PROFILE.mkdir(parents=True, exist_ok=True)
    with LOCK, sync_playwright() as playwright:
        context = playwright.chromium.launch_persistent_context(
            str(PROFILE), executable_path=CHROME, headless=True,
            args=["--disable-gpu", "--disable-dev-shm-usage", "--disable-extensions", "--no-first-run"],
        )
        try:
            page = context.pages[0]

            def protect(route):
                if any(operation in route.request.url for operation in BLOCKED):
                    route.abort()
                else:
                    route.continue_()

            page.route("**/*", protect)
            page.goto(canonical, wait_until="domcontentloaded", timeout=60_000)
            article = page.locator('article[data-testid="tweet"]').first
            article.wait_for(timeout=60_000)
            text = first_text(article.locator('[data-testid="tweetText"]').first)
            if not text:
                raise RuntimeError("Post text was not found; profile may require login")
            time_node = article.locator("time").first
            published = time_node.get_attribute("datetime") if time_node.count() else None
            result: dict[str, object] = {
                "collected_at": datetime.now(timezone.utc).isoformat(),
                "url": canonical,
                "author": match.group(1),
                "text": text,
                "published_at": published,
            }
            selectors = {
                "replies": '[data-testid="reply"]',
                "reposts": '[data-testid="retweet"]',
                "likes": '[data-testid="like"]',
                "bookmarks": '[data-testid="bookmark"]',
                "views": 'a[href$="/analytics"]',
            }
            for key, selector in selectors.items():
                node = article.locator(selector).first
                result[key] = (
                    node.get_attribute("aria-label") or first_text(node)
                    if node.count() else None
                )
            return result
        finally:
            context.close()


class Handler(BaseHTTPRequestHandler):
    server_version = "XCollector/1.0"

    def send_json(self, status: int, payload: dict[str, object]) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def authorized(self) -> bool:
        supplied = self.headers.get("Authorization", "").removeprefix("Bearer ")
        return bool(supplied) and hmac.compare_digest(supplied, TOKEN)

    def do_GET(self) -> None:
        if urlparse(self.path).path == "/health":
            self.send_json(200, {"status": "ok"})
        else:
            self.send_json(404, {"error": "not_found"})

    def do_POST(self) -> None:
        if urlparse(self.path).path != "/collect":
            self.send_json(404, {"error": "not_found"})
            return
        if not self.authorized():
            self.send_json(401, {"error": "unauthorized"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0 or length > 16_384:
                raise ValueError("Invalid request size")
            payload = json.loads(self.rfile.read(length))
            self.send_json(200, collect(str(payload.get("url", ""))))
        except (ValueError, json.JSONDecodeError) as error:
            self.send_json(400, {"error": str(error)})
        except PlaywrightTimeoutError:
            self.send_json(504, {"error": "X page timed out"})
        except Exception as error:
            self.send_json(502, {"error": f"collector_failed: {type(error).__name__}"})

    def log_message(self, fmt: str, *args: object) -> None:
        print(f"{self.address_string()} - {fmt % args}")


if __name__ == "__main__":
    ThreadingHTTPServer((BIND, PORT), Handler).serve_forever()

