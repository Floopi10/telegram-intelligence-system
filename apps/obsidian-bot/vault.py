"""Запись заметок в Obsidian vault."""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Iterable, List, Optional

import aiofiles
from slugify import slugify

from config import Settings
from llm import StructuredPost
from parser import ParsedPost

log = logging.getLogger(__name__)


def _slug(title: str, max_len: int = 60) -> str:
    s = slugify(title, lowercase=False, allow_unicode=True, separator=" ")
    if not s:
        s = "note"
    return s[:max_len]


def _safe_filename(title: str, max_len: int = 100) -> str:
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", title).strip()
    name = re.sub(r"\s+", " ", name)
    name = name.rstrip(". ")
    if not name:
        name = _slug(title)
    return name[:max_len].rstrip(". ")


def _yaml_str(value: str) -> str:
    # A JSON string is a valid YAML double-quoted scalar, including quotes and newlines.
    return json.dumps(" ".join(str(value).split()), ensure_ascii=False)


def _yaml_list(items: Iterable[str]) -> str:
    return "[" + ", ".join(_yaml_str(item) for item in items) + "]"


def _attachment_name(suggested_name: str) -> str:
    """Telegram file names are sender-controlled and must stay inside the attachments folder."""
    name = suggested_name.replace("\\", "/").rsplit("/", 1)[-1]
    name = re.sub(r'[<>:"|?*\x00-\x1f]', "", name).strip().rstrip(". ")
    return name[:150] or "file"


def _merge_sources(body: str, urls: Iterable[str]) -> str:
    missing = [url for url in urls if url and url not in body]
    if not missing:
        return body
    lines = "\n".join(f"- {url}" for url in missing)
    heading = "## Цитата и источник"
    if heading not in body:
        return f"{body.rstrip()}\n\n{heading}\n{lines}".strip()
    match = re.search(r"(?m)^## Цитата и источник\s*$", body)
    if not match:
        return body
    next_heading = re.search(r"(?m)^## ", body[match.end():])
    insert_at = match.end() + (next_heading.start() if next_heading else len(body[match.end():]))
    return (body[:insert_at].rstrip() + "\n" + lines + "\n\n" + body[insert_at:].lstrip()).strip()


def _format_frontmatter(
    *,
    title: str,
    created: datetime,
    category: str,
    tags: List[str],
    source: Optional[str],
    chat: Optional[str],
    author: Optional[str],
    summary: str,
) -> str:
    lines = [
        "---",
        f"title: {_yaml_str(title)}",
        f"created: {created.strftime('%Y-%m-%dT%H:%M:%S')}",
        f"category: {_yaml_str(category)}",
        f"tags: {_yaml_list(tags)}",
    ]
    if source:
        lines.append(f"source: {_yaml_str(source)}")
    if chat:
        lines.append(f"chat: {_yaml_str(chat)}")
    if author:
        lines.append(f"author: {_yaml_str(author)}")
    if summary:
        lines.append(f"summary: {_yaml_str(summary)}")
    lines.append("---")
    return "\n".join(lines)


class Vault:
    def __init__(self, settings: Settings):
        self._s = settings
        self._s.inbox_path.mkdir(parents=True, exist_ok=True)
        self._s.attachments_path.mkdir(parents=True, exist_ok=True)

    # ---------- медиа ----------

    async def save_media(self, file_bytes: bytes, suggested_name: str) -> Path:
        """Сохраняет файл во вложения. Возвращает путь относительно inbox."""
        target = self._s.attachments_path / _attachment_name(suggested_name)
        target = self._unique_path(target)
        async with aiofiles.open(target, "wb") as f:
            await f.write(file_bytes)
        log.info("Saved media: %s (%d bytes)", target, len(file_bytes))
        return target

    # ---------- заметки ----------

    async def save_note(
        self,
        *,
        structured: StructuredPost,
        parsed: ParsedPost,
        media_paths: List[Path],
        source_url: Optional[str] = None,
        chat_title: Optional[str] = None,
        author: Optional[str] = None,
    ) -> Path:
        category_dir = self._s.category_path(structured.category)
        category_dir.mkdir(parents=True, exist_ok=True)

        created = datetime.now()
        # Объединяем теги: из текста + из LLM + категория-как-тег
        all_tags = self._merge_tags(parsed.tags, structured.suggested_tags)

        frontmatter = _format_frontmatter(
            title=structured.title,
            created=created,
            category=structured.category,
            tags=all_tags,
            source=source_url,
            chat=chat_title,
            author=author,
            summary=structured.summary,
        )

        body_parts: List[str] = [frontmatter, "", f"# {structured.title}", ""]

        if structured.body_md:
            body_parts.append(_merge_sources(structured.body_md, parsed.urls))
            body_parts.append("")

        if media_paths:
            body_parts.append("## Вложения")
            for p in media_paths:
                rel = self._relpath_from_note(p, category_dir)
                body_parts.append(f"![[{rel}]]")
            body_parts.append("")




        filename = f"{_safe_filename(structured.title)}.md"
        target = self._unique_path(category_dir / filename)

        async with aiofiles.open(target, "w", encoding="utf-8") as f:
            await f.write("\n".join(body_parts))

        log.info("Saved note: %s", target)
        return target

    # ---------- helpers ----------

    @staticmethod
    def _merge_tags(*lists: Iterable[str]) -> List[str]:
        seen = set()
        out: List[str] = []
        for lst in lists:
            for t in lst:
                norm = t.strip().lstrip("#").lower()
                if not norm or norm in seen:
                    continue
                seen.add(norm)
                out.append(norm)
        return out

    @staticmethod
    def _unique_path(path: Path) -> Path:
        if not path.exists():
            return path
        stem, suffix = path.stem, path.suffix
        i = 2
        while True:
            candidate = path.with_name(f"{stem} ({i}){suffix}")
            if not candidate.exists():
                return candidate
            i += 1

    @staticmethod
    def _relpath_from_note(media: Path, note_dir: Path) -> str:
        # Obsidian wikilink: используем относительный путь без слэшей в начале.
        try:
            rel = media.relative_to(note_dir)
        except ValueError:
            # media лежит в attachments, выходим вверх
            import os
            rel = Path(os.path.relpath(media, note_dir))
        return str(rel).replace("\\", "/")
