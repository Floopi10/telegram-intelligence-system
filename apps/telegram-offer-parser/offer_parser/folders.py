from __future__ import annotations

import csv

from telethon.tl.functions.messages import GetDialogFiltersRequest
from telethon.utils import get_peer_id

from .chats import FIELDS, write_chats
from .config import Settings
from .exclusions import apply_exclusions_to_chats
from .service import make_client


def _filters(result):
    return getattr(result, "filters", result)


def _title(dialog_filter) -> str:
    title = getattr(dialog_filter, "title", "")
    return str(getattr(title, "text", title))


async def list_folders(settings: Settings) -> None:
    client = make_client(settings)
    await client.start()
    try:
        result = await client(GetDialogFiltersRequest())
        print("ID | Папка | Явно добавлено | Закреплено | Исключено")
        for item in _filters(result):
            folder_id = getattr(item, "id", None)
            if folder_id is None:
                continue
            print(
                f"{folder_id} | {_title(item)} | "
                f"{len(getattr(item, 'include_peers', ()) or ())} | "
                f"{len(getattr(item, 'pinned_peers', ()) or ())} | "
                f"{len(getattr(item, 'exclude_peers', ()) or ())}"
            )
    finally:
        await client.disconnect()


def _explicit_members(dialog_filter) -> set[int]:
    automatic_flags = (
        "contacts",
        "non_contacts",
        "groups",
        "broadcasts",
        "bots",
    )
    if any(getattr(dialog_filter, flag, False) for flag in automatic_flags):
        raise ValueError(
            f"Папка {_title(dialog_filter)!r} использует автоматические категории. "
            "Пока поддерживаются папки с явно добавленными чатами."
        )
    included = {
        get_peer_id(peer)
        for peer in (
            list(getattr(dialog_filter, "include_peers", ()) or ())
            + list(getattr(dialog_filter, "pinned_peers", ()) or ())
        )
    }
    excluded = {
        get_peer_id(peer)
        for peer in (getattr(dialog_filter, "exclude_peers", ()) or ())
    }
    return included - excluded


async def _select_folders_to(
    settings: Settings,
    folder_ids: list[int],
    source,
    destination,
    *,
    selection_name: str,
) -> None:
    if not source.exists():
        raise FileNotFoundError("Сначала выполни команду scan-chats")
    client = make_client(settings)
    await client.start()
    try:
        result = await client(GetDialogFiltersRequest())
    finally:
        await client.disconnect()

    available = {
        getattr(item, "id", None): item
        for item in _filters(result)
        if getattr(item, "id", None) is not None
    }
    missing_ids = [folder_id for folder_id in folder_ids if folder_id not in available]
    if missing_ids:
        raise ValueError(f"Папки с ID {missing_ids} не найдены")

    selected_ids: set[int] = set()
    selected_names: list[str] = []
    for folder_id in folder_ids:
        dialog_filter = available[folder_id]
        selected_ids.update(_explicit_members(dialog_filter))
        selected_names.append(_title(dialog_filter))

    with source.open("r", encoding="utf-8-sig", newline="") as file:
        rows = list(csv.DictReader(file))
    matched = 0
    for row in rows:
        enabled = int(row["chat_id"]) in selected_ids
        row["enabled"] = "1" if enabled else "0"
        matched += int(enabled)
    write_chats(destination, [{key: row.get(key, "") for key in FIELDS} for row in rows])

    print(f"{selection_name}. Выбраны папки: {', '.join(selected_names)}")
    print(f"Включено групп и каналов: {matched}")
    ignored = len(selected_ids) - matched
    if ignored:
        print(f"Пропущено личных или недоступных диалогов: {ignored}")


async def select_folders(settings: Settings, folder_ids: list[int]) -> None:
    await _select_folders_to(
        settings,
        folder_ids,
        settings.chats_file,
        settings.chats_file,
        selection_name="Офферы",
    )


async def select_knowledge_folders(settings: Settings, folder_ids: list[int]) -> None:
    await _select_folders_to(
        settings,
        folder_ids,
        settings.chats_file,
        settings.knowledge_chats_file,
        selection_name="Knowledge",
    )
    excluded = apply_exclusions_to_chats(
        settings.knowledge_chats_file,
        settings.knowledge_exclusions_file,
    )
    if excluded:
        print(f"Постоянно исключено из knowledge-потока: {excluded}")

