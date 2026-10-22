"""Telegram-бот: принимает посты и сохраняет в Obsidian vault."""
from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramNetworkError
from aiogram.filters import CommandStart, Command
from aiogram.types import Message
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application
from aiohttp import web

from config import Settings, load_settings
from llm import LLM
from machine_note import structure_machine_message
from parser import parse_post
from vault import Vault

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


def _is_allowed(settings: Settings, user_id: int) -> bool:
    if not settings.allowed_user_ids:
        return True
    return user_id in settings.allowed_user_ids


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
    if not _is_allowed(settings, message.from_user.id):
        await message.answer("Этот бот закрытый. Доступа нет.")
        return
    await message.answer(
        "Привет! Я сохраняю посты из Telegram в твой Obsidian.\n\n"
        "Просто перешли мне сообщение, либо отправь текст/фото/файл — "
        "я разберу его через OpenRouter/AI, разложу теги, "
        "сохраню медиа и положу .md-заметку в нужную папку vault.\n\n"
        f"Vault: <code>{settings.vault_path}</code>\n"
        f"Inbox: <code>{settings.inbox_path}</code>\n\n"
        "/help — список категорий"
    )


async def on_help(message: Message, settings: Settings) -> None:
    await message.answer(
        "Доступные категории (AI выбирает одну):\n\n"
        + "\n".join(f"• {c}" for c in settings.categories)
    )


async def on_album_part(
    message: Message, settings: Settings, llm: LLM, vault: Vault, bot: Bot
) -> None:
    """Альбом из нескольких медиа приходит несколькими апдейтами с одним media_group_id."""
    if not _is_allowed(settings, message.from_user.id):
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
        _flush_album_after(mg_id, settings, llm, vault)
    )


async def _flush_album_after(
    mg_id: str, settings: Settings, llm: LLM, vault: Vault
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
    )


async def on_single_message(
    message: Message, settings: Settings, llm: LLM, vault: Vault, bot: Bot
) -> None:
    """Одиночное сообщение (не альбом)."""
    if not _is_allowed(settings, message.from_user.id):
        return
    if message.media_group_id:
        return  # обработает on_album_part

    text = _post_text(message)
    if not text and not _extract_media(message):
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
    )


async def _process_post(
    *,
    message: Message,
    text: str,
    media_paths: List[Path],
    llm: LLM,
    vault: Vault,
) -> None:
    status = await message.answer("Разбираю пост…")

    parsed = parse_post(text)

    # Если текста нет — структурируем по filename'ам
    raw_for_llm = text or "[Пост без текста, только вложения: " + ", ".join(
        p.name for p in media_paths
    ) + "]"

    structured = structure_machine_message(raw_for_llm)
    machine_routed = structured is not None
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

    tags_str = ", ".join(f"#{t}" for t in (
        list(dict.fromkeys(parsed.tags + structured.suggested_tags))
    )) or "—"

    if machine_routed:
        header = "✅ Сохранено без повторного AI-запроса\n\n"
    elif structured.fallback:
        header = (
            "⚠️ Сохранено через fallback (AI/OpenRouter не отвечает)\n"
            "Категория принудительно = 'Другое'.\n"
            "Проверь OPENROUTER_API_KEY в .env и логи бота.\n\n"
        )
    else:
        header = "✅ Сохранено\n\n"

    await status.edit_text(
        f"{header}"
        f"<b>{structured.title}</b>\n"
        f"<i>{structured.summary}</i>\n\n"
        f"📁 <code>{structured.category}</code>\n"
        f"🏷 {tags_str}\n"
        f"📎 медиа: {len(media_paths)}\n"
        f"📝 <code>{note_path}</code>"
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
    dp.include_router(build_router())

    log.info("Бот запущен. Vault: %s", settings.vault_path)
    log.info(
        "Allowed users: %s",
        settings.allowed_user_ids if settings.allowed_user_ids else "ВСЕ (открытый бот)",
    )

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

