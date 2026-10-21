"""Построение экранов бота (без обращений к Telegram)."""

from __future__ import annotations

from datetime import date, datetime
from typing import Optional

from .dates import (
    MONTHS_GEN, BirthDate, age_on, days_until, days_word, format_offset, format_offset_short,
    format_price, next_birthday,
)
from .db import Person
from .ui import Button, Footer, Heading, Para, Quote, Screen, Table, esc

PAGE_SIZE = 10

SORTS = {
    "up": "⏳ Ближайшие",
    "name": "🔤 Имя",
    "cal": "📅 Календарь",
    "price": "💰 Цена",
}

BACK_MENU = Button("🏠 Меню", "m")
CANCEL = Button("✖️ Отмена", "cancel", "danger")


def sort_people(people: list[Person], key: str, today: date) -> list[Person]:
    if key == "name":
        return sorted(people, key=lambda p: (p.name.casefold(), p.id))
    if key == "cal":
        return sorted(people, key=lambda p: (p.month, p.day, p.name.casefold()))
    if key == "price":
        # дорогие сверху, без цены — в конце
        return sorted(people, key=lambda p: (p.price is None, -(p.price or 0), p.name.casefold()))
    return sorted(people, key=lambda p: (days_until(p.day, p.month, today), p.name.casefold()))


def _short(s: Optional[str], n: int = 24) -> str:
    if not s:
        return "—"
    return s if len(s) <= n else s[: n - 1] + "…"


# ---------------------------------------------------------------------------

def main_menu(people: list[Person], today: date, is_dev: bool, notice: Optional[str] = None) -> Screen:
    s = Screen()
    s.add(Heading("🎂 BirthdayBot", 1))
    if notice:
        s.add(Quote(notice))
    s.add(Para(f"Людей в списке: <b>{len(people)}</b>"))
    upcoming = sort_people(people, "up", today)[:5]
    if upcoming:
        rows = []
        for p in upcoming:
            nb = next_birthday(p.day, p.month, today)
            rows.append([p.name, f"{nb.day:02d}.{nb.month:02d}", days_word((nb - today).days)])
        s.add(Table(["Кто", "Когда", "Через"], rows, align=["left", "center", "right"],
                    caption="Ближайшие дни рождения"))
    else:
        s.add(Para("Пока никого нет — нажми <b>«Добавить»</b>."))
    s.row(Button("📋 Список", "l:up:0", "primary"), Button("➕ Добавить", "a", "success"))
    s.row(Button("⏰ Алерты", "al"), Button("ℹ️ Помощь", "h"))
    if is_dev:
        s.row(Button("🧪 Тестовый алерт", "t", "danger"))
    return s


def help_screen(is_dev: bool) -> Screen:
    s = Screen()
    s.add(Heading("ℹ️ Помощь", 2))
    s.add(Para("Бот хранит дни рождения потока и заранее напоминает админам."))
    s.add(Table(["Команда", "Что делает"], [
        ["/menu", "главное меню"],
        ["/list", "список дней рождения"],
        ["/add", "добавить человека"],
        ["/alerts", "настройка напоминаний"],
        ["/cancel", "отменить ввод"],
    ] + ([["/test_alert", "тестовое напоминание (dev)"]] if is_dev else [])))
    s.add(Para("Дата: <code>ДД.ММ.ГГГГ</code> или <code>ДД.ММ</code>, если год неизвестен. "
               "Цена: <code>1500</code>, <code>2к</code>. Алерты: <code>7d</code>, <code>1d 12h</code>, "
               "<code>3h</code>, <code>30m</code> (можно по-русски: <code>7д</code>, <code>3ч</code>)."))
    s.row(BACK_MENU)
    return s


