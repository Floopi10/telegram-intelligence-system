"""Простой парсер: вытаскивает теги, ссылки и упоминания из текста поста."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List

# #тег — кириллица/латиница/цифры/_, минимум 2 символа
HASHTAG_RE = re.compile(r"(?<!\w)#([\wЀ-ӿ][\wЀ-ӿ\d_-]{1,})", re.UNICODE)

# @username — латиница/цифры/_, минимум 3 символа (как в Telegram)
MENTION_RE = re.compile(r"(?<!\w)@([A-Za-z][A-Za-z0-9_]{2,})")

# URL: http(s)://, t.me/, www.
URL_RE = re.compile(
    r"(?:https?://|www\.|t\.me/)[^\s<>\"')]+",
    re.IGNORECASE,
)


@dataclass
class ParsedPost:
    text: str
    tags: List[str]
    mentions: List[str]
    urls: List[str]


def _dedup(seq):
    seen = set()
    out = []
    for x in seq:
        key = x.lower()
        if key not in seen:
            seen.add(key)
            out.append(x)
    return out


def parse_post(text: str) -> ParsedPost:
    """Разбирает текст поста на теги/упоминания/ссылки."""
    if not text:
        return ParsedPost(text="", tags=[], mentions=[], urls=[])

    tags = _dedup(HASHTAG_RE.findall(text))
    mentions = _dedup(MENTION_RE.findall(text))
    urls = _dedup(URL_RE.findall(text))

    return ParsedPost(
        text=text.strip(),
        tags=tags,
        mentions=mentions,
        urls=urls,
    )
