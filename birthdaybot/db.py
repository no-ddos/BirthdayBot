"""Хранилище на SQLite: люди, смещения алертов, настройки, отправленные алерты, сессии."""

from __future__ import annotations

import json
import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from .dates import BirthDate

SCHEMA = """
CREATE TABLE IF NOT EXISTS people (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT    NOT NULL,
    day         INTEGER NOT NULL,
    month       INTEGER NOT NULL,
    year        INTEGER,
    gift        TEXT,
    price       INTEGER,
    created_by  INTEGER,
    created_at  TEXT    NOT NULL,
    updated_at  TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS alert_offsets (
    minutes INTEGER PRIMARY KEY
);

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- защита от повторной отправки: один алерт на человека/смещение/год ДР
CREATE TABLE IF NOT EXISTS sent_alerts (
    person_id      INTEGER NOT NULL,
    offset_minutes INTEGER NOT NULL,
    bday_year      INTEGER NOT NULL,
    sent_at        TEXT    NOT NULL,
    PRIMARY KEY (person_id, offset_minutes, bday_year)
);

-- состояние диалога админа (мастер добавления, ожидание ввода), переживает рестарт
CREATE TABLE IF NOT EXISTS sessions (
    user_id   INTEGER PRIMARY KEY,
    state     TEXT,
    data      TEXT NOT NULL DEFAULT '{}',
    panel_id  INTEGER
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class Person:
    id: int
    name: str
    day: int
    month: int
    year: Optional[int]
    gift: Optional[str]
    price: Optional[int]

    @property
    def birth(self) -> BirthDate:
        return BirthDate(self.day, self.month, self.year)


@dataclass
class Session:
    user_id: int
    state: Optional[str] = None
    data: dict[str, Any] = None  # type: ignore[assignment]
    panel_id: Optional[int] = None

    def __post_init__(self) -> None:
        if self.data is None:
            self.data = {}


class Database:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        if str(path) != ":memory:":
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA foreign_keys=ON")
            self._conn.executescript(SCHEMA)

    def close(self) -> None:
        self._conn.close()

    def _exec(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        with self._lock:
            return self._conn.execute(sql, params)

    # -- инициализация -----------------------------------------------------
    def bootstrap(self, default_offsets: tuple[int, ...], default_remind_at: str) -> None:
        """Заполнить настройки по умолчанию при первом запуске."""
        if self.get_setting("initialized") is None:
            for off in default_offsets:
                self._exec("INSERT OR IGNORE INTO alert_offsets(minutes) VALUES (?)", (off,))
            self.set_setting("remind_at", default_remind_at)
            self.set_setting("initialized", "1")

    # -- люди --------------------------------------------------------------
    def add_person(self, name: str, birth: BirthDate, gift: Optional[str], price: Optional[int],
                   created_by: Optional[int] = None) -> int:
        now = _now()
        cur = self._exec(
            "INSERT INTO people(name, day, month, year, gift, price, created_by, created_at, updated_at)"
            " VALUES (?,?,?,?,?,?,?,?,?)",
            (name, birth.day, birth.month, birth.year, gift, price, created_by, now, now),
        )
        return int(cur.lastrowid)

    def get_person(self, person_id: int) -> Optional[Person]:
        row = self._exec("SELECT * FROM people WHERE id=?", (person_id,)).fetchone()
        return self._row_to_person(row) if row else None

    def list_people(self) -> list[Person]:
        rows = self._exec("SELECT * FROM people ORDER BY id").fetchall()
        return [self._row_to_person(r) for r in rows]

    def update_person(self, person_id: int, **fields: Any) -> None:
        allowed = {"name", "day", "month", "year", "gift", "price"}
        if "birth" in fields:
            b: BirthDate = fields.pop("birth")
            fields.update(day=b.day, month=b.month, year=b.year)
        bad = set(fields) - allowed
        if bad:
            raise ValueError(f"Unknown fields: {bad}")
        if not fields:
            return
        sets = ", ".join(f"{k}=?" for k in fields)
        self._exec(f"UPDATE people SET {sets}, updated_at=? WHERE id=?",
                   (*fields.values(), _now(), person_id))
        if {"day", "month"} & set(fields):
            # дата поменялась — разрешим алерты заново
            self._exec("DELETE FROM sent_alerts WHERE person_id=?", (person_id,))

    def delete_person(self, person_id: int) -> None:
        self._exec("DELETE FROM sent_alerts WHERE person_id=?", (person_id,))
        self._exec("DELETE FROM people WHERE id=?", (person_id,))

    @staticmethod
    def _row_to_person(row: sqlite3.Row) -> Person:
        return Person(id=row["id"], name=row["name"], day=row["day"], month=row["month"],
                      year=row["year"], gift=row["gift"], price=row["price"])

    # -- смещения алертов --------------------------------------------------
    def list_offsets(self) -> list[int]:
        return [r[0] for r in self._exec("SELECT minutes FROM alert_offsets ORDER BY minutes DESC")]

    def add_offset(self, minutes: int) -> bool:
        cur = self._exec("INSERT OR IGNORE INTO alert_offsets(minutes) VALUES (?)", (minutes,))
        return cur.rowcount > 0

    def remove_offset(self, minutes: int) -> None:
        self._exec("DELETE FROM alert_offsets WHERE minutes=?", (minutes,))

    def replace_offset(self, old: int, new: int) -> bool:
        if old == new:
            return True
        if new in self.list_offsets():
            return False
        with self._lock:
            self._exec("DELETE FROM alert_offsets WHERE minutes=?", (old,))
            self._exec("INSERT OR IGNORE INTO alert_offsets(minutes) VALUES (?)", (new,))
        return True

    # -- настройки ---------------------------------------------------------
    def get_setting(self, key: str, default: Optional[str] = None) -> Optional[str]:
        row = self._exec("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return row[0] if row else default

    def set_setting(self, key: str, value: str) -> None:
        self._exec("INSERT INTO settings(key, value) VALUES(?,?) "
                   "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))

    # -- отправленные алерты ----------------------------------------------
    def sent_keys(self) -> set[tuple[int, int, int]]:
        rows = self._exec("SELECT person_id, offset_minutes, bday_year FROM sent_alerts")
        return {(r[0], r[1], r[2]) for r in rows}

    def mark_sent(self, person_id: int, offset_minutes: int, bday_year: int) -> None:
        self._exec("INSERT OR IGNORE INTO sent_alerts(person_id, offset_minutes, bday_year, sent_at)"
                   " VALUES (?,?,?,?)", (person_id, offset_minutes, bday_year, _now()))

    def cleanup_sent(self, before_year: int) -> None:
        self._exec("DELETE FROM sent_alerts WHERE bday_year < ?", (before_year,))

    # -- сессии ------------------------------------------------------------
    def get_session(self, user_id: int) -> Session:
        row = self._exec("SELECT * FROM sessions WHERE user_id=?", (user_id,)).fetchone()
        if not row:
            return Session(user_id)
        return Session(user_id, row["state"], json.loads(row["data"] or "{}"), row["panel_id"])

    def save_session(self, s: Session) -> None:
        self._exec(
            "INSERT INTO sessions(user_id, state, data, panel_id) VALUES (?,?,?,?) "
            "ON CONFLICT(user_id) DO UPDATE SET state=excluded.state, data=excluded.data, "
            "panel_id=excluded.panel_id",
            (s.user_id, s.state, json.dumps(s.data, ensure_ascii=False), s.panel_id),
        )
