"""Text layout for bot replies. Telegram HTML only: <b>, <i>, <code>, <blockquote>."""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta
from html import escape
from pathlib import Path
from typing import Iterable, Optional

CATEGORY_ICONS = {
    "Twitter Intelligence": "𝕏",
    "Вакансии": "💼",
    "AI": "🧠",
    "Claude": "🧠",
    "Промпты": "🧠",
    "Web3": "⛓",
    "AirDrop": "⛓",
    "Финансы": "📈",
    "Роботы": "🤖",
    "BD": "🤝",
    "Нетворкинг": "🤝",
    "Маркетинг": "📣",
    "SMM": "📣",
    "Шиллинг": "📣",
    "Контент": "✍️",
    "Twitter": "✍️",
    "YouTube": "✍️",
    "Инструменты": "🛠",
    "Автоматизация": "🛠",
    "Vibe-кодинг": "🛠",
    "Безопасность": "🔒",
    "Дизайн": "🎨",
    "Идеи": "💡",
    "Гайды": "📘",
    "Ресурсы": "📘",
    "Новости": "📰",
    "Задачи": "☑️",
    "Личное": "🗂",
}
DEFAULT_ICON = "🗂"
MAX_TAGS = 8


def category_icon(category: str) -> str:
    return CATEGORY_ICONS.get(category, DEFAULT_ICON)


def _plural(count: int, one: str, few: str, many: str) -> str:
    tail = count % 100
    if 11 <= tail <= 14:
        return many
    last = count % 10
    if last == 1:
        return one
    if 2 <= last <= 4:
        return few
    return many


def _relative_note_path(note_path: Path, inbox_path: Path) -> str:
    try:
        return note_path.relative_to(inbox_path).as_posix()
    except ValueError:
        return note_path.name


def progress_text(text: str, media_count: int) -> str:
    parts = []
    if text.strip():
        parts.append(f"{len(text.split())} {_plural(len(text.split()), 'слово', 'слова', 'слов')}")
    if media_count:
        parts.append(f"{media_count} {_plural(media_count, 'вложение', 'вложения', 'вложений')}")
    detail = " · ".join(parts) or "пустое сообщение"
    return f"⏳ <b>Разбираю пост</b>\n<i>{escape(detail)}</i>"


def saved_card(
    *,
    title: str,
    summary: str,
    category: str,
    tags: Iterable[str],
    media_count: int,
    note_path: Path,
    inbox_path: Path,
    route: str,
    x_metrics: Optional[dict] = None,
) -> str:
    """route: 'machine', 'ai' or 'fallback'."""
    unique_tags = list(dict.fromkeys(tag for tag in tags if tag))
    shown = unique_tags[:MAX_TAGS]
    hidden = len(unique_tags) - len(shown)
    tag_line = " ".join(f"#{escape(tag.replace(' ', '_'))}" for tag in shown)
    if hidden:
        tag_line += f" +{hidden}"

    route_label = {
        "machine": "⚡ из парсера, без AI",
        "ai": "✦ разобрано AI",
        "fallback": "⚠️ AI не ответил — сохранено как есть",
    }[route]
    meta = [f"{category_icon(category)} {escape(category)}", route_label]
    if media_count:
        meta.append(f"📎 {media_count}")

    lines = [
        "✅ <b>Сохранено в Obsidian</b>" if route != "fallback" else "⚠️ <b>Сохранено без разбора</b>",
        "",
        f"<b>{escape(title)}</b>",
    ]
    if summary.strip():
        lines.append(f"<blockquote expandable>{escape(summary.strip())}</blockquote>")
    lines.append(" · ".join(meta))
    metrics_line = x_metrics_line(x_metrics)
    if metrics_line:
        lines.append(metrics_line)
    if tag_line:
        lines.append(tag_line)
    lines.append(f"<code>{escape(_relative_note_path(note_path, inbox_path))}</code>")
    if route == "fallback":
        lines.extend(["", "<i>Категория «Другое». Проверь OPENROUTER_API_KEY и логи бота.</i>"])
    return "\n".join(lines)


def _count(value: int) -> str:
    return f"{value:,}".replace(",", " ")


def x_metrics_line(metrics: Optional[dict]) -> str:
    if not metrics:
        return ""
    parts = [
        f"{icon} {_count(metrics[key])}"
        for key, icon in (("views", "👁"), ("likes", "♥"), ("reposts", "↻"), ("bookmarks", "🔖"))
        if metrics.get(key) is not None
    ]
    return "𝕏 " + " · ".join(parts) if parts else ""


def fetching_x_text() -> str:
    return "⏳ <b>Разбираю пост</b>\n<i>подтягиваю текст и метрики из X…</i>"


