"""Link a new note to notes that really exist in the vault."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Set

GENERIC_TAGS = {
    "telegram", "parser", "telegram-source", "twitter-intelligence", "twitter", "knowledge",
    "vacancy", "generic", "x", "ai", "web3", "crypto",
}
STOP_WORDS = {
    "telegram", "note", "заметка", "about", "with", "this", "that", "from", "your", "what",
    "when", "have", "will", "into", "как", "что", "это", "для", "или", "про", "без", "через",
    "post", "пост", "твит", "tweet", "thread", "тред",
}
MIN_SCORE = 3
MAX_LINKS = 5
HEAD_BYTES = 2048
LINKS_HEADING = "## Связи"
WIKILINK_RE = re.compile(r"\[\[([^\]|#]+)(?:[#|][^\]]*)?\]\]")


@dataclass(frozen=True)
class NoteRef:
    stem: str
    tags: frozenset
    words: frozenset


def title_words(title: str) -> Set[str]:
    return {
        word for word in re.findall(r"[a-zа-яё0-9]{4,}", title.casefold())
        if word not in STOP_WORDS and not word.isdigit()
    }


def meaningful_tags(tags: Iterable[str]) -> Set[str]:
    return {tag.strip().lstrip("#").casefold() for tag in tags if tag.strip()} - GENERIC_TAGS


def _frontmatter_value(head: str, key: str) -> str:
    match = re.search(rf"(?m)^{key}:\s*(.+)$", head)
    return match.group(1).strip() if match else ""


def _parse_title(value: str) -> str:
    if value.startswith('"'):
        try:
            return str(json.loads(value))
        except json.JSONDecodeError:
            pass
    return value.strip("\"'")


def _parse_tags(value: str) -> List[str]:
    try:
        parsed = json.loads(value)
        if isinstance(parsed, list):
            return [str(item) for item in parsed]
    except json.JSONDecodeError:
        pass
    return re.findall(r'"([^"]+)"', value)


def read_note_ref(path: Path) -> NoteRef:
    with path.open("rb") as handle:
        head = handle.read(HEAD_BYTES).decode("utf-8", errors="ignore")
    title = _parse_title(_frontmatter_value(head, "title")) or path.stem
    tags = _parse_tags(_frontmatter_value(head, "tags"))
    return NoteRef(path.stem, frozenset(meaningful_tags(tags)), frozenset(title_words(title)))


def index_notes(inbox_path: Path, attachments_folder: str, exclude: Path | None = None) -> List[NoteRef]:
    notes: List[NoteRef] = []
    if not inbox_path.exists():
        return notes
    for path in inbox_path.rglob("*.md"):
        relative = path.relative_to(inbox_path)
        if (relative.parts and relative.parts[0] == attachments_folder) or path == exclude:
            continue
        try:
            notes.append(read_note_ref(path))
        except OSError:
            continue
    return notes


def find_related(title: str, tags: Iterable[str], notes: Iterable[NoteRef]) -> List[str]:
    words, own_tags = title_words(title), meaningful_tags(tags)
    scored = []
    for note in notes:
        score = 2 * len(own_tags & note.tags) + len(words & note.words)
        if score >= MIN_SCORE:
            scored.append((score, note.stem))
    scored.sort(key=lambda item: (-item[0], item[1]))
    return list(dict.fromkeys(stem for _, stem in scored))[:MAX_LINKS]


def existing_note_names(vault_path: Path) -> Set[str]:
    return {path.stem.casefold() for path in vault_path.rglob("*.md")} if vault_path.exists() else set()


def rebuild_links(body: str, related: List[str], existing: Set[str]) -> str:
    """Keep only model-suggested links that resolve, add real related notes, drop the section if empty."""
    match = re.search(rf"(?m)^{re.escape(LINKS_HEADING)}\s*$", body)
    kept: List[str] = []
    if match:
        rest = body[match.end():]
        next_heading = re.search(r"(?m)^## ", rest)
        section = rest[: next_heading.start()] if next_heading else rest
        kept = [name.strip() for name in WIKILINK_RE.findall(section) if name.strip().casefold() in existing]
        body = (body[: match.start()] + (rest[next_heading.start():] if next_heading else "")).rstrip()
    links = list(dict.fromkeys([*related, *kept]))
    if not links:
        return body
    return f"{body.rstrip()}\n\n{LINKS_HEADING}\n" + "\n".join(f"- [[{name}]]" for name in links)
