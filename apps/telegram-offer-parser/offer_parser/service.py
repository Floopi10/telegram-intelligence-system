from __future__ import annotations

import asyncio
import logging
from contextlib import suppress
from datetime import timezone

from telethon import TelegramClient, events
from telethon.errors import FloodWaitError
from telethon.utils import get_display_name

from .chats import load_existing_selection, load_selected, write_chats
from .config import Settings
from .exclusions import filter_excluded
from .digest import daily_digest_loop
from .matcher import MatchResult, OfferMatcher
from .storage import OfferRecord, Storage
from .twitter_priority import TwitterPrioritySource, load_twitter_priority_sources
from .vacancy_filter import evaluate_vacancy, is_vacancy_text
from .twitter_ai import (
    TwitterAIClient,
    extract_x_preview,
    format_twitter_intelligence,
)
from .twitter_strength import XCollectorClient, decide_strength
from .twitter_niches import classify_twitter_niche
from .knowledge_balance import (
    KnowledgeBalanceDecision,
    evaluate_knowledge_balance,
)
from .digest_guard import is_knowledge_noise


LOGGER = logging.getLogger(__name__)


def make_client(settings: Settings) -> TelegramClient:
    return TelegramClient(
        str(settings.session_path),
        settings.api_id,
        settings.api_hash,
        flood_sleep_threshold=60,
        request_retries=5,
        retry_delay=2,
        proxy=settings.proxy,
    )


def source_url(chat_id: int, username: str | None, message_id: int) -> str | None:
    if username:
        return f"https://t.me/{username}/{message_id}"
    raw = str(chat_id)
    if raw.startswith("-100"):
        return f"https://t.me/c/{raw[4:]}/{message_id}"
    return None


def format_notification(offer: OfferRecord, result: MatchResult) -> str:
    rules = ", ".join(result.matched[:8])
    money = ", ".join(result.compensation) or "не найдено"
    link = offer.source_url or "прямой ссылки нет"
    text = offer.text if len(offer.text) <= 2600 else offer.text[:2600] + "…"
    return (
        f"НАЙДЕН ОФФЕР · SCORE {offer.score}\n\n"
        f"Чат: {offer.chat_title}\n"
        f"Автор: {offer.sender_name}\n"
        f"Оплата: {money}\n"
        f"Сигналы: {rules}\n\n"
        f"{text}\n\n"
        f"Источник: {link}\n"
        f"Message ID: {offer.message_id}"
    )


def _is_vacancy_text(text: str) -> bool:
    return is_vacancy_text(text)


def effective_knowledge_threshold(
    text: str,
    default_min_score: int,
    priority: TwitterPrioritySource | None,
) -> int:
    if priority is None or _is_vacancy_text(text):
        return default_min_score
    return priority.min_score


def format_knowledge_notification(
    record: OfferRecord,
    result: MatchResult,
    priority: TwitterPrioritySource | None = None,
    balance: KnowledgeBalanceDecision | None = None,
) -> str:
    """Build one bounded message for the private Obsidian inbox bot."""
    rules = ", ".join(result.matched[:10]) or "—"
    link = record.source_url or "прямой ссылки нет"
    category = (
        "Вакансия"
        if _is_vacancy_text(record.text)
        else "Twitter Intelligence"
        if priority is not None and balance is None
        else "Знание"
    )
    niche_line = f"Ниша: {balance.primary_niche}\n" if balance is not None else ""
    niche_tags = (
        f"Темы: {', '.join(balance.niches)}\n" if balance is not None else ""
    )
    header = (
        "KNOWLEDGE CANDIDATE FROM TELEGRAM\n\n"
        f"Тип: {category}\n"
        f"{niche_line}"
        f"{niche_tags}"
        f"Чат: {record.chat_title}\n"
        f"Автор: {record.sender_name}\n"
        f"Knowledge score: {record.score}\n"
        f"Сигналы: {rules}\n"
        f"Источник: {link}\n"
        f"Message ID: {record.message_id}\n\n"
        "Оригинальный текст:\n"
    )
    limit = 3900 - len(header)
    body = record.text if len(record.text) <= limit else record.text[: limit - 1] + "…"
    return header + body


async def authorize(settings: Settings) -> None:
    client = make_client(settings)
    await client.start()
    me = await client.get_me()
    print(f"Авторизация успешна: {get_display_name(me)} (ID {me.id})")
    await client.disconnect()