def people_list(people: list[Person], sort: str, page: int, today: date) -> Screen:
    s = Screen()
    s.add(Heading("📋 Дни рождения", 2))
    if sort not in SORTS:
        sort = "up"
    ordered = sort_people(people, sort, today)
    pages = max(1, (len(ordered) + PAGE_SIZE - 1) // PAGE_SIZE)
    page = min(max(page, 0), pages - 1)
    chunk = ordered[page * PAGE_SIZE:(page + 1) * PAGE_SIZE]

    s.row(*[Button(("✓ " if k == sort else "") + label, f"l:{k}:0", "primary" if k == sort else None)
            for k, label in SORTS.items()])

    if not ordered:
        s.add(Para("Список пуст."))
    else:
        rows = []
        for i, p in enumerate(chunk, start=page * PAGE_SIZE + 1):
            nb = next_birthday(p.day, p.month, today)
            d = (nb - today).days
            rows.append([
                str(i), p.name, str(p.birth),
                "сегодня 🎉" if d == 0 else f"{d} дн.",
                _short(p.gift, 20), format_price(p.price),
            ])
        s.add(Table(["№", "Имя", "Дата", "Через", "Подарок", "Цена"], rows,
                    align=["right", "left", "center", "right", "left", "right"],
                    caption=f"Сортировка: {SORTS[sort]} · стр. {page + 1}/{pages}"))
        total = sum(p.price or 0 for p in ordered)
        if total:
            s.add(Footer(f"Сумма подарков: {format_price(total)}"))
        # кнопки-карточки по 2 в ряд
        btns = [Button(f"{i}. {_short(p.name, 18)}", f"p:{p.id}", "link")
                for i, p in enumerate(chunk, start=page * PAGE_SIZE + 1)]
        for j in range(0, len(btns), 2):
            s.row(*btns[j:j + 2])
    if pages > 1:
        s.row(
            Button("◀️", f"l:{sort}:{(page - 1) % pages}"),
            Button(f"{page + 1}/{pages}", "noop"),
            Button("▶️", f"l:{sort}:{(page + 1) % pages}"),
        )
    s.row(Button("➕ Добавить", "a", "success"), BACK_MENU)
    return s


def _card_table(name: str, birth: Optional[BirthDate], gift: Optional[str], price: Optional[int],
                today: date) -> Table:
    rows = [["Имя", name or "—"]]
    if birth:
        nb = next_birthday(birth.day, birth.month, today)
        rows.append(["Дата рождения", birth.human()])
        rows.append(["Ближайший ДР", f"{nb.strftime('%d.%m.%Y')} ({days_word((nb - today).days)})"])
        age = age_on(birth.year, nb)
        if age is not None:
            rows.append(["Исполнится", str(age)])
    else:
        rows.append(["Дата рождения", "—"])
    rows.append(["Подарок", gift or "—"])
    rows.append(["Цена", format_price(price)])
    return Table([], rows, align=["left", "left"], header=False)


def person_card(p: Person, today: date, notice: Optional[str] = None) -> Screen:
    s = Screen()
    s.add(Heading(f"👤 {p.name}", 2))
    if notice:
        s.add(Quote(notice))
    s.add(_card_table(p.name, p.birth, p.gift, p.price, today))
    s.row(Button("✏️ Имя", f"e:{p.id}:name"), Button("📅 Дата", f"e:{p.id}:date"))
    s.row(Button("🎁 Подарок", f"e:{p.id}:gift"), Button("💰 Цена", f"e:{p.id}:price"))
    s.row(Button("🗑 Удалить", f"x:{p.id}", "danger"))
    s.row(Button("📋 К списку", "l:up:0"), BACK_MENU)
    return s


def confirm_delete(p: Person) -> Screen:
    s = Screen()
    s.add(Heading("🗑 Удалить?", 2))
    s.add(Para(f"Точно удалить <b>{esc(p.name)}</b> ({esc(str(p.birth))})? Это нельзя отменить."))
    s.row(Button("Да, удалить", f"X:{p.id}", "danger"), Button("Нет", f"p:{p.id}"))
    return s


FIELD_PROMPTS = {
    "name": ("✏️ Имя", "Отправь имя (например, <b>Иван Петров</b>)."),
    "date": ("📅 Дата рождения", "Отправь дату: <code>ДД.ММ.ГГГГ</code> или <code>ДД.ММ</code>, если год неизвестен."),
    "gift": ("🎁 Подарок", "Что человек хотел бы получить? Отправь текстом."),
    "price": ("💰 Примерная цена", "Сколько примерно стоит подарок (в рублях)? Например: <code>1500</code> или <code>2к</code>."),
}


def edit_prompt(p: Person, field: str, error: Optional[str] = None) -> Screen:
    title, text = FIELD_PROMPTS[field]
    s = Screen()
    s.add(Heading(f"{title} — {p.name}", 3))
    if error:
        s.add(Quote(f"⚠️ {esc(error)}"))
    s.add(Para(text))
    if field in ("gift", "price"):
        s.row(Button("🧹 Очистить", f"ec:{p.id}:{field}"))
    s.row(Button("⬅️ Назад", f"p:{p.id}"))
    return s


# -- мастер добавления ------------------------------------------------------

WIZARD_STEPS = ["name", "date", "gift", "price"]


def wizard_step(step: str, draft: dict, today: date, error: Optional[str] = None) -> Screen:
    idx = WIZARD_STEPS.index(step) + 1
    title, text = FIELD_PROMPTS[step]
    s = Screen()
    s.add(Heading(f"➕ Новый человек · шаг {idx}/4", 2))
    if draft:
        s.add(_card_table(draft.get("name", ""), _draft_birth(draft), draft.get("gift"), draft.get("price"), today))
    s.add(Para(f"<b>{title}</b>"), Para(text))
    if error:
        s.add(Quote(f"⚠️ {esc(error)}"))
    if step in ("gift", "price"):
        s.row(Button("⏭ Пропустить", "w:skip"))
    s.row(CANCEL)
    return s


def wizard_confirm(draft: dict, today: date) -> Screen:
    s = Screen()
    s.add(Heading("➕ Проверь карточку", 2))
    s.add(_card_table(draft["name"], _draft_birth(draft), draft.get("gift"), draft.get("price"), today))
    s.row(Button("💾 Сохранить", "w:save", "success"))
    s.row(Button("🔄 Заново", "a"), CANCEL)
    return s


def _draft_birth(draft: dict) -> Optional[BirthDate]:
    if "day" in draft:
        return BirthDate(draft["day"], draft["month"], draft.get("year"))
    return None


# -- алерты -----------------------------------------------------------------

def alerts_screen(offsets: list[int], remind_at: str, tz_name: str, notice: Optional[str] = None) -> Screen:
    s = Screen()
    s.add(Heading("⏰ Напоминания", 2))
    if notice:
        s.add(Quote(notice))
    s.add(Para(f"Напоминания получают все админы. Часовой пояс: <code>{esc(tz_name)}</code>."))
    if offsets:
        rows = [[f"за {format_offset(o)}" if o else format_offset(o), format_offset_short(o)] for o in offsets]
        s.add(Table(["Когда", "Код"], rows, align=["left", "center"]))
    else:
        s.add(Para("⚠️ Нет ни одного напоминания — бот ничего не пришлёт."))
    s.add(Footer(f"Напоминания «за N дней» приходят в {remind_at}; "
                 f"с часами/минутами — отсчёт от 00:00 дня рождения."))
    btns = [Button(f"⚙️ {format_offset_short(o)}", f"ao:{o}") for o in offsets]
    for j in range(0, len(btns), 4):
        s.row(*btns[j:j + 4])
    s.row(Button("➕ Добавить", "aa", "success"), Button(f"🕘 Время: {remind_at}", "at"))
    s.row(BACK_MENU)
    return s


def offset_screen(minutes: int) -> Screen:
    s = Screen()
    s.add(Heading(f"⏰ Напоминание за {format_offset(minutes)}", 3))
    s.row(Button("✏️ Изменить", f"ae:{minutes}"), Button("🗑 Удалить", f"ad:{minutes}", "danger"))
    s.row(Button("⬅️ Назад", "al"))
    return s


def offset_prompt(old: Optional[int] = None, error: Optional[str] = None) -> Screen:
    s = Screen()
    title = f"✏️ Изменить «за {format_offset(old)}»" if old is not None else "➕ Новое напоминание"
    s.add(Heading(title, 3))
    if error:
        s.add(Quote(f"⚠️ {esc(error)}"))
    s.add(Para("За сколько до дня рождения напомнить? Примеры: <code>7d</code>, <code>3d</code>, "
               "<code>1d 12h</code>, <code>3h</code>, <code>30m</code>, <code>0</code> — в сам день."))
    if old is None:
        s.row(*[Button(x, f"aq:{x}") for x in ("14d", "3d", "12h", "0")])
    s.row(Button("⬅️ Назад", "al"))
    return s


def remind_at_prompt(current: str, error: Optional[str] = None) -> Screen:
    s = Screen()
    s.add(Heading("🕘 Время напоминаний", 3))
    if error:
        s.add(Quote(f"⚠️ {esc(error)}"))
    s.add(Para(f"Сейчас: <b>{esc(current)}</b>. Отправь новое время в формате <code>ЧЧ:ММ</code>."))
    s.row(*[Button(x, f"tq:{x}") for x in ("08:00", "09:00", "10:00", "12:00")])
    s.row(Button("⬅️ Назад", "al"))
    return s


# -- напоминание -------------------------------------------------------------

def reminder(p: Person, bday: date, offset_minutes: int, now: datetime, test: bool = False) -> Screen:
    s = Screen()
    title = "🎂 Скоро день рождения!" if bday != now.date() else "🎉 Сегодня день рождения!"
    s.add(Heading(("🧪 ТЕСТ · " if test else "") + title, 2))
    when = days_word((bday - now.date()).days)
    s.add(Para(f"<b>{esc(p.name)}</b> — {bday.day} {_month(bday)} ({when})"))
    rows = [["Дата", bday.strftime("%d.%m.%Y")]]
    age = age_on(p.year, bday)
    if age is not None:
        rows.append(["Исполнится", str(age)])
    rows.append(["Подарок", p.gift or "—"])
    rows.append(["Цена", format_price(p.price)])
    s.add(Table([], rows, header=False))
    label = format_offset(offset_minutes)
    s.add(Footer("Тестовое напоминание" if test else
                 (f"Напоминание за {label}" if offset_minutes else "Напоминание в день рождения")))
    if p.id > 0:
        s.row(Button("👤 Карточка", f"rp:{p.id}", "primary"), Button("📋 Список", "rl"))
    return s


def _month(d: date) -> str:
    return MONTHS_GEN[d.month]
