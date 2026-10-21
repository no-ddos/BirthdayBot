"""Интеграционные тесты с поддельным Telegram Bot API (httpx.MockTransport)."""

import asyncio
import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx

from birthdaybot.bot import BirthdayBot
from birthdaybot.config import Config
from birthdaybot.db import Database
from birthdaybot.scheduler import ReminderScheduler
from birthdaybot.telegram import TelegramAPI

DEV, ADMIN, STRANGER = 111, 222, 999
TZ = ZoneInfo("Europe/Moscow")


class FakeTelegram:
    def __init__(self, rich_supported: bool = True):
        self.calls: list[tuple[str, dict]] = []
        self.rich_supported = rich_supported
        self.next_id = 100

    def handler(self, request: httpx.Request) -> httpx.Response:
        method = request.url.path.rsplit("/", 1)[-1]
        ctype = request.headers.get("content-type", "")
        body = {"_multipart": request.content} if ctype.startswith("multipart/") else json.loads(request.content or b"{}")
        self.calls.append((method, body))
        if method == "sendRichMessage" and not self.rich_supported:
            return httpx.Response(404, json={"ok": False, "error_code": 404, "description": "Not Found: method not found"})
        if method in ("sendRichMessage", "sendMessage"):
            self.next_id += 1
            return httpx.Response(200, json={"ok": True, "result": {"message_id": self.next_id, "chat": {"id": body["chat_id"]}}})
        if method == "getMe":
            return httpx.Response(200, json={"ok": True, "result": {"id": 1, "username": "TestBirthdayBot"}})
        return httpx.Response(200, json={"ok": True, "result": True})

    def methods(self):
        return [m for m, _ in self.calls]

    def last(self, method):
        return [b for m, b in self.calls if m == method][-1]


def make_bot(tmp_path: Path, fake: FakeTelegram, rich_mode="auto"):
    cfg = Config(bot_token="TEST", dev_admins=frozenset({DEV}), admins=frozenset({ADMIN}), tz=TZ,
                 db_path=tmp_path / "t.db", rich_mode=rich_mode)
    db = Database(cfg.db_path)
    db.bootstrap(cfg.default_offsets, cfg.default_remind_at)
    api = TelegramAPI("TEST", transport=httpx.MockTransport(fake.handler))
    return BirthdayBot(cfg, db, api), db


def msg(uid, text, mid=1):
    return {"update_id": mid, "message": {"message_id": mid, "from": {"id": uid}, "chat": {"id": uid, "type": "private"}, "text": text}}


def cb(uid, data, mid=101):
    return {"update_id": 5, "callback_query": {"id": "cq", "from": {"id": uid}, "data": data,
                                                  "message": {"message_id": mid, "chat": {"id": uid, "type": "private"}}}}


def run(coro):
    return asyncio.run(coro)


def test_start_sends_rich_menu_with_buttons(tmp_path):
    fake = FakeTelegram()
    bot, _ = make_bot(tmp_path, fake)
    run(bot.handle_update(msg(DEV, "/start")))
    body = fake.last("sendRichMessage")
    html = body["rich_message"]["html"]
    assert body["chat_id"] == DEV
    assert "<h1>🎂 BirthdayBot</h1>" in html
    assert '<tg-button type="callback_data" style="primary" data="l:up:0">📋 Список</tg-button>' in html
    assert 'data="t"' in html  # dev видит тестовый алерт


def test_regular_admin_has_no_test_alert(tmp_path):
    fake = FakeTelegram()
    bot, _ = make_bot(tmp_path, fake)
    run(bot.handle_update(msg(ADMIN, "/start")))
    assert 'data="t"' not in fake.last("sendRichMessage")["rich_message"]["html"]
    run(bot.handle_update(cb(ADMIN, "t")))
    assert fake.last("answerCallbackQuery")["show_alert"] is True


