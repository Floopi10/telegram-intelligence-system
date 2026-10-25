from __future__ import annotations

import asyncio
import json
import logging
import math
import re
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from .storage import Storage
from .twitter_niches import (
    NICHE_ORDER,
    classify_twitter_niche,
    is_twitter_radar_noise,
)
from .twitter_evidence import compact_evidence, evidence_rank_bonus

MSK = ZoneInfo("Europe/Moscow")
LOGGER = logging.getLogger(__name__)


def _short(value: str, limit: int = 240) -> str:
    clean = " ".join(value.split())
    return clean if len(clean) <= limit else clean[: limit - 1] + "…"


def _clean_hook(value: str) -> str:
    clean = " ".join(str(value or "").split())
    replacements = (
        (r"контрарн(?:ый|ое)statement", "контрарное утверждение"),
        (r"\bseguido\b", "после чего"),
    )
    for pattern, replacement in replacements:
        clean = re.sub(pattern, replacement, clean, flags=re.I)
    clean = re.sub(
        r"(?<=[А-Яа-яЁё])(?=[A-Za-z])|(?<=[A-Za-z])(?=[А-Яа-яЁё])",
        " ",
        clean,
    )
    return clean


NICHE_LIMITS = {
    "AI": 9,
    "Финансы": 8,
    "Роботы": 7,
    "Девушки и lifestyle": 6,
    "Web3": 7,
    "Технологии": 6,
    "Маркетинг и контент": 7,
    "Игры": 5,
}


def _candidate_rank(item: dict) -> float:
    views = int(item.get("views") or 0)
    velocity = float(item.get("velocity_percentile") or 0)
    signal = float(item.get("signal_percentile") or 0)
    signal_rate = float(item.get("signal_rate") or 0)
    tier_bonus = 100 if item.get("quality_tier") == "winner" else 0
    analysis = item.get("analysis") or {}
    evidence_bonus = evidence_rank_bonus(analysis.get("telegram_evidence"))
    return (
        tier_bonus
        + math.log10(max(views, 1)) * 15
        + velocity * 30
        + signal * 25
        + min(signal_rate * 100, 10)
        + float(item.get("content_score") or 0) * 2
        + evidence_bonus
    )


def _quality_tier(item: dict) -> str | None:
    """Metric-first gate. LLM score alone never makes a radar item strong."""
    views = item.get("views")
    if views is None:
        return None
    views = int(views)
    likes = int(item.get("likes") or 0)
    reposts = int(item.get("reposts") or 0)
    bookmarks = int(item.get("bookmarks") or 0)
    velocity = float(item.get("velocity_percentile") or 0)
    signal = float(item.get("signal_percentile") or 0)
    views_per_hour = float(item.get("views_per_hour") or 0)
    signal_rate = float(item.get("signal_rate") or 0)
    content_score = int(item.get("content_score") or 0)
    decision = str(item.get("strength_decision") or "")
    kind = str(item.get("strength_kind") or "")

    absolute_winner = (
        views >= 10_000 or likes >= 200 or reposts >= 50 or bookmarks >= 50
    )
    relative_winner = (
        views >= 100
        and decision == "keep"
        and kind != "полезная механика"
        and (velocity >= 0.80 or signal >= 0.80)
    )
    if absolute_winner or relative_winner:
        return "winner"

    promising_mechanic = (
        views >= 250
        and content_score >= 8
        and (
            decision == "keep"
            or velocity >= 0.75
            or signal >= 0.75
            or views_per_hour >= 100
            or signal_rate >= 0.02
        )
    )
    return "mechanic" if promising_mechanic else None


def select_twitter_radar(candidates: list[dict], limit: int = 50) -> list[dict]:
    unique: dict[str, dict] = {}
    for item in candidates:
        url = str(item.get("canonical_url") or "")
        if not url or url in unique:
            continue
        try:
            analysis = json.loads(item.get("analysis_json") or "{}")
        except json.JSONDecodeError:
            continue
        if int(item.get("content_score") or analysis.get("score") or 0) < 5:
            continue
        if is_twitter_radar_noise(
            str(analysis.get("topic") or ""),
            str(analysis.get("hook") or ""),
            str(analysis.get("analysis_ru") or ""),
            " ".join(str(value) for value in analysis.get("risk_flags", []) or []),
        ):
            continue
        niche = classify_twitter_niche(
            str(analysis.get("topic") or ""),
            str(analysis.get("hook") or ""),
            str(analysis.get("analysis_ru") or ""),
        ) or ""
        if niche not in NICHE_ORDER:
            continue
        enriched = dict(item)
        quality_tier = _quality_tier(enriched)
        if quality_tier is None:
            continue
        enriched["analysis"] = analysis
        enriched["niche"] = niche
        enriched["quality_tier"] = quality_tier
        enriched["rank"] = _candidate_rank(enriched)
        unique[url] = enriched

    ranked = sorted(unique.values(), key=lambda row: row["rank"], reverse=True)
    selected: list[dict] = []
    counts = {niche: 0 for niche in NICHE_ORDER}
    for item in ranked:
        niche = item["niche"]
        if counts[niche] >= NICHE_LIMITS[niche]:
            continue
        selected.append(item)
        counts[niche] += 1
        if len(selected) >= limit:
            break
    return selected


