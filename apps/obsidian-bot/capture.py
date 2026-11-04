"""Route the owner's own short messages into the vault notes they already keep by hand."""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

MONTHS = (
    "Январь", "Февраль", "Март", "Апрель", "Май", "Июнь",
    "Июль", "Август", "Сентябрь", "Октябрь", "Ноябрь", "Декабрь",
)
KINDS = ("log", "idea", "task")
MAX_WORDS_FOR_LOG = 20
MAX_CHARS = 400

IDEA_PREFIX = re.compile(r"^(?:💡|идея|идеи|idea)\s*[:\-—]?\s*", re.IGNORECASE)
TASK_PREFIX = re.compile(r"^(?:✅|☑️|задача|todo|туду|to do)\s*[:\-—]?\s*", re.IGNORECASE)
LOG_PREFIX = re.compile(r"^(?:📅|дневник|лог)\s*[:\-—]\s*", re.IGNORECASE)
IDEA_PHRASES = re.compile(
    r"^(?:хочу сделать|можно сделать|можно бы|было бы круто|а что если|придумал|мысль)\b", re.IGNORECASE
)
TASK_START = re.compile(r"^(?:надо|нужно|не забыть|напомни)\b|^[а-яё]+(?:ть|ться|чь)\b", re.IGNORECASE)
IDEA_NEAR_START = re.compile(r"^(?:\S+\s+){1,2}(?:идея|идеи)\s*[:\-—]", re.IGNORECASE)
# Words that end like an infinitive but open diary lines: "опять", "ебать", numbers.
NOT_INFINITIVES = {
    "опять", "ебать", "блять", "бля", "кровать", "мать", "тетрадь", "пять", "шесть", "девять",
    "десять", "двадцать", "тридцать", "сорокать", "знать",
}
DATE_SUFFIX = re.compile(r"\s(\d{1,2}\.\d{1,2})\s*$")
URL_RE = re.compile(r"https?://|t\.me/|www\.", re.IGNORECASE)


@dataclass(frozen=True)
class Capture:
    kind: str
    text: str


def classify(text: str) -> Optional[Capture]:
    """Return a capture for a short own message, or None to keep the regular note flow."""
    clean = " ".join((text or "").split())
    if not clean or len(clean) > MAX_CHARS or URL_RE.search(clean) or text.count("\n") > 2:
        return None
    for pattern, kind in ((IDEA_PREFIX, "idea"), (TASK_PREFIX, "task"), (LOG_PREFIX, "log")):
        match = pattern.match(clean)
        if match and clean[match.end():].strip():
            return Capture(kind, clean[match.end():].strip())
    if IDEA_PHRASES.match(clean) or IDEA_NEAR_START.match(clean):
        return Capture("idea", clean)
    first_word = clean.split()[0].casefold().strip(",.!?")
    if TASK_START.match(clean) and first_word not in NOT_INFINITIVES:
        return Capture("task", clean)
    if len(clean.split()) <= MAX_WORDS_FOR_LOG:
        return Capture("log", clean)
    return None


def target_path(vault: Path, kind: str, text: str, now: datetime) -> Path:
    if kind == "log":
        return vault / "Месяца" / f"{MONTHS[now.month - 1]}.md"
    if kind == "idea":
        firsay = vault / "02 Projects" / "Мой AI проект" / "Разное (мысли, идеи).md"
        if "firsay" in text.casefold() and firsay.exists():
            return firsay
        return vault / "00 Inbox" / "Идеи.md"
    return vault / "00 Inbox" / "Задачи.md"


def format_line(kind: str, text: str, now: datetime) -> str:
    if kind == "log":
        return text if DATE_SUFFIX.search(text) else f"{text} {now:%d.%m}"
    if kind == "idea":
        return f"- {text} ({now:%d.%m})"
    return f"- [ ] {text} ➕ {now:%Y-%m-%d}"


def _last_log_date(lines: list[str]) -> Optional[str]:
    for line in reversed(lines):
        match = DATE_SUFFIX.search(line)
        if match:
            return match.group(1)
        if line.strip():
            return None
    return None


def append_line(path: Path, kind: str, line: str, now: datetime) -> None:
    """Append without rewriting the file, so edits made elsewhere in the note survive."""
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    prefix = ""
    if existing and not existing.endswith("\n"):
        prefix = "\n"
    if kind == "log" and existing.strip():
        last = _last_log_date(existing.splitlines())
        # The diary groups each day's lines, with an empty line between days.
        if last and last != f"{now:%d.%m}" and not existing.endswith("\n\n"):
            prefix += "\n"
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(f"{prefix}{line}\n")


def remove_line(path: Path, line: str) -> bool:
    """Remove the last occurrence of a line this bot appended."""
    if not path.exists():
        return False
    lines = path.read_text(encoding="utf-8").split("\n")
    for index in range(len(lines) - 1, -1, -1):
        if lines[index] == line:
            del lines[index]
            path.write_text("\n".join(lines), encoding="utf-8", newline="\n")
            return True
    return False
