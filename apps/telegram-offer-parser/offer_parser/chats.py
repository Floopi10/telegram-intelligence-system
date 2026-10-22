from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path


FIELDS = ("enabled", "chat_id", "title", "username", "kind")


@dataclass(frozen=True)
class SelectedChat:
    chat_id: int
    title: str
    username: str


def load_existing_selection(path: Path) -> set[int]:
    if not path.exists():
        return set()
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        return {
            int(row["chat_id"])
            for row in csv.DictReader(file)
            if row.get("enabled", "").strip().casefold() in {"1", "true", "yes", "да"}
        }


def write_chats(path: Path, rows: list[dict[str, str]]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def load_selected(path: Path) -> list[SelectedChat]:
    if not path.exists():
        raise FileNotFoundError(f"Сначала выполни scan-chats: файл {path} не найден")
    selected: list[SelectedChat] = []
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        for row in csv.DictReader(file):
            if row.get("enabled", "").strip().casefold() in {"1", "true", "yes", "да"}:
                selected.append(
                    SelectedChat(
                        int(row["chat_id"]),
                        row.get("title", ""),
                        row.get("username", ""),
                    )
                )
    if not selected:
        raise ValueError(f"В {path} нет групп с enabled=1")
    return selected
