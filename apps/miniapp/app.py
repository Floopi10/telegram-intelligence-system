#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import sqlite3
import sys
import time
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qsl

from aiohttp import web

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = Path(os.environ.get(
    "KNOWLEDGE_DB",
    "/home/fluixoo/telegram-offer-parser/data/knowledge.db",
))
PARSER_DIR = Path(os.environ.get("PARSER_PROJECT", "/home/fluixoo/telegram-offer-parser"))
if str(PARSER_DIR) not in sys.path:
    sys.path.insert(0, str(PARSER_DIR))

from offer_parser.digest import _quality_tier as quality_tier  # noqa: E402
from offer_parser.knowledge_balance import classify_niches  # noqa: E402
from offer_parser.twitter_niches import NICHE_ORDER, classify_twitter_niche  # noqa: E402
from offer_parser.vacancy_filter import evaluate_vacancy, is_vacancy_text  # noqa: E402

VERSION = "2.7"
# Viewer decisions live apart from the parser database, which this app opens read-only.
STATE_DB = Path(os.environ.get("MINI_APP_STATE_DB", str(BASE_DIR / "data" / "state.db")))
ITEM_ID_RE = re.compile(r"(?:tg:-?\d{1,20}:\d{1,12}|x:https://x\.com/[A-Za-z0-9_]{1,15}/status/\d{1,25})")

BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
ALLOWED_USERS = {
    int(value.strip())
    for value in os.environ.get("ALLOWED_USER_IDS", "").split(",")
    if value.strip()
}
MAX_AUTH_AGE = int(os.environ.get("MINI_APP_AUTH_MAX_AGE", "86400"))

PROMO_NOISE = tuple(re.compile(pattern, re.IGNORECASE) for pattern in (
    r"\b(?:giveaway|sweep|daily combo|free claim|claim now|withdrawal open)\b",
    r"\blimited time opportunity\b",
    r"\bcomplete kyc\b.{0,40}\breward\b",
    r"\bjoin\b.{0,35}\b(?:earn|reward|bonus)\b",
    r"\bregister\b.{0,35}\b(?:airdrop|reward|bonus)\b",
    r"\b(?:раздача|розыгрыш|ежедневн\w+ комбо|забрать награду)\b",
))


def validate_init_data(raw: str, now: int | None = None) -> dict:
    values = dict(parse_qsl(raw, keep_blank_values=True))
    received_hash = values.pop("hash", "")
    if not received_hash:
        raise ValueError("missing_hash")
    data_check = "\n".join(f"{key}={values[key]}" for key in sorted(values))
    secret = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
    expected = hmac.new(secret, data_check.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, received_hash):
        raise ValueError("invalid_hash")
    auth_date = int(values.get("auth_date", "0"))
    current = int(time.time() if now is None else now)
    if auth_date <= 0 or abs(current - auth_date) > MAX_AUTH_AGE:
        raise ValueError("expired")
    user = json.loads(values.get("user", "{}"))
    user_id = int(user.get("id", 0))
    if not user_id or (ALLOWED_USERS and user_id not in ALLOWED_USERS):
        raise ValueError("forbidden_user")
    return user


