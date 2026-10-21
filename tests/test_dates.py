from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest

from birthdaybot.dates import (
    BirthDate, age_on, birthday_in_year, days_until, due_alerts, fire_time, format_offset,
    format_offset_short, next_birthday, parse_birth_date, parse_offset, parse_price, parse_time_of_day,
)

TZ = ZoneInfo("Europe/Moscow")
NINE = time(9, 0)
TODAY = date(2026, 10, 21)


# -- разбор даты -------------------------------------------------------------

@pytest.mark.parametrize("text,expected", [
    ("25.12.2004", BirthDate(25, 12, 2004)),
    ("5.1.2003", BirthDate(5, 1, 2003)),
    ("25/12/2004", BirthDate(25, 12, 2004)),
    ("2004-12-25", BirthDate(25, 12, 2004)),
    ("25.12", BirthDate(25, 12, None)),
    ("29.02", BirthDate(29, 2, None)),
    ("29.02.2004", BirthDate(29, 2, 2004)),
    ("25.12.04", BirthDate(25, 12, 2004)),
    ("25.12.98", BirthDate(25, 12, 1998)),
])
def test_parse_birth_date_ok(text, expected):
    assert parse_birth_date(text, TODAY) == expected


@pytest.mark.parametrize("text", ["", "abc", "32.01", "29.02.2003", "31.04", "10.13.2000", "01.01.2099", "1.1.1800"])
def test_parse_birth_date_bad(text):
    with pytest.raises(ValueError):
        parse_birth_date(text, TODAY)


# -- ближайший ДР -------------------------------------------------------------

def test_next_birthday_today_and_past():
    assert next_birthday(21, 10, TODAY) == date(2026, 10, 21)
    assert next_birthday(20, 10, TODAY) == date(2027, 10, 20)
    assert next_birthday(1, 1, TODAY) == date(2027, 1, 1)
    assert days_until(22, 10, TODAY) == 1


def test_feb29():
    assert birthday_in_year(29, 2, 2027) == date(2027, 2, 28)
    assert birthday_in_year(29, 2, 2028) == date(2028, 2, 29)
    assert next_birthday(29, 2, date(2027, 3, 1)) == date(2028, 2, 29)
    assert next_birthday(29, 2, date(2026, 12, 1)) == date(2027, 2, 28)


def test_age():
    assert age_on(2004, date(2026, 12, 25)) == 22
    assert age_on(None, date(2026, 12, 25)) is None


# -- смещения ------------------------------------------------------------------

@pytest.mark.parametrize("text,minutes", [
    ("7d", 7 * 1440), ("1d", 1440), ("3h", 180), ("30m", 30), ("1d 12h", 2160),
    ("1d12h30m", 2190), ("7д", 7 * 1440), ("3ч", 180), ("15 мин", 15), ("2", 2 * 1440),
    ("0", 0), ("1w", 7 * 1440), ("2 days", 2880),
])
def test_parse_offset(text, minutes):
    assert parse_offset(text) == minutes


@pytest.mark.parametrize("text", ["", "abc", "7x", "d7", "61d", "7d abc"])
def test_parse_offset_bad(text):
    with pytest.raises(ValueError):
        parse_offset(text)


def test_format_offset():
    assert format_offset(7 * 1440) == "7 дней"
    assert format_offset(1440) == "1 день"
    assert format_offset(2 * 1440) == "2 дня"
    assert format_offset(2190) == "1 день 12 ч 30 мин"
    assert format_offset(0) == "в день ДР"
    assert format_offset_short(2190) == "1d12h30m"
    assert parse_offset(format_offset_short(2190)) == 2190


def test_parse_time_and_price():
    assert parse_time_of_day("9:30") == time(9, 30)
    with pytest.raises(ValueError):
        parse_time_of_day("25:00")
    assert parse_price("1500") == 1500
    assert parse_price("1 500 ₽") == 1500
    assert parse_price("2к") == 2000
    assert parse_price("1.5k") == 1500
    with pytest.raises(ValueError):
        parse_price("дорого")


# -- время срабатывания ---------------------------------------------------------

def test_fire_time_days_use_remind_at():
    assert fire_time(date(2026, 12, 25), 7 * 1440, NINE, TZ) == datetime(2026, 12, 18, 9, 0, tzinfo=TZ)
    assert fire_time(date(2026, 12, 25), 0, NINE, TZ) == datetime(2026, 12, 25, 9, 0, tzinfo=TZ)


def test_fire_time_hours_from_midnight():
    assert fire_time(date(2026, 12, 25), 180, NINE, TZ) == datetime(2026, 12, 24, 21, 0, tzinfo=TZ)
    assert fire_time(date(2026, 12, 25), 30, NINE, TZ) == datetime(2026, 12, 24, 23, 30, tzinfo=TZ)


# -- due_alerts ------------------------------------------------------------------

OFFSETS = [7 * 1440, 1440]


def at(y, m, d, hh=0, mm=0):
    return datetime(y, m, d, hh, mm, tzinfo=TZ)