async def scan_chats(settings: Settings) -> None:
    selected = load_existing_selection(settings.chats_file)
    rows: list[dict[str, str]] = []
    client = make_client(settings)
    await client.start()
    async for dialog in client.iter_dialogs():
        if not (dialog.is_group or dialog.is_channel):
            continue
        entity = dialog.entity
        if dialog.is_group:
            kind = "group"
        else:
            kind = "channel"
        rows.append(
            {
                "enabled": "1" if dialog.id in selected else "0",
                "chat_id": str(dialog.id),
                "title": dialog.name or "",
                "username": getattr(entity, "username", None) or "",
                "kind": kind,
            }
        )
    await client.disconnect()
    rows.sort(key=lambda row: row["title"].casefold())
    write_chats(settings.chats_file, rows)
    print(f"Сохранено чатов: {len(rows)} -> {settings.chats_file}")
    print("Поставь enabled=1 напротив групп, которые нужно мониторить.")


async def _build_offer(event, chat_title: str, result: MatchResult) -> OfferRecord:
    sender = await event.get_sender()
    chat = await event.get_chat()
    sender_name = get_display_name(sender) if sender else "неизвестно"
    return OfferRecord(
        chat_id=event.chat_id,
        message_id=event.id,
        chat_title=chat_title,
        sender_id=getattr(sender, "id", None),
        sender_name=sender_name or "неизвестно",
        message_date=event.date.astimezone(timezone.utc),
        text=event.raw_text.strip(),
        score=result.score,
        matched_rules=", ".join(result.matched),
        compensation=", ".join(result.compensation),
        source_url=source_url(event.chat_id, getattr(chat, "username", None), event.id),
    )


