#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import re
import sqlite3
import time
import zipfile
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable
from xml.sax.saxutils import escape, quoteattr

from telethon import TelegramClient
from telethon.errors import FloodWaitError, RPCError
from telethon.tl.functions.users import GetFullUserRequest
from telethon.tl.types import InputUser, User

from offer_parser.config import Settings


LOG = logging.getLogger("people-radar")
UTC = timezone.utc

ROLE_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("Founder", re.compile(r"\b(?:co[- ]?founder|founder|founding partner)\b|\bосновател", re.I)),
    ("CEO", re.compile(r"\bceo\b|chief executive", re.I)),
    ("CMO", re.compile(r"\bcmo\b|chief marketing", re.I)),
    ("BD/BDM", re.compile(r"\b(?:bdm|bizdev|business development|bd manager|bd lead|bd head)\b|бизнес[- ]?девелоп", re.I)),
    ("Partnerships", re.compile(r"\b(?:partnerships?|partner manager|strategic partner)\b|партн[её]рств", re.I)),
    ("Growth", re.compile(r"\b(?:growth lead|growth manager|head of growth|growth marketer|growth)\b", re.I)),
    ("Investor", re.compile(r"\b(?:angel investor|investor|investing)\b|инвестор", re.I)),
    ("VC/Fund", re.compile(r"\b(?:venture capital|vc partner|vc associate|investment fund|fund manager)\b", re.I)),
    ("Fundraising", re.compile(r"\b(?:fundraising|fundraiser|capital raising)\b|фандрайз", re.I)),
    ("Marketing", re.compile(r"\b(?:marketing lead|marketing manager|head of marketing|marketer)\b|маркетолог", re.I)),
    ("Community", re.compile(r"\b(?:community lead|community manager|head of community|moderator)\b|комьюнити[- ]?менеджер|модератор", re.I)),
    ("Sales", re.compile(r"\b(?:sales lead|sales manager|head of sales|account executive)\b", re.I)),
    ("Product", re.compile(r"\b(?:product lead|product manager|head of product|chief product)\b", re.I)),
    ("Tech", re.compile(r"\b(?:cto|developer|software engineer|blockchain engineer|tech lead)\b|разработчик", re.I)),
    ("Advisor", re.compile(r"\b(?:advisor|adviser)\b|советник", re.I)),
    ("Research", re.compile(r"\b(?:researcher|research analyst|head of research)\b|исследователь", re.I)),
    ("Design", re.compile(r"\b(?:graphic designer|product designer|ui designer|ux designer|art director)\b", re.I)),
    ("Content", re.compile(r"\b(?:content writer|copywriter|content lead|content manager)\b", re.I)),
]

KOL_PATTERN = re.compile(
    r"\b(?:kol|influencer|shiller|shill(?:er|ing)?|ambassador)\b|инфлюенсер|амбассадор",
    re.I,
)

SELF_DECLARED = re.compile(
    r"\b(?:i am|i'm|im|my role is|working as|work as|i work (?:at|with|for)|"
    r"i founded|i co-founded|i built|we built|we launched|founder of|building)\b|"
    r"\b(?:я работаю|я основал|я запустил|я создал|мы запустили|мы создали|моя роль)\b",
    re.I,
)

URL_RE = re.compile(r"https?://[^\s<>\]\[()]+", re.I)
X_RE = re.compile(r"https?://(?:www\.)?(?:x\.com|twitter\.com)/([A-Za-z0-9_]{1,15})(?:/|$)", re.I)


def now_iso() -> str:
    return datetime.now(UTC).isoformat()


def detect_roles(text: str) -> list[str]:
    return [name for name, pattern in ROLE_PATTERNS if pattern.search(text or "")]


def is_kol_only(text: str, roles: Iterable[str]) -> bool:
    return bool(KOL_PATTERN.search(text or "")) and not list(roles)


def text_evidence(text: str) -> bool:
    return bool(SELF_DECLARED.search(text or "") or detect_roles(text or ""))