def test_stranger_is_ignored(tmp_path):
    fake = FakeTelegram()
    bot, _ = make_bot(tmp_path, fake)
    run(bot.handle_update(msg(STRANGER, "/start")))
    assert fake.calls == []
    run(bot.handle_update(cb(STRANGER, "m")))
    assert fake.methods() == ["answerCallbackQuery"]


def test_group_chat_ignored(tmp_path):
    fake = FakeTelegram()
    bot, _ = make_bot(tmp_path, fake)
    upd = msg(DEV, "/start")
    upd["message"]["chat"] = {"id": -100, "type": "supergroup"}
    run(bot.handle_update(upd))
    assert fake.calls == []


def test_add_wizard_edit_and_delete(tmp_path):
    fake = FakeTelegram()
    bot, db = make_bot(tmp_path, fake)
    run(bot.handle_update(cb(ADMIN, "a")))
    assert fake.last("editMessageText")["rich_message"]["html"].count("шаг 1/4") == 1
    run(bot.handle_update(msg(ADMIN, "test", 2)))
    run(bot.handle_update(msg(ADMIN, "31.02.2004", 3)))       # ошибка
    assert "⚠️" in fake.last("editMessageText")["rich_message"]["html"]
    run(bot.handle_update(msg(ADMIN, "25.12.2004", 4)))
    run(bot.handle_update(msg(ADMIN, "Наушники", 5)))
    run(bot.handle_update(msg(ADMIN, "3к", 6)))
    html = fake.last("editMessageText")["rich_message"]["html"]
    assert 'data="w:save"' in html and "Наушники" in html
    assert db.list_people() == []                                  # до «Сохранить» ничего нет
    run(bot.handle_update(cb(ADMIN, "w:save")))
    [p] = db.list_people()
    assert (p.name, p.day, p.month, p.year, p.gift, p.price) == ("test", 25, 12, 2004, "Наушники", 3000)
    # пользовательские сообщения удаляются, панель редактируется (один и тот же message_id)
    assert "deleteMessage" in fake.methods()
    assert all(b["message_id"] == 101 for m, b in fake.calls if m == "editMessageText")

    # редактирование подарка
    run(bot.handle_update(cb(ADMIN, f"e:{p.id}:gift")))
    run(bot.handle_update(msg(ADMIN, "Книга", 7)))
    assert db.get_person(p.id).gift == "Книга"
    # смена даты
    run(bot.handle_update(cb(ADMIN, f"e:{p.id}:date")))
    run(bot.handle_update(msg(ADMIN, "01.03", 8)))
    assert (db.get_person(p.id).day, db.get_person(p.id).month, db.get_person(p.id).year) == (1, 3, None)
    # удаление с подтверждением
    run(bot.handle_update(cb(ADMIN, f"x:{p.id}")))
    assert db.get_person(p.id) is not None
    run(bot.handle_update(cb(ADMIN, f"X:{p.id}")))
    assert db.get_person(p.id) is None


def test_wizard_skip_optional(tmp_path):
    fake = FakeTelegram()
    bot, db = make_bot(tmp_path, fake)
    run(bot.handle_update(msg(ADMIN, "/add")))
    run(bot.handle_update(msg(ADMIN, "Аня", 2)))
    run(bot.handle_update(msg(ADMIN, "29.02", 3)))
    run(bot.handle_update(cb(ADMIN, "w:skip")))
    run(bot.handle_update(cb(ADMIN, "w:skip")))
    run(bot.handle_update(cb(ADMIN, "w:save")))
    [p] = db.list_people()
    assert (p.gift, p.price, p.day, p.month) == (None, None, 29, 2)


def test_list_sorting_and_pagination(tmp_path):
    fake = FakeTelegram()
    bot, db = make_bot(tmp_path, fake)
    from birthdaybot.dates import BirthDate
    for i in range(23):
        db.add_person(f"Студент {i:02d}", BirthDate(1 + i, 1 + i % 12, 2004), None, i * 100)
    for sort in ("up", "name", "cal", "price"):
        run(bot.handle_update(cb(ADMIN, f"l:{sort}:1")))
        html = fake.last("editMessageText")["rich_message"]["html"]
        assert "<table bordered striped compact>" in html
        assert "стр. 2/3" in html
    assert html.index("Студент 12") < html.index("Студент 11")  # по цене: дороже — выше


