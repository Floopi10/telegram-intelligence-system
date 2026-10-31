from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .dedupe import build_signature, similarity
from .digest_guard import split_digest_rows


@dataclass(frozen=True)
class OfferRecord:
    chat_id: int
    message_id: int
    chat_title: str
    sender_id: int | None
    sender_name: str
    message_date: datetime
    text: str
    score: int
    matched_rules: str
    compensation: str
    source_url: str | None


class Storage:
    def __init__(self, path: Path):
        self.connection = sqlite3.connect(path, timeout=30)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute("PRAGMA synchronous=NORMAL")
        self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS offers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER NOT NULL,
                message_id INTEGER NOT NULL,
                chat_title TEXT NOT NULL,
                sender_id INTEGER,
                sender_name TEXT NOT NULL,
                message_date TEXT NOT NULL,
                text TEXT NOT NULL,
                score INTEGER NOT NULL,
                matched_rules TEXT NOT NULL,
                compensation TEXT NOT NULL,
                source_url TEXT,
                item_kind TEXT NOT NULL DEFAULT 'knowledge',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(chat_id, message_id)
            )
            """
        )
        columns = {row[1] for row in self.connection.execute("PRAGMA table_info(offers)")}
        if "item_kind" not in columns:
            self.connection.execute(
                "ALTER TABLE offers ADD COLUMN item_kind TEXT NOT NULL DEFAULT 'knowledge'"
            )
        self.connection.execute("CREATE INDEX IF NOT EXISTS idx_offers_date ON offers(message_date DESC)")
        self.connection.execute("CREATE INDEX IF NOT EXISTS idx_offers_score ON offers(score DESC)")
        self.connection.execute("CREATE INDEX IF NOT EXISTS idx_offers_kind_date ON offers(item_kind, message_date DESC)")
        self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS twitter_intelligence (
                canonical_url TEXT PRIMARY KEY,
                chat_id INTEGER NOT NULL,
                message_id INTEGER NOT NULL,
                chat_title TEXT NOT NULL,
                decision TEXT NOT NULL,
                ai_score INTEGER NOT NULL,
                topic TEXT NOT NULL,
                risk_flags TEXT NOT NULL,
                analysis_json TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS semantic_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                kind TEXT NOT NULL,
                exact_hash TEXT NOT NULL,
                tokens_json TEXT NOT NULL,
                urls_json TEXT NOT NULL,
                chat_id INTEGER NOT NULL,
                message_id INTEGER NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(kind, exact_hash)
            )
            """
        )
        self.connection.execute("CREATE INDEX IF NOT EXISTS idx_semantic_recent ON semantic_items(kind, created_at DESC)")
        self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS audit_decisions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER NOT NULL,
                message_id INTEGER NOT NULL,
                chat_title TEXT NOT NULL,
                stream TEXT NOT NULL,
                decision TEXT NOT NULL,
                reason TEXT NOT NULL,
                score INTEGER,
                text_preview TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(chat_id, message_id, stream)
            )
            """
        )
        self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS twitter_strength_candidates (
                canonical_url TEXT PRIMARY KEY,
                author TEXT NOT NULL,
                chat_id INTEGER NOT NULL,
                message_id INTEGER NOT NULL,
                chat_title TEXT NOT NULL,
                content_score INTEGER NOT NULL,
                published_at TEXT NOT NULL,
                collected_at TEXT NOT NULL,
                age_bucket TEXT NOT NULL,
                age_hours REAL NOT NULL,
                views INTEGER,
                likes INTEGER,
                reposts INTEGER,
                replies INTEGER,
                bookmarks INTEGER,
                views_per_hour REAL,
                signal_rate REAL,
                strength_kind TEXT NOT NULL,
                strength_reason TEXT NOT NULL,
                baseline_scope TEXT NOT NULL,
                baseline_size INTEGER NOT NULL,
                velocity_percentile REAL,
                signal_percentile REAL,
                decision TEXT NOT NULL,
                analysis_json TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        self.connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_strength_author_bucket "
            "ON twitter_strength_candidates(author, age_bucket, collected_at DESC)"
        )
        self.connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_strength_decision_day "
            "ON twitter_strength_candidates(decision, created_at DESC)"
        )
        self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS digest_runs (
                digest_day TEXT PRIMARY KEY,
                sent_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS digest_parts (
                digest_day TEXT NOT NULL,
                part_index INTEGER NOT NULL,
                sent_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (digest_day, part_index)
            )
            """
        )
        self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS outbox (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                dedupe_key TEXT NOT NULL UNIQUE,
                target TEXT NOT NULL,
                body TEXT NOT NULL,
                attempts INTEGER NOT NULL DEFAULT 0,
                last_error TEXT NOT NULL DEFAULT '',
                next_attempt_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                status TEXT NOT NULL DEFAULT 'pending',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        self.connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_outbox_due ON outbox(status, next_attempt_at)"
        )
        self.connection.execute("DELETE FROM outbox WHERE status = 'sent' AND created_at < datetime('now', '-30 days')")
        self.connection.execute("DELETE FROM digest_parts WHERE sent_at < datetime('now', '-30 days')")
        self.connection.execute("DELETE FROM audit_decisions WHERE created_at < datetime('now', '-30 days')")
        self.connection.execute("DELETE FROM semantic_items WHERE created_at < datetime('now', '-30 days')")
        self.connection.commit()

    def save(self, offer: OfferRecord, kind: str = "knowledge") -> bool:
        cursor = self.connection.execute(
            """
            INSERT OR IGNORE INTO offers (
                chat_id, message_id, chat_title, sender_id, sender_name,
                message_date, text, score, matched_rules, compensation, source_url,
                item_kind
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                offer.chat_id, offer.message_id, offer.chat_title, offer.sender_id,
                offer.sender_name, offer.message_date.isoformat(), offer.text,
                offer.score, offer.matched_rules, offer.compensation,
                offer.source_url, kind,
            ),
        )
        self.connection.commit()
        return cursor.rowcount == 1

    def save_unique(self, offer: OfferRecord, kind: str, threshold: float = 0.82) -> tuple[bool, str]:
        signature = build_signature(offer.text)
        exact = self.connection.execute(
            "SELECT 1 FROM semantic_items WHERE kind = ? AND exact_hash = ?",
            (kind, signature.exact_hash),
        ).fetchone()
        if exact:
            return False, "exact_duplicate"
        recent = self.connection.execute(
            """
            SELECT tokens_json, urls_json FROM semantic_items
            WHERE kind = ? AND created_at >= datetime('now', '-7 days')
            ORDER BY created_at DESC LIMIT 500
            """,
            (kind,),
        ).fetchall()
        if any(similarity(signature, row["tokens_json"], row["urls_json"]) >= threshold for row in recent):
            return False, "semantic_duplicate"
        if not self.save(offer, kind=kind):
            return False, "message_duplicate"
        self.connection.execute(
            """
            INSERT OR IGNORE INTO semantic_items (
                kind, exact_hash, tokens_json, urls_json, chat_id, message_id
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                kind, signature.exact_hash, json.dumps(signature.tokens, ensure_ascii=False),
                json.dumps(signature.urls, ensure_ascii=False), offer.chat_id, offer.message_id,
            ),
        )
        self.connection.commit()
        return True, "accepted"

    def record_decision(
        self, *, chat_id: int, message_id: int, chat_title: str, stream: str,
        decision: str, reason: str, score: int | None, text: str,
    ) -> None:
        self.connection.execute(
            """
            INSERT OR REPLACE INTO audit_decisions (
                chat_id, message_id, chat_title, stream, decision, reason, score, text_preview
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (chat_id, message_id, chat_title, stream, decision, reason, score, text[:500]),
        )
        self.connection.commit()

    def twitter_url_seen(self, canonical_url: str) -> bool:
        return self.connection.execute(
            """
            SELECT 1 FROM twitter_intelligence WHERE canonical_url = ?
            UNION ALL
            SELECT 1 FROM twitter_strength_candidates WHERE canonical_url = ?
            LIMIT 1
            """,
            (canonical_url, canonical_url),
        ).fetchone() is not None

    def twitter_strength_baselines(
        self, *, author: str, age_bucket: str, limit: int = 500, exclude_url: str = "",
    ) -> tuple[list[dict], list[dict]]:
        columns = "views_per_hour, signal_rate"
        author_rows = self.connection.execute(
            f"""
            SELECT {columns} FROM twitter_strength_candidates
            WHERE author = ? AND age_bucket = ? AND canonical_url != ?
            ORDER BY collected_at DESC LIMIT ?
            """,
            (author, age_bucket, exclude_url, limit),
        ).fetchall()
        cohort_rows = self.connection.execute(
            f"""
            SELECT {columns} FROM twitter_strength_candidates
            WHERE age_bucket = ? AND canonical_url != ?
            ORDER BY collected_at DESC LIMIT ?
            """,
            (age_bucket, exclude_url, limit),
        ).fetchall()
        return [dict(row) for row in author_rows], [dict(row) for row in cohort_rows]

    def twitter_strength_daily_counts(self) -> tuple[int, int]:
        row = self.connection.execute(
            """
            SELECT
                SUM(CASE WHEN decision = 'keep' THEN 1 ELSE 0 END),
                SUM(CASE WHEN decision = 'keep' AND strength_kind = 'полезная механика' THEN 1 ELSE 0 END)
            FROM twitter_strength_candidates
            WHERE created_at >= datetime('now', '+3 hours', 'start of day', '-3 hours')
            """
        ).fetchone()
        return int(row[0] or 0), int(row[1] or 0)

    def save_twitter_strength(
        self, *, canonical_url: str, author: str, chat_id: int,
        message_id: int, chat_title: str, content_score: int,
        metrics, strength, decision: str, analysis: dict,
    ) -> bool:
        cursor = self.connection.execute(
            """
            INSERT OR IGNORE INTO twitter_strength_candidates (
                canonical_url, author, chat_id, message_id, chat_title,
                content_score, published_at, collected_at, age_bucket,
                age_hours, views, likes, reposts, replies, bookmarks,
                views_per_hour, signal_rate, strength_kind, strength_reason,
                baseline_scope, baseline_size, velocity_percentile,
                signal_percentile, decision, analysis_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                canonical_url, author, chat_id, message_id, chat_title,
                content_score, metrics.published_at.isoformat(),
                metrics.collected_at.isoformat(), metrics.age_bucket,
                metrics.age_hours, metrics.views, metrics.likes,
                metrics.reposts, metrics.replies, metrics.bookmarks,
                metrics.views_per_hour, metrics.signal_rate, strength.kind,
                strength.reason_ru, strength.baseline_scope,
                strength.baseline_size, strength.velocity_percentile,
                strength.signal_percentile, decision,
                json.dumps(analysis, ensure_ascii=False),
            ),
        )
        self.connection.commit()
        return cursor.rowcount == 1

    def radar_refresh_candidates(self, start_utc: datetime, limit: int) -> list[dict]:
        rows = self.connection.execute(
            """
            SELECT ti.canonical_url, ti.chat_id, ti.message_id, ti.chat_title,
                   ti.ai_score, ti.analysis_json,
                   ts.decision AS strength_decision, ts.collected_at
            FROM twitter_intelligence AS ti
            LEFT JOIN twitter_strength_candidates AS ts
              ON ts.canonical_url = ti.canonical_url
            WHERE ti.created_at >= datetime(?)
              AND json_extract(ti.analysis_json, '$.ai_keep') = 1
            ORDER BY ti.ai_score DESC, ti.created_at DESC
            LIMIT ?
            """,
            (start_utc.strftime("%Y-%m-%d %H:%M:%S"), limit),
        ).fetchall()
        return [dict(row) for row in rows]

    def update_twitter_strength(self, *, canonical_url: str, metrics, strength) -> None:
        """Store fresh metrics. A later snapshot can confirm a post, never demote it."""
        self.connection.execute(
            """
            UPDATE twitter_strength_candidates SET
                collected_at = ?, age_bucket = ?, age_hours = ?,
                views = ?, likes = ?, reposts = ?, replies = ?, bookmarks = ?,
                views_per_hour = ?, signal_rate = ?,
                baseline_scope = ?, baseline_size = ?,
                velocity_percentile = ?, signal_percentile = ?,
                strength_kind = CASE WHEN ? THEN ? ELSE strength_kind END,
                strength_reason = CASE WHEN ? THEN ? ELSE strength_reason END,
                decision = CASE WHEN ? THEN 'keep' ELSE decision END,
                updated_at = CURRENT_TIMESTAMP
            WHERE canonical_url = ?
            """,
            (
                metrics.collected_at.isoformat(), metrics.age_bucket, metrics.age_hours,
                metrics.views, metrics.likes, metrics.reposts, metrics.replies,
                metrics.bookmarks, metrics.views_per_hour, metrics.signal_rate,
                strength.baseline_scope, strength.baseline_size,
                strength.velocity_percentile, strength.signal_percentile,
                strength.keep, strength.kind,
                strength.keep, strength.reason_ru,
                strength.keep,
                canonical_url,
            ),
        )
        self.connection.commit()

    def save_twitter_analysis(
        self, *, canonical_url: str, chat_id: int, message_id: int,
        chat_title: str, decision: str, ai_score: int, topic: str,
        risk_flags: tuple[str, ...], analysis: dict,
    ) -> bool:
        cursor = self.connection.execute(
            """
            INSERT OR IGNORE INTO twitter_intelligence (
                canonical_url, chat_id, message_id, chat_title, decision,
                ai_score, topic, risk_flags, analysis_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                canonical_url, chat_id, message_id, chat_title, decision,
                ai_score, topic, ", ".join(risk_flags),
                json.dumps(analysis, ensure_ascii=False),
            ),
        )
        self.connection.commit()
        return cursor.rowcount == 1

    def digest_items(self, start_utc: datetime) -> dict:
        start_iso = start_utc.isoformat()
        offers = self.connection.execute(
            """
            SELECT text, score, source_url, item_kind, chat_title
            FROM offers WHERE message_date >= ?
            ORDER BY score DESC, message_date DESC
            """,
            (start_iso,),
        ).fetchall()
        twitter_since = start_utc.strftime("%Y-%m-%d %H:%M:%S")
        twitter = self.connection.execute(
            """
            SELECT canonical_url, ai_score, topic, analysis_json, chat_title
            FROM twitter_intelligence
            WHERE decision = 'keep' AND created_at >= datetime(?)
            ORDER BY ai_score DESC, created_at DESC
            """,
            (twitter_since,),
        ).fetchall()
        twitter_candidates = self.connection.execute(
            """
            SELECT ti.canonical_url,
                   ti.ai_score AS content_score,
                   ts.views, ts.likes, ts.reposts, ts.replies, ts.bookmarks,
                   ts.views_per_hour, ts.signal_rate,
                   ts.velocity_percentile, ts.signal_percentile,
                   ts.decision AS strength_decision,
                   ts.strength_kind, ts.age_hours,
                   ti.analysis_json, ti.chat_title, ti.created_at
            FROM twitter_intelligence AS ti
            LEFT JOIN twitter_strength_candidates AS ts
              ON ts.canonical_url = ti.canonical_url
            WHERE ti.created_at >= datetime(?)
              AND ti.ai_score >= 5
            ORDER BY ti.ai_score DESC, ti.created_at DESC
            """,
            (twitter_since,),
        ).fetchall()
        audit = self.connection.execute(
            """
            SELECT decision, COUNT(*) AS amount FROM audit_decisions
            WHERE created_at >= datetime(?) GROUP BY decision
            """,
            (twitter_since,),
        ).fetchall()
        vacancies, knowledge, rejected_vacancies = split_digest_rows(
            [dict(row) for row in offers]
        )
        return {
            "vacancies": vacancies,
            "knowledge": knowledge,
            "twitter": [dict(row) for row in twitter],
            "twitter_candidates": [dict(row) for row in twitter_candidates],
            "audit_counts": {row["decision"]: row["amount"] for row in audit},
            "rejected_vacancies": rejected_vacancies,
        }

    def audit_rows(self, since_utc: datetime) -> list[dict]:
        since = since_utc.strftime("%Y-%m-%d %H:%M:%S")
        rows = self.connection.execute(
            """
            SELECT chat_title, stream, decision, reason, score, text_preview, created_at
            FROM audit_decisions WHERE created_at >= datetime(?)
            ORDER BY created_at DESC
            """,
            (since,),
        ).fetchall()
        return [dict(row) for row in rows]

    def knowledge_balance_daily_counts(self) -> tuple[int, dict[str, int]]:
        rows = self.connection.execute(
            """
            SELECT reason, COUNT(*) AS amount
            FROM audit_decisions
            WHERE stream = 'knowledge'
              AND decision = 'accepted'
              AND reason LIKE 'niche:%'
              AND created_at >= datetime('now', '+3 hours', 'start of day', '-3 hours')
            GROUP BY reason
            """
        ).fetchall()
        by_niche = {
            str(row["reason"])[len("niche:"):]: int(row["amount"])
            for row in rows
        }
        return sum(by_niche.values()), by_niche

    def digest_was_sent(self, digest_day: str) -> bool:
        return self.connection.execute(
            "SELECT 1 FROM digest_runs WHERE digest_day = ?", (digest_day,)
        ).fetchone() is not None

    def mark_digest_sent(self, digest_day: str, sent_at: datetime | None = None) -> None:
        moment = (sent_at or datetime.now(timezone.utc)).astimezone(timezone.utc)
        self.connection.execute(
            "INSERT OR IGNORE INTO digest_runs (digest_day, sent_at) VALUES (?, ?)",
            (digest_day, moment.strftime("%Y-%m-%d %H:%M:%S")),
        )
        self.connection.commit()

    def last_digest_sent_before(self, digest_day: str) -> datetime | None:
        row = self.connection.execute(
            "SELECT sent_at FROM digest_runs WHERE digest_day < ? ORDER BY digest_day DESC LIMIT 1",
            (digest_day,),
        ).fetchone()
        if row is None:
            return None
        return datetime.fromisoformat(row["sent_at"]).replace(tzinfo=timezone.utc)

    def digest_parts_sent(self, digest_day: str) -> set[int]:
        rows = self.connection.execute(
            "SELECT part_index FROM digest_parts WHERE digest_day = ?", (digest_day,)
        ).fetchall()
        return {int(row["part_index"]) for row in rows}

    def mark_digest_part_sent(self, digest_day: str, part_index: int) -> None:
        self.connection.execute(
            "INSERT OR IGNORE INTO digest_parts (digest_day, part_index) VALUES (?, ?)",
            (digest_day, part_index),
        )
        self.connection.commit()

    def enqueue_message(self, *, dedupe_key: str, target: str, body: str) -> bool:
        cursor = self.connection.execute(
            "INSERT OR IGNORE INTO outbox (dedupe_key, target, body) VALUES (?, ?, ?)",
            (dedupe_key, target, body),
        )
        self.connection.commit()
        return cursor.rowcount == 1

    def outbox_due(self, limit: int = 20) -> list[dict]:
        rows = self.connection.execute(
            """
            SELECT id, target, body, attempts FROM outbox
            WHERE status = 'pending' AND next_attempt_at <= CURRENT_TIMESTAMP
            ORDER BY id LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [dict(row) for row in rows]

    def outbox_mark_sent(self, message_id: int) -> None:
        self.connection.execute("UPDATE outbox SET status = 'sent' WHERE id = ?", (message_id,))
        self.connection.commit()

    def outbox_defer(
        self, message_id: int, *, delay_seconds: int, error: str,
        count_attempt: bool = True, give_up: bool = False,
    ) -> None:
        self.connection.execute(
            """
            UPDATE outbox SET
                attempts = attempts + ?,
                last_error = ?,
                status = ?,
                next_attempt_at = datetime('now', ?)
            WHERE id = ?
            """,
            (
                int(count_attempt), error[:500], "failed" if give_up else "pending",
                f"+{int(delay_seconds)} seconds", message_id,
            ),
        )
        self.connection.commit()

    def stats(self) -> tuple[int, int | None, str | None]:
        row = self.connection.execute(
            "SELECT COUNT(*), MAX(score), MAX(message_date) FROM offers"
        ).fetchone()
        return int(row[0]), row[1], row[2]

    def reclassify(self, matcher, min_score: int) -> tuple[int, int]:
        rows = self.connection.execute("SELECT id, text FROM offers").fetchall()
        kept = removed = 0
        for row in rows:
            result = matcher.evaluate(row["text"])
            if result.score < min_score:
                self.connection.execute("DELETE FROM offers WHERE id = ?", (row["id"],))
                removed += 1
            else:
                self.connection.execute(
                    "UPDATE offers SET score = ?, matched_rules = ?, compensation = ? WHERE id = ?",
                    (result.score, ", ".join(result.matched), ", ".join(result.compensation), row["id"]),
                )
                kept += 1
        self.connection.commit()
        return kept, removed

    def close(self) -> None:
        self.connection.close()
