from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class TwitterPrioritySource:
    chat_id: int
    title: str
    tier: str
    min_score: int


def load_twitter_priority_sources(
    path: Path,
) -> dict[int, TwitterPrioritySource]:
    if not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    rows = payload.get("sources") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        raise ValueError(f"Некорректный Twitter priority config: {path}")

    result: dict[int, TwitterPrioritySource] = {}
    for index, row in enumerate(rows, start=1):
        if not isinstance(row, dict):
            raise ValueError(f"Twitter priority source #{index} должен быть объектом")
        try:
            chat_id = int(row["chat_id"])
            min_score = int(row["min_score"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(
                f"Некорректные chat_id/min_score у Twitter priority source #{index}"
            ) from exc
        title = str(row.get("title", "")).strip()
        tier = str(row.get("tier", "")).strip().upper()
        if not title or tier not in {"P0", "P1"} or min_score < 0:
            raise ValueError(f"Некорректный Twitter priority source #{index}")
        if chat_id in result:
            raise ValueError(f"Повтор chat_id в Twitter priority config: {chat_id}")
        result[chat_id] = TwitterPrioritySource(
            chat_id=chat_id,
            title=title,
            tier=tier,
            min_score=min_score,
        )
    return result

