from __future__ import annotations

import asyncio
import json
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from dotenv import dotenv_values


AUTHOR_RE = re.compile(r"^https://x\.com/([A-Za-z0-9_]{1,15})/status/\d+$", re.I)


@dataclass(frozen=True)
class XMetrics:
    author: str
    published_at: datetime
    collected_at: datetime
    views: int | None
    likes: int | None
    reposts: int | None
    replies: int | None
    bookmarks: int | None
    age_hours: float
    views_per_hour: float | None
    signal_rate: float | None
    age_bucket: str
    raw: dict


@dataclass(frozen=True)
class StrengthDecision:
    keep: bool
    kind: str
    reason_ru: str
    baseline_scope: str
    baseline_size: int
    velocity_percentile: float | None
    signal_percentile: float | None

    def metrics_block(self, metrics: XMetrics) -> str:
        def value(number: int | None) -> str:
            return "недоступно" if number is None else f"{number:,}".replace(",", " ")

        lines = [
            "Реальные метрики X:",
            f"- Просмотры: {value(metrics.views)}",
            f"- Лайки: {value(metrics.likes)}",
            f"- Репосты: {value(metrics.reposts)}",
            f"- Ответы: {value(metrics.replies)}",
            f"- Закладки: {value(metrics.bookmarks)}",
            f"- Возраст поста: {metrics.age_hours:.1f} ч",
        ]
        if metrics.views_per_hour is not None:
            lines.append(f"- Скорость: {metrics.views_per_hour:,.0f} просмотров/час".replace(",", " "))
        if metrics.signal_rate is not None:
            lines.append(f"- Взвешенная реакция: {metrics.signal_rate * 100:.2f}%")
        lines.extend([
            f"- Класс: {self.kind}",
            f"- Основание: {self.reason_ru}",
        ])
        return "\n".join(lines)


class XCollectorClient:
    def __init__(self, env_path: Path, timeout: int = 150):
        values = dotenv_values(env_path)
        self.base_url = str(values.get("X_COLLECTOR_URL") or "").strip().rstrip("/")
        self.token = str(values.get("X_COLLECTOR_TOKEN") or "").strip()
        self.timeout = timeout
        # The collector drives one browser at a time; queue here so waiting does not eat the HTTP timeout.
        self._gate = asyncio.Semaphore(1)
        if not self.base_url or not self.token:
            raise RuntimeError(f"X collector не настроен в {env_path}")

    async def collect(self, url: str) -> XMetrics:
        async with self._gate:
            raw = await asyncio.to_thread(self._request, url)
        return parse_metrics(url, raw)

    def _request(self, url: str) -> dict:
        request = urllib.request.Request(
            self.base_url + "/collect",
            data=json.dumps({"url": url}).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.token}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
        if not isinstance(payload, dict) or payload.get("error"):
            raise RuntimeError(f"X collector error: {payload}")
        return payload


def parse_number(value: object) -> int | None:
    if value is None:
        return None
    text = str(value).replace("\u202f", "").replace("\xa0", "").replace(" ", "")
    match = re.search(r"([0-9][0-9,.]*)([KMB]?)", text, re.I)
    if not match:
        return None
    number = match.group(1)
    suffix = match.group(2).upper()
    if "," in number and "." not in number:
        tail = number.rsplit(",", 1)[-1]
        number = number.replace(",", "." if suffix and len(tail) <= 2 else "")
    else:
        number = number.replace(",", "")
    try:
        return int(float(number) * {"": 1, "K": 1_000, "M": 1_000_000, "B": 1_000_000_000}[suffix])
    except ValueError:
        return None


def parse_time(value: object) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def bucket_for(age_hours: float) -> str:
    if age_hours < 6:
        return "under_6h"
    if age_hours < 30:
        return "6_to_30h"
    if age_hours < 168:
        return "30_to_168h"
    return "mature"


def parse_metrics(url: str, raw: dict) -> XMetrics:
    match = AUTHOR_RE.match(url)
    author = str(raw.get("author") or (match.group(1) if match else "unknown")).casefold()
    published = parse_time(raw["published_at"])
    collected = parse_time(raw.get("collected_at") or datetime.now(timezone.utc).isoformat())
    age_hours = max((collected - published).total_seconds() / 3600, 0.05)
    counts = {
        key: parse_number(raw.get(key))
        for key in ("views", "likes", "reposts", "replies", "bookmarks")
    }
    views = counts["views"]
    views_per_hour = views / age_hours if views is not None else None
    signal_rate = None
    if views:
        # Это наш индекс полезного действия, а не заявленные веса алгоритма X.
        weighted = (
            (counts["likes"] or 0)
            + 2 * (counts["replies"] or 0)
            + 3 * (counts["reposts"] or 0)
            + 4 * (counts["bookmarks"] or 0)
        )
        signal_rate = weighted / views
    return XMetrics(
        author=author,
        published_at=published,
        collected_at=collected,
        views=views,
        likes=counts["likes"],
        reposts=counts["reposts"],
        replies=counts["replies"],
        bookmarks=counts["bookmarks"],
        age_hours=age_hours,
        views_per_hour=views_per_hour,
        signal_rate=signal_rate,
        age_bucket=bucket_for(age_hours),
        raw=raw,
    )


def percentile(value: float | None, samples: list[float]) -> float | None:
    if value is None or not samples:
        return None
    return sum(1 for sample in samples if sample <= value) / len(samples)


def decide_strength(
    *,
    content_score: int,
    metrics: XMetrics,
    author_rows: list[dict],
    cohort_rows: list[dict],
    kept_today: int,
    mechanics_today: int,
    daily_limit: int,
    mechanics_limit: int,
) -> StrengthDecision:
    usable_author = [row for row in author_rows if row.get("views_per_hour") is not None and row.get("signal_rate") is not None]
    usable_cohort = [row for row in cohort_rows if row.get("views_per_hour") is not None and row.get("signal_rate") is not None]
    if len(usable_author) >= 5:
        baseline, scope = usable_author, "author"
    elif len(usable_cohort) >= 20:
        baseline, scope = usable_cohort, "cohort"
    else:
        baseline, scope = [], "warming_up"

    velocity = percentile(metrics.views_per_hour, [float(row["views_per_hour"]) for row in baseline])
    signal = percentile(metrics.signal_rate, [float(row["signal_rate"]) for row in baseline])

    if kept_today >= daily_limit:
        return StrengthDecision(False, "watch", "дневной лимит Twitter заполнен", scope, len(baseline), velocity, signal)

    if baseline and metrics.views is not None and metrics.views >= 100:
        if metrics.age_hours < 6:
            strong = content_score >= 8 and velocity is not None and signal is not None and velocity >= 0.90 and signal >= 0.50
            if strong:
                return StrengthDecision(True, "ранний кандидат", "скорость в верхних 10% при сильной структуре", scope, len(baseline), velocity, signal)
        else:
            strong = (
                content_score >= 7
                and velocity is not None
                and signal is not None
                and ((velocity >= 0.80 and signal >= 0.50) or (velocity >= 0.50 and signal >= 0.80))
            )
            if strong:
                return StrengthDecision(True, "подтверждённый победитель", "пост сильнее своего базового распределения", scope, len(baseline), velocity, signal)

    if content_score >= 9 and mechanics_today < mechanics_limit:
        return StrengthDecision(True, "полезная механика", "сильная применимая механика; метрики ещё не подтверждают победителя", scope, len(baseline), velocity, signal)

    return StrengthDecision(False, "watch", "недостаточно относительных метрик или практической ценности", scope, len(baseline), velocity, signal)