def x_fetch_failed_text(url: str) -> str:
    return (
        "⚠️ <b>Не смог получить пост из X</b>\n"
        "Заметку не сохранил: из одной ссылки вышла бы пустая.\n\n"
        "Перешли ссылку ещё раз через пару минут или добавь к ней текст поста.\n"
        f"<code>{escape(url)}</code>"
    )


CAPTURE_LABELS = {
    "log": ("📅", "В дневник"),
    "idea": ("💡", "В идеи"),
    "task": ("☑️", "В задачи"),
}


def capture_card(kind: str, line: str, relative_path: str) -> str:
    icon, label = CAPTURE_LABELS[kind]
    return f"{icon} <b>{label}</b>\n<i>{escape(line)}</i>\n<code>{escape(relative_path)}</code>"


def capture_undone_text(line: str) -> str:
    return f"↩️ <b>Отменено</b>\n<s>{escape(line)}</s>"


def category_picker_text(title: str) -> str:
    return f"📁 <b>Куда перенести?</b>\n<i>{escape(title)}</i>"


def delete_confirm_text(title: str) -> str:
    return (
        f"🗑 <b>Удалить заметку?</b>\n<i>{escape(title)}</i>\n\n"
        "Она переедет в корзину Obsidian (.trash), её можно будет вернуть."
    )


def deleted_text(title: str) -> str:
    return f"🗑 <b>Заметка в корзине</b>\n<i>{escape(title)}</i>\nВернуть можно из папки .trash в Obsidian."


def failure_text() -> str:
    return (
        "❌ <b>Заметка не сохранена</b>\n"
        "Перешли сообщение ещё раз. Если повторится — причина в логах бота."
    )


def welcome_text(note_stats: "NoteStats") -> str:
    today = note_stats.today
    return (
        "<b>Obsidian Inbox</b>\n"
        "Пересылай сюда посты, ссылки, фото и файлы — я разберу их и положу "
        "заметку в нужную папку vault.\n\n"
        f"Сегодня сохранено: <b>{today}</b> {_plural(today, 'заметка', 'заметки', 'заметок')}\n\n"
        "<b>Свои мысли раскладываю сам</b>\n"
        "«оплатил Claude» → дневник в Месяца\n"
        "«идея: …» → идеи, «надо …» → задачи\n"
        "Пересланные посты и ссылки → заметки\n\n"
        "<b>Команды</b>\n"
        "/stats — что сохранено за неделю\n"
        "/help — категории\n\n"
        "<i>Дашборд Radar открывается кнопкой меню слева от поля ввода.</i>"
    )


def help_text(categories: Iterable[str]) -> str:
    rows = "\n".join(f"{category_icon(name)} {escape(name)}" for name in categories)
    return (
        "<b>Категории</b>\n"
        "AI выбирает одну. Сообщения парсера сохраняются без повторного запроса к AI.\n\n"
        f"{rows}"
    )


@dataclass(frozen=True)
class NoteStats:
    today: int
    week: int
    total: int
    top_week: tuple[tuple[str, int], ...]
    last_note: Optional[str]


def collect_note_stats(inbox_path: Path, attachments_folder: str, now: datetime) -> NoteStats:
    today = week = total = 0
    categories: Counter[str] = Counter()
    last_time = 0.0
    last_note: Optional[str] = None
    week_start = (now - timedelta(days=7)).timestamp()
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
    if inbox_path.exists():
        for path in inbox_path.rglob("*.md"):
            relative = path.relative_to(inbox_path)
            if relative.parts and relative.parts[0] == attachments_folder:
                continue
            try:
                modified = path.stat().st_mtime
            except OSError:
                continue
            total += 1
            if modified >= week_start:
                week += 1
                categories[relative.parts[0] if len(relative.parts) > 1 else "Inbox"] += 1
            if modified >= day_start:
                today += 1
            if modified > last_time:
                last_time, last_note = modified, path.stem
    return NoteStats(today, week, total, tuple(categories.most_common(5)), last_note)


def stats_text(note_stats: NoteStats) -> str:
    lines = [
        "<b>Vault за неделю</b>",
        f"Сегодня: <b>{note_stats.today}</b> · 7 дней: <b>{note_stats.week}</b> · всего: <b>{note_stats.total}</b>",
    ]
    if note_stats.top_week:
        top = max(count for _, count in note_stats.top_week)
        lines.append("")
        for name, count in note_stats.top_week:
            bar = "▰" * max(1, round(count / top * 8))
            lines.append(f"{category_icon(name)} {escape(name)}  <code>{bar}</code> {count}")
    else:
        lines.extend(["", "<i>За неделю заметок нет.</i>"])
    if note_stats.last_note:
        lines.extend(["", f"Последняя: <i>{escape(note_stats.last_note)}</i>"])
    return "\n".join(lines)
