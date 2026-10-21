"""Логирование: ротация файлов с общим лимитом размера, маскировка токена, чтение хвоста логов."""

from __future__ import annotations

import logging
import re
import sys
from collections import deque
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Iterable, Optional

LOG_FILE_NAME = "birthdaybot.log"
LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"

# токен бота вида 123456789:AA... — где бы он ни встретился (URL httpx, текст исключений)
_TOKEN_RE = re.compile(r"\b\d{5,}:[A-Za-z0-9_-]{30,}\b")
_BOT_URL_RE = re.compile(r"/bot[^/\s\"']+/")
REDACTED = "<REDACTED>"

_secrets: list[str] = []


def register_secret(secret: Optional[str]) -> None:
    if secret and len(secret) >= 8 and secret not in _secrets:
        _secrets.append(secret)


def redact(text: str) -> str:
    for s in _secrets:
        text = text.replace(s, REDACTED)
    text = _TOKEN_RE.sub(REDACTED, text)
    text = _BOT_URL_RE.sub(f"/bot{REDACTED}/", text)
    return text


class RedactingFormatter(logging.Formatter):
    """Форматтер, который вырезает токен из итоговой строки (включая traceback)."""

    def format(self, record: logging.LogRecord) -> str:
        return redact(super().format(record))


def rotation_params(max_mb: int, files: int) -> tuple[int, int]:
    """(maxBytes одного файла, backupCount) так, чтобы files * maxBytes <= max_mb МБ."""
    files = max(1, files)
    max_bytes = max(1024, (max_mb * 1024 * 1024) // files)
    return max_bytes, files - 1


def setup_logging(level: str = "INFO", log_dir: Optional[Path] = None, max_mb: int = 100,
                  files: int = 10, secrets: Iterable[str] = ()) -> Optional[Path]:
    """Настраивает корневой логгер. Возвращает путь к файлу логов (или None)."""
    for s in secrets:
        register_secret(s)
    root = logging.getLogger()
    for h in list(root.handlers):
        root.removeHandler(h)
        h.close()
    root.setLevel(getattr(logging, level.upper(), logging.INFO))
    fmt = RedactingFormatter(LOG_FORMAT)

    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(fmt)
    root.addHandler(console)

    # httpx/httpcore пишут URL запросов (с токеном) на INFO/DEBUG — глушим
    for noisy in ("httpx", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    log_path: Optional[Path] = None
    if log_dir is not None:
        try:
            log_dir.mkdir(parents=True, exist_ok=True)
            log_path = log_dir / LOG_FILE_NAME
            max_bytes, backups = rotation_params(max_mb, files)
            fh = RotatingFileHandler(log_path, maxBytes=max_bytes, backupCount=backups, encoding="utf-8")
            fh.setFormatter(fmt)
            root.addHandler(fh)
        except OSError as e:
            root.warning("Не удалось открыть лог-файл в %s (%s) — пишу только в консоль", log_dir, e)
            log_path = None
    return log_path


def current_log_path() -> Optional[Path]:
    for h in logging.getLogger().handlers:
        if isinstance(h, RotatingFileHandler):
            return Path(h.baseFilename)
    return None


def log_files(path: Path) -> list[Path]:
    """Текущий файл и ротированные (.1, .2, …) — от нового к старому."""
    out = [path] if path.exists() else []
    i = 1
    while True:
        p = path.with_name(f"{path.name}.{i}")
        if not p.exists():
            break
        out.append(p)
        i += 1
    return out


def total_size(path: Path) -> int:
    return sum(p.stat().st_size for p in log_files(path))


# ---------------------------------------------------------------------------
# Чтение логов для экрана в боте
# ---------------------------------------------------------------------------

_RECORD_START = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3} (\w+) ")

LEVEL_FILTERS = {
    "all": None,
    "warn": {"WARNING", "ERROR", "CRITICAL"},
    "err": {"ERROR", "CRITICAL"},
}


def _records(lines: Iterable[str]) -> list[tuple[str, str]]:
    """Склеивает строки в записи (traceback — часть предыдущей записи). -> [(level, text)]."""
    recs: list[tuple[str, str]] = []
    for line in lines:
        line = line.rstrip("\n")
        m = _RECORD_START.match(line)
        if m or not recs:
            recs.append((m.group(1) if m else "INFO", line))
        else:
            lvl, txt = recs[-1]
            recs[-1] = (lvl, txt + "\n" + line)
    return recs


def tail_records(path: Path, count: int, level: str = "all") -> list[str]:
    """Последние `count` записей (с учётом фильтра уровня), от старых к новым.

    Читает текущий файл и при нехватке — ротированные.
    """
    allowed = LEVEL_FILTERS.get(level)
    result: deque[str] = deque(maxlen=count)
    collected: list[str] = []
    for f in log_files(path):  # от нового к старому
        try:
            with f.open("r", encoding="utf-8", errors="replace") as fh:
                recs = _records(fh)
        except OSError:
            continue
        chunk = [txt for lvl, txt in recs if allowed is None or lvl in allowed]
        collected = chunk + collected
        if len(collected) >= count:
            break
    result.extend(collected)
    return [redact(r) for r in result]
