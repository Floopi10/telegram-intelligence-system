#!/usr/bin/env python3
import argparse
import sqlite3
from pathlib import Path

from offer_parser.config import Settings


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--destination", type=Path, required=True)
    args = parser.parse_args()

    settings = Settings.load()
    source = Path(settings.session_path)
    if source.suffix != ".session":
        source = source.with_suffix(".session")
    if not source.exists():
        raise SystemExit(f"Telegram session not found: {source}")

    destination = args.destination
    if destination.suffix != ".session":
        destination = destination.with_suffix(".session")
    destination.parent.mkdir(parents=True, exist_ok=True)

    with sqlite3.connect(source) as src, sqlite3.connect(destination) as dst:
        src.backup(dst)

    destination.chmod(0o600)
    print(f"SESSION_COPY={destination}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
