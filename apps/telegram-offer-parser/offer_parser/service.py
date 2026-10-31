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
from .digest import daily_digest_loop, send_daily_digest_once
from .outbox import outbox_loop
from .matcher import MatchResult, OfferMatcher
from .storage import OfferRecord, Storage
from .twitter_priority import TwitterPrioritySource, load_twitter_priority_sources
from .vacancy_filter import evaluate_vacancy, is_vacancy_text
from .twitter_ai import (
    TwitterAIClient,
    TwitterAnalysis,
    XPreview,
    extract_x_preview,
    first_status_url,
    format_twitter_intelligence,
)
from .twitter_strength import XCollectorClient, decide_strength
from .twitter_evidence import extract_telegram_performance_evidence
from .twitter_niches import classify_twitter_niche, is_sport
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


class MessagePipeline:
    """Routes one Telegram message through the offer, X and knowledge streams."""

    LLM_CONCURRENCY = 3

    def __init__(
        self,
        *,
        settings: Settings,
        selected_by_id: dict[int, str],
        matcher: OfferMatcher,
        storage: Storage,
        knowledge_by_id: dict[int, str],
        knowledge_matcher: OfferMatcher | None,
        knowledge_storage: Storage | None,
        twitter_priority_by_id: dict[int, TwitterPrioritySource],
        twitter_ai: TwitterAIClient | None,
        twitter_strength: XCollectorClient | None,
    ) -> None:
        self.settings = settings
        self.selected_by_id = selected_by_id
        self.matcher = matcher
        self.storage = storage
        self.knowledge_by_id = knowledge_by_id
        self.knowledge_matcher = knowledge_matcher
        self.knowledge_storage = knowledge_storage
        self.twitter_priority_by_id = twitter_priority_by_id
        self.twitter_ai = twitter_ai
        self.twitter_strength = twitter_strength
        # Telethon runs handlers concurrently: daily counters and URL checks must not interleave.
        self._in_flight: set[str] = set()
        self._llm_gate = asyncio.Semaphore(self.LLM_CONCURRENCY)
        self._twitter_lock = asyncio.Lock()
        self._knowledge_lock = asyncio.Lock()

    async def handle(self, event) -> None:
        text = event.raw_text.strip()
        if not text:
            return
        if event.chat_id in self.selected_by_id:
            await self._guarded(self._handle_offer, self.storage, "offer", event, text)
        if (
            event.chat_id in self.knowledge_by_id
            and self.knowledge_matcher is not None
            and self.knowledge_storage is not None
        ):
            await self._guarded(self._handle_knowledge, self.knowledge_storage, "knowledge", event, text)

    async def _guarded(self, handler, storage: Storage, stream: str, event, text: str) -> None:
        try:
            await handler(event, text)
        except asyncio.CancelledError:
            raise
        except FloodWaitError as exc:
            LOGGER.warning("Telegram просит подождать %s секунд", exc.seconds)
            self._record_error(storage, stream, event, text, f"flood_wait:{exc.seconds}")
        except Exception as exc:
            LOGGER.exception("Ошибка обработки сообщения %s/%s", event.chat_id, event.id)
            self._record_error(storage, stream, event, text, type(exc).__name__)

    def _record_error(self, storage: Storage, stream: str, event, text: str, reason: str) -> None:
        title = self.selected_by_id.get(event.chat_id) or self.knowledge_by_id.get(event.chat_id) or ""
        try:
            storage.record_decision(
                chat_id=event.chat_id, message_id=event.id, chat_title=title,
                stream=stream, decision="error", reason=reason, score=None, text=text,
            )
        except Exception:
            LOGGER.exception("Не удалось записать ошибку в аудит")

    async def _handle_offer(self, event, text: str) -> None:
        result = self.matcher.evaluate(text)
        if result.score < self.settings.min_score:
            return
        offer = await _build_offer(event, self.selected_by_id[event.chat_id], result)
        if self.storage.save(offer):
            self.storage.enqueue_message(
                dedupe_key=f"offer:{offer.chat_id}:{offer.message_id}",
                target=self.settings.notify_target,
                body=format_notification(offer, result),
            )
            LOGGER.info("Оффер сохранён: %s/%s", offer.chat_id, offer.message_id)

    async def _handle_knowledge(self, event, text: str) -> None:
        priority = self.twitter_priority_by_id.get(event.chat_id)
        if priority is not None and self.twitter_ai is not None:
            url = first_status_url(text)
            if url and await self._handle_twitter(event, text, url, priority):
                return
        await self._handle_plain_knowledge(event, text, priority)

    def _record_knowledge(
        self, event, stream: str, decision: str, reason: str, score: int | None, text: str,
    ) -> None:
        self.knowledge_storage.record_decision(
            chat_id=event.chat_id, message_id=event.id,
            chat_title=self.knowledge_by_id[event.chat_id], stream=stream,
            decision=decision, reason=reason, score=score, text=text,
        )

    async def _handle_twitter(self, event, text: str, url: str, priority: TwitterPrioritySource) -> bool:
        """Return True when the message was consumed by the X stream."""
        storage = self.knowledge_storage
        if url in self._in_flight or storage.twitter_url_seen(url):
            self._record_knowledge(event, "twitter", "duplicate", "canonical_url", None, text)
            return True
        self._in_flight.add(url)
        try:
            preview = extract_x_preview(event.message)
            metrics = None
            if preview is None:
                # Telegram often omits the link preview; the post text can still come from X directly.
                if self.twitter_strength is None:
                    return False
                try:
                    metrics = await self.twitter_strength.collect(url)
                except Exception as exc:
                    LOGGER.warning("X-пост без превью недоступен %s: %s", url, type(exc).__name__)
                    return False
                collected_text = str(metrics.raw.get("text") or "").strip()
                if not collected_text:
                    return False
                preview = XPreview(url, "", collected_text[:5000])
            await self._analyze_twitter(event, text, preview, priority, metrics)
            return True
        finally:
            self._in_flight.discard(url)

    async def _analyze_twitter(self, event, text, preview, priority, metrics) -> None:
        storage = self.knowledge_storage
        chat_title = self.knowledge_by_id[event.chat_id]
        url = preview.canonical_url
        try:
            async with self._llm_gate:
                analysis = await self.twitter_ai.analyze(preview, text)
        except Exception as exc:
            # The URL stays unseen, so the next mention of this post gets a new attempt.
            LOGGER.warning("Twitter AI не ответил %s: %s", url, exc)
            self._record_knowledge(event, "twitter", "error", f"ai_error:{type(exc).__name__}", None, text)
            return

        telegram_evidence = extract_telegram_performance_evidence(text, preview.title, preview.description)
        niche = classify_twitter_niche(
            url, preview.title, preview.description, analysis.topic, analysis.analysis_ru,
        )
        sport = is_sport(preview.title, preview.description, analysis.topic, analysis.analysis_ru)
        final_keep = analysis.keep and niche is not None
        reason = (
            "sport_blocked" if sport
            else "no_niche" if niche is None
            else analysis.reason_ru or "ai_decision"
        )
        payload = _twitter_payload(analysis, niche, sport, telegram_evidence)
        payload["keep"] = final_keep
        payload["ai_keep"] = analysis.keep and niche is not None

        strength = None
        if final_keep and self.twitter_strength is not None:
            try:
                if metrics is None:
                    metrics = await self.twitter_strength.collect(url)
            except Exception as exc:
                final_keep = False
                reason = f"collector_error:{type(exc).__name__}"
                payload["keep"] = False
                LOGGER.warning("Twitter strength не смог получить метрики %s: %s", url, exc)
            else:
                async with self._twitter_lock:
                    author_rows, cohort_rows = storage.twitter_strength_baselines(
                        author=metrics.author, age_bucket=metrics.age_bucket, exclude_url=url,
                    )
                    kept_today, mechanics_today = storage.twitter_strength_daily_counts()
                    strength = decide_strength(
                        content_score=analysis.score,
                        metrics=metrics,
                        author_rows=author_rows,
                        cohort_rows=cohort_rows,
                        kept_today=kept_today,
                        mechanics_today=mechanics_today,
                        daily_limit=self.settings.twitter_daily_limit,
                        mechanics_limit=self.settings.twitter_mechanics_daily_limit,
                    )
                    final_keep = strength.keep
                    reason = strength.reason_ru
                    payload["keep"] = final_keep
                    payload["strength"] = {
                        "kind": strength.kind,
                        "reason_ru": strength.reason_ru,
                        "baseline_scope": strength.baseline_scope,
                        "baseline_size": strength.baseline_size,
                        "velocity_percentile": strength.velocity_percentile,
                        "signal_percentile": strength.signal_percentile,
                        "metrics": metrics.raw,
                    }
                    storage.save_twitter_strength(
                        canonical_url=url,
                        author=metrics.author,
                        chat_id=event.chat_id,
                        message_id=event.id,
                        chat_title=chat_title,
                        content_score=analysis.score,
                        metrics=metrics,
                        strength=strength,
                        decision="keep" if final_keep else "watch",
                        analysis=payload,
                    )

        storage.save_twitter_analysis(
            canonical_url=url,
            chat_id=event.chat_id,
            message_id=event.id,
            chat_title=chat_title,
            decision="keep" if final_keep else "reject",
            ai_score=analysis.score,
            topic=analysis.topic,
            risk_flags=analysis.risk_flags,
            analysis=payload,
        )
        if final_keep:
            storage.enqueue_message(
                dedupe_key=f"twitter:{url}",
                target=self.settings.knowledge_target,
                body=format_twitter_intelligence(
                    chat_title,
                    priority.tier,
                    preview,
                    analysis,
                    strength.metrics_block(metrics) if strength is not None else "",
                ),
            )
        self._record_knowledge(
            event, "twitter", "keep" if final_keep else "rejected", reason, analysis.score, text,
        )
        LOGGER.info(
            "Twitter AI: %s score=%s topic=%s url=%s",
            "keep" if final_keep else "reject", analysis.score, analysis.topic, url,
        )

    async def _handle_plain_knowledge(self, event, text: str, priority) -> None:
        if is_knowledge_noise(text):
            self._record_knowledge(event, "knowledge", "ignored", "knowledge_noise", None, text)
            LOGGER.info("Knowledge-мусор пропущен: %s/%s", event.chat_id, event.id)
            return
        vacancy = _is_vacancy_text(text)
        if vacancy:
            decision = evaluate_vacancy(text)
            if not decision.accepted:
                self._record_knowledge(event, "vacancy", "rejected", decision.reason, None, text)
                LOGGER.info(
                    "Вакансия пропущена: %s/%s reason=%s", event.chat_id, event.id, decision.reason,
                )
                return
        result = self.knowledge_matcher.evaluate(text)
        storage = self.knowledge_storage
        async with self._knowledge_lock:
            balance = None
            if self.settings.knowledge_balance_enabled and not vacancy:
                daily_total, daily_by_niche = storage.knowledge_balance_daily_counts()
                balance = evaluate_knowledge_balance(
                    text=text,
                    chat_title=self.knowledge_by_id[event.chat_id],
                    matcher_score=result.score,
                    daily_total=daily_total,
                    daily_by_niche=daily_by_niche,
                    daily_limit=self.settings.knowledge_daily_limit,
                )
                if not balance.accepted:
                    self._record_knowledge(event, "knowledge", "ignored", balance.reason, result.score, text)
                    return
            else:
                threshold = effective_knowledge_threshold(text, self.settings.knowledge_min_score, priority)
                if result.score < threshold:
                    self._record_knowledge(event, "knowledge", "ignored", "below_threshold", result.score, text)
                    return
            record = await _build_offer(event, self.knowledge_by_id[event.chat_id], result)
            item_kind = "vacancy" if vacancy else "knowledge"
            saved, save_reason = storage.save_unique(record, item_kind)
            self._record_knowledge(
                event, item_kind,
                "accepted" if saved else "duplicate",
                balance.reason if saved and balance is not None else save_reason,
                record.score, text,
            )
        if saved:
            storage.enqueue_message(
                dedupe_key=f"knowledge:{record.chat_id}:{record.message_id}",
                target=self.settings.knowledge_target,
                body=format_knowledge_notification(record, result, priority, balance),
            )
            LOGGER.info(
                "Knowledge сохранён: %s/%s score=%s priority=%s",
                record.chat_id, record.message_id, record.score,
                priority.tier if priority else "standard",
            )
        else:
            LOGGER.info(
                "Knowledge дубликат: %s/%s reason=%s", record.chat_id, record.message_id, save_reason,
            )