async def monitor(settings: Settings) -> None:
    selected = load_selected(settings.chats_file)
    selected_by_id = {chat.chat_id: chat.title for chat in selected}
    matcher = OfferMatcher(settings.rules_file)
    storage = Storage(settings.database_path)

    knowledge_selected = []
    knowledge_by_id: dict[int, str] = {}
    knowledge_matcher = None
    knowledge_storage = None
    if settings.knowledge_enabled:
        if not settings.knowledge_chats_file.exists():
            raise RuntimeError(
                "Knowledge включён, но список чатов не создан. "
                "Выполни select-knowledge-folders."
            )
        if not settings.knowledge_rules_file.exists():
            raise FileNotFoundError(settings.knowledge_rules_file)
        knowledge_selected = filter_excluded(
            load_selected(settings.knowledge_chats_file),
            settings.knowledge_exclusions_file,
        )
        if not knowledge_selected:
            raise RuntimeError("Knowledge включён, но не выбрано ни одного чата")
        knowledge_by_id = {
            chat.chat_id: chat.title for chat in knowledge_selected
        }
        knowledge_matcher = OfferMatcher(settings.knowledge_rules_file)
        knowledge_storage = Storage(settings.knowledge_database_path)

    twitter_priority_by_id = load_twitter_priority_sources(
        settings.twitter_priority_file
    )
    twitter_ai = None
    if settings.twitter_ai_enabled:
        twitter_ai = TwitterAIClient(
            settings.twitter_ai_env_file,
            settings.twitter_ai_min_score,
        )
    twitter_strength = None
    if twitter_ai is not None and settings.twitter_strength_enabled:
        twitter_strength = XCollectorClient(settings.x_collector_env_file)
    unavailable_priority = sorted(
        source.title
        for chat_id, source in twitter_priority_by_id.items()
        if chat_id not in knowledge_by_id
    )
    if unavailable_priority:
        LOGGER.warning(
            "Twitter priority источники не активны в Knowledge: %s",
            ", ".join(unavailable_priority),
        )

    client = make_client(settings)
    await client.start()
    digest_task = None
    if settings.daily_digest_enabled and knowledge_storage is not None:
        digest_task = asyncio.create_task(
            daily_digest_loop(
                client, knowledge_storage, target=settings.daily_digest_target,
                hour_msk=settings.daily_digest_hour_msk,
            )
        )

    all_selected = {chat.chat_id: chat for chat in selected}
    all_selected.update({chat.chat_id: chat for chat in knowledge_selected})
    available_entities = []
    for chat in all_selected.values():
        try:
            available_entities.append(await client.get_input_entity(chat.chat_id))
        except (ValueError, TypeError):
            LOGGER.warning("Чат больше не доступен: %s", chat.title)
    missing = len(all_selected) - len(available_entities)
    if missing:
        LOGGER.warning("Не найдено выбранных чатов: %s", missing)
    if not available_entities:
        storage.close()
        if knowledge_storage is not None:
            knowledge_storage.close()
        await client.disconnect()
        raise RuntimeError("Ни один выбранный чат не доступен аккаунту")

    @client.on(events.NewMessage(chats=available_entities, incoming=True))
    async def on_message(event) -> None:
        text = event.raw_text.strip()
        if not text:
            return
        try:
            if event.chat_id in selected_by_id:
                result = matcher.evaluate(text)
                if result.score >= settings.min_score:
                    offer = await _build_offer(
                        event,
                        selected_by_id[event.chat_id],
                        result,
                    )
                    if storage.save(offer):
                        await client.send_message(
                            settings.notify_target,
                            format_notification(offer, result),
                            link_preview=False,
                        )
                        LOGGER.info(
                            "Оффер сохранён: %s/%s", offer.chat_id, offer.message_id
                        )

            if (
                event.chat_id in knowledge_by_id
                and knowledge_matcher is not None
                and knowledge_storage is not None
            ):
                priority = twitter_priority_by_id.get(event.chat_id)
                preview = extract_x_preview(event.message) if priority else None
                if preview is not None and twitter_ai is not None:
                    if knowledge_storage.twitter_url_seen(preview.canonical_url):
                        knowledge_storage.record_decision(
                            chat_id=event.chat_id, message_id=event.id,
                            chat_title=knowledge_by_id[event.chat_id], stream="twitter",
                            decision="duplicate", reason="canonical_url", score=None, text=text,
                        )
                        return
                    analysis = await twitter_ai.analyze(preview)
                    niche = classify_twitter_niche(
                        preview.title,
                        preview.description,
                        analysis.topic,
                        analysis.analysis_ru,
                    )
                    blocked_sport = niche is None
                    final_keep = analysis.keep and not blocked_sport
                    strength = None
                    metrics = None
                    strength_error = ""
                    if final_keep and twitter_strength is not None:
                        try:
                            metrics = await twitter_strength.collect(preview.canonical_url)
                            author_rows, cohort_rows = knowledge_storage.twitter_strength_baselines(
                                author=metrics.author,
                                age_bucket=metrics.age_bucket,
                            )
                            kept_today, mechanics_today = knowledge_storage.twitter_strength_daily_counts()
                            strength = decide_strength(
                                content_score=analysis.score,
                                metrics=metrics,
                                author_rows=author_rows,
                                cohort_rows=cohort_rows,
                                kept_today=kept_today,
                                mechanics_today=mechanics_today,
                                daily_limit=settings.twitter_daily_limit,
                                mechanics_limit=settings.twitter_mechanics_daily_limit,
                            )
                            final_keep = strength.keep
                        except Exception as exc:
                            final_keep = False
                            strength_error = f"collector_error:{type(exc).__name__}"
                            LOGGER.warning(
                                "Twitter strength не смог получить метрики %s: %s",
                                preview.canonical_url, exc,
                            )
                    analysis_payload = {
                        "keep": final_keep,
                        "ai_keep": analysis.keep,
                        "niche": niche or "Спорт — отклонено",
                        "score": analysis.score,
                        "topic": analysis.topic,
                        "hook": analysis.hook,
                        "analysis_ru": analysis.analysis_ru,
                        "key_points": list(analysis.key_points),
                        "metrics_summary": analysis.metrics_summary,
                        "performance_evidence": analysis.performance_evidence,
                        "risk_flags": list(analysis.risk_flags),
                        "reason_ru": analysis.reason_ru,
                    }
                    if strength is not None and metrics is not None:
                        analysis_payload["strength"] = {
                            "kind": strength.kind,
                            "reason_ru": strength.reason_ru,
                            "baseline_scope": strength.baseline_scope,
                            "baseline_size": strength.baseline_size,
                            "velocity_percentile": strength.velocity_percentile,
                            "signal_percentile": strength.signal_percentile,
                            "metrics": metrics.raw,
                        }
                        knowledge_storage.save_twitter_strength(
                            canonical_url=preview.canonical_url,
                            author=metrics.author,
                            chat_id=event.chat_id,
                            message_id=event.id,
                            chat_title=knowledge_by_id[event.chat_id],
                            content_score=analysis.score,
                            metrics=metrics,
                            strength=strength,
                            decision="keep" if final_keep else "watch",
                            analysis=analysis_payload,
                        )
                    if final_keep:
                        await client.send_message(
                            settings.knowledge_target,
                            format_twitter_intelligence(
                                knowledge_by_id[event.chat_id],
                                priority.tier,
                                preview,
                                analysis,
                                strength.metrics_block(metrics)
                                if strength is not None and metrics is not None
                                else "",
                            ),
                            link_preview=False,
                        )
                    knowledge_storage.save_twitter_analysis(
                        canonical_url=preview.canonical_url,
                        chat_id=event.chat_id,
                        message_id=event.id,
                        chat_title=knowledge_by_id[event.chat_id],
                        decision="keep" if final_keep else "reject",
                        ai_score=analysis.score,
                        topic=analysis.topic,
                        risk_flags=analysis.risk_flags,
                        analysis=analysis_payload,
                    )
                    knowledge_storage.record_decision(
                        chat_id=event.chat_id, message_id=event.id,
                        chat_title=knowledge_by_id[event.chat_id], stream="twitter",
                        decision="keep" if final_keep else "rejected",
                        reason=(
                            "sport_blocked" if blocked_sport
                            else strength.reason_ru if strength is not None
                            else strength_error or analysis.reason_ru or "ai_decision"
                        ),
                        score=analysis.score, text=text,
                    )
                    LOGGER.info(
                        "Twitter AI: %s score=%s topic=%s url=%s",
                        "keep" if final_keep else "reject",
                        analysis.score,
                        analysis.topic,
                        preview.canonical_url,
                    )
                    return
                if is_knowledge_noise(text):
                    knowledge_storage.record_decision(
                        chat_id=event.chat_id, message_id=event.id,
                        chat_title=knowledge_by_id[event.chat_id], stream="knowledge",
                        decision="ignored", reason="knowledge_noise",
                        score=None, text=text,
                    )
                    LOGGER.info(
                        "Knowledge-мусор пропущен: %s/%s",
                        event.chat_id, event.id,
                    )
                    return
                if _is_vacancy_text(text):
                    vacancy_decision = evaluate_vacancy(text)
                    if not vacancy_decision.accepted:
                        knowledge_storage.record_decision(
                            chat_id=event.chat_id, message_id=event.id,
                            chat_title=knowledge_by_id[event.chat_id], stream="vacancy",
                            decision="rejected", reason=vacancy_decision.reason,
                            score=None, text=text,
                        )
                        LOGGER.info(
                            "Вакансия пропущена: %s/%s reason=%s",
                            event.chat_id, event.id, vacancy_decision.reason,
                        )
                        return
                knowledge_result = knowledge_matcher.evaluate(text)
                balance = None
                vacancy = _is_vacancy_text(text)
                if settings.knowledge_balance_enabled and not vacancy:
                    daily_total, daily_by_niche = (
                        knowledge_storage.knowledge_balance_daily_counts()
                    )
                    balance = evaluate_knowledge_balance(
                        text=text,
                        chat_title=knowledge_by_id[event.chat_id],
                        matcher_score=knowledge_result.score,
                        daily_total=daily_total,
                        daily_by_niche=daily_by_niche,
                        daily_limit=settings.knowledge_daily_limit,
                    )
                    if not balance.accepted:
                        knowledge_storage.record_decision(
                            chat_id=event.chat_id,
                            message_id=event.id,
                            chat_title=knowledge_by_id[event.chat_id],
                            stream="knowledge",
                            decision="ignored",
                            reason=balance.reason,
                            score=knowledge_result.score,
                            text=text,
                        )
                        return
                    knowledge_threshold = -10_000
                else:
                    knowledge_threshold = effective_knowledge_threshold(
                        text,
                        settings.knowledge_min_score,
                        priority,
                    )
                if knowledge_result.score >= knowledge_threshold:
                    record = await _build_offer(
                        event, knowledge_by_id[event.chat_id], knowledge_result,
                    )
                    item_kind = "vacancy" if vacancy else "knowledge"
                    saved, save_reason = knowledge_storage.save_unique(record, item_kind)
                    knowledge_storage.record_decision(
                        chat_id=event.chat_id, message_id=event.id,
                        chat_title=knowledge_by_id[event.chat_id], stream=item_kind,
                        decision="accepted" if saved else "duplicate",
                        reason=(balance.reason if saved and balance is not None else save_reason),
                        score=record.score,
                        text=text,
                    )
                    if saved:
                        await client.send_message(
                            settings.knowledge_target,
                            format_knowledge_notification(
                                record, knowledge_result, priority, balance
                            ),
                            link_preview=False,
                        )
                        LOGGER.info(
                            "Knowledge сохранён: %s/%s score=%s threshold=%s priority=%s",
                            record.chat_id, record.message_id, record.score,
                            knowledge_threshold, priority.tier if priority else "standard",
                        )
                    else:
                        LOGGER.info(
                            "Knowledge дубликат: %s/%s reason=%s",
                            record.chat_id, record.message_id, save_reason,
                        )
                else:
                    knowledge_storage.record_decision(
                        chat_id=event.chat_id, message_id=event.id,
                        chat_title=knowledge_by_id[event.chat_id], stream="knowledge",
                        decision="ignored", reason="below_threshold",
                        score=knowledge_result.score, text=text,
                    )
        except FloodWaitError as exc:
            LOGGER.warning("Telegram просит подождать %s секунд", exc.seconds)
        except Exception:
            LOGGER.exception("Ошибка обработки сообщения %s/%s", event.chat_id, event.id)

    print(
        f"Мониторинг запущен: {len(available_entities)} чатов, "
        f"offer score>={settings.min_score}, "
        f"knowledge={'on' if settings.knowledge_enabled else 'off'}"
        + (
            f" score>={settings.knowledge_min_score}"
            if settings.knowledge_enabled
            else ""
        )
        + f", twitter_priority={len(twitter_priority_by_id)}"
        + f", twitter_ai={'on' if twitter_ai is not None else 'off'}"
        + (
            f", twitter_strength=on limit={settings.twitter_daily_limit}/day"
            if twitter_strength is not None
            else ", twitter_strength=off"
        )
        + (
            f", knowledge_balance=on limit={settings.knowledge_daily_limit}/day"
            if settings.knowledge_balance_enabled
            else ", knowledge_balance=off"
        )
        + (f", digest={settings.daily_digest_hour_msk:02d}:00 MSK→{settings.daily_digest_target}" if digest_task else ", digest=off")
        + ". Для остановки нажми Ctrl+C."
    )
    try:
        await client.run_until_disconnected()
    finally:
        if digest_task is not None:
            digest_task.cancel()
            with suppress(asyncio.CancelledError):
                await digest_task
        storage.close()
        if knowledge_storage is not None:
            knowledge_storage.close()