def clean_snippet(text: str, limit: int = 600) -> str:
    value = " ".join((text or "").split())
    return value if len(value) <= limit else value[: limit - 1] + "…"


def message_link(chat_id: int, username: str, message_id: int) -> str:
    if username:
        return f"https://t.me/{username}/{message_id}"
    raw = str(abs(chat_id))
    if raw.startswith("100"):
        return f"https://t.me/c/{raw[3:]}/{message_id}"
    return ""


def open_db(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    db.executescript(
        """
        PRAGMA journal_mode=WAL;
        PRAGMA synchronous=NORMAL;
        CREATE TABLE IF NOT EXISTS people (
            user_id INTEGER PRIMARY KEY,
            access_hash INTEGER,
            first_name TEXT NOT NULL DEFAULT '',
            last_name TEXT NOT NULL DEFAULT '',
            username TEXT NOT NULL DEFAULT '',
            phone TEXT NOT NULL DEFAULT '',
            is_bot INTEGER NOT NULL DEFAULT 0,
            bio TEXT NOT NULL DEFAULT '',
            bio_fetched INTEGER NOT NULL DEFAULT 0,
            profile_error TEXT NOT NULL DEFAULT '',
            first_seen TEXT NOT NULL,
            last_seen TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS evidence (
            user_id INTEGER NOT NULL,
            chat_id INTEGER NOT NULL,
            chat_title TEXT NOT NULL,
            chat_username TEXT NOT NULL DEFAULT '',
            message_id INTEGER NOT NULL,
            message_date TEXT NOT NULL,
            snippet TEXT NOT NULL,
            link TEXT NOT NULL DEFAULT '',
            PRIMARY KEY (chat_id, message_id, user_id)
        );
        CREATE TABLE IF NOT EXISTS appearances (
            user_id INTEGER NOT NULL,
            chat_id INTEGER NOT NULL,
            chat_title TEXT NOT NULL,
            first_seen TEXT NOT NULL,
            last_seen TEXT NOT NULL,
            messages INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (user_id, chat_id)
        );
        CREATE TABLE IF NOT EXISTS source_state (
            chat_id INTEGER PRIMARY KEY,
            title TEXT NOT NULL,
            username TEXT NOT NULL DEFAULT '',
            newest_message_id INTEGER NOT NULL DEFAULT 0,
            oldest_message_id INTEGER NOT NULL DEFAULT 0,
            history_complete INTEGER NOT NULL DEFAULT 0,
            processed_messages INTEGER NOT NULL DEFAULT 0,
            error TEXT NOT NULL DEFAULT '',
            updated_at TEXT NOT NULL
        );
        """
    )
    return db


def upsert_person(db: sqlite3.Connection, sender: User, seen_at: str) -> None:
    db.execute(
        """
        INSERT INTO people (
            user_id, access_hash, first_name, last_name, username, phone,
            is_bot, first_seen, last_seen, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(user_id) DO UPDATE SET
            access_hash=COALESCE(excluded.access_hash, people.access_hash),
            first_name=CASE WHEN excluded.first_name != '' THEN excluded.first_name ELSE people.first_name END,
            last_name=CASE WHEN excluded.last_name != '' THEN excluded.last_name ELSE people.last_name END,
            username=CASE WHEN excluded.username != '' THEN excluded.username ELSE people.username END,
            phone=CASE WHEN excluded.phone != '' THEN excluded.phone ELSE people.phone END,
            is_bot=excluded.is_bot,
            last_seen=CASE WHEN excluded.last_seen > people.last_seen THEN excluded.last_seen ELSE people.last_seen END,
            updated_at=excluded.updated_at
        """,
        (
            int(sender.id), getattr(sender, "access_hash", None),
            str(sender.first_name or ""), str(sender.last_name or ""),
            str(sender.username or ""), str(sender.phone or ""), int(bool(sender.bot)),
            seen_at, seen_at, now_iso(),
        ),
    )


async def scan_source(
    client: TelegramClient,
    db: sqlite3.Connection,
    source: dict,
    commit_every: int,
) -> tuple[int, int]:
    chat_id = int(source["chat_id"])
    title = source["title"]
    state = db.execute("SELECT * FROM source_state WHERE chat_id=?", (chat_id,)).fetchone()
    try:
        entity = await client.get_entity(chat_id)
    except Exception as exc:
        db.execute(
            """INSERT INTO source_state(chat_id,title,error,updated_at)
               VALUES(?,?,?,?) ON CONFLICT(chat_id) DO UPDATE SET error=excluded.error, updated_at=excluded.updated_at""",
            (chat_id, title, f"{type(exc).__name__}: {exc}", now_iso()),
        )
        db.commit()
        LOG.error("SOURCE_ERROR title=%s error=%s", title, exc)
        return 0, 0

    username = str(getattr(entity, "username", "") or "")
    state = db.execute("SELECT * FROM source_state WHERE chat_id=?", (chat_id,)).fetchone()
    history_complete = bool(state["history_complete"]) if state else False
    newest = int(state["newest_message_id"]) if state else 0
    oldest = int(state["oldest_message_id"]) if state else 0

    kwargs = {}
    mode = "history"
    if history_complete:
        kwargs = {"min_id": newest, "reverse": True}
        mode = "incremental"
    elif oldest:
        kwargs = {"max_id": oldest}

    processed = 0
    people_seen: set[int] = set()
    async for message in client.iter_messages(entity, **kwargs):
        sender = await message.get_sender()
        if not isinstance(sender, User) or sender.bot:
            continue
        date = message.date.astimezone(UTC).isoformat() if message.date else now_iso()
        upsert_person(db, sender, date)
        people_seen.add(int(sender.id))
        db.execute(
            """
            INSERT INTO appearances(user_id,chat_id,chat_title,first_seen,last_seen,messages)
            VALUES(?,?,?,?,?,1)
            ON CONFLICT(user_id,chat_id) DO UPDATE SET
                first_seen=CASE WHEN excluded.first_seen < appearances.first_seen THEN excluded.first_seen ELSE appearances.first_seen END,
                last_seen=CASE WHEN excluded.last_seen > appearances.last_seen THEN excluded.last_seen ELSE appearances.last_seen END,
                messages=appearances.messages+1
            """,
            (int(sender.id), chat_id, title, date, date),
        )
        raw = (message.raw_text or "").strip()
        if raw and text_evidence(raw):
            db.execute(
                """INSERT OR IGNORE INTO evidence
                   (user_id,chat_id,chat_title,chat_username,message_id,message_date,snippet,link)
                   VALUES(?,?,?,?,?,?,?,?)""",
                (
                    int(sender.id), chat_id, title, username, int(message.id), date,
                    clean_snippet(raw), message_link(chat_id, username, int(message.id)),
                ),
            )
        mid = int(message.id)
        newest = max(newest, mid)
        oldest = mid if not oldest else min(oldest, mid)
        processed += 1
        if processed % commit_every == 0:
            db.execute(
                """INSERT INTO source_state
                   (chat_id,title,username,newest_message_id,oldest_message_id,history_complete,processed_messages,error,updated_at)
                   VALUES(?,?,?,?,?,?,?,'',?)
                   ON CONFLICT(chat_id) DO UPDATE SET
                     title=excluded.title, username=excluded.username,
                     newest_message_id=excluded.newest_message_id,
                     oldest_message_id=excluded.oldest_message_id,
                     processed_messages=source_state.processed_messages+?, error='', updated_at=excluded.updated_at""",
                (chat_id, title, username, newest, oldest, int(history_complete), processed, now_iso(), processed),
            )
            db.commit()
            LOG.info("SCAN title=%s mode=%s messages=%d people=%d", title, mode, processed, len(people_seen))
            processed = 0

    db.execute(
        """INSERT INTO source_state
           (chat_id,title,username,newest_message_id,oldest_message_id,history_complete,processed_messages,error,updated_at)
           VALUES(?,?,?,?,?,1,?,'',?)
           ON CONFLICT(chat_id) DO UPDATE SET
             title=excluded.title, username=excluded.username,
             newest_message_id=excluded.newest_message_id,
             oldest_message_id=excluded.oldest_message_id,
             history_complete=1,
             processed_messages=source_state.processed_messages+excluded.processed_messages,
             error='', updated_at=excluded.updated_at""",
        (chat_id, title, username, newest, oldest, processed, now_iso()),
    )
    db.commit()
    LOG.info("SOURCE_DONE title=%s mode=%s remaining_batch=%d unique_people=%d", title, mode, processed, len(people_seen))
    return processed, len(people_seen)


async def enrich_profiles(client: TelegramClient, db: sqlite3.Connection, delay: float) -> tuple[int, int]:
    rows = db.execute(
        "SELECT user_id,access_hash FROM people WHERE bio_fetched=0 AND is_bot=0 ORDER BY last_seen DESC"
    ).fetchall()
    enriched = 0
    errors = 0
    for index, row in enumerate(rows, 1):
        try:
            if row["access_hash"] is not None:
                entity = InputUser(int(row["user_id"]), int(row["access_hash"]))
            else:
                entity = await client.get_entity(int(row["user_id"]))
            full = await client(GetFullUserRequest(entity))
            about = str(getattr(full.full_user, "about", "") or "").strip()
            db.execute(
                "UPDATE people SET bio=?,bio_fetched=1,profile_error='',updated_at=? WHERE user_id=?",
                (about, now_iso(), int(row["user_id"])),
            )
            enriched += 1
        except FloodWaitError as exc:
            wait = int(exc.seconds) + 3
            LOG.warning("FLOOD_WAIT seconds=%d", wait)
            db.commit()
            await asyncio.sleep(wait)
            continue
        except (ConnectionError, TimeoutError) as exc:
            LOG.warning("PROFILE_RETRY_NEXT_RUN user_id=%s error=%s", row["user_id"], exc)
            continue
        except (RPCError, ValueError, TypeError) as exc:
            db.execute(
                "UPDATE people SET bio_fetched=1,profile_error=?,updated_at=? WHERE user_id=?",
                (f"{type(exc).__name__}: {exc}", now_iso(), int(row["user_id"])),
            )
            errors += 1
        if index % 25 == 0:
            db.commit()
            LOG.info("PROFILES %d/%d enriched=%d errors=%d", index, len(rows), enriched, errors)
        await asyncio.sleep(delay)
    db.commit()
    return enriched, errors


def build_rows(db: sqlite3.Connection) -> tuple[list[dict], int]:
    people = db.execute("SELECT * FROM people WHERE is_bot=0").fetchall()
    output = []
    excluded_kol = 0
    for person in people:
        evidence = db.execute(
            "SELECT * FROM evidence WHERE user_id=? ORDER BY message_date DESC LIMIT 5",
            (person["user_id"],),
        ).fetchall()
        appearances = db.execute(
            "SELECT * FROM appearances WHERE user_id=? ORDER BY last_seen DESC",
            (person["user_id"],),
        ).fetchall()
        combined = "\n".join([person["bio"]] + [item["snippet"] for item in evidence])
        roles = detect_roles(combined)
        if is_kol_only(combined, roles):
            excluded_kol += 1
            continue
        urls = list(dict.fromkeys(URL_RE.findall(combined)))
        x_urls = [url for url in urls if X_RE.search(url)]
        name = " ".join(part for part in [person["first_name"], person["last_name"]] if part).strip()
        username = person["username"]
        telegram = f"https://t.me/{username}" if username else ""
        explicit = any(SELF_DECLARED.search(item["snippet"] or "") for item in evidence)
        score = min(10, min(6, len(roles) * 3) + (2 if person["bio"] else 0) + (1 if explicit else 0) + (1 if x_urls else 0))
        output.append({
            "score": score,
            "name": name or "Deleted/Unnamed",
            "username": f"@{username}" if username else "",
            "telegram": telegram,
            "roles": ", ".join(roles) if roles else "Unknown",
            "bio": person["bio"],
            "links": "\n".join(urls[:8]),
            "x": "\n".join(x_urls[:3]),
            "sources": ", ".join(item["chat_title"] for item in appearances),
            "evidence": "\n---\n".join(item["snippet"] for item in evidence[:3]),
            "evidence_link": next((item["link"] for item in evidence if item["link"]), ""),
            "last_seen": person["last_seen"],
            "user_id": int(person["user_id"]),
        })
    output.sort(key=lambda row: (row["score"], row["last_seen"]), reverse=True)
    return output, excluded_kol


def col_name(index: int) -> str:
    value = ""
    while index:
        index, rem = divmod(index - 1, 26)
        value = chr(65 + rem) + value
    return value


def xml_text(value: object) -> str:
    text = str(value if value is not None else "")
    filtered = "".join(ch for ch in text if ch in "\t\n\r" or ord(ch) >= 32)
    return escape(filtered)


def sheet_xml(headers: list[str], rows: list[list[object]]) -> str:
    widths = [8, 26, 20, 31, 28, 60, 50, 42, 52, 70, 42, 24, 18]
    columns = "".join(
        f'<col min="{i}" max="{i}" width="{width}" customWidth="1"/>'
        for i, width in enumerate(widths[: len(headers)], 1)
    )
    all_rows = [headers] + rows
    rendered = []
    for row_index, row in enumerate(all_rows, 1):
        cells = []
        for col_index, value in enumerate(row, 1):
            ref = f"{col_name(col_index)}{row_index}"
            style = "1" if row_index == 1 else "2"
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                cells.append(f'<c r="{ref}" s="{style}"><v>{value}</v></c>')
            else:
                cells.append(
                    f'<c r="{ref}" s="{style}" t="inlineStr"><is><t xml:space="preserve">{xml_text(value)}</t></is></c>'
                )
        rendered.append(f'<row r="{row_index}">{"".join(cells)}</row>')
    last = f"{col_name(len(headers))}{max(1, len(all_rows))}"
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        '<sheetViews><sheetView workbookViewId="0"><pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/></sheetView></sheetViews>'
        f'<cols>{columns}</cols><sheetData>{"".join(rendered)}</sheetData>'
        f'<autoFilter ref="A1:{last}"/></worksheet>'
    )


