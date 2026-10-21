"""Чистая логика дат: разбор ввода, ближайший ДР, смещения алертов, расчёт напоминаний.

Модуль не зависит от Telegram и БД — его удобно тестировать.
"""

from __future__ import annotations

import calendar
import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Iterable, Optional
from zoneinfo import ZoneInfo

MONTHS_GEN = [
    "", "января", "февраля", "марта", "апреля", "мая", "июня",
    "июля", "августа", "сентября", "октября", "ноября", "декабря",
]

MINUTES_IN_DAY = 24 * 60


# ---------------------------------------------------------------------------
# Дата рождения
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class BirthDate:
    day: int
    month: int
    year: Optional[int] = None  # год может быть неизвестен

    def __str__(self) -> str:
        if self.year:
            return f"{self.day:02d}.{self.month:02d}.{self.year}"
        return f"{self.day:02d}.{self.month:02d}"

    def human(self) -> str:
        s = f"{self.day} {MONTHS_GEN[self.month]}"
        return f"{s} {self.year}" if self.year else s


_DATE_PATTERNS = [
    # 25.12.2003, 25/12/2003, 25-12-2003, 25.12.03
    re.compile(r"^(?P<d>\d{1,2})[./\-\s](?P<m>\d{1,2})(?:[./\-\s](?P<y>\d{2}|\d{4}))?$"),
    # 2003-12-25
    re.compile(r"^(?P<y>\d{4})-(?P<m>\d{1,2})-(?P<d>\d{1,2})$"),
]


def parse_birth_date(text: str, today: Optional[date] = None) -> BirthDate:
    """Разбирает дату рождения. Год необязателен. Бросает ValueError с понятным текстом."""
    s = (text or "").strip()
    match = None
    for pattern in _DATE_PATTERNS:
        match = pattern.match(s)
        if match:
            break
    if not match:
        raise ValueError("Не понял дату. Формат: ДД.ММ.ГГГГ или ДД.ММ (например, 25.12.2004)")

    day, month = int(match.group("d")), int(match.group("m"))
    year_s = match.group("y")
    year: Optional[int] = None
    if year_s:
        year = int(year_s)
        if len(year_s) == 2:
            today = today or date.today()
            # 05 -> 2005, 98 -> 1998
            year += 2000 if 2000 + year <= today.year else 1900
    if not 1 <= month <= 12:
        raise ValueError("Месяц должен быть от 1 до 12")
    if year is not None:
        today = today or date.today()
        if year < 1900 or year > today.year:
            raise ValueError(f"Год должен быть от 1900 до {today.year}")
        max_day = calendar.monthrange(year, month)[1]
    else:
        # без года 29 февраля допустимо
        max_day = calendar.monthrange(2000, month)[1]
    if not 1 <= day <= max_day:
        raise ValueError("В этом месяце нет такого дня")
    return BirthDate(day, month, year)


def birthday_in_year(day: int, month: int, year: int) -> date:
    """Дата ДР в конкретном году. 29 февраля в невисокосный год → 28 февраля."""
    if month == 2 and day == 29 and not calendar.isleap(year):
        return date(year, 2, 28)
    return date(year, month, day)


def next_birthday(day: int, month: int, today: date) -> date:
    """Ближайший ДР начиная с сегодняшнего дня (сегодня включительно)."""
    d = birthday_in_year(day, month, today.year)
    if d < today:
        d = birthday_in_year(day, month, today.year + 1)
    return d


def days_until(day: int, month: int, today: date) -> int:
    return (next_birthday(day, month, today) - today).days


def age_on(birth_year: Optional[int], on: date) -> Optional[int]:
    """Сколько исполнится в дату ДР `on` (если год рождения известен)."""
    if not birth_year:
        return None
    return on.year - birth_year


# ---------------------------------------------------------------------------
# Смещения алертов ("7d", "1d 12h", "30m")
# ---------------------------------------------------------------------------

_OFFSET_UNITS = {
    "d": MINUTES_IN_DAY, "д": MINUTES_IN_DAY, "дн": MINUTES_IN_DAY, "day": MINUTES_IN_DAY, "days": MINUTES_IN_DAY,
    "w": 7 * MINUTES_IN_DAY, "н": 7 * MINUTES_IN_DAY, "нед": 7 * MINUTES_IN_DAY,
    "h": 60, "ч": 60, "час": 60, "hour": 60, "hours": 60,
    "m": 1, "м": 1, "мин": 1, "min": 1, "minutes": 1,
}
_OFFSET_TOKEN = re.compile(r"(\d+)\s*([a-zа-я]+)?", re.IGNORECASE)

