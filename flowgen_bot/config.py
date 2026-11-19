"""Настройки бота из переменных окружения (или файла .env рядом с проектом)."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import FrozenSet, Optional

try:
    from dotenv import load_dotenv
except ImportError:                     # python-dotenv необязателен
    load_dotenv = None


def _ids(value: str) -> FrozenSet[str]:
    """«123, @vasya, petya» -> {'123', 'vasya', 'petya'} (id или username без @)."""
    return frozenset(x.strip().lstrip("@").lower() for x in value.replace(";", ",").split(",") if x.strip())


@dataclass(frozen=True)
class Config:
    token: str
    db_path: Path = Path("data/flowgen_bot.sqlite3")
    allowed: FrozenSet[str] = field(default_factory=frozenset)   # пусто — пользоваться могут все
    admins: FrozenSet[str] = field(default_factory=frozenset)    # /stats и /export
    max_file_kb: int = 300            # больше — это уже не лабораторная
    max_functions: int = 40           # столько функций рисуется за раз
    per_minute: int = 6               # запросов в минуту от одного человека
    timeout_s: int = 90               # время на одну программу
    workers: int = 2                  # сколько программ рисуется одновременно
    api_url: str = ""                 # свой сервер Bot API (необязательно), например http://localhost:8081

    def is_allowed(self, user_id: int, username: Optional[str]) -> bool:
        if not self.allowed:
            return True
        return str(user_id) in self.allowed or (username or "").lower() in self.allowed

    def is_admin(self, user_id: int, username: Optional[str]) -> bool:
        return str(user_id) in self.admins or (username or "").lower() in self.admins


def load_config(env_file: Optional[str] = None) -> Config:
    if load_dotenv is not None:
        load_dotenv(env_file or Path.cwd() / ".env")
    token = os.environ.get("BOT_TOKEN", "").strip()
    if not token:
        raise SystemExit("Не задан BOT_TOKEN. Получите токен у @BotFather и запишите его в файл .env:\n"
                         "BOT_TOKEN=123456:ABC...")
    env = os.environ.get
    return Config(
        token=token,
        db_path=Path(env("DB_PATH", "data/flowgen_bot.sqlite3")),
        allowed=_ids(env("ALLOWED_USERS", "")),
        admins=_ids(env("ADMIN_IDS", "")),
        max_file_kb=int(env("MAX_FILE_KB", "300")),
        max_functions=int(env("MAX_FUNCTIONS", "40")),
        per_minute=int(env("PER_MINUTE", "6")),
        timeout_s=int(env("TIMEOUT_S", "90")),
        workers=int(env("WORKERS", "2")),
        api_url=env("TELEGRAM_API_URL", "").strip(),
    )
