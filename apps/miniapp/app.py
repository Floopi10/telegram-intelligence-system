#!/usr/bin/env python3
from __future__ import annotations

import os
import sqlite3
from pathlib import Path

from fastapi import FastAPI, Query
from fastapi.responses import HTMLResponse


DB_PATH = Path(os.environ.get("INTELLIGENCE_DB", "./data/knowledge.db"))
app = FastAPI(title="Telegram Intelligence Dashboard", version="1.0.0")


def rows(query: str, params: tuple = ()) -> list[dict]:
    if not DB_PATH.exists():
        return []
    with sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True) as connection:
        connection.row_factory = sqlite3.Row
        return [dict(row) for row in connection.execute(query, params).fetchall()]


@app.get("/health")
def health() -> dict[str, object]:
    return {"status": "ok", "db": DB_PATH.exists(), "version": "1.0.0"}


@app.get("/api/recent")
def recent(limit: int = Query(50, ge=1, le=200)) -> list[dict]:
    return rows(
        "SELECT item_kind, chat_title, message_date, text, score, source_url "
        "FROM offers ORDER BY message_date DESC LIMIT ?", (limit,),
    )


@app.get("/api/metrics")
def metrics() -> dict[str, object]:
    totals = rows("SELECT item_kind, COUNT(*) AS count FROM offers GROUP BY item_kind")
    twitter = rows(
        "SELECT COUNT(*) AS total, SUM(decision='accepted') AS accepted "
        "FROM twitter_strength_candidates"
    )
    return {"items": totals, "twitter": twitter[0] if twitter else {"total": 0, "accepted": 0}}


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    return Path(__file__).with_name("index.html").read_text(encoding="utf-8")

