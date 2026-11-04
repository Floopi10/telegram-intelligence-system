#!/usr/bin/env python3
"""Enrich Obsidian Twitter notes with read-only metrics from x-collector."""

from __future__ import annotations

import argparse
import json
import os
import re
import stat
import sqlite3
import sys
import tempfile
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo


X_URL_RE = re.compile(
    r"https://(?:www\.)?(?:x\.com|twitter\.com)/"
    r"([A-Za-z0-9_]{1,15})/status/(\d+)"
)
METRICS_SECTION_RE = re.compile(
    r"(?ms)^## Метрики X\s*\n.*?(?=^##\s|\Z)"
)
MSK = ZoneInfo("Europe/Moscow")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--vault", type=Path, required=True)
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--limit", type=int, default=2)
    return parser.parse_args()


def load_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip()
    return values


def open_db(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS snapshots (
            url TEXT NOT NULL,
            note_path TEXT NOT NULL,
            collected_at TEXT NOT NULL,
            published_at TEXT,
            views INTEGER,
            likes INTEGER,
            reposts INTEGER,
            replies INTEGER,
            bookmarks INTEGER,
            raw_json TEXT NOT NULL,
            PRIMARY KEY (url, collected_at)
        )
        """
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_snapshots_url_time "
        "ON snapshots(url, collected_at DESC)"
    )
    connection.commit()
    return connection


def parse_count(value: object) -> int | None:
    if not isinstance(value, str) or not value.strip():
        return None
    normalized = value.replace("\u202f", "").replace(" ", "")
    match = re.search(r"([0-9][0-9,.]*)(?:([KMB])(?![A-Za-z]))?", normalized, re.I)
    if not match:
        return None
    number = match.group(1)
    suffix = (match.group(2) or "").upper()
    if "," in number and "." not in number:
        tail = number.rsplit(",", 1)[-1]
        number = number.replace(",", "." if suffix and len(tail) <= 2 else "")
    else:
        number = number.replace(",", "")
    multiplier = {"": 1, "K": 1_000, "M": 1_000_000, "B": 1_000_000_000}[suffix]
    try:
        return int(float(number) * multiplier)
    except ValueError:
        return None


def parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def latest_snapshot(db: sqlite3.Connection, url: str) -> sqlite3.Row | None:
    db.row_factory = sqlite3.Row
    return db.execute(
        "SELECT * FROM snapshots WHERE url=? ORDER BY collected_at DESC LIMIT 1",
        (url,),
    ).fetchone()


def is_due(snapshot: sqlite3.Row | None, now: datetime) -> bool:
    if snapshot is None:
        return True
    collected = parse_iso(snapshot["collected_at"])
    published = parse_iso(snapshot["published_at"])
    if collected is None:
        return True
    if published is None:
        return now - collected >= timedelta(days=7)
    age = now - published
    if age <= timedelta(days=2):
        interval = timedelta(hours=2)
    elif age <= timedelta(days=7):
        interval = timedelta(hours=12)
    else:
        return False
    return now - collected >= interval


def request_metrics(base_url: str, token: str, url: str) -> dict[str, object]:
    payload = json.dumps({"url": url}).encode("utf-8")
    request = urllib.request.Request(
        base_url.rstrip("/") + "/collect",
        data=payload,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=150) as response:
        return json.loads(response.read().decode("utf-8"))


def fmt(value: int | None) -> str:
    return "недоступно" if value is None else f"{value:,}".replace(",", " ")


def delta(current: int | None, previous: object) -> str:
    if current is None or previous is None:
        return ""
    difference = current - int(previous)
    return f" (Δ {difference:+,})".replace(",", " ")


def build_section(
    metrics: dict[str, int | None],
    previous: sqlite3.Row | None,
    collected_at: datetime,
) -> str:
    lines = ["## Метрики X"]
    labels = (
        ("Просмотры", "views"),
        ("Лайки", "likes"),
        ("Репосты", "reposts"),
        ("Ответы", "replies"),
        ("Закладки", "bookmarks"),
    )
    for label, key in labels:
        old = previous[key] if previous is not None else None
        lines.append(f"- {label}: **{fmt(metrics[key])}**{delta(metrics[key], old)}")

    views = metrics["views"]
    reactions = sum(metrics[key] or 0 for key in ("likes", "reposts", "replies"))
    if views and views > 0:
        lines.append(f"- ER по видимым реакциям: **{reactions / views * 100:.2f}%**")

    if previous is not None and views is not None and previous["views"] is not None:
        previous_time = parse_iso(previous["collected_at"])
        if previous_time is not None:
            hours = max((collected_at - previous_time).total_seconds() / 3600, 0.01)
            velocity = (views - int(previous["views"])) / hours
            lines.append(f"- Скорость просмотров: **{velocity:,.0f}/час**".replace(",", " "))

    local_time = collected_at.astimezone(MSK).strftime("%d.%m.%Y %H:%M MSK")
    lines.append(f"- Снято: `{local_time}`")
    return "\n".join(lines) + "\n"


def update_note(path: Path, original: str, section: str, original_mtime: int) -> bool:
    if path.stat().st_mtime_ns != original_mtime:
        return False
    if METRICS_SECTION_RE.search(original):
        updated = METRICS_SECTION_RE.sub(section.rstrip() + "\n\n", original, count=1)
    elif "\n## Ключевые мысли" in original:
        updated = original.replace(
            "\n## Ключевые мысли",
            "\n" + section + "\n## Ключевые мысли",
            1,
        )
    else:
        updated = original.rstrip() + "\n\n" + section
    if updated == original:
        return True
    fd, temporary = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as output:
            output.write(updated)
        os.chmod(temporary, stat.S_IMODE(path.stat().st_mode))
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return True


def main() -> int:
    args = parse_args()
    config = load_env(args.config)
    base_url = config.get("X_COLLECTOR_URL", "")
    token = config.get("X_COLLECTOR_TOKEN", "")
    if not base_url or not token:
        print("ERROR=missing_collector_config", file=sys.stderr)
        return 2

    db = open_db(args.db)
    now = datetime.now(timezone.utc)
    candidates: list[tuple[float, Path, str, str, int, sqlite3.Row | None]] = []

    for path in args.vault.rglob("*.md"):
        try:
            stat = path.stat()
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        match = X_URL_RE.search(text)
        if not match:
            continue
        url = f"https://x.com/{match.group(1)}/status/{match.group(2)}"
        snapshot = latest_snapshot(db, url)
        if is_due(snapshot, now):
            candidates.append((-stat.st_mtime, path, text, url, stat.st_mtime_ns, snapshot))

    candidates.sort(key=lambda item: item[0])
    processed = updated_count = errors = 0

    for _, path, text, url, mtime, previous in candidates[: max(args.limit, 0)]:
        try:
            raw = request_metrics(base_url, token, url)
            collected = parse_iso(str(raw.get("collected_at") or "")) or datetime.now(timezone.utc)
            metrics = {
                key: parse_count(raw.get(key))
                for key in ("views", "likes", "reposts", "replies", "bookmarks")
            }
            section = build_section(metrics, previous, collected)
            if not update_note(path, text, section, mtime):
                print(f"SKIPPED_CHANGED={path}")
                continue
            db.execute(
                """
                INSERT INTO snapshots(
                    url, note_path, collected_at, published_at,
                    views, likes, reposts, replies, bookmarks, raw_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    url,
                    str(path),
                    collected.isoformat(),
                    raw.get("published_at"),
                    metrics["views"],
                    metrics["likes"],
                    metrics["reposts"],
                    metrics["replies"],
                    metrics["bookmarks"],
                    json.dumps(raw, ensure_ascii=False),
                ),
            )
            db.commit()
            processed += 1
            updated_count += 1
            print(f"UPDATED={path.name}")
        except (OSError, ValueError, urllib.error.URLError, json.JSONDecodeError, sqlite3.Error) as error:
            errors += 1
            print(f"ERROR={path.name}: {type(error).__name__}: {error}", file=sys.stderr)

    print(f"CANDIDATES={len(candidates)}")
    print(f"PROCESSED={processed}")
    print(f"UPDATED_TOTAL={updated_count}")
    print(f"ERRORS={errors}")
    return 0 if errors == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
