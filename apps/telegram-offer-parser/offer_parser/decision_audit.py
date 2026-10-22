from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .storage import Storage


def write_decision_audit(storage: Storage, hours: int, output: Path) -> Path:
    since = datetime.now(timezone.utc) - timedelta(hours=hours)
    rows = storage.audit_rows(since)
    decisions = Counter(row["decision"] for row in rows)
    reasons = Counter(row["reason"] for row in rows)
    lines = [
        "# Passive Filter Audit",
        "",
        f"- Период: **{hours} часов**",
        f"- Решений: **{len(rows)}**",
        "",
        "## Решения",
        "",
    ]
    lines.extend(f"- {key}: {value}" for key, value in decisions.most_common())
    lines.extend(["", "## Причины", ""])
    lines.extend(f"- {key}: {value}" for key, value in reasons.most_common(15))
    lines.extend(["", "## Предложения", ""])
    if reasons.get("below_threshold", 0):
        lines.append("- Просмотреть примеры below_threshold перед изменением score: среди них могут быть ложные пропуски.")
    if reasons.get("payment_not_confirmed", 0):
        lines.append("- Не ослаблять paid-фильтр автоматически; проверить 5 примеров вручную.")
    duplicate_count = decisions.get("duplicate", 0)
    if duplicate_count:
        lines.append(f"- Дедупликация убрала {duplicate_count} повторов; проверить, не объединены ли разные вакансии одной компании.")
    if not rows:
        lines.append("- Данных пока недостаточно; повторить аудит после 48 часов работы.")
    lines.extend(["", "## Последние отклонённые примеры", ""])
    rejected = [row for row in rows if row["decision"] in {"rejected", "ignored"}][:20]
    for row in rejected:
        preview = " ".join(row["text_preview"].split())[:300]
        lines.append(f"- **{row['chat_title']}** · `{row['reason']}` · score={row['score']}: {preview}")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return output
