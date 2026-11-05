"""Daily health report: one Telegram message a day that says what works and what broke.

Everything comes from data the system already keeps: parser decisions, X Radar candidates,
the X metrics enricher database (collector health and last snapshot), Firsay feed decisions,
the digest log, the outbox and the state of the neighbouring services.

Env: HEALTH_REPORT_HOUR_MSK (default 9, -1 turns it off), HEALTH_REPORT_TARGET (default: the
digest target).
"""
from __future__ import annotations

import asyncio
import logging
import os
import sqlite3
import subprocess
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

LOGGER = logging.getLogger(__name__)
MSK = ZoneInfo("Europe/Moscow")
SERVICES = ("obsidian-telegram-bot.service", "obsidian-miniapp.service", "x-metrics-enricher.timer")
OK, WARN, BAD = "✅", "⚠️", "❌"


@dataclass(frozen=True)
class Check:
    status: str
    title: str
    detail: str

    def line(self) -> str:
        return f"{self.status} {self.title}: {self.detail}"


def _one(connection: sqlite3.Connection, sql: str, args: tuple = ()) -> tuple:
    row = connection.execute(sql, args).fetchone()
    return tuple(row) if row is not None else ()


def _hours_since(value: object, now: datetime) -> float | None:
    if not value:
        return None
    try:
        moment = datetime.fromisoformat(str(value).replace("Z", "+00:00").replace(" ", "T"))
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return (now - moment).total_seconds() / 3600


def check_messages(knowledge: sqlite3.Connection, offers: sqlite3.Connection | None) -> Check:
    total, accepted = _one(knowledge, """
        SELECT COUNT(*), COALESCE(SUM(decision = 'accepted'), 0) FROM audit_decisions
        WHERE created_at >= datetime('now', '-1 day') AND chat_title NOT LIKE 'Firsay%'""")
    own_offers = _one(offers, "SELECT COUNT(*) FROM offers WHERE created_at >= datetime('now', '-1 day')")[0] if offers else 0
    status = OK if total else BAD
    detail = f"разобрано {total}, принято {accepted}, офферов {own_offers}" if total else "за сутки ни одного решения, парсер не читает чаты"
    return Check(status, "Парсер", detail)


def check_radar(knowledge: sqlite3.Connection) -> Check:
    total, keep = _one(knowledge, """
        SELECT COUNT(*), COALESCE(SUM(decision = 'keep'), 0) FROM twitter_strength_candidates
        WHERE created_at >= datetime('now', '-1 day')""")
    if not total:
        return Check(WARN, "X Radar", "за сутки нет новых X-постов с метриками")
    return Check(OK, "X Radar", f"постов с метриками {total}, сильных {keep}")


def check_collector(metrics_db: Path, now: datetime) -> Check:
    if not metrics_db.exists():
        return Check(WARN, "Коллектор X", "нет базы сбора метрик")
    with closing(sqlite3.connect(f"file:{metrics_db}?mode=ro", uri=True)) as connection:
        failures, last_error = 0, ""
        try:
            failures, last_error = _one(
                connection, "SELECT consecutive_failures, COALESCE(last_error, '') FROM collector_health WHERE id = 1",
            ) or (0, "")
        except sqlite3.OperationalError:
            pass
        last = _one(connection, "SELECT MAX(collected_at) FROM snapshots")
    age = _hours_since(last[0] if last else None, now)
    if failures >= 3:
        return Check(BAD, "Коллектор X", f"{failures} сборов подряд с ошибкой, скорее всего X просит проверку ({last_error[:80]})")
    if age is None or age > 6:
        when = "ни разу" if age is None else f"{age:.0f} ч назад"
        return Check(WARN, "Коллектор X", f"последние метрики сняты {when}")
    note = f", ошибок подряд {failures}" if failures else ""
    return Check(OK, "Коллектор X", f"метрики сняты {age:.0f} ч назад{note}")


def check_firsay(knowledge: sqlite3.Connection, enabled: bool) -> Check:
    if not enabled:
        return Check(OK, "Firsay", "подхват выключен")
    total, offers, notified = _one(knowledge, """
        SELECT COUNT(*), COALESCE(SUM(stream = 'firsay_offer' AND decision = 'accepted'), 0),
               COALESCE(SUM(stream = 'firsay_offer' AND reason = 'notified'), 0)
        FROM audit_decisions WHERE created_at >= datetime('now', '-1 day') AND chat_title LIKE 'Firsay%'""")
    if not total:
        return Check(WARN, "Firsay", "за сутки ни одного сообщения, проверь выгрузку и ключ")
    return Check(OK, "Firsay", f"сообщений {total}, офферов {offers}, в Telegram {notified}")


