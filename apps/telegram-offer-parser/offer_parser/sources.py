from __future__ import annotations

import csv
import unicodedata
from pathlib import Path


def normalize_title(value: str) -> str:
    value = unicodedata.normalize("NFKC", value)
    value = "".join(char for char in value if unicodedata.category(char) != "Cf")
    return " ".join(value.casefold().split())


def _read(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    if not path.exists():
        return [], []
    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        return list(reader.fieldnames or []), list(reader)


def _write(path: Path, fields: list[str], rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def add_knowledge_source(chats_file: Path, knowledge_file: Path, title: str) -> tuple[int, str]:
    target = normalize_title(title)
    chat_fields, chats = _read(chats_file)
    matches = [row for row in chats if normalize_title(row.get("title", "")) == target]
    ids = {row.get("chat_id") or row.get("id") for row in matches}
    ids.discard(None)
    if not matches:
        raise ValueError(f"Чат не найден в {chats_file}: {title}. Сначала выполни scan-chats")
    if len(ids) != 1:
        raise ValueError(f"Найдено несколько разных чатов с названием {title}; нужен chat_id")
    source = matches[0].copy()
    chat_id = int(source.get("chat_id") or source.get("id"))
    source["enabled"] = "1"

    knowledge_fields, knowledge = _read(knowledge_file)
    fields = knowledge_fields or chat_fields
    for field in source:
        if field not in fields:
            fields.append(field)
    updated = False
    for row in knowledge:
        current_id = row.get("chat_id") or row.get("id")
        if str(current_id) == str(chat_id):
            row.update(source)
            row["enabled"] = "1"
            updated = True
    if not updated:
        knowledge.append(source)
    knowledge.sort(key=lambda row: normalize_title(row.get("title", "")))
    _write(knowledge_file, fields, knowledge)
    return chat_id, source.get("title", title)
