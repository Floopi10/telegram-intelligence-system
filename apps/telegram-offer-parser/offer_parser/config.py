from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


def _project_dir() -> Path:
    configured = os.getenv("OFFER_PARSER_HOME", "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    current = Path.cwd().resolve()
    if (current / ".env").exists() or (current / "rules.json").exists():
        return current
    return Path(__file__).resolve().parent.parent


PROJECT_DIR = _project_dir()


def _project_path(value: str) -> Path:
    path = Path(value).expanduser()
    return path if path.is_absolute() else PROJECT_DIR / path


@dataclass(frozen=True)
class Settings:
    api_id: int
    api_hash: str
    notify_target: str
    min_score: int
    session_path: Path
    database_path: Path
    chats_file: Path
    rules_file: Path
    knowledge_enabled: bool
    knowledge_target: str
    knowledge_min_score: int
    knowledge_database_path: Path
    knowledge_chats_file: Path
    knowledge_rules_file: Path
    knowledge_exclusions_file: Path
    twitter_priority_file: Path
    twitter_ai_enabled: bool
    twitter_ai_env_file: Path
    twitter_ai_min_score: int
    twitter_strength_enabled: bool
    x_collector_env_file: Path
    twitter_daily_limit: int
    twitter_mechanics_daily_limit: int
    knowledge_balance_enabled: bool
    knowledge_daily_limit: int
    daily_digest_enabled: bool
    daily_digest_target: str
    daily_digest_hour_msk: int
    proxy: tuple[str, str, int, bool, str | None, str | None] | None

    @classmethod
    def load(cls, require_api: bool = True) -> "Settings":
        load_dotenv(PROJECT_DIR / ".env")
        api_id_raw = os.getenv("TELEGRAM_API_ID", "").strip()
        api_hash = os.getenv("TELEGRAM_API_HASH", "").strip()
        if require_api and (not api_id_raw or not api_hash or "replace_" in api_hash):
            raise ValueError(
                "Заполни TELEGRAM_API_ID и TELEGRAM_API_HASH в файле .env"
            )
        if api_id_raw:
            try:
                api_id = int(api_id_raw)
            except ValueError as exc:
                raise ValueError("TELEGRAM_API_ID должен быть целым числом") from exc
        else:
            api_id = 0

        proxy_type = os.getenv("PROXY_TYPE", "").strip().casefold()
        proxy_host = os.getenv("PROXY_HOST", "").strip()
        proxy_port_raw = os.getenv("PROXY_PORT", "").strip()
        proxy = None
        if proxy_type or proxy_host or proxy_port_raw:
            if proxy_type not in {"http", "socks5", "socks4"}:
                raise ValueError("PROXY_TYPE должен быть http, socks5 или socks4")
            if not proxy_host or not proxy_port_raw:
                raise ValueError("Для прокси нужны PROXY_HOST и PROXY_PORT")
            try:
                proxy_port = int(proxy_port_raw)
            except ValueError as exc:
                raise ValueError("PROXY_PORT должен быть целым числом") from exc
            if not 1 <= proxy_port <= 65535:
                raise ValueError("PROXY_PORT должен быть от 1 до 65535")
            proxy = (
                proxy_type,
                proxy_host,
                proxy_port,
                True,
                os.getenv("PROXY_USERNAME") or None,
                os.getenv("PROXY_PASSWORD") or None,
            )

        settings = cls(
            api_id=api_id,
            api_hash=api_hash,
            notify_target=os.getenv("NOTIFY_TARGET", "me").strip() or "me",
            min_score=int(os.getenv("MIN_SCORE", "5")),
            session_path=_project_path(os.getenv("SESSION_PATH", "data/account")),
            database_path=_project_path(os.getenv("DATABASE_PATH", "data/offers.db")),
            chats_file=_project_path(os.getenv("CHATS_FILE", "data/chats.csv")),
            rules_file=_project_path(os.getenv("RULES_FILE", "rules.json")),
            knowledge_enabled=os.getenv("KNOWLEDGE_ENABLED", "0").strip().casefold()
            in {"1", "true", "yes", "on"},
            knowledge_target=os.getenv("KNOWLEDGE_TARGET", "me").strip() or "me",
            knowledge_min_score=int(os.getenv("KNOWLEDGE_MIN_SCORE", "4")),
            knowledge_database_path=_project_path(
                os.getenv("KNOWLEDGE_DATABASE_PATH", "data/knowledge.db")
            ),
            knowledge_chats_file=_project_path(
                os.getenv("KNOWLEDGE_CHATS_FILE", "data/knowledge_chats.csv")
            ),
            knowledge_rules_file=_project_path(
                os.getenv("KNOWLEDGE_RULES_FILE", "knowledge_rules.json")
            ),
            knowledge_exclusions_file=_project_path(
                os.getenv("KNOWLEDGE_EXCLUSIONS_FILE", "data/knowledge_exclusions.csv")
            ),
            twitter_priority_file=_project_path(
                os.getenv("TWITTER_PRIORITY_FILE", "twitter_priority_sources.json")
            ),
            twitter_ai_enabled=os.getenv("TWITTER_AI_ENABLED", "1").strip().casefold()
            in {"1", "true", "yes", "on"},
            twitter_ai_env_file=Path(
                os.getenv(
                    "TWITTER_AI_ENV_FILE",
                    str(PROJECT_DIR / ".env"),
                )
            ).expanduser().resolve(),
            twitter_ai_min_score=int(os.getenv("TWITTER_AI_MIN_SCORE", "7")),
            twitter_strength_enabled=os.getenv(
                "TWITTER_STRENGTH_ENABLED", "1"
            ).strip().casefold() in {"1", "true", "yes", "on"},
            x_collector_env_file=Path(
                os.getenv(
                    "X_COLLECTOR_ENV_FILE",
                    str(PROJECT_DIR / ".env"),
                )
            ).expanduser().resolve(),
            twitter_daily_limit=int(os.getenv("TWITTER_DAILY_LIMIT", "5")),
            twitter_mechanics_daily_limit=int(
                os.getenv("TWITTER_MECHANICS_DAILY_LIMIT", "2")
            ),
            knowledge_balance_enabled=os.getenv(
                "KNOWLEDGE_BALANCE_ENABLED", "1"
            ).strip().casefold() in {"1", "true", "yes", "on"},
            knowledge_daily_limit=int(os.getenv("KNOWLEDGE_DAILY_LIMIT", "15")),
            daily_digest_enabled=os.getenv("DAILY_DIGEST_ENABLED", "1").strip().casefold()
            in {"1", "true", "yes", "on"},
            daily_digest_target=os.getenv("DAILY_DIGEST_TARGET", "me").strip() or "me",
            daily_digest_hour_msk=int(os.getenv("DAILY_DIGEST_HOUR_MSK", "21")),
            proxy=proxy,
        )
        if not 0 <= settings.daily_digest_hour_msk <= 23:
            raise ValueError("DAILY_DIGEST_HOUR_MSK должен быть от 0 до 23")
        if settings.twitter_daily_limit < 1:
            raise ValueError("TWITTER_DAILY_LIMIT должен быть >= 1")
        if not 0 <= settings.twitter_mechanics_daily_limit <= settings.twitter_daily_limit:
            raise ValueError(
                "TWITTER_MECHANICS_DAILY_LIMIT должен быть от 0 до TWITTER_DAILY_LIMIT"
            )
        if settings.knowledge_daily_limit < 1:
            raise ValueError("KNOWLEDGE_DAILY_LIMIT должен быть >= 1")
        for path in (
            settings.session_path,
            settings.database_path,
            settings.chats_file,
            settings.knowledge_database_path,
            settings.knowledge_chats_file,
            settings.knowledge_exclusions_file,
        ):
            path.parent.mkdir(parents=True, exist_ok=True)
        return settings
