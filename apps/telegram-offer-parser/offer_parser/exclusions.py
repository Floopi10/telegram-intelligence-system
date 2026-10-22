from __future__ import annotations

import csv
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .chats import FIELDS, SelectedChat, write_chats


EXCLUSION_FIELDS = ("chat_id", "title", "requested_name", "created_at")


@dataclass(frozen=True)
class ExclusionResult:
    requested: int
    matched_names: int
    matched_chats: int
    duplicate_names: int
    already_excluded: int
    not_found: tuple[str, ...]
    report_path: Path


def normalize_title(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value)
    normalized = "".join(
        char
        for char in normalized
        if unicodedata.category(char) != "Cf" and char not in {"\ufe0e", "\ufe0f"}
    )
    return " ".join(normalized.casefold().split())


def load_excluded_ids(path: Path) -> set[int]:
    if not path.exists():
        return set()
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        return {int(row["chat_id"]) for row in csv.DictReader(file) if row.get("chat_id")}


def filter_excluded(chats: list[SelectedChat], path: Path) -> list[SelectedChat]:
    excluded = load_excluded_ids(path)
    return [chat for chat in chats if chat.chat_id not in excluded]


def apply_exclusions_to_chats(chats_path: Path, exclusions_path: Path) -> int:
    excluded = load_excluded_ids(exclusions_path)
    if not excluded or not chats_path.exists():
        return 0
    with chats_path.open("r", encoding="utf-8-sig", newline="") as file:
        rows = list(csv.DictReader(file))
    changed = 0
    for row in rows:
        if int(row["chat_id"]) in excluded and row.get("enabled") != "0":
            row["enabled"] = "0"
            changed += 1
    write_chats(chats_path, [{key: row.get(key, "") for key in FIELDS} for row in rows])
    return changed


def exclude_by_names(
    chats_path: Path,
    exclusions_path: Path,
    names_path: Path,
    report_path: Path,
) -> ExclusionResult:
    if not chats_path.exists():
        raise FileNotFoundError(chats_path)
    if not names_path.exists():
        raise FileNotFoundError(names_path)

    requested_raw = [line.strip() for line in names_path.read_text(encoding="utf-8-sig").splitlines() if line.strip()]
    unique: dict[str, str] = {}
    for name in requested_raw:
        unique.setdefault(normalize_title(name), name)

    with chats_path.open("r", encoding="utf-8-sig", newline="") as file:
        chat_rows = list(csv.DictReader(file))
    by_title: dict[str, list[dict[str, str]]] = {}
    for row in chat_rows:
        by_title.setdefault(normalize_title(row.get("title", "")), []).append(row)

    existing_rows: dict[int, dict[str, str]] = {}
    if exclusions_path.exists():
        with exclusions_path.open("r", encoding="utf-8-sig", newline="") as file:
            existing_rows = {
                int(row["chat_id"]): {key: row.get(key, "") for key in EXCLUSION_FIELDS}
                for row in csv.DictReader(file)
                if row.get("chat_id")
            }

    matched_names = 0
    matched_ids: set[int] = set()
    already = 0
    not_found: list[str] = []
    matches_for_report: list[tuple[str, list[dict[str, str]]]] = []
    created_at = datetime.now(timezone.utc).isoformat()
    for normalized, requested_name in unique.items():
        matches = by_title.get(normalized, [])
        if not matches:
            not_found.append(requested_name)
            continue
        matched_names += 1
        matches_for_report.append((requested_name, matches))
        for row in matches:
            chat_id = int(row["chat_id"])
            matched_ids.add(chat_id)
            if chat_id in existing_rows:
                already += 1
                continue
            existing_rows[chat_id] = {
                "chat_id": str(chat_id),
                "title": row.get("title", ""),
                "requested_name": requested_name,
                "created_at": created_at,
            }

    exclusions_path.parent.mkdir(parents=True, exist_ok=True)
    with exclusions_path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=EXCLUSION_FIELDS)
        writer.writeheader()
        writer.writerows(existing_rows[key] for key in sorted(existing_rows))
    apply_exclusions_to_chats(chats_path, exclusions_path)

    report_path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Knowledge exclusions",
        "",
        f"- Строк в запросе: **{len(requested_raw)}**",
        f"- Уникальных названий: **{len(unique)}**",
        f"- Найдено названий: **{matched_names}**",
        f"- Исключено chat_id: **{len(matched_ids)}**",
        f"- Уже были исключены: **{already}**",
        f"- Не найдено: **{len(not_found)}**",
        "",
        "## Найдено",
        "",
    ]
    for requested_name, matches in matches_for_report:
        for row in matches:
            lines.append(f"- `{row['chat_id']}` — {row.get('title', '')} (запрос: {requested_name})")
    lines.extend(["", "## Не найдено", ""])
    lines.extend(f"- {name}" for name in not_found)
    report_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")

    return ExclusionResult(
        requested=len(requested_raw),
        matched_names=matched_names,
        matched_chats=len(matched_ids),
        duplicate_names=len(requested_raw) - len(unique),
        already_excluded=already,
        not_found=tuple(not_found),
        report_path=report_path.resolve(),
    )


def include_ids(chats_path: Path, exclusions_path: Path, chat_ids: list[int]) -> int:
    if not exclusions_path.exists():
        return 0
    with exclusions_path.open("r", encoding="utf-8-sig", newline="") as file:
        rows = list(csv.DictReader(file))
    target = set(chat_ids)
    kept = [row for row in rows if int(row["chat_id"]) not in target]
    removed = len(rows) - len(kept)
    with exclusions_path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=EXCLUSION_FIELDS)
        writer.writeheader()
        writer.writerows(kept)
    if chats_path.exists():
        with chats_path.open("r", encoding="utf-8-sig", newline="") as file:
            chat_rows = list(csv.DictReader(file))
        for row in chat_rows:
            if int(row["chat_id"]) in target:
                row["enabled"] = "1"
        write_chats(chats_path, [{key: row.get(key, "") for key in FIELDS} for row in chat_rows])
    return removed