def _connect() -> sqlite3.Connection:
    if not DB_PATH.exists():
        raise FileNotFoundError(DB_PATH)
    db = sqlite3.connect(f"file:{DB_PATH.resolve()}?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    return db


def classify_niche(text: str) -> str:
    niches = classify_niches(text)
    priority = (
        "Роботы", "BD", "AI", "Финансы", "Безопасность",
        "Инструменты", "Маркетинг", "Web3", "Контент",
    )
    return next((name for name in priority if name in niches), "Другое")


def _compact(value: str) -> str:
    return " ".join((value or "").split())


def _preview(value: str, limit: int = 190) -> str:
    compact = _compact(value)
    return compact if len(compact) <= limit else compact[: limit - 1].rstrip() + "…"


def knowledge_is_clean(text: str, score: int) -> bool:
    compact = _compact(text)
    words = re.findall(r"[\w$%]+", compact, re.UNICODE)
    if is_vacancy_text(compact):
        return False
    if any(pattern.search(compact) for pattern in PROMO_NOISE):
        return False
    if len(words) < 12 and score < 8:
        return False
    if classify_niche(compact) == "Другое" and score < 7:
        return False
    return True


def vacancy_is_clean(text: str) -> bool:
    return is_vacancy_text(text) and evaluate_vacancy(text).accepted


def _offer(row: sqlite3.Row) -> dict:
    text = str(row["text"] or "")
    return {
        "id": f"tg:{row['chat_id']}:{row['message_id']}",
        "title": _preview(text, 135),
        "text": _preview(text, 260),
        "full_text": text[:6000],
        "score": int(row["score"] or 0),
        "url": row["source_url"],
        "source": row["chat_title"],
        "date": row["message_date"],
        "niche": classify_niche(text),
        "kind": row["item_kind"],
        "metrics": {},
    }


def _twitter_item(row: sqlite3.Row) -> dict:
    try:
        analysis = json.loads(row["analysis_json"] or "{}")
    except json.JSONDecodeError:
        analysis = {}
    hook = str(analysis.get("hook") or analysis.get("topic") or row["strength_reason"] or "X-пост")
    review = str(analysis.get("analysis_ru") or "")
    niche = str(analysis.get("niche") or "")
    if niche not in NICHE_ORDER:
        niche = classify_twitter_niche(
            row["canonical_url"], hook, str(analysis.get("topic") or ""), review,
        ) or "Другое"
    points = analysis.get("key_points") or []
    return {
        "id": f"x:{row['canonical_url']}",
        "title": _preview(hook, 160),
        "text": _preview(review or str(row["strength_reason"] or ""), 260),
        "full_text": review[:6000],
        "points": [str(point)[:350] for point in points if str(point).strip()][:6] if isinstance(points, list) else [],
        "topic": str(analysis.get("topic") or ""),
        "reason": str(row["strength_reason"] or ""),
        "score": int(row["content_score"] or 0),
        "url": row["canonical_url"],
        "source": row["chat_title"],
        "author": row["author"],
        "date": row["collected_at"],
        "published": row["published_at"],
        "niche": niche,
        "kind": "twitter",
        "strength": row["strength_kind"],
        "metrics": {
            "views": row["views"], "likes": row["likes"],
            "reposts": row["reposts"], "replies": row["replies"],
            "bookmarks": row["bookmarks"], "views_per_hour": row["views_per_hour"],
        },
    }


def _is_today(value: object) -> bool:
    today_msk = datetime.now(timezone(timedelta(hours=3))).date().isoformat()
    return str(value or "")[:10] == today_msk


def load_dashboard() -> dict:
    with closing(_connect()) as db:
        today = "datetime('now', '+3 hours', 'start of day', '-3 hours')"
        rows = db.execute(
            """SELECT chat_id, message_id, text, score, source_url, chat_title,
            message_date, item_kind FROM offers
            WHERE created_at >= datetime('now', '-7 days')
            ORDER BY message_date DESC LIMIT 500"""
        ).fetchall()
        vacancies, knowledge = [], []
        for row in rows:
            item = _offer(row)
            if row["item_kind"] == "vacancy" and vacancy_is_clean(row["text"]):
                item.pop("kind", None)
                vacancies.append(item)
            elif row["item_kind"] == "knowledge" and knowledge_is_clean(row["text"], row["score"]):
                item.pop("kind", None)
                knowledge.append(item)

        twitter_rows = db.execute(
            """SELECT canonical_url, author, chat_title, content_score,
            collected_at, published_at, views, likes, reposts, replies, bookmarks,
            views_per_hour, signal_rate, velocity_percentile, signal_percentile,
            decision AS strength_decision, strength_kind, strength_reason, analysis_json
            FROM twitter_strength_candidates WHERE created_at >= datetime('now', '-7 days')
            ORDER BY collected_at DESC LIMIT 500"""
        ).fetchall()
        # Same metric gate as the daily digest: an AI score alone never puts a post in the radar.
        twitter = []
        for row in twitter_rows:
            tier = quality_tier(dict(row))
            if tier is not None:
                twitter.append({**_twitter_item(row), "tier": tier})
        twitter = twitter[:100]
        hidden = hidden_ids()
        vacancies = [item for item in vacancies if item["id"] not in hidden]
        knowledge = [item for item in knowledge if item["id"] not in hidden]
        twitter = [item for item in twitter if item["id"] not in hidden]

        decisions = {
            str(row["decision"]): int(row["amount"])
            for row in db.execute(
                f"""SELECT decision, COUNT(*) amount FROM audit_decisions
                WHERE created_at >= {today} GROUP BY decision"""
            )
        }
        sources_today = db.execute(
            f"""SELECT COUNT(DISTINCT chat_title) amount FROM offers
            WHERE created_at >= {today}"""
        ).fetchone()["amount"]
        twitter_today = db.execute(
            f"""SELECT COUNT(*) amount FROM twitter_strength_candidates
            WHERE decision='keep' AND created_at >= {today}"""
        ).fetchone()["amount"]
        niches: dict[str, int] = {}
        for item in knowledge:
            niches[item["niche"]] = niches.get(item["niche"], 0) + 1
        top_sources = [
            {"name": row["chat_title"], "count": int(row["amount"])}
            for row in db.execute(
                """SELECT chat_title, COUNT(*) amount FROM offers
                WHERE created_at >= datetime('now', '-7 days')
                GROUP BY chat_title ORDER BY amount DESC LIMIT 6"""
            )
        ]
        accepted = decisions.get("accepted", 0)
        rejected = decisions.get("rejected", 0) + decisions.get("ignored", 0)
        duplicates = decisions.get("duplicate", 0)
        total = accepted + rejected + duplicates
        return {
            "stats": {
                "vacancies_today": sum(_is_today(item["date"]) for item in vacancies),
                "knowledge_today": sum(_is_today(item["date"]) for item in knowledge),
                "twitter_today": int(twitter_today or 0),
                "sources_today": int(sources_today or 0),
                "accepted": accepted,
                "rejected": rejected,
                "duplicates": duplicates,
                "acceptance_rate": round((accepted / total * 100), 1) if total else 0,
            },
            "vacancies": vacancies[:80],
            "twitter": twitter,
            "knowledge": knowledge[:140],
            "niches": niches,
            "top_sources": top_sources,
            "hidden_count": len(hidden),
            "updated_at": int(time.time()),
        }


def _state() -> sqlite3.Connection:
    STATE_DB.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(STATE_DB, timeout=10)
    db.execute(
        "CREATE TABLE IF NOT EXISTS hidden (item_id TEXT PRIMARY KEY, hidden_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)"
    )
    return db


def hidden_ids() -> set[str]:
    # The dashboard must keep working even when the state file cannot be opened,
    # for example under a read-only systemd sandbox.
    try:
        with closing(_state()) as db:
            return {row[0] for row in db.execute("SELECT item_id FROM hidden")}
    except (sqlite3.Error, OSError) as error:
        print(f"hidden items unavailable: {error}", file=sys.stderr, flush=True)
        return set()


def set_hidden(item_id: str, hidden: bool) -> None:
    with closing(_state()) as db:
        if hidden:
            db.execute("INSERT OR IGNORE INTO hidden (item_id) VALUES (?)", (item_id,))
        else:
            db.execute("DELETE FROM hidden WHERE item_id = ?", (item_id,))
        db.commit()


async def index(_: web.Request) -> web.FileResponse:
    response = web.FileResponse(BASE_DIR / "index.html")
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
    return response


async def dashboard(request: web.Request) -> web.Response:
    try:
        validate_init_data(request.headers.get("X-Telegram-Init-Data", ""))
    except (ValueError, TypeError, json.JSONDecodeError) as error:
        return web.json_response({"error": str(error)}, status=401)
    try:
        payload = load_dashboard()
    except (sqlite3.Error, OSError) as error:
        return web.json_response({"error": "database_unavailable", "detail": str(error)}, status=503)
    return web.json_response(payload, headers={"Cache-Control": "no-store"})


async def hide(request: web.Request) -> web.Response:
    try:
        validate_init_data(request.headers.get("X-Telegram-Init-Data", ""))
    except (ValueError, TypeError, json.JSONDecodeError) as error:
        return web.json_response({"error": str(error)}, status=401)
    try:
        body = await request.json()
    except (json.JSONDecodeError, ValueError):
        return web.json_response({"error": "invalid_body"}, status=400)
    item_id = str(body.get("id") or "") if isinstance(body, dict) else ""
    if not ITEM_ID_RE.fullmatch(item_id):
        return web.json_response({"error": "invalid_id"}, status=400)
    try:
        set_hidden(item_id, bool(body.get("hidden", True)))
    except (sqlite3.Error, OSError) as error:
        return web.json_response({"error": "state_unavailable", "detail": str(error)}, status=503)
    return web.json_response({"ok": True})


async def health(_: web.Request) -> web.Response:
    return web.json_response({"status": "ok", "db": DB_PATH.exists(), "version": VERSION})


def create_app() -> web.Application:
    app = web.Application(client_max_size=1024 * 1024)
    app.router.add_get("/", index)
    app.router.add_get("/api/dashboard", dashboard)
    app.router.add_post("/api/hide", hide)
    app.router.add_get("/health", health)
    return app


if __name__ == "__main__":
    web.run_app(create_app(), host="127.0.0.1", port=int(os.environ.get("MINI_APP_PORT", "8090")))