def test_not_due_before_time():
    people = [(1, 25, 12)]
    assert due_alerts(people, OFFSETS, at(2026, 12, 18, 8, 59), NINE, TZ, set()) == []


def test_week_alert_due():
    res = due_alerts([(1, 25, 12)], OFFSETS, at(2026, 12, 18, 9, 0), NINE, TZ, set())
    assert len(res) == 1
    a = res[0]
    assert (a.person_id, a.offset_minutes, a.bday_year, a.bday) == (1, 7 * 1440, 2026, date(2026, 12, 25))
    assert a.also_mark == ()


def test_no_double_send():
    sent = {(1, 7 * 1440, 2026)}
    assert due_alerts([(1, 25, 12)], OFFSETS, at(2026, 12, 19, 12), NINE, TZ, sent) == []


def test_day_alert_after_week_alert():
    sent = {(1, 7 * 1440, 2026)}
    res = due_alerts([(1, 25, 12)], OFFSETS, at(2026, 12, 24, 9, 1), NINE, TZ, sent)
    assert [r.offset_minutes for r in res] == [1440]


def test_missed_alerts_collapse_to_smallest():
    # бот лежал с 17 по 24 декабря — должно уйти одно напоминание (за 1 день), а 7d пометиться
    res = due_alerts([(1, 25, 12)], OFFSETS, at(2026, 12, 24, 15), NINE, TZ, set())
    assert len(res) == 1
    assert res[0].offset_minutes == 1440
    assert res[0].also_mark == (7 * 1440,)


def test_catch_up_until_end_of_birthday():
    res = due_alerts([(1, 25, 12)], OFFSETS, at(2026, 12, 25, 23, 59), NINE, TZ, set())
    assert [r.offset_minutes for r in res] == [1440]
    # после окончания ДР — уже следующий год, рано
    assert due_alerts([(1, 25, 12)], OFFSETS, at(2026, 12, 26, 0, 1), NINE, TZ, set()) == []


def test_year_wrap():
    # ДР 2 января, неделя до — 26 декабря предыдущего года
    res = due_alerts([(1, 2, 1)], OFFSETS, at(2026, 12, 26, 9, 0), NINE, TZ, set())
    assert len(res) == 1
    assert res[0].bday == date(2027, 1, 2)
    assert res[0].bday_year == 2027
    # отправка в 2026 году не блокирует напоминание о ДР 2028 года
    sent = {(1, 7 * 1440, 2027), (1, 1440, 2027)}
    res = due_alerts([(1, 2, 1)], OFFSETS, at(2027, 12, 26, 9, 0), NINE, TZ, sent)
    assert [(r.offset_minutes, r.bday_year) for r in res] == [(7 * 1440, 2028)]


def test_year_wrap_jan1_one_day():
    res = due_alerts([(1, 1, 1)], OFFSETS, at(2026, 12, 31, 9, 0), NINE, TZ, {(1, 7 * 1440, 2027)})
    assert [(r.offset_minutes, r.bday) for r in res] == [(1440, date(2027, 1, 1))]


def test_feb29_non_leap_reminder():
    # 2027 не високосный → ДР 28.02, напоминание за день — 27.02 09:00
    res = due_alerts([(1, 29, 2)], [1440], at(2027, 2, 27, 9, 0), NINE, TZ, set())
    assert len(res) == 1 and res[0].bday == date(2027, 2, 28)
    # в високосный — 28.02 09:00 за ДР 29.02
    res = due_alerts([(1, 29, 2)], [1440], at(2028, 2, 28, 9, 0), NINE, TZ, set())
    assert len(res) == 1 and res[0].bday == date(2028, 2, 29)
    assert due_alerts([(1, 29, 2)], [1440], at(2028, 2, 27, 9, 0), NINE, TZ, set()) == []


def test_hour_and_minute_offsets():
    offs = [180, 30]
    assert due_alerts([(1, 25, 12)], offs, at(2026, 12, 24, 20, 59), NINE, TZ, set()) == []
    res = due_alerts([(1, 25, 12)], offs, at(2026, 12, 24, 21, 0), NINE, TZ, set())
    assert [r.offset_minutes for r in res] == [180]
    res = due_alerts([(1, 25, 12)], offs, at(2026, 12, 24, 23, 30), NINE, TZ, {(1, 180, 2026)})
    assert [r.offset_minutes for r in res] == [30]


def test_zero_offset_on_birthday():
    res = due_alerts([(1, 25, 12)], [0], at(2026, 12, 25, 9, 0), NINE, TZ, set())
    assert len(res) == 1 and res[0].offset_minutes == 0


def test_now_in_other_timezone_is_converted():
    # 06:00 UTC = 09:00 МСК
    now_utc = datetime(2026, 12, 18, 6, 0, tzinfo=ZoneInfo("UTC"))
    assert len(due_alerts([(1, 25, 12)], OFFSETS, now_utc, NINE, TZ, set())) == 1
    assert due_alerts([(1, 25, 12)], OFFSETS, now_utc - timedelta(minutes=1), NINE, TZ, set()) == []