def test_alert_offsets_management(tmp_path):
    fake = FakeTelegram()
    bot, db = make_bot(tmp_path, fake)
    assert db.list_offsets() == [10080, 1440]
    run(bot.handle_update(cb(DEV, "aa")))
    run(bot.handle_update(msg(DEV, "3h", 2)))
    assert db.list_offsets() == [10080, 1440, 180]
    run(bot.handle_update(cb(DEV, "ae:180")))
    run(bot.handle_update(msg(DEV, "30m", 3)))
    assert db.list_offsets() == [10080, 1440, 30]
    run(bot.handle_update(cb(DEV, "ad:30")))
    assert db.list_offsets() == [10080, 1440]
    run(bot.handle_update(cb(DEV, "aq:3d")))
    assert 4320 in db.list_offsets()
    run(bot.handle_update(cb(DEV, "at")))
    run(bot.handle_update(msg(DEV, "10:30", 4)))
    assert db.get_setting("remind_at") == "10:30"


def test_scheduler_sends_to_all_admins_once(tmp_path):
    fake = FakeTelegram()
    bot, db = make_bot(tmp_path, fake)
    from birthdaybot.dates import BirthDate
    db.add_person("test", BirthDate(25, 12, 2004), "Наушники", 3000)
    sched = ReminderScheduler(bot.cfg, db, bot.ui)
    now = datetime(2026, 12, 18, 9, 0, 30, tzinfo=TZ)
    assert run(sched.tick(now)) == 1
    sends = [b for m, b in fake.calls if m == "sendRichMessage"]
    assert sorted(b["chat_id"] for b in sends) == [DEV, ADMIN]
    html = sends[0]["rich_message"]["html"]
    assert "test" in html and "Наушники" in html and "Исполнится" in html and "22" in html
    # повторная проверка (и после «рестарта» — новая БД-сессия) ничего не шлёт
    assert run(sched.tick(now)) == 0
    db2 = Database(bot.cfg.db_path)
    assert run(ReminderScheduler(bot.cfg, db2, bot.ui).tick(now)) == 0
    # за день — ещё одно
    assert run(sched.tick(datetime(2026, 12, 24, 9, 0, tzinfo=TZ))) == 1


def test_test_alert_for_dev(tmp_path):
    fake = FakeTelegram()
    bot, _ = make_bot(tmp_path, fake)
    run(bot.handle_update(cb(DEV, "t")))
    html = fake.last("sendRichMessage")["rich_message"]["html"]
    assert "🧪 ТЕСТ" in html
    assert fake.last("sendRichMessage")["chat_id"] == DEV


def test_fallback_to_classic_when_rich_unsupported(tmp_path):
    fake = FakeTelegram(rich_supported=False)
    bot, _ = make_bot(tmp_path, fake)
    run(bot.handle_update(msg(DEV, "/start")))
    body = fake.last("sendMessage")
    assert body["parse_mode"] == "HTML"
    assert body["reply_markup"]["inline_keyboard"][0][0]["callback_data"] == "l:up:0"
    # дальше rich даже не пробуем
    n = fake.methods().count("sendRichMessage")
    run(bot.handle_update(msg(DEV, "/menu", 2)))
    assert fake.methods().count("sendRichMessage") == n


def test_rich_mode_off(tmp_path):
    fake = FakeTelegram()
    bot, _ = make_bot(tmp_path, fake, rich_mode="off")
    run(bot.handle_update(msg(DEV, "/start")))
    assert "sendRichMessage" not in fake.methods()
    assert "<table" not in fake.last("sendMessage")["text"]