def _twitter_payload(analysis: TwitterAnalysis, niche: str | None, sport: bool, evidence) -> dict:
    return {
        "niche": niche or ("Спорт — отклонено" if sport else "Вне ниш — отклонено"),
        "score": analysis.score,
        "topic": analysis.topic,
        "hook": analysis.hook,
        "analysis_ru": analysis.analysis_ru,
        "key_points": list(analysis.key_points),
        "metrics_summary": analysis.metrics_summary,
        "performance_evidence": analysis.performance_evidence,
        "risk_flags": list(analysis.risk_flags),
        "reason_ru": analysis.reason_ru,
        "telegram_evidence": evidence.as_dict(),
    }


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

    pipeline = MessagePipeline(
        settings=settings,
        selected_by_id=selected_by_id,
        matcher=matcher,
        storage=storage,
        knowledge_by_id=knowledge_by_id,
        knowledge_matcher=knowledge_matcher,
        knowledge_storage=knowledge_storage,
        twitter_priority_by_id=twitter_priority_by_id,
        twitter_ai=twitter_ai,
        twitter_strength=twitter_strength,
    )

    client = make_client(settings)
    await client.start()
    background: list[asyncio.Task] = [
        asyncio.create_task(outbox_loop(
            client,
            [item for item in (storage, knowledge_storage) if item is not None],
        ))
    ]
    digest_enabled = settings.daily_digest_enabled and knowledge_storage is not None
    if digest_enabled:
        background.append(asyncio.create_task(
            daily_digest_loop(
                client, knowledge_storage, target=settings.daily_digest_target,
                hour_msk=settings.daily_digest_hour_msk,
                collector=twitter_strength,
            )
        ))

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
        for task in background:
            task.cancel()
        storage.close()
        if knowledge_storage is not None:
            knowledge_storage.close()
        await client.disconnect()
        raise RuntimeError("Ни один выбранный чат не доступен аккаунту")

    @client.on(events.NewMessage(chats=available_entities, incoming=True))
    async def on_message(event) -> None:
        await pipeline.handle(event)

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
        + (f", digest={settings.daily_digest_hour_msk:02d}:00 MSK→{settings.daily_digest_target}" if digest_enabled else ", digest=off")
        + ". Для остановки нажми Ctrl+C."
    )
    try:
        await client.run_until_disconnected()
    finally:
        for task in background:
            task.cancel()
        for task in background:
            with suppress(asyncio.CancelledError):
                await task
        storage.close()
        if knowledge_storage is not None:
            knowledge_storage.close()


async def send_digest_now(settings: Settings, *, force: bool = False) -> bool:
    if not settings.knowledge_enabled:
        raise RuntimeError("Knowledge-поток выключен; дайджест недоступен")
    storage = Storage(settings.knowledge_database_path)
    collector = None
    if settings.twitter_strength_enabled:
        try:
            collector = XCollectorClient(settings.x_collector_env_file)
        except RuntimeError as exc:
            LOGGER.warning("Метрики X не будут обновлены: %s", exc)
    client = make_client(settings)
    await client.start()
    try:
        return await send_daily_digest_once(
            client,
            storage,
            target=settings.daily_digest_target,
            force=force,
            collector=collector,
        )
    finally:
        storage.close()
        await client.disconnect()


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



