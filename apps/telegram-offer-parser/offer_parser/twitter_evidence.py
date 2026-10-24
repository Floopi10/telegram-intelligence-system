from __future__ import annotations

import re
import math
from dataclasses import asdict, dataclass


NUMBER = r"(?P<number>\d+(?:[.,]\d+)?)\s*(?P<suffix>k|к|m|м|тыс(?:яч[аи]?)?|млн)?"


def _scaled(match: re.Match[str]) -> int:
    value = float(match.group("number").replace(",", "."))
    suffix = (match.group("suffix") or "").casefold()
    if suffix in {"k", "к"} or suffix.startswith("тыс"):
        value *= 1_000
    elif suffix in {"m", "м"} or suffix == "млн":
        value *= 1_000_000
    return int(value)


def _scaled_text(value: str) -> int:
    match = re.search(NUMBER, value, re.I)
    if not match:
        raise ValueError(f"number not found in {value!r}")
    return _scaled(match)


def _first(
    patterns: tuple[str, ...],
    text: str,
    max_value: int = 1_000_000_000,
) -> int | None:
    for pattern in patterns:
        for match in re.finditer(pattern, text, re.I):
            value = _scaled(match)
            if value <= max_value:
                return value
    return None


@dataclass(frozen=True)
class TelegramPerformanceEvidence:
    """Explicit outcome claims found next to an X URL in a Telegram message.

    These values are self-reported context. They improve ranking and explainability,
    but never replace metrics collected directly from X.
    """

    claimed_views: int | None = None
    claimed_likes: int | None = None
    followers_gained: int | None = None
    result_hours: float | None = None
    revenue_usd: int | None = None
    content_format: str | None = None

    @property
    def follower_conversion(self) -> float | None:
        if not self.claimed_views or self.followers_gained is None:
            return None
        return self.followers_gained / self.claimed_views

    @property
    def has_outcome(self) -> bool:
        return any((
            self.claimed_views,
            self.claimed_likes,
            self.followers_gained,
            self.revenue_usd,
        ))

    def as_dict(self) -> dict:
        result = asdict(self)
        result["follower_conversion"] = self.follower_conversion
        result["evidence_level"] = "self_reported" if self.has_outcome else "none"
        return result