async def backfill(settings: Settings, limit: int) -> None:
    selected = load_selected(settings.chats_file)
    matcher = OfferMatcher(settings.rules_file)
    storage = Storage(settings.database_path)
    client = make_client(settings)
    await client.start()
    saved = 0
    try:
        for index, selected_chat in enumerate(selected, start=1):
            try:
                entity = await client.get_input_entity(selected_chat.chat_id)
            except (ValueError, TypeError):
                LOGGER.warning("Чат недоступен: %s", selected_chat.title)
                continue
            print(f"[{index}/{len(selected)}] {selected_chat.title}")
            async for message in client.iter_messages(entity, limit=limit):
                text = (message.raw_text or "").strip()
                if not text:
                    continue
                result = matcher.evaluate(text)
                if result.score < settings.min_score:
                    continue
                sender = await message.get_sender()
                record = OfferRecord(
                    chat_id=selected_chat.chat_id,
                    message_id=message.id,
                    chat_title=selected_chat.title,
                    sender_id=getattr(sender, "id", None),
                    sender_name=get_display_name(sender) if sender else "неизвестно",
                    message_date=message.date.astimezone(timezone.utc),
                    text=text,
                    score=result.score,
                    matched_rules=", ".join(result.matched),
                    compensation=", ".join(result.compensation),
                    source_url=source_url(
                        selected_chat.chat_id,
                        selected_chat.username or None,
                        message.id,
                    ),
                )
                if storage.save(record):
                    saved += 1
        print(f"История обработана. Новых офферов в базе: {saved}")
        print("Backfill не отправляет сотни уведомлений в Избранное.")
    finally:
        storage.close()
        await client.disconnect()



