from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass

URL_RE = re.compile(r"https?://[^\s<>]+", re.IGNORECASE)
TOKEN_RE = re.compile(r"[a-zа-яё0-9]{3,}", re.IGNORECASE)
STOP_WORDS = {
    "the", "and", "for", "with", "this", "that", "from", "your", "you", "are", "our",
    "или", "для", "как", "это", "что", "при", "его", "она", "они", "уже", "все", "всё",
    "https", "http", "com", "www", "telegram", "message",
}


@dataclass(frozen=True)
class SemanticSignature:
    exact_hash: str
    tokens: tuple[str, ...]
    urls: tuple[str, ...]


def build_signature(text: str) -> SemanticSignature:
    normalized_urls = tuple(sorted(dict.fromkeys(
        url.rstrip(".,;:!?)]}").casefold() for url in URL_RE.findall(text)
    )))
    without_urls = URL_RE.sub(" ", text.casefold())
    tokens = tuple(sorted(dict.fromkeys(
        token for token in TOKEN_RE.findall(without_urls)
        if token not in STOP_WORDS
    )))
    canonical = " ".join(tokens) + "|" + "|".join(normalized_urls)
    exact_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return SemanticSignature(exact_hash, tokens, normalized_urls)


def similarity(left: SemanticSignature, right_tokens_json: str, right_urls_json: str) -> float:
    right_tokens = set(json.loads(right_tokens_json))
    right_urls = set(json.loads(right_urls_json))
    left_tokens = set(left.tokens)
    left_urls = set(left.urls)
    if left_urls and right_urls and left_urls.intersection(right_urls):
        return 1.0
    if len(left_tokens) < 8 or len(right_tokens) < 8:
        return 0.0
    union = left_tokens.union(right_tokens)
    return len(left_tokens.intersection(right_tokens)) / len(union) if union else 0.0
