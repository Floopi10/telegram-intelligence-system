"""Pull the text and public metrics of an X post through the read-only collector service."""
from __future__ import annotations

import asyncio
import json
import logging
import re
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional

from dotenv import dotenv_values

log = logging.getLogger(__name__)

STATUS_RE = re.compile(
    r"https?://(?:www\.|mobile\.)?(?:x|twitter)\.com/([A-Za-z0-9_]{1,15})/status/(\d+)",
    re.IGNORECASE,
)
METRIC_LABELS = (
    ("views", "Просмотры"),
    ("likes", "Лайки"),
    ("reposts", "Репосты"),
    ("replies", "Ответы"),
    ("bookmarks", "Закладки"),
)


@dataclass(frozen=True)
class XPost:
    url: str
    author: str
    text: str
    published_at: str
    metrics: Dict[str, Optional[int]]


def first_status_url(text: str) -> Optional[str]:
    match = STATUS_RE.search(text or "")
    if not match or match.group(1).lower() == "i":
        return None
    return f"https://x.com/{match.group(1)}/status/{match.group(2)}"


def is_bare_link(text: str) -> bool:
    """True when the message is a link with at most a couple of words around it."""
    without_links = re.sub(r"https?://\S+", " ", text or "")
    return len(re.findall(r"\w+", without_links)) < 3


def parse_count(value: object) -> Optional[int]:
    if value is None:
        return None
    text = str(value).replace(" ", "").replace("\xa0", "").replace(" ", "")
    match = re.search(r"([0-9][0-9,.]*)([KMB]?)", text, re.IGNORECASE)
    if not match:
        return None
    number, suffix = match.group(1), match.group(2).upper()
    if "," in number and "." not in number:
        tail = number.rsplit(",", 1)[-1]
        number = number.replace(",", "." if suffix and len(tail) <= 2 else "")
    else:
        number = number.replace(",", "")
    try:
        return int(float(number) * {"": 1, "K": 1_000, "M": 1_000_000, "B": 1_000_000_000}[suffix])
    except ValueError:
        return None


def enriched_text(original: str, post: XPost) -> str:
    """Original message plus the post itself, in the line format the note builder already parses."""
    lines = [original.strip(), "", f"X URL: {post.url}", f"Автор: @{post.author}"]
    if post.published_at:
        lines.append(f"Опубликован: {post.published_at}")
    lines.extend(["", "Текст поста:", post.text.strip(), ""])
    lines.extend(
        f"{label}: {post.metrics[key]}" for key, label in METRIC_LABELS if post.metrics.get(key) is not None
    )
    return "\n".join(lines).strip()


class XCollector:
    def __init__(self, env_file: str, timeout: int = 150):
        values = dotenv_values(Path(env_file)) if env_file and Path(env_file).exists() else {}
        self.base_url = str(values.get("X_COLLECTOR_URL") or "").strip().rstrip("/")
        self.token = str(values.get("X_COLLECTOR_TOKEN") or "").strip()
        self.timeout = timeout

    @property
    def enabled(self) -> bool:
        return bool(self.base_url and self.token)

    async def fetch(self, url: str) -> Optional[XPost]:
        if not self.enabled:
            return None
        try:
            raw = await asyncio.to_thread(self._request, url)
        except Exception as exc:
            log.warning("X-пост не получен %s: %s", url, type(exc).__name__)
            return None
        text = str(raw.get("text") or "").strip()
        if not text:
            return None
        return XPost(
            url=url,
            author=str(raw.get("author") or url.split("/")[3]),
            text=text[:6000],
            published_at=str(raw.get("published_at") or ""),
            metrics={key: parse_count(raw.get(key)) for key, _ in METRIC_LABELS},
        )

    def _request(self, url: str) -> dict:
        request = urllib.request.Request(
            self.base_url + "/collect",
            data=json.dumps({"url": url}).encode("utf-8"),
            headers={"Authorization": f"Bearer {self.token}", "Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
        if not isinstance(payload, dict) or payload.get("error"):
            raise RuntimeError("collector_error")
        return payload
