"""Deterministic conversion of parser messages into Obsidian notes.

These messages have already been classified by telegram-offer-parser.  Calling
an LLM again is both wasteful and can distort fields produced by the parser.
"""
from __future__ import annotations

import re
from typing import Dict, List, Optional, Tuple

from llm import StructuredPost


MACHINE_MARKERS = (
    "KNOWLEDGE CANDIDATE FROM TELEGRAM",
    "TWITTER INTELLIGENCE FROM TELEGRAM",
)

BD_TERMS = (
    "business development", "bd manager", "partnership", "partnerships",
    "outreach", "affiliate", "kol", "influencer", "ambassador",
    "бизнес-девелоп", "партнерств", "партнёрств", "аутрич",
)
WEB3_TERMS = (
    "web3", "crypto", "blockchain", "defi", "airdrop", "token", "dao",
    "крипто", "блокчейн", "токен", "аирдроп",
)


def _fields(text: str) -> Dict[str, str]:
    """Read one-line `Key: value` fields without treating body lines as fields."""
    result: Dict[str, str] = {}
    for line in text.splitlines():
        match = re.match(r"^([^:\n]{2,40}):\s*(.*)$", line.strip())
        if match:
            # Parser headers come first; a "Чат: x" line inside the quoted post must not replace them.
            result.setdefault(match.group(1).strip().casefold(), match.group(2).strip())
    return result


# Only real section labels end a section; body lines such as "- Просмотры: 100" belong to it.
SECTION_LABELS = (
    "Полный разбор", "Ключевые мысли", "Метрики", "Метрики поста",
    "Реальные метрики X", "Механика удержания", "Паттерн для стратегии",
    "Цитата и источник", "Оригинальный текст",
)
_SECTION_END = "|".join(re.escape(label) for label in SECTION_LABELS)


def _section(text: str, label: str) -> str:
    pattern = rf"(?ims)^(?:##\s*)?{re.escape(label)}:[ \t]*\n?(.*?)(?=\n(?:##\s*)?(?:{_SECTION_END}):|\Z)"
    match = re.search(pattern, text)
    return match.group(1).strip() if match else ""


def _original_text(text: str) -> str:
    match = re.search(r"(?ims)^Оригинальный текст:\s*\n(.*)\Z", text)
    return match.group(1).strip() if match else ""


def _clean_title(value: str, fallback: str) -> str:
    value = re.sub(r"\s+", " ", value).strip(" -–—:.")
    if not value:
        value = fallback
    return value[:140]


NICHE_CATEGORIES = {
    "ai": "AI",
    "роботы": "Роботы",
    "финансы": "Финансы",
    "web3": "Web3",
    "bd": "BD",
    "маркетинг": "Маркетинг",
    "контент": "Контент",
    "инструменты": "Инструменты",
    "безопасность": "Безопасность",
}


def _category(kind: str, combined: str, niche: str = "") -> str:
    lowered = combined.casefold()
    if "twitter intelligence" in kind.casefold():
        return "Twitter Intelligence"
    if "вакан" in kind.casefold():
        return "Вакансии"
    explicit = NICHE_CATEGORIES.get(niche.strip().casefold())
    if explicit:
        return explicit
    if any(term in lowered for term in BD_TERMS):
        return "BD"
    if any(term in lowered for term in WEB3_TERMS):
        return "Web3"
    return "Другое"


def _tags(category: str, fields: Dict[str, str]) -> List[str]:
    tags = ["telegram", "parser"]
    mapping = {
        "Twitter Intelligence": "twitter-intelligence",
        "Вакансии": "vacancy",
        "BD": "bd",
        "Web3": "web3",
        "AI": "ai",
        "Роботы": "robotics",
        "Финансы": "finance",
        "Маркетинг": "marketing",
        "Контент": "content",
        "Инструменты": "tools",
        "Безопасность": "security",
        "Другое": "knowledge",
    }
    tags.append(mapping[category])
    if fields.get("чат"):
        tags.append("telegram-source")
    return tags


def _source_line(fields: Dict[str, str]) -> str:
    source = fields.get("источник") or fields.get("x url") or "Источник не указан"
    chat = fields.get("чат")
    return f"{source}" + (f" · {chat}" if chat else "")


