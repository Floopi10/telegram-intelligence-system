from __future__ import annotations

import asyncio
import csv
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from telethon.errors import FloodWaitError

from .chats import load_selected
from .config import Settings
from .exclusions import filter_excluded
from .matcher import OfferMatcher
from .service import make_client, source_url


AUDIT_FIELDS = (
    "chat_id",
    "chat_title",
    "username",
    "status",
    "message_id",
    "message_date",
    "score",
    "matched_rules",
    "source_url",
    "excerpt",
    "error",
)

TOKEN_RE = re.compile(r"[a-zа-яё][a-zа-яё0-9_-]{3,}", re.IGNORECASE)
STOP_WORDS = {
    "about", "after", "also", "been", "before", "being", "between", "could",
    "from", "have", "here", "into", "more", "most", "only", "other", "some",
    "that", "their", "them", "then", "there", "these", "they", "this", "very",
    "what", "when", "where", "which", "while", "with", "would", "your",
    "будет", "были", "было", "быть", "вами", "всего", "всех", "если", "есть",
    "здесь", "какие", "когда", "который", "может", "можно", "очень", "после",
    "потом", "просто", "сейчас", "также", "того", "только", "этого", "этот",
}


def _topics(rows: list[dict[str, str]], limit: int = 30) -> list[tuple[str, int]]:
    counter: Counter[str] = Counter()
    for row in rows:
        if row["status"] != "ok":
            continue
        tokens = {
            token.casefold()
            for token in TOKEN_RE.findall(row["excerpt"])
            if token.casefold() not in STOP_WORDS
        }
        counter.update(tokens)
    return counter.most_common(limit)


def render_summary(rows: list[dict[str, str]], csv_path: Path) -> str:
    statuses = Counter(row["status"] for row in rows)
    scores = Counter(int(row["score"] or 0) for row in rows if row["status"] == "ok")
    rules: Counter[str] = Counter()
    for row in rows:
        rules.update(part.strip() for part in row["matched_rules"].split(",") if part.strip())
    candidates = sorted(
        (row for row in rows if row["status"] == "ok"),
        key=lambda row: (int(row["score"] or 0), row["message_date"]),
        reverse=True,
    )[:30]

    lines = [
        "# Telegram Knowledge Audit",
        "",
        f"- Проверено чатов: **{len(rows)}**",
        f"- Сообщений с текстом: **{statuses['ok']}**",
        f"- Без текстового сообщения: **{statuses['no_text']}**",
        f"- Недоступно или ошибка: **{statuses['unavailable'] + statuses['error']}**",
        f"- CSV: `{csv_path}`",
        "",
        "## Распределение score",
        "",
    ]
    lines.extend(f"- score {score}: {count}" for score, count in sorted(scores.items(), reverse=True))
    lines.extend(["", "## Частые темы", ""])
    lines.extend(f"- {topic}: {count}" for topic, count in _topics(rows))
    lines.extend(["", "## Сработавшие правила", ""])
    lines.extend(f"- {rule}: {count}" for rule, count in rules.most_common(30))
    lines.extend(["", "## Самые сильные кандидаты", ""])
    for row in candidates:
        excerpt = row["excerpt"].replace("\n", " ")[:240]
        lines.append(
            f"- **{row['score']}** — {row['chat_title']} — {excerpt} "
            f"{row['source_url']}"
        )
    return "\n".join(lines).rstrip() + "\n"


async def _latest_messages(client, entity, limit: int):
    for attempt in range(2):
        try:
            return await client.get_messages(entity, limit=limit)
        except FloodWaitError as exc:
            if attempt:
                raise
            await asyncio.sleep(exc.seconds + 1)
    return []


async def audit_knowledge(settings: Settings, per_chat: int, output_dir: Path) -> None:
    selected = filter_excluded(
        load_selected(settings.knowledge_chats_file),
        settings.knowledge_exclusions_file,
    )
    if not selected:
        raise RuntimeError("Knowledge-чаты не выбраны")
    matcher = OfferMatcher(settings.knowledge_rules_file)
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    csv_path = (output_dir / f"knowledge-audit-{stamp}.csv").resolve()
    md_path = (output_dir / f"knowledge-audit-{stamp}.md").resolve()
    rows: list[dict[str, str]] = []

    client = make_client(settings)
    await client.start()
    try:
        for index, chat in enumerate(selected, start=1):
            base = {
                "chat_id": str(chat.chat_id),
                "chat_title": chat.title,
                "username": chat.username,
                "status": "ok",
                "message_id": "",
                "message_date": "",
                "score": "0",
                "matched_rules": "",
                "source_url": "",
                "excerpt": "",
                "error": "",
            }
            try:
                entity = await client.get_input_entity(chat.chat_id)
                messages = await _latest_messages(client, entity, per_chat)
                text_messages = [message for message in messages if (message.raw_text or "").strip()]
                if not text_messages:
                    base["status"] = "no_text"
                    rows.append(base)
                    continue
                best = max(
                    text_messages,
                    key=lambda message: matcher.evaluate(message.raw_text or "").score,
                )
                text = (best.raw_text or "").strip()
                result = matcher.evaluate(text)
                base.update(
                    message_id=str(best.id),
                    message_date=best.date.astimezone(timezone.utc).isoformat(),
                    score=str(result.score),
                    matched_rules=", ".join(result.matched),
                    source_url=source_url(chat.chat_id, chat.username or None, best.id) or "",
                    excerpt=text[:1500].replace("\x00", ""),
                )
            except (ValueError, TypeError) as exc:
                base["status"] = "unavailable"
                base["error"] = f"{type(exc).__name__}: {exc}"[:500]
            except Exception as exc:  # one bad chat must not abort the audit
                base["status"] = "error"
                base["error"] = f"{type(exc).__name__}: {exc}"[:500]
            rows.append(base)
            if index % 25 == 0 or index == len(selected):
                print(f"Проверено {index}/{len(selected)} чатов")
    finally:
        await client.disconnect()

    with csv_path.open("w", encoding="utf-8-sig", newline="") as output:
        writer = csv.DictWriter(output, fieldnames=AUDIT_FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    md_path.write_text(render_summary(rows, csv_path), encoding="utf-8")
    print(f"CSV: {csv_path}")
    print(f"SUMMARY: {md_path}")
    print("Ничего не отправлено и не переслано в Telegram.")
