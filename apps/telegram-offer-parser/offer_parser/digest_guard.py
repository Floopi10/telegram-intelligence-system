from __future__ import annotations

import re

from .vacancy_filter import evaluate_vacancy, is_vacancy_text


AMBASSADOR_SPAM = re.compile(
    r"\bambassador\s+program\b|\b(?:making|earn)\s*\$?\d+\s*(?:to|[-–])\s*\$?\d+\s*(?:monthly|per month)\b|"
    r"\bjoin\s+(?:our|us|now)\b.*\b(?:opportunit|community|channel)\b|"
    r"\b(?:kol manager|event host|content creator)\b.*\bjoin\b",
    re.I | re.S,
)

CONTEXT_FRAGMENT = re.compile(
    r"^(?:alright|okay|ok|thanks|thank you|pls|please|sure|yeah|yes|no)\b|"
    r"\b(?:can you|could you|please)\s+(?:explain|help|check)\b",
    re.I,
)


def is_knowledge_noise(text: str) -> bool:
    normalized = " ".join((text or "").split())
    if AMBASSADOR_SPAM.search(normalized):
        return True
    if len(normalized) < 260 and CONTEXT_FRAGMENT.search(normalized):
        return True
    return False


def split_digest_rows(rows: list[dict]) -> tuple[list[dict], list[dict], list[dict]]:
    """Re-check historical rows so rejected vacancies never leak into knowledge."""
    vacancies: list[dict] = []
    knowledge: list[dict] = []
    rejected: list[dict] = []
    for row in rows:
        item = dict(row)
        text = str(item.get("text") or "")
        if is_vacancy_text(text):
            if evaluate_vacancy(text).accepted:
                vacancies.append(item)
            else:
                rejected.append(item)
        elif item.get("item_kind") == "knowledge" and not is_knowledge_noise(text):
            knowledge.append(item)
    return vacancies, knowledge, rejected
