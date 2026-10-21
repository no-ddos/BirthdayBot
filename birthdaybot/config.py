"""Загрузка настроек из .env / переменных окружения."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from dotenv import load_dotenv


def _parse_ids(raw: str | None) -> frozenset[int]:
    ids = set()
    for part in (raw or "").replace(";", ",").replace(" ", ",").split(","):
        part = part.strip()
        if not part:
            continue
        try:
            ids.add(int(part))
        except ValueError:
            raise SystemExit(f"Некорректный Telegram ID в .env: {part!r}") from None
    return frozenset(ids)


def _get(*names: str, default: str = "") -> str:
    for n in names:
        v = os.getenv(n)
        if v is not None and v.strip() != "":
            return v.strip()
    return default


@dataclass(frozen=True)
class Config:
    bot_token: str
    dev_admins: frozenset[int]
    admins: frozenset[int]
    tz: ZoneInfo
    db_path: Path
    rich_mode: str = "auto"         # auto | on | off
    api_base: str = "https://api.telegram.org"
    log_level: str = "INFO"
    default_offsets: tuple[int, ...] = field(default=(7 * 1440, 1440))
    default_remind_at: str = "09:00"
    log_dir: Optional[Path] = None     # None — только консоль
    log_max_mb: int = 100              # суммарный лимит всех файлов логов
    log_files: int = 10                # на сколько файлов делится лимит (ротация)

    @property
    def all_admins(self) -> frozenset[int]:
        return self.dev_admins | self.admins

    def is_admin(self, user_id: int) -> bool:
        return user_id in self.all_admins

    def is_dev(self, user_id: int) -> bool:
        return user_id in self.dev_admins


def load_config(env_file: str | os.PathLike | None = None, require_token: bool = True) -> Config:
    load_dotenv(env_file or os.getenv("BIRTHDAYBOT_ENV", ".env"), override=False)

    token = _get("BOT_TOKEN")
    if require_token and not token:
        raise SystemExit("BOT_TOKEN не задан. Скопируйте .env.example в .env и заполните.")

    dev_admins = _parse_ids(_get("DEV_ADMIN", "dev_admin", "DEV_ADMINS"))
    admins = _parse_ids(_get("ADMIN", "admin", "ADMINS"))
    if require_token and not (dev_admins or admins):
        raise SystemExit("Не задан ни один админ: заполните DEV_ADMIN и/или ADMIN в .env")

    tz_name = _get("TZ", "BOT_TZ", default="Europe/Moscow")
    try:
        tz = ZoneInfo(tz_name)
    except (ZoneInfoNotFoundError, ValueError):
        raise SystemExit(f"Неизвестный часовой пояс TZ={tz_name!r}") from None

    rich_mode = _get("RICH_MODE", default="auto").lower()
    if rich_mode not in {"auto", "on", "off"}:
        raise SystemExit("RICH_MODE должен быть auto, on или off")

    log_dir_raw = _get("LOG_DIR", default="logs")
    log_dir = None if log_dir_raw.lower() in {"-", "off", "none", "false", "0"} else Path(log_dir_raw)
    try:
        log_max_mb = int(_get("LOG_MAX_MB", default="100"))
        log_files = int(_get("LOG_FILES", default="10"))
    except ValueError:
        raise SystemExit("LOG_MAX_MB и LOG_FILES должны быть целыми числами") from None
    if log_max_mb < 1 or not 1 <= log_files <= 100:
        raise SystemExit("LOG_MAX_MB должен быть >= 1, LOG_FILES — от 1 до 100")

    return Config(
        bot_token=token,
        dev_admins=dev_admins,
        admins=admins,
        tz=tz,
        db_path=Path(_get("DB_PATH", default="data/birthdays.db")),
        rich_mode=rich_mode,
        api_base=_get("TELEGRAM_API_BASE", default="https://api.telegram.org").rstrip("/"),
        log_level=_get("LOG_LEVEL", default="INFO").upper(),
        default_remind_at=_get("REMIND_AT", default="09:00"),
        log_dir=log_dir,
        log_max_mb=log_max_mb,
        log_files=log_files,
    )