def write_xlsx(path: Path, datasets: list[tuple[str, list[dict]]]) -> None:
    headers = [
        "Score", "Name", "Username", "Telegram", "Roles", "Bio", "Links/Project",
        "X", "Sources", "Evidence", "Evidence link", "Last seen", "Telegram user ID",
    ]
    keys = [
        "score", "name", "username", "telegram", "roles", "bio", "links",
        "x", "sources", "evidence", "evidence_link", "last_seen", "user_id",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        overrides = "".join(
            f'<Override PartName="/xl/worksheets/sheet{i}.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
            for i in range(1, len(datasets) + 1)
        )
        archive.writestr(
            "[Content_Types].xml",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
            '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
            f'{overrides}</Types>',
        )
        archive.writestr(
            "_rels/.rels",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
            '</Relationships>',
        )
        sheets = "".join(
            f'<sheet name={quoteattr(name[:31])} sheetId="{i}" r:id="rId{i}"/>'
            for i, (name, _) in enumerate(datasets, 1)
        )
        archive.writestr(
            "xl/workbook.xml",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
            'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
            f'<sheets>{sheets}</sheets></workbook>',
        )
        rels = "".join(
            f'<Relationship Id="rId{i}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet{i}.xml"/>'
            for i in range(1, len(datasets) + 1)
        )
        rels += (
            f'<Relationship Id="rId{len(datasets)+1}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>'
        )
        archive.writestr(
            "xl/_rels/workbook.xml.rels",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            f'{rels}</Relationships>',
        )
        archive.writestr(
            "xl/styles.xml",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
            '<fonts count="2"><font><sz val="11"/><name val="Calibri"/></font><font><b/><color rgb="FFFFFFFF"/><sz val="11"/><name val="Calibri"/></font></fonts>'
            '<fills count="3"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill><fill><patternFill patternType="solid"><fgColor rgb="FF1F4E78"/><bgColor indexed="64"/></patternFill></fill></fills>'
            '<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>'
            '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
            '<cellXfs count="3"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>'
            '<xf numFmtId="0" fontId="1" fillId="2" borderId="0" xfId="0" applyFont="1" applyFill="1" applyAlignment="1"><alignment vertical="center"/></xf>'
            '<xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0" applyAlignment="1"><alignment wrapText="1" vertical="top"/></xf>'
            '</cellXfs><cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles></styleSheet>',
        )
        for index, (_, rows) in enumerate(datasets, 1):
            values = [[row[key] for key in keys] for row in rows]
            archive.writestr(f"xl/worksheets/sheet{index}.xml", sheet_xml(headers, values))


def export(db: sqlite3.Connection, output_dir: Path) -> tuple[Path, int, int]:
    rows, excluded_kol = build_rows(db)
    by_role = lambda names: [row for row in rows if any(name in row["roles"] for name in names)]
    datasets = [
        ("Все люди", rows),
        ("Founders", by_role(["Founder", "CEO"])),
        ("BD и Partnerships", by_role(["BD/BDM", "Partnerships", "Sales"])),
        ("Marketing и Growth", by_role(["CMO", "Marketing", "Growth"])),
        ("Investors и VC", by_role(["Investor", "VC/Fund", "Fundraising"])),
        ("Без роли", [row for row in rows if row["roles"] == "Unknown"]),
    ]
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M")
    path = output_dir / f"people-radar-{stamp}.xlsx"
    write_xlsx(path, datasets)
    latest = output_dir / "people-radar-latest.xlsx"
    latest.write_bytes(path.read_bytes())
    return path, len(rows), excluded_kol


async def run(args: argparse.Namespace) -> int:
    settings = Settings.load()
    sources = json.loads(args.sources.read_text(encoding="utf-8"))
    db = open_db(args.database)
    client = TelegramClient(
        str(args.session), settings.api_id, settings.api_hash,
        flood_sleep_threshold=60, request_retries=5, connection_retries=5,
    )
    await client.start()
    try:
        for index, source in enumerate(sources, 1):
            LOG.info("SOURCE %d/%d %s", index, len(sources), source["title"])
            try:
                await scan_source(client, db, source, args.commit_every)
            except (RPCError, ConnectionError, TimeoutError) as exc:
                # The saved cursor lets the next run resume this source; the others still get scanned.
                db.commit()
                LOG.error("SOURCE_FAILED title=%s error=%s", source["title"], exc)
        enriched, profile_errors = await enrich_profiles(client, db, args.profile_delay)
        path, exported, excluded_kol = export(db, args.output_dir)
        caption = (
            "👥 People Radar завершён\n"
            f"Людей в файле: {exported}\n"
            f"KOL-only исключено: {excluded_kol}\n"
            f"Bio обновлено: {enriched}\n"
            f"Ошибок профилей: {profile_errors}"
        )
        await client.send_file("me", str(path), caption=caption)
        LOG.info("PEOPLE_RADAR=PASS")
        LOG.info("EXPORTED=%d", exported)
        LOG.info("KOL_ONLY_EXCLUDED=%d", excluded_kol)
        LOG.info("FILE=%s", path)
        LOG.info("SENT_TO_SAVED_MESSAGES=YES")
        return 0
    finally:
        db.close()
        await client.disconnect()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sources", type=Path, required=True)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--session", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--commit-every", type=int, default=500)
    parser.add_argument("--profile-delay", type=float, default=0.45)
    return parser.parse_args()


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
    started = time.monotonic()
    code = asyncio.run(run(parse_args()))
    LOG.info("ELAPSED_SECONDS=%.1f", time.monotonic() - started)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
