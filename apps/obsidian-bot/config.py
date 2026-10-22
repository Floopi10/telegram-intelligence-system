"""Конфигурация бота. Читает переменные из .env."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import List

from dotenv import load_dotenv

load_dotenv()


def _get_required(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(
            f"Не задана переменная окружения {name}. "
            f"Скопируй .env.example в .env и заполни."
        )
    return value


def _parse_int_list(raw: str) -> List[int]:
    if not raw:
        return []
    return [int(x.strip()) for x in raw.split(",") if x.strip()]


def _parse_str_list(raw: str) -> List[str]:
    if not raw:
        return []
    return [x.strip() for x in raw.split(",") if x.strip()]

def _with_required_categories(values: List[str]) -> List[str]:
    return list(dict.fromkeys([
        *values,
        "Twitter Intelligence", "Вакансии", "Web3", "BD", "AI", "Роботы",
        "Финансы", "Маркетинг", "Контент", "Инструменты", "Безопасность",
    ]))


@dataclass(frozen=True)
class Settings:
    bot_token: str
    openrouter_api_key: str
    openrouter_model: str
    vault_path: Path
    inbox_folder: str
    attachments_folder: str
    allowed_user_ids: List[int] = field(default_factory=list)
    categories: List[str] = field(default_factory=list)
    bot_mode: str = "polling"
    webhook_host: str = "127.0.0.1"
    webhook_port: int = 8080
    webhook_path: str = "/telegram-webhook"
    webhook_base_url: str = ""
    webhook_secret: str = ""

    @property
    def inbox_path(self) -> Path:
        return self.vault_path / self.inbox_folder

    def category_path(self, category: str) -> Path:
        # Категория тоже папка внутри inbox
        return self.inbox_path / category

    @property
    def attachments_path(self) -> Path:
        return self.inbox_path / self.attachments_folder


def load_settings() -> Settings:
    vault = Path(_get_required("VAULT_PATH")).expanduser().resolve()
    if not vault.exists():
        raise RuntimeError(f"VAULT_PATH не существует: {vault}")
    if not vault.is_dir():
        raise RuntimeError(f"VAULT_PATH не папка: {vault}")

    # Поддерживаем и старое имя GEMINI_API_KEY как алиас, чтобы не сломать существующий .env
    api_key = os.getenv("OPENROUTER_API_KEY") or os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError(
            "Не задан OPENROUTER_API_KEY. Возьми ключ на https://openrouter.ai/keys "
            "и пропиши в .env: OPENROUTER_API_KEY=sk-or-v1-..."
        )

    return Settings(
        bot_token=_get_required("TELEGRAM_BOT_TOKEN"),
        openrouter_api_key=api_key,
        openrouter_model=os.getenv("OPENROUTER_MODEL", "openrouter/free"),
        vault_path=vault,
        inbox_folder=os.getenv("INBOX_FOLDER", "Telegram"),
        attachments_folder=os.getenv("ATTACHMENTS_FOLDER", "attachments"),
        allowed_user_ids=_parse_int_list(os.getenv("ALLOWED_USER_IDS", "")),
        categories=_with_required_categories(_parse_str_list(
            os.getenv(
                "CATEGORIES",
                "AI,Идеи,AirDrop,Vibe-кодинг,Claude,Контент,Twitter,YouTube,"
                "Маркетинг,Дизайн,SMM,Шиллинг,Амбассадорки,Нетворкинг,"
                "Промпты,Автоматизация,Безопасность,Гайды,Финансы,Задачи,"
                "Ресурсы,Новости,Личное,Другое",
            )
        )),
        bot_mode=os.getenv("BOT_MODE", "polling").strip().lower(),
        webhook_host=os.getenv("WEBHOOK_HOST", "127.0.0.1").strip(),
        webhook_port=int(os.getenv("WEBHOOK_PORT", "8080")),
        webhook_path=os.getenv("WEBHOOK_PATH", "/telegram-webhook").strip() or "/telegram-webhook",
        webhook_base_url=os.getenv("WEBHOOK_BASE_URL", "").strip().rstrip("/"),
        webhook_secret=os.getenv("WEBHOOK_SECRET", "").strip(),
    )