MAX_OFFSET_MINUTES = 60 * MINUTES_IN_DAY  # не больше 60 дней


def parse_offset(text: str) -> int:
    """'7d' -> 10080, '1d 12h' -> 2160, '30m' -> 30, '3ч' -> 180, '0' -> 0 (в день ДР).

    Число без единицы трактуется как дни.
    """
    s = (text or "").strip().lower().replace(",", " ")
    if not s:
        raise ValueError("Пустое значение")
    pos, total, found = 0, 0, False
    for m in _OFFSET_TOKEN.finditer(s):
        if s[pos:m.start()].strip():
            raise ValueError("Не понял. Примеры: 7d, 1d 12h, 3h, 30m (или 7д, 3ч, 30м)")
        num = int(m.group(1))
        unit = (m.group(2) or "d").lower()
        if unit not in _OFFSET_UNITS:
            raise ValueError(f"Неизвестная единица «{unit}». Используй d/h/m (д/ч/м)")
        total += num * _OFFSET_UNITS[unit]
        pos = m.end()
        found = True
    if not found or s[pos:].strip():
        raise ValueError("Не понял. Примеры: 7d, 1d 12h, 3h, 30m (или 7д, 3ч, 30м)")
    if total > MAX_OFFSET_MINUTES:
        raise ValueError("Слишком далеко: максимум 60 дней")
    return total


def _plural(n: int, one: str, few: str, many: str) -> str:
    n = abs(n)
    if n % 10 == 1 and n % 100 != 11:
        return one
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return few
    return many


def format_offset(minutes: int) -> str:
    """10080 -> '7 дней', 2160 -> '1 день 12 ч', 0 -> 'в день ДР'."""
    if minutes == 0:
        return "в день ДР"
    days, rest = divmod(minutes, MINUTES_IN_DAY)
    hours, mins = divmod(rest, 60)
    parts = []
    if days:
        parts.append(f"{days} {_plural(days, 'день', 'дня', 'дней')}")
    if hours:
        parts.append(f"{hours} ч")
    if mins:
        parts.append(f"{mins} мин")
    return " ".join(parts)


def format_offset_short(minutes: int) -> str:
    """10080 -> '7d', 2160 -> '1d12h', 0 -> '0'."""
    if minutes == 0:
        return "0"
    days, rest = divmod(minutes, MINUTES_IN_DAY)
    hours, mins = divmod(rest, 60)
    out = ""
    if days:
        out += f"{days}d"
    if hours:
        out += f"{hours}h"
    if mins:
        out += f"{mins}m"
    return out


def parse_time_of_day(text: str) -> time:
    m = re.match(r"^\s*(\d{1,2})[:.\s](\d{2})\s*$", text or "")
    if not m:
        raise ValueError("Формат времени: ЧЧ:ММ, например 09:00")
    h, mi = int(m.group(1)), int(m.group(2))
    if not (0 <= h <= 23 and 0 <= mi <= 59):
        raise ValueError("Часы 0–23, минуты 0–59")
    return time(h, mi)