def build_twitter_radar_parts(storage: Storage, now_utc: datetime | None = None) -> list[str]:
    now_utc = now_utc or datetime.now(timezone.utc)
    now_msk = now_utc.astimezone(MSK)
    start_msk = datetime.combine(now_msk.date(), time.min, tzinfo=MSK)
    data = storage.digest_items(start_msk.astimezone(timezone.utc))
    selected = select_twitter_radar(data.get("twitter_candidates", []), 50)
    if not selected:
        return []

    parts: list[str] = []
    winners = sum(item["quality_tier"] == "winner" for item in selected)
    mechanics = len(selected) - winners
    current = (
        f"🐦 TWITTER RADAR · {now_msk:%d.%m.%Y}\n"
        f"Залетевшие: {winners} · Перспективные механики: {mechanics}"
    )
    for niche in NICHE_ORDER:
        items = [item for item in selected if item["niche"] == niche]
        if not items:
            continue
        block_lines = [f"\n{niche}"]
        for item in items:
            analysis = item["analysis"]
            hook = _short(_clean_hook(str(analysis.get("hook") or analysis.get("topic") or "X-пост")), 130)
            views = int(item.get("views") or 0)
            metrics = f" · {views:,} views".replace(",", " ") if views else ""
            evidence = compact_evidence(analysis.get("telegram_evidence"))
            evidence_suffix = f" · {evidence}" if evidence else ""
            label = "ЗАЛЕТЕЛ" if item["quality_tier"] == "winner" else "МЕХАНИКА"
            block_lines.extend([
                f"• [{label} · {item['content_score']}/10]{metrics}{evidence_suffix} {hook}",
                str(item["canonical_url"]),
            ])
        block = "\n".join(block_lines)
        if len(current) + len(block) > 3800:
            parts.append(current)
            current = f"🐦 TWITTER RADAR · продолжение\n{block.lstrip()}"
        else:
            current += block
    parts.append(current)
    return parts


def build_daily_digest(storage: Storage, now_utc: datetime | None = None) -> str:
    now_utc = now_utc or datetime.now(timezone.utc)
    now_msk = now_utc.astimezone(MSK)
    start_msk = datetime.combine(now_msk.date(), time.min, tzinfo=MSK)
    start_utc = start_msk.astimezone(timezone.utc)
    data = storage.digest_items(start_utc)

    lines = [f"📊 ДАЙДЖЕСТ · {now_msk:%d.%m.%Y}", ""]
    vacancies = data["vacancies"][:5]
    twitter = [
        item for item in select_twitter_radar(data.get("twitter_candidates", []), 50)
        if item["quality_tier"] == "winner"
    ][:5]
    knowledge = data["knowledge"][:5]

    lines.append("💼 Вакансии")
    if vacancies:
        for item in vacancies:
            lines.append(f"• [{item['score']}] {_short(item['text'])}")
            if item["source_url"]:
                lines.append(item["source_url"])
    else:
        lines.append("• Подходящих вакансий сегодня нет")

    lines.extend(["", "🐦 Twitter-находки"])
    if twitter:
        for item in twitter:
            analysis = item["analysis"]
            hook = _clean_hook(str(analysis.get("hook") or analysis.get("topic") or "X-пост"))
            evidence = compact_evidence(analysis.get("telegram_evidence"))
            evidence_prefix = f"{evidence} · " if evidence else ""
            lines.append(f"• [{item['content_score']}/10] {_short(evidence_prefix + str(hook), 180)}")
            lines.append(item["canonical_url"])
    else:
        lines.append("• Сильных находок сегодня нет")

    lines.extend(["", "🧠 Web3 и knowledge"])
    if knowledge:
        for item in knowledge:
            lines.append(f"• [{item['score']}] {_short(item['text'])}")
            if item["source_url"]:
                lines.append(item["source_url"])
    else:
        lines.append("• Сильных knowledge-находок сегодня нет")

    counts = data["audit_counts"]
    accepted = sum(value for key, value in counts.items() if key == "accepted" or key == "keep")
    duplicates = counts.get("duplicate", 0)
    rejected = sum(value for key, value in counts.items() if key in {"rejected", "ignored"})
    lines.extend(["", f"Фильтр за сутки: принято {accepted} · дубликаты {duplicates} · отклонено {rejected}"])
    rendered = "\n".join(lines)
    return rendered if len(rendered) <= 3900 else rendered[:3899].rstrip() + "…"


async def send_daily_digest_once(
    client,
    storage: Storage,
    *,
    target: str,
    now_utc: datetime | None = None,
    force: bool = False,
) -> bool:
    now_utc = now_utc or datetime.now(timezone.utc)
    digest_day = now_utc.astimezone(MSK).date().isoformat()
    if not force and storage.digest_was_sent(digest_day):
        return False

    message = build_daily_digest(storage, now_utc)
    await client.send_message(target, message, link_preview=False)
    for part in build_twitter_radar_parts(storage, now_utc):
        await client.send_message(target, part, link_preview=False)
        await asyncio.sleep(1)
    storage.mark_digest_sent(digest_day)
    LOGGER.info("Дайджест отправлен: day=%s target=%s", digest_day, target)
    return True


async def daily_digest_loop(client, storage: Storage, *, target: str, hour_msk: int) -> None:
    while True:
        now_utc = datetime.now(timezone.utc)
        now_msk = now_utc.astimezone(MSK)
        digest_day = now_msk.date().isoformat()
        scheduled = datetime.combine(now_msk.date(), time(hour_msk, 0), tzinfo=MSK)
        if now_msk >= scheduled and not storage.digest_was_sent(digest_day):
            try:
                await send_daily_digest_once(
                    client,
                    storage,
                    target=target,
                    now_utc=now_utc,
                )
            except asyncio.CancelledError:
                raise
            except Exception:
                LOGGER.exception(
                    "Ошибка отправки дайджеста; повтор через 5 минут: day=%s target=%s",
                    digest_day,
                    target,
                )
                await asyncio.sleep(300)
                continue
            await asyncio.sleep(60)
            continue
        next_run = scheduled if now_msk < scheduled else scheduled + timedelta(days=1)
        delay = max(30.0, (next_run - now_msk).total_seconds())
        await asyncio.sleep(min(delay, 3600.0))