def _twitter_body(text: str, fields: Dict[str, str], original: str) -> Tuple[str, str, str]:
    topic = fields.get("тема") or "Twitter-пост"
    hook = fields.get("хук") or "Хук не выделен парсером"
    analysis = (
        _section(text, "Полный разбор")
        or fields.get("полезный вывод")
        or original
        or "Парсер не передал текст для анализа."
    )
    key_points = _section(text, "Ключевые мысли") or fields.get("сигналы") or analysis
    retention = _section(text, "Механика удержания") or "Не определена без повторного AI-разбора."
    pattern = _section(text, "Паттерн для стратегии") or "Требует ручной проверки по исходному посту."
    metrics = "\n".join(
        value for value in (
            _section(text, "Реальные метрики X"),
            _section(text, "Метрики поста") or _section(text, "Метрики"),
            fields.get("подтверждение метрик"),
        )
        if value and value.casefold() != "none"
    )
    metrics_block = f"\n\n## Метрики поста\n{metrics}" if metrics else ""
    body = (
        f"## Суть\n{analysis}\n\n"
        f"## Ключевые мысли\n{key_points}\n\n"
        f"## Хук и упаковка\n{hook}\n\n"
        f"## Механика удержания\n{retention}"
        f"{metrics_block}\n\n"
        f"## Паттерн для стратегии\n{pattern}\n\n"
        f"## Цитата и источник\n{hook}\n\n{_source_line(fields)}"
    )
    return topic, analysis[:240], body


def _vacancy_body(fields: Dict[str, str], original: str) -> Tuple[str, str, str]:
    chat = fields.get("чат") or "Telegram"
    signals = fields.get("сигналы") or "Соответствие определено фильтром вакансий."
    content = original or "Текст вакансии не передан."
    title = _clean_title(content.splitlines()[0] if content else "", f"Вакансия из {chat}")
    body = (
        f"## Суть вакансии\n{content}\n\n"
        f"## Почему подходит\n{signals}\n\n"
        "## Условия\nТолько условия из исходного текста выше; отсутствующие данные не додумываются.\n\n"
        "## Требования\nТолько требования из исходного текста выше.\n\n"
        f"## Отклик\n{_source_line(fields)}"
    )
    return title, f"Релевантная вакансия из {chat}: {signals}"[:240], body


def _knowledge_body(fields: Dict[str, str], original: str, category: str) -> Tuple[str, str, str]:
    chat = fields.get("чат") or "Telegram"
    signals = fields.get("сигналы") or "Сохранено фильтром базы знаний."
    content = original or "Текст знания не передан."
    title = _clean_title(content.splitlines()[0] if content else "", f"{category} · {chat}")
    body = (
        f"## Суть\n{content}\n\n"
        f"## Ключевые мысли\n- {signals}\n"
        f"- Источник: {chat}\n\n"
        f"## Цитата и источник\n{_source_line(fields)}"
    )
    return title, f"Материал категории {category} из {chat}: {signals}"[:240], body


def structure_machine_message(text: str) -> Optional[StructuredPost]:
    """Return a deterministic note for parser output, otherwise ``None``."""
    upper = text.upper()
    if not any(marker in upper for marker in MACHINE_MARKERS):
        return None

    fields = _fields(text)
    kind = fields.get("тип", "")
    if "TWITTER INTELLIGENCE FROM TELEGRAM" in upper:
        kind = "Twitter Intelligence"
    original = _original_text(text)
    category = _category(kind, f"{text}\n{original}", fields.get("ниша", ""))

    if category == "Twitter Intelligence":
        title, summary, body = _twitter_body(text, fields, original)
    elif category == "Вакансии":
        title, summary, body = _vacancy_body(fields, original)
    else:
        title, summary, body = _knowledge_body(fields, original, category)

    return StructuredPost(
        title=_clean_title(title, "Telegram Parser Note"),
        summary=summary or "Автоматически сохранено без повторного AI-запроса.",
        body_md=body,
        category=category,
        suggested_tags=_tags(category, fields),
        fallback=False,
    )
