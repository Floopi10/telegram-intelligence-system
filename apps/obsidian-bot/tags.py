"""Tag cleanup: drop the broken tags the free model sometimes invents."""
from __future__ import annotations

import re
from typing import Iterable, List

MAX_TAG_CHARS = 24
MAX_TAG_PARTS = 2
CYRILLIC = re.compile(r"[а-яё]")
LATIN = re.compile(r"[a-z]")
ALLOWED = re.compile(r"^[0-9a-zа-яё_-]+$")


def clean_tag(tag: str) -> str:
    """Return the normalized tag, or "" when it should be dropped.

    Dropped: longer than MAX_TAG_CHARS, more than MAX_TAG_PARTS words,
    Cyrillic and Latin letters in one tag, or characters Obsidian tags do not allow.
    """
    norm = re.sub(r"\s+", "_", str(tag).strip().lstrip("#").lower())
    if not norm or len(norm) > MAX_TAG_CHARS or not ALLOWED.match(norm):
        return ""
    if len([part for part in re.split(r"[_-]", norm) if part]) > MAX_TAG_PARTS:
        return ""
    if CYRILLIC.search(norm) and LATIN.search(norm):
        return ""
    return norm


def clean_tags(*lists: Iterable[str]) -> List[str]:
    """Merge tag lists, cleaning and deduplicating them in order."""
    seen = set()
    out: List[str] = []
    for tags in lists:
        for tag in tags:
            norm = clean_tag(tag)
            if norm and norm not in seen:
                seen.add(norm)
                out.append(norm)
    return out