def extract_telegram_performance_evidence(*values: str) -> TelegramPerformanceEvidence:
    text = " ".join(str(value or "") for value in values)
    low = " ".join(text.split()).casefold()

    views = _first((
        rf"{NUMBER}\s*(?:просмотр\w*|views?\b)",
        rf"(?:просмотр\w*|views?\b)\s*[:=+-]?\s*{NUMBER}",
    ), low)
    likes = _first((
        rf"{NUMBER}\s*(?:лайк\w*|likes?\b)",
        rf"(?:лайк\w*|likes?\b)\s*[:=+-]?\s*{NUMBER}",
    ), low)
    followers = _first((
        rf"\+\s*{NUMBER}\s*(?:пдп|подпис\w*|followers?\b|subs?\b)",
        rf"(?:получил\w*|набрал\w*|лутанул\w*|принес\w*|дало)\s*(?:(?:около|больше|примерно)\s*)?{NUMBER}\s*(?:пдп|подпис\w*|followers?\b)",
        rf"(?:пдп|подпис\w*|followers?\b)\s*[:=]\s*\+\s*{NUMBER}",
    ), low)

    hours: float | None = None

    revenue = _first((
        rf"(?:заработал\w*|получил\w*|лутанул\w*|выплат\w*|монетизац\w*|earned|revenue|made)\D{{0,40}}?\$\s*{NUMBER}",
        rf"(?:заработал\w*|получил\w*|лутанул\w*|выплат\w*|монетизац\w*|earned|revenue|made)\D{{0,40}}?{NUMBER}\s*(?:usd\b|usdt\b|\$)",
        rf"\+\s*{NUMBER}\s*(?:usd\b|usdt\b|\$)",
    ), low)

    # Pair the closest explicit view/follower mentions. This prevents a long
    # case study with several unrelated figures from producing a fake ratio.
    number_token = r"\d+(?:[.,]\d+)?\s*(?:k|к|m|м|тыс(?:яч[аи]?)?|млн)?"
    view_mentions = [
        (match.start(), _scaled_text(match.group(0)))
        for pattern in (
            rf"{number_token}\s*(?:просмотр\w*|views?\b)",
            rf"(?:просмотр\w*|views?\b)\s*[:=+-]?\s*{number_token}",
        )
        for match in re.finditer(pattern, low, re.I)
    ]
    follower_mentions = [
        (match.start(), _scaled_text(match.group(0)))
        for pattern in (
            rf"\+\s*{number_token}\s*(?:пдп|подпис\w*|followers?\b|subs?\b)",
            rf"{number_token}\s*\+\s*(?:пдп|подпис\w*|followers?\b|subs?\b)",
            rf"(?:получил\w*|набрал\w*|лутанул\w*|принес\w*|дало)\s*(?:(?:около|больше|примерно)\s*)?{number_token}\s*(?:пдп|подпис\w*|followers?\b)",
        )
        for match in re.finditer(pattern, low, re.I)
    ]
    paired_anchor: int | None = None
    if view_mentions and follower_mentions:
        distance, paired_view, paired_followers, view_pos, follower_pos = min(
            (abs(view_pos - follower_pos), view_value, follower_value, view_pos, follower_pos)
            for view_pos, view_value in view_mentions
            for follower_pos, follower_value in follower_mentions
        )
        if distance <= 180:
            views, followers = paired_view, paired_followers
            paired_anchor = follower_pos

    metric_anchor = paired_anchor
    if metric_anchor is None and follower_mentions:
        metric_anchor = follower_mentions[0][0]
    if metric_anchor is None and view_mentions:
        metric_anchor = view_mentions[0][0]
    durations: list[tuple[int, float]] = []
    for duration in re.finditer(
        r"(?:за|через|в течение)\s*(\d+(?:[.,]\d+)?)\s*(час\w*|дн(?:я|ей|ь)?|сут\w*|недел\w*|месяц\w*)",
        low,
        re.I,
    ):
        amount = float(duration.group(1).replace(",", "."))
        unit = duration.group(2)
        multiplier = 720 if unit.startswith("месяц") else 168 if unit.startswith("недел") else 24 if unit.startswith(("дн", "сут")) else 1
        durations.append((duration.start(), amount * multiplier))
    if metric_anchor is not None and durations:
        duration_distance, candidate_hours = min(
            (abs(position - metric_anchor), value) for position, value in durations
        )
        if duration_distance <= 180:
            hours = candidate_hours

    formats = (
        ("статья", r"\b(?:статья|article|substack)\b"),
        ("короткое видео", r"\b(?:shorts?|reels?|коротк\w*\s+видео|нарезк\w*)\b"),
        ("видео", r"\b(?:видео|video|ролик)\b"),
        ("визуализация", r"\b(?:визуализац\w*|visuali[sz]ation|3d\s+visual)\b"),
        ("тред", r"\b(?:тред|thread)\b"),
        ("quote-пост", r"\b(?:квот|quote)\b"),
    )
    content_format = next((name for name, pattern in formats if re.search(pattern, low, re.I)), None)

    return TelegramPerformanceEvidence(
        claimed_views=views,
        claimed_likes=likes,
        followers_gained=followers,
        result_hours=hours,
        revenue_usd=revenue,
        content_format=content_format,
    )


def evidence_rank_bonus(evidence: dict | None) -> float:
    if not evidence or evidence.get("evidence_level") != "self_reported":
        return 0.0
    bonus = 0.0
    conversion = evidence.get("follower_conversion")
    if conversion is not None:
        conversion = float(conversion)
        bonus += min(conversion * 2_000, 18)
    if evidence.get("followers_gained"):
        bonus += min(float(evidence["followers_gained"]) / 100, 8)
    if evidence.get("claimed_views"):
        bonus += min(max(math.log10(float(evidence["claimed_views"])) - 2, 0) * 1.5, 5)
    if evidence.get("result_hours"):
        bonus += 4
    if evidence.get("revenue_usd"):
        bonus += 5
    if evidence.get("content_format"):
        bonus += 2
    return min(bonus, 30)


def compact_evidence(evidence: dict | None) -> str:
    if not evidence or evidence.get("evidence_level") != "self_reported":
        return ""
    parts: list[str] = []
    if evidence.get("claimed_views") is not None:
        parts.append(f"заявлено {int(evidence['claimed_views']):,} views".replace(",", " "))
    if evidence.get("claimed_likes") is not None:
        parts.append(f"заявлено {int(evidence['claimed_likes']):,} likes".replace(",", " "))
    if evidence.get("followers_gained") is not None:
        parts.append(f"+{int(evidence['followers_gained']):,} подписчиков".replace(",", " "))
    if evidence.get("result_hours") is not None:
        hours = float(evidence["result_hours"])
        parts.append(f"за {hours / 24:g} дн." if hours >= 24 and hours % 24 == 0 else f"за {hours:g} ч")
    if evidence.get("follower_conversion") is not None:
        parts.append(f"конверсия {float(evidence['follower_conversion']) * 100:.3f}%")
    if evidence.get("revenue_usd") is not None:
        parts.append(f"${int(evidence['revenue_usd']):,}".replace(",", " "))
    if evidence.get("content_format"):
        parts.append(str(evidence["content_format"]))
    return " · ".join(parts)
