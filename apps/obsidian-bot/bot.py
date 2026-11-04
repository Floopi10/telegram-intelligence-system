"""Telegram-бот: принимает посты и сохраняет в Obsidian vault."""
from __future__ import annotations

import asyncio
import logging
import secrets
from collections import OrderedDict, defaultdict
from dataclasses import dataclass, field, replace
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo
from typing import Dict, List, Optional, Tuple

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramNetworkError
from aiogram.filters import CommandStart, Command
from aiogram.types import (
    BotCommand,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application
from aiohttp import web

from capture import KINDS, QUEUE_DIR, Capture, classify, format_line, relative_target, write_queue_entry
from config import Settings, load_settings
from llm import LLM
from machine_note import structure_machine_message
from parser import parse_post
from tags import clean_tags
from ui import (
    CAPTURE_LABELS,
    capture_card,
    capture_undone_text,
    category_icon,
    category_picker_text,
    collect_note_stats,
    deleted_text,
    delete_confirm_text,
    failure_text,
    fetching_x_text,
    x_fetch_failed_text,
    help_text,
    progress_text,
    saved_card,
    stats_text,
    welcome_text,
)
from vault import Vault
from x_enrich import XCollector, enriched_text, first_status_url, is_bare_link

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
log = logging.getLogger("obsidian-bot")

# Буфер для media_group: media_group_id -> [(message, file_bytes, filename), ...]
# Ждём 2 секунды и обрабатываем альбом целиком.
_album_buffer: Dict[str, List[Tuple[Message, Optional[bytes], Optional[str]]]] = defaultdict(list)
_album_tasks: Dict[str, asyncio.Task] = {}
ALBUM_DEBOUNCE = 2.0
MAX_CARDS = 300


@dataclass
class CardState:
    """What a saved card shows; kept in memory so its buttons can act on the note."""
    note_path: Path
    title: str
    summary: str
    category: str
    tags: List[str]
    media_count: int
    route: str
    source_url: Optional[str]
    x_metrics: Optional[dict] = field(default=None)


_cards: "OrderedDict[str, CardState]" = OrderedDict()
MSK = ZoneInfo("Europe/Moscow")


QUEUE_KEEP_DAYS = 7


@dataclass
class CaptureState:
    entry_id: str
    text: str
    kind: str
    created: datetime
    queue_path: Path


_captures: "OrderedDict[str, CaptureState]" = OrderedDict()


def _is_forwarded(message: Message) -> bool:
    return any(
        getattr(message, name, None) is not None
        for name in ("forward_origin", "forward_from", "forward_from_chat", "forward_sender_name", "forward_date")
    )


def _capture_keyboard(token: str, kind: str) -> InlineKeyboardMarkup:
    switch = [
        InlineKeyboardButton(text=f"{icon} {label}", callback_data=f"cap:{token}:{other}")
        for other, (icon, label) in CAPTURE_LABELS.items()
        if other != kind
    ]
    return InlineKeyboardMarkup(inline_keyboard=[
        switch,
        [
            InlineKeyboardButton(text="📝 Заметкой", callback_data=f"cap:{token}:note"),
            InlineKeyboardButton(text="↩️ Отменить", callback_data=f"cap:{token}:undo"),
        ],
    ])


def _prune_queue(settings: Settings, now: datetime) -> None:
    """The send-only server keeps entries the computer already merged; drop old ones."""
    cutoff = now.timestamp() - QUEUE_KEEP_DAYS * 86_400
    for path in (settings.vault_path / QUEUE_DIR).glob("*.json"):
        try:
            if path.stat().st_mtime < cutoff:
                path.unlink()
        except OSError:
            continue


def _queue_capture(settings: Settings, capture: Capture, entry_id: str, created: datetime) -> CaptureState:
    path = write_queue_entry(settings.vault_path, entry_id, capture.kind, capture.text, created)
    log.info("capture queued kind=%s file=%s", capture.kind, path.name)
    return CaptureState(entry_id, capture.text, capture.kind, created, path)


def _capture_text(state: CaptureState) -> str:
    return capture_card(
        state.kind,
        format_line(state.kind, state.text, state.created),
        relative_target(state.kind, state.text, state.created).as_posix(),
    )


async def on_capture_action(
    callback: CallbackQuery, settings: Settings, llm: LLM, vault: Vault, xcollector: XCollector
) -> None:
    user = callback.from_user
    if user is None or user.id not in settings.allowed_user_ids:
        await callback.answer()
        return
    _, token, action = (callback.data or "").split(":", 2)
    state = _captures.get(token)
    if state is None or callback.message is None:
        await callback.answer("Кнопка устарела: бот перезапускался после записи.", show_alert=True)
        return
    line = format_line(state.kind, state.text, state.created)
    if action in {"undo", "note"}:
        state.queue_path.unlink(missing_ok=True)
    if action == "undo":
        _captures.pop(token, None)
        await callback.message.edit_text(capture_undone_text(line))
    elif action == "note":
        _captures.pop(token, None)
        await callback.answer("Делаю заметку")
        origin = SimpleNamespace(
            message_id=callback.message.message_id, forward_from_chat=None, forward_from=None, from_user=user,
        )
        await _save_post(callback.message, origin, state.text, [], llm, vault, settings.inbox_path, xcollector)
        return
    elif action in KINDS:
        moved = _queue_capture(settings, Capture(action, state.text), state.entry_id, state.created)
        _captures[token] = moved
        await callback.message.edit_text(_capture_text(moved), reply_markup=_capture_keyboard(token, moved.kind))
    await callback.answer()


def _remember_card(card: CardState) -> str:
    token = secrets.token_hex(5)
    _cards[token] = card
    while len(_cards) > MAX_CARDS:
        _cards.popitem(last=False)
    return token


def _card_text(card: CardState, inbox_path: Path) -> str:
    return saved_card(
        title=card.title,
        summary=card.summary,
        category=card.category,
        tags=card.tags,
        media_count=card.media_count,
        note_path=card.note_path,
        inbox_path=inbox_path,
        route=card.route,
        x_metrics=card.x_metrics,
    )


def _card_keyboard(token: str, card: CardState) -> InlineKeyboardMarkup:
    rows = []
    if card.source_url:
        rows.append([InlineKeyboardButton(text="🔗 Источник", url=card.source_url)])
    rows.append([
        InlineKeyboardButton(text="📁 Категория", callback_data=f"cat:{token}"),
        InlineKeyboardButton(text="🗑 Удалить", callback_data=f"del:{token}"),
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _category_keyboard(token: str, categories: List[str], current: str) -> InlineKeyboardMarkup:
    buttons = [
        InlineKeyboardButton(text=f"{category_icon(name)} {name}", callback_data=f"mv:{token}:{index}")
        for index, name in enumerate(categories)
        if name != current
    ]
    rows = [buttons[index:index + 2] for index in range(0, len(buttons), 2)]
    rows.append([InlineKeyboardButton(text="← Назад", callback_data=f"back:{token}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _delete_keyboard(token: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="Да, удалить", callback_data=f"rm:{token}"),
        InlineKeyboardButton(text="← Отмена", callback_data=f"back:{token}"),
    ]])


def _is_allowed(settings: Settings, message: Message) -> bool:
    user = message.from_user
    return user is not None and user.id in settings.allowed_user_ids


async def _download_file(bot: Bot, file_id: str) -> bytes:
    file = await bot.get_file(file_id)
    buf = await bot.download_file(file.file_path)
    return buf.read()


def _extract_media(message: Message) -> Optional[Tuple[str, str]]:
    """Возвращает (file_id, suggested_filename) для медиа в сообщении."""
    if message.photo:
        photo = message.photo[-1]  # самая большая версия
        return photo.file_id, f"photo_{photo.file_unique_id}.jpg"
    if message.video:
        v = message.video
        ext = (v.file_name or "video.mp4").rsplit(".", 1)[-1] if v.file_name else "mp4"
        return v.file_id, v.file_name or f"video_{v.file_unique_id}.{ext}"
    if message.document:
        d = message.document
        return d.file_id, d.file_name or f"document_{d.file_unique_id}"
    if message.audio:
        a = message.audio
        return a.file_id, a.file_name or f"audio_{a.file_unique_id}.mp3"
    if message.voice:
        v = message.voice
        return v.file_id, f"voice_{v.file_unique_id}.ogg"
    if message.video_note:
        n = message.video_note
        return n.file_id, f"videonote_{n.file_unique_id}.mp4"
    return None


def _post_text(message: Message) -> str:
    return message.text or message.caption or ""


def _source_url(message: Message) -> Optional[str]:
    """Если пост переслан из публичного канала — даём ссылку."""
    fwd = message.forward_from_chat
    if fwd and fwd.username and message.forward_from_message_id:
        return f"https://t.me/{fwd.username}/{message.forward_from_message_id}"
    return None


def _author_name(message: Message) -> Optional[str]:
    if message.forward_from_chat and message.forward_from_chat.title:
        return message.forward_from_chat.title
    if message.forward_from:
        u = message.forward_from
        return u.full_name or u.username
    if message.from_user:
        return message.from_user.full_name
    return None


def _chat_title(message: Message) -> Optional[str]:
    fwd = message.forward_from_chat
    if fwd:
        return fwd.title or (f"@{fwd.username}" if fwd.username else None)
    return None


# ---------- хэндлеры ----------

async def on_start(message: Message, settings: Settings) -> None:
    if not _is_allowed(settings, message):
        await message.answer("Этот бот закрытый. Доступа нет.")
        return
    await message.answer(welcome_text(_note_stats(settings)))


async def on_help(message: Message, settings: Settings) -> None:
    if not _is_allowed(settings, message):
        return
    await message.answer(help_text(settings.categories))


def _note_stats(settings: Settings):
    return collect_note_stats(settings.inbox_path, settings.attachments_folder, datetime.now())


async def on_stats(message: Message, settings: Settings) -> None:
    if not _is_allowed(settings, message):
        return
    await message.answer(stats_text(_note_stats(settings)))


async def on_card_action(callback: CallbackQuery, settings: Settings, vault: Vault) -> None:
    user = callback.from_user
    if user is None or user.id not in settings.allowed_user_ids:
        await callback.answer()
        return
    action, _, rest = (callback.data or "").partition(":")
    token, _, argument = rest.partition(":")
    card = _cards.get(token)
    if card is None or callback.message is None:
        await callback.answer("Кнопка устарела: бот перезапускался после сохранения.", show_alert=True)
        return
    if not card.note_path.exists() and action in {"cat", "mv", "del", "rm"}:
        await callback.answer("Заметки уже нет на месте: её перенесли или удалили в Obsidian.", show_alert=True)
        return

    if action == "cat":
        await callback.message.edit_text(
            category_picker_text(card.title),
            reply_markup=_category_keyboard(token, settings.categories, card.category),
        )
    elif action == "mv":
        try:
            category = settings.categories[int(argument)]
        except (ValueError, IndexError):
            await callback.answer()
            return
        moved = replace(card, note_path=vault.move_note(card.note_path, category), category=category)
        _cards[token] = moved
        await callback.message.edit_text(
            _card_text(moved, settings.inbox_path),
            reply_markup=_card_keyboard(token, moved),
            disable_web_page_preview=True,
        )
        await callback.answer(f"Перенесено в «{category}»")
        return
    elif action == "del":
        await callback.message.edit_text(delete_confirm_text(card.title), reply_markup=_delete_keyboard(token))
    elif action == "rm":
        vault.trash_note(card.note_path)
        _cards.pop(token, None)
        await callback.message.edit_text(deleted_text(card.title))
        await callback.answer("Удалено")
        return
    elif action == "back":
        await callback.message.edit_text(
            _card_text(card, settings.inbox_path),
            reply_markup=_card_keyboard(token, card),
            disable_web_page_preview=True,
        )
    await callback.answer()


async def on_album_part(
    message: Message, settings: Settings, llm: LLM, vault: Vault, bot: Bot, xcollector: XCollector
) -> None:
    """Альбом из нескольких медиа приходит несколькими апдейтами с одним media_group_id."""
    if not _is_allowed(settings, message):
        return

    mg_id = message.media_group_id
    media = _extract_media(message)
    file_bytes: Optional[bytes] = None
    filename: Optional[str] = None
    if media:
        file_id, filename = media
        try:
            file_bytes = await _download_file(bot, file_id)
        except Exception:
            log.exception("Не смог скачать медиа из альбома")
            file_bytes = None

    _album_buffer[mg_id].append((message, file_bytes, filename))

    # Перезапускаем дебаунс
    if mg_id in _album_tasks:
        _album_tasks[mg_id].cancel()
    _album_tasks[mg_id] = asyncio.create_task(
        _flush_album_after(mg_id, settings, llm, vault, xcollector)
    )


async def _flush_album_after(
    mg_id: str, settings: Settings, llm: LLM, vault: Vault, xcollector: XCollector
) -> None:
    try:
        await asyncio.sleep(ALBUM_DEBOUNCE)
    except asyncio.CancelledError:
        return

    parts = _album_buffer.pop(mg_id, [])
    _album_tasks.pop(mg_id, None)
    if not parts:
        return

    # Текст обычно у одного из сообщений (caption)
    head_msg = next((m for m, _, _ in parts if _post_text(m)), parts[0][0])
    text = _post_text(head_msg)

    media_paths: List[Path] = []
    for _, file_bytes, filename in parts:
        if file_bytes and filename:
            try:
                p = await vault.save_media(file_bytes, filename)
                media_paths.append(p)
            except Exception:
                log.exception("Не смог сохранить медиа из альбома")

    await _process_post(
        message=head_msg,
        text=text,
        media_paths=media_paths,
        llm=llm,
        vault=vault,
        inbox_path=settings.inbox_path,
        xcollector=xcollector,
    )


async def on_single_message(
    message: Message, settings: Settings, llm: LLM, vault: Vault, bot: Bot, xcollector: XCollector
) -> None:
    """Одиночное сообщение (не альбом)."""
    if not _is_allowed(settings, message):
        return
    if message.media_group_id:
        return  # обработает on_album_part

    text = _post_text(message)
    if not text and not _extract_media(message):
        return

    capture = None if _is_forwarded(message) or _extract_media(message) else classify(text)
    if capture is not None:
        now = datetime.now(MSK)
        _prune_queue(settings, now)
        token = secrets.token_hex(5)
        state = _queue_capture(settings, capture, token, now)
        _captures[token] = state
        while len(_captures) > MAX_CARDS:
            _captures.popitem(last=False)
        await message.answer(_capture_text(state), reply_markup=_capture_keyboard(token, state.kind))
        return

    media_paths: List[Path] = []
    media = _extract_media(message)
    if media:
        file_id, filename = media
        try:
            file_bytes = await _download_file(bot, file_id)
            p = await vault.save_media(file_bytes, filename)
            media_paths.append(p)
        except Exception:
            log.exception("Не смог скачать/сохранить медиа")

    await _process_post(
        message=message,
        text=text,
        media_paths=media_paths,
        llm=llm,
        vault=vault,
        inbox_path=settings.inbox_path,
        xcollector=xcollector,
    )


async def _process_post(
    *,
    message: Message,
    text: str,
    media_paths: List[Path],
    llm: LLM,
    vault: Vault,
    inbox_path: Path,
    xcollector: XCollector,
) -> None:
    status = await message.answer(progress_text(text, len(media_paths)))
    try:
        await _save_post(status, message, text, media_paths, llm, vault, inbox_path, xcollector)
    except Exception:
        log.exception("Заметка не сохранена: message_id=%s", message.message_id)
        await status.edit_text(failure_text())


async def _save_post(
    status: Message,
    message: Message,
    text: str,
    media_paths: List[Path],
    llm: LLM,
    vault: Vault,
    inbox_path: Path,
    xcollector: XCollector,
) -> None:
    parsed = parse_post(text)

    # Если текста нет — структурируем по filename'ам
    raw_for_llm = text or "[Пост без текста, только вложения: " + ", ".join(
        p.name for p in media_paths
    ) + "]"

    structured = structure_machine_message(raw_for_llm)
    machine_routed = structured is not None
    x_metrics = None
    x_url = None if machine_routed else first_status_url(text)
    if x_url:
        post = None
        if xcollector.enabled:
            await status.edit_text(fetching_x_text())
            post = await xcollector.fetch(x_url)
        if post is not None:
            raw_for_llm = enriched_text(raw_for_llm, post)
            x_metrics = post.metrics
        elif is_bare_link(text) and not media_paths:
            # A bare link without the post body would only produce an empty note.
            await status.edit_text(x_fetch_failed_text(x_url), disable_web_page_preview=True)
            return
    if structured is None:
        structured = await llm.structure(raw_for_llm)
        log.info("processing_route=ai message_id=%s", message.message_id)
    else:
        log.info("processing_route=machine message_id=%s category=%s", message.message_id, structured.category)

    note_path = await vault.save_note(
        structured=structured,
        parsed=parsed,
        media_paths=media_paths,
        source_url=_source_url(message),
        chat_title=_chat_title(message),
        author=_author_name(message),
    )

    card = CardState(
        note_path=note_path,
        title=structured.title,
        summary=structured.summary,
        category=structured.category,
        tags=clean_tags(parsed.tags, structured.suggested_tags),
        media_count=len(media_paths),
        route="machine" if machine_routed else "fallback" if structured.fallback else "ai",
        source_url=x_url or _source_url(message) or next(
            (url for url in parsed.urls if url.startswith("https://")), None
        ),
        x_metrics=x_metrics,
    )
    token = _remember_card(card)
    await status.edit_text(
        _card_text(card, inbox_path),
        reply_markup=_card_keyboard(token, card),
        disable_web_page_preview=True,
    )


def build_router() -> Router:
    """Create a fresh router for every dispatcher lifecycle.

    aiogram routers can only be attached to one dispatcher. Network retries
    recreate the dispatcher, so reusing a module-level router crashes with
    ``Router is already attached`` on the second attempt.
    """
    current = Router()
    current.message.register(on_start, CommandStart())
    current.message.register(on_help, Command("help"))
    current.message.register(on_stats, Command("stats"))
    current.callback_query.register(on_capture_action, F.data.startswith("cap:"))
    current.callback_query.register(on_card_action)
    current.message.register(on_album_part, F.media_group_id)
    current.message.register(
        on_single_message,
        F.text
        | F.caption
        | F.photo
        | F.document
        | F.video
        | F.audio
        | F.voice
        | F.video_note,
    )
    return current


# ---------- bootstrap ----------

async def main() -> None:
    settings = load_settings()
    bot = Bot(
        token=settings.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    llm = LLM(
        api_key=settings.openrouter_api_key,
        model_name=settings.openrouter_model,
        categories=settings.categories,
    )
    vault = Vault(settings)

    dp = Dispatcher()
    # Прокидываем зависимости в хэндлеры через workflow_data
    dp["settings"] = settings
    dp["llm"] = llm
    dp["vault"] = vault
    dp["xcollector"] = XCollector(settings.x_collector_env_file)
    dp.include_router(build_router())

    try:
        await bot.set_my_commands([
            BotCommand(command="start", description="Что умеет бот"),
            BotCommand(command="stats", description="Заметки за неделю"),
            BotCommand(command="help", description="Категории"),
        ])
    except TelegramNetworkError:
        log.warning("Меню команд не обновлено: Telegram недоступен")
    log.info("Бот запущен. Vault: %s", settings.vault_path)
    log.info("X-посты: %s", "подтягиваются через коллектор" if dp["xcollector"].enabled else "коллектор не настроен")
    if settings.allowed_user_ids:
        log.info("Allowed users: %s", settings.allowed_user_ids)
    else:
        log.warning("ALLOWED_USER_IDS пуст: бот отклоняет всех пользователей")

    if settings.bot_mode == "webhook":
        app = web.Application()
        SimpleRequestHandler(
            dispatcher=dp,
            bot=bot,
            secret_token=settings.webhook_secret or None,
        ).register(app, path=settings.webhook_path)
        setup_application(app, dp, bot=bot)

        if settings.webhook_base_url:
            webhook_url = f"{settings.webhook_base_url}{settings.webhook_path}"
            await bot.set_webhook(
                webhook_url,
                secret_token=settings.webhook_secret or None,
                drop_pending_updates=True,
            )
            log.info("Telegram webhook установлен: %s", webhook_url)
        else:
            log.info(
                "WEBHOOK_BASE_URL не задан. Локальный webhook-сервер стартует без set_webhook; "
                "внешний скрипт может установить webhook после получения Cloudflare URL."
            )

        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, settings.webhook_host, settings.webhook_port)
        await site.start()
        log.info(
            "Локальный webhook-сервер: http://%s:%s%s",
            settings.webhook_host,
            settings.webhook_port,
            settings.webhook_path,
        )

        try:
            await asyncio.Event().wait()
        finally:
            await runner.cleanup()
            await bot.session.close()
    else:
        try:
            await bot.delete_webhook(drop_pending_updates=True)
            await dp.start_polling(bot)
        finally:
            await bot.session.close()


async def run_forever() -> None:
    """Keep the local polling bot alive through temporary Telegram network failures."""
    while True:
        try:
            await main()
            return
        except asyncio.CancelledError:
            raise
        except TelegramNetworkError as e:
            log.error("Telegram API недоступен, повтор через 30 секунд: %s", e)
            await asyncio.sleep(30)
        except OSError as e:
            log.error("Сетевая ошибка, повтор через 30 секунд: %s", e)
            await asyncio.sleep(30)


if __name__ == "__main__":
    try:
        asyncio.run(run_forever())
    except (KeyboardInterrupt, SystemExit):
        log.info("Остановлено.")

