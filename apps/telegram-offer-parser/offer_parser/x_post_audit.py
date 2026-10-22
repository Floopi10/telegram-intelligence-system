from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Audit:
    score: int
    hook: int
    specificity: int
    utility: int
    retention: int
    shareability: int
    warnings: tuple[str, ...]


def audit_post(text: str) -> Audit:
    """Deterministic, API-free editorial audit. Never invents X metrics."""
    clean = " ".join(text.split())
    low = clean.casefold()
    first = clean[:220]
    hook = 4
    if re.search(r"\d", first): hook += 2
    if re.search(r"\b(?:how|why|built|launched|free|just|never|here(?:'s| is)|как|почему)\b", first, re.I): hook += 2
    if len(first) <= 140: hook += 1
    specificity = 3 + min(3, len(re.findall(r"\b\d+(?:[.,]\d+)?[%kmbx]?\b", low)))
    if re.search(r"https?://", clean): specificity += 1
    utility = 3
    if re.search(r"\b(?:guide|breakdown|framework|template|prompt|steps?|гайд|разбор|шаблон|промпт|шаг)\b", low): utility += 4
    retention = 3
    if len(clean) >= 280: retention += 2
    if re.search(r"(?:\n[-•]|\b1[.)]\s|\b2[.)]\s)", text): retention += 2
    shareability = 3
    if utility >= 6: shareability += 2
    if re.search(r"\b(?:free|save|reference|resource|чеклист|бесплат)\b", low): shareability += 2
    warnings = []
    if re.search(r"\b(?:guaranteed|100%|миллион гарантирован|секретный алгоритм)\b", low):
        warnings.append("неподтвержденное обещание")
    if len(clean) < 80: warnings.append("слишком мало материала для глубокого разбора")
    parts = [min(10, x) for x in (hook, specificity, utility, retention, shareability)]
    score = round(sum(parts) * 2) - 8 * len(warnings)
    return Audit(max(0, min(100, score)), *parts, tuple(warnings))


def render_audit(text: str, metrics: dict[str, int] | None = None) -> str:
    result = audit_post(text)
    metrics_line = "не зафиксированы; вывод о залёте не делаем"
    if metrics:
        allowed = ("views", "likes", "replies", "reposts", "bookmarks")
        metrics_line = ", ".join(f"{key}: {int(metrics[key])}" for key in allowed if key in metrics) or metrics_line
    warning = ", ".join(result.warnings) or "нет"
    return (
        "## Аудит поста\n"
        f"- Редакционный score: **{result.score}/100**\n"
        f"- Хук: {result.hook}/10\n"
        f"- Конкретика и доказательность: {result.specificity}/10\n"
        f"- Практическая ценность: {result.utility}/10\n"
        f"- Удержание: {result.retention}/10\n"
        f"- Потенциал сохранения/пересылки: {result.shareability}/10\n"
        f"- Публичные метрики X: {metrics_line}\n"
        f"- Ограничения: {warning}. Публичные метрики не показывают dwell time, private shares и copy-link.\n"
    )
