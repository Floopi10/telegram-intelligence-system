from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

from .config import Settings
from .diagnostics import diagnose
from .decision_audit import write_decision_audit
from .exclusions import exclude_by_names, include_ids
from .folders import list_folders, select_folders, select_knowledge_folders
from .knowledge_audit import audit_knowledge
from .matcher import OfferMatcher
from .service import authorize, backfill, monitor, scan_chats, send_digest_now
from .storage import Storage
from .sources import add_knowledge_source


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description="Парсер офферов из Telegram-групп")
    root.add_argument("--debug", action="store_true", help="подробные логи")
    commands = root.add_subparsers(dest="command", required=True)
    commands.add_parser("auth", help="войти в Telegram-аккаунт")
    commands.add_parser("scan-chats", help="выгрузить доступные группы в CSV")
    commands.add_parser("monitor", help="слушать новые сообщения")
    send_digest = commands.add_parser(
        "send-digest",
        help="отправить дневной дайджест сейчас",
    )
    send_digest.add_argument(
        "--force",
        action="store_true",
        help="отправить повторно, даже если сегодняшний дайджест уже отмечен отправленным",
    )
    history = commands.add_parser("backfill", help="проверить последние сообщения")
    history.add_argument(
        "--limit",
        type=int,
        default=100,
        help="сообщений на каждый чат (по умолчанию: 100)",
    )
    commands.add_parser("stats", help="показать статистику базы")
    commands.add_parser("reclassify", help="пересчитать базу по текущим правилам")
    commands.add_parser("diagnose", help="проверить соединение с Telegram")
    commands.add_parser("folders", help="показать папки Telegram")
    folder_select = commands.add_parser(
        "select-folders", help="выбрать чаты из папок Telegram"
    )
    folder_select.add_argument(
        "--id",
        dest="folder_ids",
        action="append",
        type=int,
        required=True,
        help="ID папки; параметр можно повторить",
    )
    knowledge_folder_select = commands.add_parser(
        "select-knowledge-folders",
        help="выбрать папки для knowledge-потока, не меняя выбор офферов",
    )
    knowledge_folder_select.add_argument(
        "--id",
        dest="folder_ids",
        action="append",
        type=int,
        required=True,
        help="ID папки; параметр можно повторить",
    )
    audit = commands.add_parser(
        "audit-knowledge",
        help="проанализировать последние сообщения knowledge-чатов без отправки",
    )
    audit.add_argument(
        "--per-chat",
        type=int,
        default=1,
        help="последних сообщений на чат (по умолчанию: 1)",
    )
    audit.add_argument(
        "--output-dir",
        default="data/audits",
        help="каталог CSV и Markdown-отчёта",
    )
    exclude = commands.add_parser(
        "exclude-knowledge",
        help="навсегда исключить knowledge-чаты по списку названий",
    )
    exclude.add_argument("--names-file", required=True, help="UTF-8 файл, одно название на строку")
    include = commands.add_parser(
        "include-knowledge",
        help="вернуть ранее исключённые knowledge-чаты по chat_id",
    )
    include.add_argument("--id", dest="chat_ids", action="append", type=int, required=True)
    add_source = commands.add_parser(
        "add-knowledge-source",
        help="добавить один чат в knowledge-поток по точному названию",
    )
    add_source.add_argument("--name", required=True)
    decision_audit = commands.add_parser(
        "decision-audit", help="отчёт пассивного аудита фильтров"
    )
    decision_audit.add_argument("--hours", type=int, default=48)
    decision_audit.add_argument("--output", default="data/audits/passive-filter-audit-latest.md")
    return root


def main() -> None:
    args = parser().parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )
    try:
        settings = Settings.load(
            require_api=args.command
            not in {"stats", "diagnose", "reclassify", "exclude-knowledge", "include-knowledge", "add-knowledge-source", "decision-audit"}
        )
        if args.command == "auth":
            asyncio.run(authorize(settings))
        elif args.command == "scan-chats":
            asyncio.run(scan_chats(settings))
        elif args.command == "monitor":
            asyncio.run(monitor(settings))
        elif args.command == "send-digest":
            sent = asyncio.run(send_digest_now(settings, force=args.force))
            print("DIGEST_SENT=YES" if sent else "DIGEST_ALREADY_SENT=YES")
        elif args.command == "backfill":
            if args.limit < 1 or args.limit > 5000:
                raise ValueError("--limit должен быть от 1 до 5000")
            asyncio.run(backfill(settings, args.limit))
        elif args.command == "stats":
            storage = Storage(settings.database_path)
            count, max_score, latest = storage.stats()
            storage.close()
            print(f"Офферов: {count}; максимальный score: {max_score}; последнее: {latest}")
        elif args.command == "reclassify":
            storage = Storage(settings.database_path)
            kept, removed = storage.reclassify(
                OfferMatcher(settings.rules_file), settings.min_score
            )
            storage.close()
            print(f"Пересчитано: {kept}; удалено ниже порога: {removed}")
        elif args.command == "diagnose":
            asyncio.run(diagnose(settings))
        elif args.command == "folders":
            asyncio.run(list_folders(settings))
        elif args.command == "select-folders":
            asyncio.run(select_folders(settings, args.folder_ids))
        elif args.command == "select-knowledge-folders":
            asyncio.run(select_knowledge_folders(settings, args.folder_ids))
        elif args.command == "audit-knowledge":
            if args.per_chat < 1 or args.per_chat > 20:
                raise ValueError("--per-chat должен быть от 1 до 20")
            output_dir = Path(args.output_dir).expanduser()
            if not output_dir.is_absolute():
                output_dir = settings.knowledge_chats_file.parent.parent / output_dir
            asyncio.run(audit_knowledge(settings, args.per_chat, output_dir.resolve()))
        elif args.command == "exclude-knowledge":
            names_path = Path(args.names_file).expanduser().resolve()
            report_path = settings.knowledge_chats_file.parent / "audits" / "knowledge-exclusions-latest.md"
            result = exclude_by_names(
                settings.knowledge_chats_file,
                settings.knowledge_exclusions_file,
                names_path,
                report_path,
            )
            print(
                f"Запрошено строк: {result.requested}; найдено названий: {result.matched_names}; "
                f"исключено chat_id: {result.matched_chats}; не найдено: {len(result.not_found)}"
            )
            print(f"Отчёт: {result.report_path}")
        elif args.command == "include-knowledge":
            removed = include_ids(
                settings.knowledge_chats_file,
                settings.knowledge_exclusions_file,
                args.chat_ids,
            )
            print(f"Возвращено knowledge-чатов: {removed}")
        elif args.command == "add-knowledge-source":
            chat_id, title = add_knowledge_source(
                settings.chats_file, settings.knowledge_chats_file, args.name
            )
            include_ids(
                settings.knowledge_chats_file, settings.knowledge_exclusions_file, [chat_id]
            )
            print(f"Knowledge-источник добавлен: {title} ({chat_id})")
        elif args.command == "decision-audit":
            if args.hours < 1 or args.hours > 24 * 30:
                raise ValueError("--hours должен быть от 1 до 720")
            output = Path(args.output).expanduser()
            if not output.is_absolute():
                output = settings.knowledge_chats_file.parent.parent / output
            storage = Storage(settings.knowledge_database_path)
            report = write_decision_audit(storage, args.hours, output.resolve())
            storage.close()
            print(f"Отчёт пассивного аудита: {report}")
    except (ValueError, FileNotFoundError, RuntimeError, ConnectionError, EOFError) as exc:
        print(f"Ошибка: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc
    except KeyboardInterrupt:
        print("\nОстановлено.")


if __name__ == "__main__":
    main()
