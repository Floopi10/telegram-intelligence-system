from __future__ import annotations

import asyncio
import json
import math
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from .storage import Storage
from .twitter_niches import (
    NICHE_ORDER,
    classify_twitter_niche,
    is_twitter_radar_noise,
)

MSK = ZoneInfo("Europe/Moscow")


def _short(value: str, limit: int = 240) -> str:
    clean = " ".join(value.split())
    return clean if len(clean) <= limit else clean[: limit - 1] + "…"


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
    return (
        float(item.get("content_score") or 0) * 10
        + math.log10(max(views, 1)) * 2
        + velocity * 8
        + signal * 8
        + min(signal_rate * 100, 5)
    )


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
        niche = str(analysis.get("niche") or "").strip()
        if not niche or niche not in NICHE_ORDER:
            niche = classify_twitter_niche(
                str(analysis.get("topic") or ""),
                str(analysis.get("hook") or ""),
                str(analysis.get("analysis_ru") or ""),
            ) or ""
        if niche not in NICHE_ORDER:
            continue
        enriched = dict(item)
        enriched["analysis"] = analysis
        enriched["niche"] = niche
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
    if len(selected) < limit:
        selected_urls = {item["canonical_url"] for item in selected}
        for item in ranked:
            if item["canonical_url"] in selected_urls:
                continue
            selected.append(item)
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
    current = f"🐦 TWITTER RADAR · {now_msk:%d.%m.%Y}\nНайдено: {len(selected)}"
    for niche in NICHE_ORDER:
        items = [item for item in selected if item["niche"] == niche]
        if not items:
            continue
        block_lines = [f"\n{niche}"]
        for item in items:
            analysis = item["analysis"]
            hook = _short(str(analysis.get("hook") or analysis.get("topic") or "X-пост"), 130)
            views = int(item.get("views") or 0)
            metrics = f" · {views:,} views".replace(",", " ") if views else ""
            block_lines.extend([
                f"• [{item['content_score']}/10]{metrics} {hook}",
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
    twitter = select_twitter_radar(data.get("twitter_candidates", []), 5)
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
            hook = analysis.get("hook") or analysis.get("topic") or "X-пост"
            lines.append(f"• [{item['content_score']}/10] {_short(str(hook), 180)}")
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


async def daily_digest_loop(client, storage: Storage, *, target: str, hour_msk: int) -> None:
    while True:
        now_utc = datetime.now(timezone.utc)
        now_msk = now_utc.astimezone(MSK)
        digest_day = now_msk.date().isoformat()
        scheduled = datetime.combine(now_msk.date(), time(hour_msk, 0), tzinfo=MSK)
        if now_msk >= scheduled and not storage.digest_was_sent(digest_day):
            message = build_daily_digest(storage, now_utc)
            await client.send_message(target, message, link_preview=False)
            for part in build_twitter_radar_parts(storage, now_utc):
                await client.send_message(target, part, link_preview=False)
                await asyncio.sleep(1)
            storage.mark_digest_sent(digest_day)
            await asyncio.sleep(60)
            continue
        next_run = scheduled if now_msk < scheduled else scheduled + timedelta(days=1)
        delay = max(30.0, (next_run - now_msk).total_seconds())
        await asyncio.sleep(min(delay, 3600.0))
