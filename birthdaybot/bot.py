"""Обработка апдейтов: команды, кнопки, мастер добавления, редактирование, алерты."""

from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime
from typing import Optional

import httpx

from . import screens
from .config import Config
from .dates import (
    MINUTES_IN_DAY, BirthDate, format_offset, next_birthday, parse_birth_date, parse_offset, parse_price, parse_time_of_day,
)
from .db import Database, Person, Session
from .logs import current_log_path, tail_records, total_size
from .scheduler import ReminderScheduler
from .telegram import TelegramAPI, TelegramError
from .ui import Renderer, Screen, esc

log = logging.getLogger(__name__)

COMMANDS = [
    ("menu", "Главное меню"),
    ("list", "Список дней рождения"),
    ("add", "Добавить человека"),
    ("alerts", "Настройка напоминаний"),
    ("cancel", "Отменить ввод"),
    ("help", "Помощь"),
]
DEV_COMMANDS = COMMANDS + [("test_alert", "Тестовое напоминание"), ("logs", "Логи бота")]

MAX_NAME = 100
MAX_GIFT = 300


class BirthdayBot:
    def __init__(self, cfg: Config, db: Database, api: TelegramAPI):
        self.cfg = cfg
        self.db = db
        self.api = api
        self.ui = Renderer(api, cfg.rich_mode)
        self.scheduler = ReminderScheduler(cfg, db, self.ui)
        self._stop = asyncio.Event()

    # ------------------------------------------------------------------
    def now(self) -> datetime:
        return datetime.now(self.cfg.tz)

    def today(self) -> date:
        return self.now().date()

    def remind_at(self) -> str:
        return self.db.get_setting("remind_at", self.cfg.default_remind_at) or "09:00"

    # ------------------------------------------------------------------
    async def setup(self) -> None:
        self.db.bootstrap(self.cfg.default_offsets, self.cfg.default_remind_at)
        while True:
            try:
                me = await self.api.get_me()
                break
            except TelegramError as e:
                if e.code == 401:
                    raise SystemExit("Неверный BOT_TOKEN (401 Unauthorized) — проверьте .env") from None
                raise
            except httpx.HTTPError as e:
                log.warning("Telegram недоступен (%s) — повтор через 10 с", e)
                await asyncio.sleep(10)
        log.info("Запущен как @%s (rich mode: %s)", me.get("username"), self.cfg.rich_mode)
        try:
            await self.api.set_my_commands(COMMANDS)
            for uid in self.cfg.dev_admins:
                await self.api.set_my_commands(DEV_COMMANDS, scope={"type": "chat", "chat_id": uid})
        except TelegramError as e:
            log.warning("setMyCommands: %s", e)

    async def run(self) -> None:
        await self.setup()
        sched = asyncio.create_task(self.scheduler.run(), name="scheduler")
        offset: Optional[int] = None
        try:
            while not self._stop.is_set():
                try:
                    updates = await self.api.get_updates(offset)
                except (httpx.HTTPError, TelegramError) as e:
                    if isinstance(e, TelegramError) and e.code == 401:
                        raise SystemExit("Неверный BOT_TOKEN (401 Unauthorized)") from None
                    log.warning("getUpdates: %s — повтор через 5 с", e)
                    await asyncio.sleep(5)
                    continue
                for upd in updates:
                    offset = upd["update_id"] + 1
                    try:
                        await self.handle_update(upd)
                    except Exception:  # noqa: BLE001 — один сбойный апдейт не должен ронять бота
                        log.exception("Ошибка при обработке апдейта %s", upd.get("update_id"))
        finally:
            self.scheduler.stop()
            sched.cancel()

    def stop(self) -> None:
        self._stop.set()

    # ------------------------------------------------------------------
    async def handle_update(self, upd: dict) -> None:
        if "callback_query" in upd:
            await self.on_callback(upd["callback_query"])
        elif "message" in upd:
            await self.on_message(upd["message"])

    def _allowed(self, user: dict, chat: Optional[dict]) -> bool:
        if not user or not self.cfg.is_admin(int(user["id"])):
            return False
        return chat is None or chat.get("type") == "private"

    # -- сообщения -----------------------------------------------------
    async def on_message(self, msg: dict) -> None:
        user, chat = msg.get("from") or {}, msg.get("chat") or {}
        if not self._allowed(user, chat):
            log.info("Игнорирую сообщение от %s (не админ или не личный чат)", user.get("id"))
            return
        uid, chat_id = int(user["id"]), int(chat["id"])
        text = (msg.get("text") or "").strip()
        sess = self.db.get_session(uid)

        if text.startswith("/"):
            cmd = text.split()[0][1:].split("@")[0].lower()
            await self.on_command(uid, chat_id, cmd, sess)
            return

        if not sess.state:
            # не ждём ввода — просто покажем меню новой панелью
            await self.show(uid, chat_id, sess, self.menu_screen(uid), new=True)
            return

        # ввод для мастера — сообщение пользователя удаляем, чтобы чат оставался чистым
        await self.api.delete_message(chat_id, msg["message_id"])
        if not text:
            return
        await self.on_input(uid, chat_id, sess, text)

    async def on_command(self, uid: int, chat_id: int, cmd: str, sess: Session) -> None:
        sess.state, sess.data = None, {}
        if cmd in ("start", "menu"):
            screen = self.menu_screen(uid)
        elif cmd == "help":
            screen = screens.help_screen(self.cfg.is_dev(uid))
        elif cmd == "list":
            screen = screens.people_list(self.db.list_people(), "up", 0, self.today())
        elif cmd == "add":
            sess.state, sess.data = "add:name", {}
            screen = screens.wizard_step("name", {}, self.today())
        elif cmd == "alerts":
            screen = self.alerts_screen()
        elif cmd == "cancel":
            screen = self.menu_screen(uid, "Ввод отменён.")
        elif cmd == "test_alert":
            if not self.cfg.is_dev(uid):
                screen = self.menu_screen(uid, "⛔ Тестовые алерты доступны только DEV_ADMIN.")
            else:
                await self.send_test_alert(chat_id)
                screen = self.menu_screen(uid, "🧪 Тестовое напоминание отправлено.")
        elif cmd == "logs":
            if not self.cfg.is_dev(uid):
                log.warning("Пользователь %s (не DEV_ADMIN) пытался открыть логи", uid)
                screen = self.menu_screen(uid, "⛔ Логи доступны только DEV_ADMIN.")
            else:
                screen = self.logs_screen("all", 20)
        else:
            screen = self.menu_screen(uid, f"Неизвестная команда /{esc(cmd)}")
        await self.show(uid, chat_id, sess, screen, new=True)

    # -- кнопки ----------------------------------------------------------
    async def on_callback(self, cq: dict) -> None:
        user = cq.get("from") or {}
        msg = cq.get("message") or {}
        chat = msg.get("chat")
        if not self._allowed(user, chat):
            await self.api.answer_callback(cq["id"], "⛔ Нет доступа", show_alert=True)
            return
        uid = int(user["id"])
        chat_id = int(chat["id"]) if chat else uid
        data = cq.get("data") or ""
        sess = self.db.get_session(uid)
        # панель — то сообщение, на котором нажали кнопку
        if msg.get("message_id") and not data.startswith("r"):
            sess.panel_id = int(msg["message_id"])

        toast: Optional[str] = None
        new_panel = False
        screen: Optional[Screen] = None
        head, _, arg = data.partition(":")
        today = self.today()

        if data == "noop":
            await self.api.answer_callback(cq["id"])
            return

        # кнопки на сообщениях-напоминаниях открывают новую панель, не трогая напоминание
        if head in ("rp", "rl"):
            new_panel = True
            head = "p" if head == "rp" else "l"
            arg = arg if head == "p" else "up:0"

        if head == "m":
            sess.state, sess.data = None, {}
            screen = self.menu_screen(uid)
        elif head == "h":
            screen = screens.help_screen(self.cfg.is_dev(uid))
        elif head == "cancel":
            sess.state, sess.data = None, {}
            screen = self.menu_screen(uid, "Отменено.")
        elif head == "l":
            sort, _, page = arg.partition(":")
            sess.state = None
            screen = screens.people_list(self.db.list_people(), sort or "up", _int(page), today)
        elif head == "p":
            sess.state, sess.data = None, {}
            screen = self.card_or_list(_int(arg))
        elif head == "e":
            pid_s, _, field = arg.partition(":")
            p = self.db.get_person(_int(pid_s))
            if p and field in screens.FIELD_PROMPTS:
                sess.state, sess.data = f"edit:{field}", {"pid": p.id}
                screen = screens.edit_prompt(p, field)
            else:
                screen = self.card_or_list(_int(pid_s))
        elif head == "ec":
            pid_s, _, field = arg.partition(":")
            pid = _int(pid_s)
            if field in ("gift", "price") and self.db.get_person(pid):
                self.db.update_person(pid, **{field: None})
                toast = "Очищено"
            sess.state, sess.data = None, {}
            screen = self.card_or_list(pid)
        elif head == "x":
            p = self.db.get_person(_int(arg))
            screen = screens.confirm_delete(p) if p else self.card_or_list(0)
        elif head == "X":
            p = self.db.get_person(_int(arg))
            if p:
                self.db.delete_person(p.id)
                toast = f"{p.name} удалён(а)"
            screen = screens.people_list(self.db.list_people(), "up", 0, today)
        elif head == "a":
            sess.state, sess.data = "add:name", {}
            screen = screens.wizard_step("name", {}, today)
        elif head == "w":
            screen, toast = self.wizard_button(uid, sess, arg)
        elif head == "al":
            sess.state, sess.data = None, {}
            screen = self.alerts_screen()
        elif head == "ao":
            screen = screens.offset_screen(_int(arg))
        elif head == "ad":
            self.db.remove_offset(_int(arg))
            screen = self.alerts_screen("Напоминание удалено.")
        elif head == "ae":
            sess.state, sess.data = "offset:edit", {"old": _int(arg)}
            screen = screens.offset_prompt(_int(arg))
        elif head == "aa":
            sess.state, sess.data = "offset:add", {}
            screen = screens.offset_prompt()
        elif head == "aq":
            sess.state, sess.data = "offset:add", {}
            screen = self.apply_offset(sess, arg)
        elif head == "at":
            sess.state, sess.data = "remind_at", {}
            screen = screens.remind_at_prompt(self.remind_at())
        elif head == "tq":
            sess.state, sess.data = "remind_at", {}
            screen = self.apply_remind_at(sess, arg)
        elif head in ("lg", "lgd"):
            if not self.cfg.is_dev(uid):
                log.warning("Пользователь %s (не DEV_ADMIN) пытался открыть логи", uid)
                await self.api.answer_callback(cq["id"], "⛔ Только для DEV_ADMIN", show_alert=True)
                return
            if head == "lg":
                level, _, cnt = arg.partition(":")
                screen = self.logs_screen(level, _int(cnt))
            else:
                toast = await self.send_log_file(chat_id)
        elif head == "t":
            if self.cfg.is_dev(uid):
                await self.send_test_alert(chat_id)
                toast = "🧪 Тестовое напоминание отправлено"
            else:
                await self.api.answer_callback(cq["id"], "⛔ Только для DEV_ADMIN", show_alert=True)
                return
        else:
            screen = self.menu_screen(uid)

        await self.api.answer_callback(cq["id"], toast)
        if screen is not None:
            await self.show(uid, chat_id, sess, screen, new=new_panel)
        else:
            self.db.save_session(sess)

    # -- ввод текста -------------------------------------------------------
    async def on_input(self, uid: int, chat_id: int, sess: Session, text: str) -> None:
        state = sess.state or ""
        today = self.today()
        screen: Screen
        if state.startswith("add:"):
            screen = self.wizard_input(sess, state.split(":", 1)[1], text)
        elif state.startswith("edit:"):
            field = state.split(":", 1)[1]
            p = self.db.get_person(int(sess.data.get("pid", 0)))
            if not p:
                sess.state, sess.data = None, {}
                screen = self.menu_screen(uid, "Карточка не найдена.")
            else:
                try:
                    self.db.update_person(p.id, **self._parse_field(field, text))
                    sess.state, sess.data = None, {}
                    screen = screens.person_card(self.db.get_person(p.id), today, "✅ Сохранено")  # type: ignore[arg-type]
                except ValueError as e:
                    screen = screens.edit_prompt(p, field, str(e))
        elif state.startswith("offset:"):
            screen = self.apply_offset(sess, text)
        elif state == "remind_at":
            screen = self.apply_remind_at(sess, text)
        else:
            sess.state = None
            screen = self.menu_screen(uid)
        await self.show(uid, chat_id, sess, screen)

    def _parse_field(self, field: str, text: str) -> dict:
        if field == "name":
            name = " ".join(text.split())
            if not name:
                raise ValueError("Имя не может быть пустым")
            if len(name) > MAX_NAME:
                raise ValueError(f"Слишком длинное имя (максимум {MAX_NAME} символов)")
            return {"name": name}
        if field == "date":
            return {"birth": parse_birth_date(text, self.today())}
        if field == "gift":
            gift = text.strip()
            if len(gift) > MAX_GIFT:
                raise ValueError(f"Слишком длинно (максимум {MAX_GIFT} символов)")
            return {"gift": gift or None}
        if field == "price":
            return {"price": parse_price(text)}
        raise ValueError("unknown field")

    # -- мастер ------------------------------------------------------------
    def wizard_input(self, sess: Session, step: str, text: str) -> Screen:
        today = self.today()
        draft = sess.data
        try:
            parsed = self._parse_field(step, text)
        except ValueError as e:
            return screens.wizard_step(step, draft, today, str(e))
        if "birth" in parsed:
            b = parsed.pop("birth")
            draft.update(day=b.day, month=b.month, year=b.year)
        draft.update(parsed)
        return self._wizard_next(sess, step)

    def _wizard_next(self, sess: Session, step: str) -> Screen:
        steps = screens.WIZARD_STEPS
        i = steps.index(step)
        if i + 1 < len(steps):
            nxt = steps[i + 1]
            sess.state = f"add:{nxt}"
            return screens.wizard_step(nxt, sess.data, self.today())
        sess.state = "add:confirm"
        return screens.wizard_confirm(sess.data, self.today())

    def wizard_button(self, uid: int, sess: Session, arg: str) -> tuple[Screen, Optional[str]]:
        state = sess.state or ""
        if arg == "skip" and state in ("add:gift", "add:price"):
            step = state.split(":", 1)[1]
            sess.data[step] = None
            return self._wizard_next(sess, step), None
        if arg == "save" and state == "add:confirm":
            d = sess.data
            pid = self.db.add_person(d["name"], BirthDate(d["day"], d["month"], d.get("year")),
                                     d.get("gift"), d.get("price"), created_by=uid)
            sess.state, sess.data = None, {}
            return screens.person_card(self.db.get_person(pid), self.today(), "✅ Сохранено в базу"), "Сохранено"  # type: ignore[arg-type]
        # устаревшая кнопка
        return self.menu_screen(uid, "Эта кнопка уже неактуальна."), None

    # -- алерты --------------------------------------------------------------
    def alerts_screen(self, notice: Optional[str] = None) -> Screen:
        return screens.alerts_screen(self.db.list_offsets(), self.remind_at(), str(self.cfg.tz), notice)

    def apply_offset(self, sess: Session, text: str) -> Screen:
        old = sess.data.get("old")
        try:
            minutes = parse_offset(text)
        except ValueError as e:
            return screens.offset_prompt(old, str(e))
        if old is not None:
            ok = self.db.replace_offset(int(old), minutes)
        else:
            ok = self.db.add_offset(minutes)
        if not ok:
            return screens.offset_prompt(old, "Такое напоминание уже есть")
        sess.state, sess.data = None, {}
        label = format_offset(minutes)
        return self.alerts_screen(f"✅ Напоминание {'за ' + label if minutes else label} сохранено.")

    def apply_remind_at(self, sess: Session, text: str) -> Screen:
        try:
            t = parse_time_of_day(text)
        except ValueError as e:
            return screens.remind_at_prompt(self.remind_at(), str(e))
        self.db.set_setting("remind_at", t.strftime("%H:%M"))
        sess.state, sess.data = None, {}
        return self.alerts_screen(f"✅ Время напоминаний: {t.strftime('%H:%M')}")

    async def send_test_alert(self, chat_id: int) -> None:
        """Тестовое напоминание (только DEV_ADMIN): ближайший реальный ДР или пример."""
        today = self.today()
        people = screens.sort_people(self.db.list_people(), "up", today)
        if people:
            p = people[0]
        else:
            p = Person(id=0, name="Тестовый Студент", day=today.day, month=today.month,
                       year=today.year - 20, gift="Книга по Python", price=1500)
        bday = next_birthday(p.day, p.month, today)
        offs = self.db.list_offsets() or [MINUTES_IN_DAY]
        await self.ui.send(chat_id, screens.reminder(p, bday, offs[-1], self.now(), test=True))

    # -- логи (только DEV_ADMIN) ---------------------------------------------
    def logs_screen(self, level: str, count: int) -> Screen:
        if level not in screens.LOG_LEVEL_LABELS:
            level = "all"
        if count not in screens.LOG_COUNTS:
            count = 20
        path = current_log_path()
        if path is None or not path.exists():
            return screens.logs_screen([], level, count, file_name=None, size_total=0,
                                       max_mb=self.cfg.log_max_mb, enabled=False)
        return screens.logs_screen(tail_records(path, count, level), level, count,
                                   file_name=str(path), size_total=total_size(path),
                                   max_mb=self.cfg.log_max_mb, enabled=True)

    async def send_log_file(self, chat_id: int) -> str:
        path = current_log_path()
        if path is None or not path.exists():
            return "Файл логов не найден"
        for h in logging.getLogger().handlers:
            h.flush()
        try:
            await self.api.send_document(chat_id, str(path), filename=path.name,
                                         caption=f"📜 {path.name} · {path.stat().st_size // 1024} КБ")
        except (TelegramError, httpx.HTTPError, OSError) as e:
            log.warning("Не удалось отправить лог: %s", e)
            return "Не удалось отправить файл"
        return "📥 Файл отправлен"

    # -- вспомогательное -----------------------------------------------------
    def menu_screen(self, uid: int, notice: Optional[str] = None) -> Screen:
        return screens.main_menu(self.db.list_people(), self.today(), self.cfg.is_dev(uid), notice)

    def card_or_list(self, pid: int) -> Screen:
        p = self.db.get_person(pid)
        if p:
            return screens.person_card(p, self.today())
        return screens.people_list(self.db.list_people(), "up", 0, self.today())

    async def show(self, uid: int, chat_id: int, sess: Session, screen: Screen, new: bool = False) -> None:
        if new:
            sess.panel_id = await self.ui.send(chat_id, screen)
        else:
            sess.panel_id = await self.ui.edit(chat_id, sess.panel_id, screen)
        self.db.save_session(sess)


def _int(s: str) -> int:
    try:
        return int(s)
    except (TypeError, ValueError):
        return 0
