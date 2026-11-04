#!/usr/bin/env python3
"""Merge the bot's queued own messages into the diary, ideas and tasks notes.

Runs on the computer that owns the vault. The server folder is send-only and holds
only Telegram/, so this is the one place that edits Месяца and 00 Inbox.
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from capture import KINDS, QUEUE_DIR, append_line, format_line, read_queue_entry, target_path  # noqa: E402

# Buttons under the bot reply may still change an entry shortly after it was sent.
MIN_AGE = timedelta(minutes=10)


def merge(vault: Path, ledger: Path, now: datetime, min_age: timedelta = MIN_AGE) -> dict:
    done = set(ledger.read_text(encoding="utf-8").split()) if ledger.exists() else set()
    counts = {"merged": 0, "waiting": 0, "skipped": 0, "broken": 0}
    queue = vault / QUEUE_DIR
    for path in sorted(queue.glob("*.json")) if queue.exists() else []:
        try:
            entry = read_queue_entry(path)
            created = datetime.fromisoformat(entry["created"])
            kind, text, entry_id = entry["kind"], entry["text"], entry["id"]
        except (OSError, ValueError, KeyError):
            counts["broken"] += 1
            continue
        if now - created < min_age:
            counts["waiting"] += 1
            continue
        if entry_id in done or kind not in KINDS:
            counts["skipped"] += 1
        else:
            target = target_path(vault, kind, text, created)
            append_line(target, kind, format_line(kind, text, created), created)
            ledger.parent.mkdir(parents=True, exist_ok=True)
            with ledger.open("a", encoding="utf-8") as handle:
                handle.write(entry_id + "\n")
            done.add(entry_id)
            counts["merged"] += 1
        path.unlink()
    return counts


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--vault", type=Path, required=True)
    parser.add_argument("--ledger", type=Path, required=True)
    args = parser.parse_args()
    counts = merge(args.vault, args.ledger, datetime.now(timezone(timedelta(hours=3))))
    print(" ".join(f"{key}={value}" for key, value in counts.items()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