def check_digest(knowledge: sqlite3.Connection, now: datetime, digest_hour: int) -> Check:
    now_msk = now.astimezone(MSK)
    day = now_msk.date() if now_msk.hour >= digest_hour else now_msk.date() - timedelta(days=1)
    sent = _one(knowledge, "SELECT sent_at FROM digest_runs WHERE digest_day = ?", (day.isoformat(),))
    if not sent:
        return Check(BAD, "Дайджест", f"за {day:%d.%m} не отправлен")
    return Check(OK, "Дайджест", f"за {day:%d.%m} отправлен")


def check_outbox(connections: list[sqlite3.Connection]) -> Check:
    stuck = failed = 0
    for connection in connections:
        stuck += _one(connection, """SELECT COUNT(*) FROM outbox WHERE status = 'pending'
            AND created_at < datetime('now', '-30 minutes')""")[0]
        failed += _one(connection, """SELECT COUNT(*) FROM outbox WHERE status = 'failed'
            AND created_at >= datetime('now', '-1 day')""")[0]
    if failed:
        return Check(BAD, "Отправка", f"не доставлено {failed}, ждут больше 30 минут {stuck}")
    if stuck:
        return Check(WARN, "Отправка", f"ждут отправки больше 30 минут: {stuck}")
    return Check(OK, "Отправка", "очередь пустая")


def check_services(runner=subprocess.run) -> Check:
    down = []
    for name in SERVICES:
        try:
            state = runner(["systemctl", "is-active", name], capture_output=True, text=True, timeout=10).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            return Check(WARN, "Сервисы", "не удалось спросить systemd")
        if state != "active":
            down.append(f"{name.split('.')[0]} ({state or 'нет ответа'})")
    if down:
        return Check(BAD, "Сервисы", "не работают: " + ", ".join(down))
    return Check(OK, "Сервисы", "бот, Radar и сбор метрик работают")


def build_report(
    *, knowledge_db: Path, offers_db: Path | None, metrics_db: Path, firsay_enabled: bool,
    digest_hour: int, now: datetime | None = None, runner=subprocess.run,
) -> str:
    now = now or datetime.now(timezone.utc)
    knowledge = sqlite3.connect(f"file:{knowledge_db}?mode=ro", uri=True)
    offers = sqlite3.connect(f"file:{offers_db}?mode=ro", uri=True) if offers_db and offers_db.exists() else None
    try:
        checks = [
            check_messages(knowledge, offers),
            check_radar(knowledge),
            check_collector(metrics_db, now),
            check_firsay(knowledge, firsay_enabled),
            check_digest(knowledge, now, digest_hour),
            check_outbox([item for item in (knowledge, offers) if item is not None]),
            check_services(runner),
        ]
    finally:
        knowledge.close()
        if offers is not None:
            offers.close()
    bad = sum(check.status == BAD for check in checks)
    warn = sum(check.status == WARN for check in checks)
    head = "всё работает" if not bad and not warn else f"проблем {bad}, предупреждений {warn}"
    title = f"🩺 Здоровье системы · {now.astimezone(MSK):%d.%m} · {head}"
    return "\n".join([title, "", *(check.line() for check in checks)])


async def health_report_loop(storage, *, target: str, hour_msk: int, build) -> None:
    """Queue the report once a day after hour_msk; the outbox delivers it."""
    done_day = ""
    while True:
        now_msk = datetime.now(timezone.utc).astimezone(MSK)
        day = now_msk.date().isoformat()
        scheduled = datetime.combine(now_msk.date(), time(hour_msk, 0), tzinfo=MSK)
        if now_msk >= scheduled and done_day != day:
            try:
                text = await asyncio.to_thread(build)
                # The dedupe key keeps a restart on the same day from sending it twice.
                storage.enqueue_message(dedupe_key=f"health:{day}", target=target, body=text)
                done_day = day
            except asyncio.CancelledError:
                raise
            except Exception:
                LOGGER.exception("Сводка о здоровье не собрана; повтор через час")
        if now_msk >= scheduled:
            scheduled += timedelta(days=1)
        await asyncio.sleep(max(60.0, min((scheduled - now_msk).total_seconds(), 3600.0)))


def report_hour() -> int:
    try:
        return int(os.getenv("HEALTH_REPORT_HOUR_MSK", "9"))
    except ValueError:
        return 9