def format_until(delta: timedelta) -> str:
    """Человекочитаемое «через сколько»."""
    total_min = max(0, int(delta.total_seconds() // 60))
    if total_min < 1:
        return "прямо сейчас"
    days, rest = divmod(total_min, MINUTES_IN_DAY)
    hours, mins = divmod(rest, 60)
    if days:
        s = f"{days} {_plural(days, 'день', 'дня', 'дней')}"
        if hours and days < 3:
            s += f" {hours} ч"
        return s
    if hours:
        return f"{hours} ч" + (f" {mins} мин" if mins else "")
    return f"{mins} мин"


def days_word(n: int) -> str:
    if n == 0:
        return "сегодня"
    if n == 1:
        return "завтра"
    return f"через {n} {_plural(n, 'день', 'дня', 'дней')}"


# ---------------------------------------------------------------------------
# Расчёт напоминаний
# ---------------------------------------------------------------------------

def fire_time(bday: date, offset_minutes: int, remind_at: time, tz: ZoneInfo) -> datetime:
    """Момент срабатывания алерта для ДР `bday`.

    * Смещения, кратные суткам (7d, 1d, 0) срабатывают в `remind_at` (по умолчанию 09:00)
      за N дней до ДР.
    * Остальные (3h, 30m, 1d12h) отсчитываются от начала дня рождения (00:00):
      «3h» = в 21:00 накануне.
    """
    midnight = datetime.combine(bday, time(0, 0), tzinfo=tz)
    if offset_minutes % MINUTES_IN_DAY == 0:
        base = datetime.combine(bday - timedelta(days=offset_minutes // MINUTES_IN_DAY), remind_at, tzinfo=tz)
        return base
    return midnight - timedelta(minutes=offset_minutes)


@dataclass(frozen=True)
class DueAlert:
    person_id: int
    offset_minutes: int
    bday: date            # дата ДР, о котором напоминаем
    bday_year: int        # ключ дедупликации
    fire_at: datetime
    # смещения, которые «поглощены» этим алертом (проспанные бóльшие) — их тоже пометить
    also_mark: tuple = ()


def due_alerts(
    people: Iterable[tuple[int, int, int]],
    offsets: Iterable[int],
    now: datetime,
    remind_at: time,
    tz: ZoneInfo,
    already_sent: set[tuple[int, int, int]],
) -> list[DueAlert]:
    """Какие алерты нужно отправить прямо сейчас.

    people       — (person_id, day, month)
    offsets      — смещения в минутах
    already_sent — множество (person_id, offset_minutes, bday_year)

    Правила:
    * Алерт срабатывает, когда now >= fire_time и ДР ещё не закончился
      (окно до 23:59 дня рождения) — так бот «догоняет» алерты, пропущенные из-за простоя.
    * Если для одного человека одновременно «созрели» несколько смещений
      (например, бот лежал неделю, или человека добавили за 12 часов до ДР —
      «созрели» и 7d, и 1d), отправляется только одно — с наименьшим смещением,
      остальные помечаются отправленными.
    * Переход через Новый год: next_birthday() для 2 января 28 декабря вернёт
      ДР следующего года, ключ дедупликации — год самого ДР.
    """
    now = now.astimezone(tz)
    offsets = sorted(set(offsets))
    result: list[DueAlert] = []
    today = now.date()
    for person_id, day, month in people:
        # ближайший ДР: сегодняшний (если сегодня) или следующий, в т.ч. в следующем году
        for bday in (next_birthday(day, month, today),):
            end_of_bday = datetime.combine(bday + timedelta(days=1), time(0, 0), tzinfo=tz)
            ripe = []
            for off in offsets:
                if (person_id, off, bday.year) in already_sent:
                    continue
                ft = fire_time(bday, off, remind_at, tz)
                if ft <= now < end_of_bday:
                    ripe.append((off, ft))
            if not ripe:
                continue
            ripe.sort()  # наименьшее смещение = самое «свежее» напоминание
            off, ft = ripe[0]
            result.append(DueAlert(
                person_id=person_id,
                offset_minutes=off,
                bday=bday,
                bday_year=bday.year,
                fire_at=ft,
                also_mark=tuple(o for o, _ in ripe[1:]),
            ))
    return result


# ---------------------------------------------------------------------------
# Цена подарка
# ---------------------------------------------------------------------------

def parse_price(text: str) -> int:
    """'1500' -> 1500, '1 500 ₽' -> 1500, '2к' -> 2000, '1.5k' -> 1500."""
    s = (text or "").strip().lower()
    s = s.replace("₽", "").replace("руб.", "").replace("руб", "").replace("р.", "").replace("р", "")
    s = s.replace(" ", "").replace("\u00a0", "").replace(",", ".")
    mult = 1
    if s.endswith(("k", "к")):
        mult, s = 1000, s[:-1]
    try:
        value = float(s)
    except ValueError:
        raise ValueError("Цена — число в рублях, например 1500 или 2к") from None
    value = round(value * mult)
    if value < 0 or value > 10_000_000:
        raise ValueError("Цена должна быть от 0 до 10 000 000")
    return int(value)


def format_price(value: Optional[int]) -> str:
    if value is None:
        return "—"
    return f"{value:,}".replace(",", "\u202f") + " ₽"
