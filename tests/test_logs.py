import asyncio
import logging
from pathlib import Path

import pytest

from birthdaybot import logs
from birthdaybot.logs import (
    REDACTED, current_log_path, log_files, redact, rotation_params, setup_logging, tail_records, total_size,
)
from test_bot_flow import ADMIN, DEV, FakeTelegram, cb, make_bot, msg

TOKEN = "1234567890:AAH5x_FAKE-token-for-tests-abcdefghijk"


@pytest.fixture
def logdir(tmp_path):
    root = logging.getLogger()
    saved_handlers, saved_level = list(root.handlers), root.level
    d = tmp_path / "logs"
    yield d
    for h in list(root.handlers):
        root.removeHandler(h)
        h.close()
    for h in saved_handlers:
        root.addHandler(h)
    root.setLevel(saved_level)
    logs._secrets.clear()


# -- маскировка токена ------------------------------------------------------------

def test_redact_patterns():
    url = f"https://api.telegram.org/bot{TOKEN}/getUpdates"
    assert TOKEN not in redact(url)
    assert redact(url) == f"https://api.telegram.org/bot{REDACTED}/getUpdates"
    assert TOKEN not in redact(f"token={TOKEN} trailing")
    # обычные числа и время не трогаем
    assert redact("2026-10-21 18:00:00 user 123456789 id 42:15") == "2026-10-21 18:00:00 user 123456789 id 42:15"


def test_token_not_in_log_file(logdir):
    path = setup_logging("DEBUG", logdir, 1, 2, secrets=["short:tok-custom-secret"])
    lg = logging.getLogger("birthdaybot.test")
    lg.info("request https://api.telegram.org/bot%s/getMe", TOKEN)
    lg.info("custom short:tok-custom-secret here")
    try:
        raise RuntimeError(f"boom {TOKEN}")
    except RuntimeError:
        lg.exception("failed")
    # httpx пишет URL с токеном на INFO — должен быть заглушен
    logging.getLogger("httpx").info("HTTP Request: POST https://api.telegram.org/bot%s/x", TOKEN)
    for h in logging.getLogger().handlers:
        h.flush()
    text = path.read_text(encoding="utf-8")
    assert TOKEN not in text and "tok-custom-secret" not in text
    assert REDACTED in text and "Traceback" in text
    assert "HTTP Request" not in text
    assert logging.getLogger("httpx").level == logging.WARNING


# -- ротация и общий лимит ----------------------------------------------------------

def test_rotation_params():
    mb = 1024 * 1024
    assert rotation_params(100, 10) == (10 * mb, 9)
    max_bytes, backups = rotation_params(100, 10)
    assert max_bytes * (backups + 1) <= 100 * mb
    assert rotation_params(5, 1) == (5 * mb, 0)


def test_rotation_total_size_cap(logdir):
    path = setup_logging("INFO", logdir, max_mb=1, files=4)
    lg = logging.getLogger("birthdaybot.flood")
    line = "x" * 500
    for i in range(6000):  # ~3 МБ при лимите 1 МБ
        lg.info("%05d %s", i, line)
    for h in logging.getLogger().handlers:
        h.flush()
    files = log_files(path)
    assert len(files) == 4                      # текущий + 3 бэкапа, старые удалены
    assert not path.with_name(path.name + ".4").exists()
    assert total_size(path) <= 1024 * 1024
    assert all(f.stat().st_size <= 1024 * 1024 // 4 for f in files)
    # свежие записи на месте, самые старые удалены
    assert "05999" in path.read_text(encoding="utf-8")
    assert "00000 " not in "".join(f.read_text(encoding="utf-8") for f in files)


def test_log_dir_off(logdir):
    assert setup_logging("INFO", None) is None
    assert current_log_path() is None


# -- чтение хвоста ---------------------------------------------------------------------

def test_tail_records_filter_and_traceback(logdir):
    path = setup_logging("INFO", logdir, 10, 2)
    lg = logging.getLogger("birthdaybot.t")
    for i in range(30):
        lg.info("info %d", i)
    lg.warning("warn one")
    try:
        1 / 0
    except ZeroDivisionError:
        lg.exception("err one")
    lg.info("last info")
    for h in logging.getLogger().handlers:
        h.flush()
    recs = tail_records(path, 5)
    assert len(recs) == 5 and recs[-1].endswith("last info")
    errs = tail_records(path, 10, "err")
    assert len(errs) == 1 and "ZeroDivisionError" in errs[0] and "Traceback" in errs[0]
    warns = tail_records(path, 10, "warn")
    assert len(warns) == 2


# -- права доступа ----------------------------------------------------------------------

def run(c):
    return asyncio.run(c)


def test_logs_visible_only_for_dev(tmp_path, logdir):
    setup_logging("INFO", logdir, 1, 2)
    logging.getLogger("birthdaybot.x").error("something broke")
    fake = FakeTelegram()
    bot, _ = make_bot(tmp_path, fake)

    # обычный админ: нет кнопки, команда и колбэки не работают
    run(bot.handle_update(msg(ADMIN, "/start")))
    assert 'data="lg:' not in fake.last("sendRichMessage")["rich_message"]["html"]
    run(bot.handle_update(msg(ADMIN, "/logs", 2)))
    html = fake.last("sendRichMessage")["rich_message"]["html"]
    assert "только DEV_ADMIN" in html and "something broke" not in html
    n_edits = fake.methods().count("editMessageText")
    run(bot.handle_update(cb(ADMIN, "lg:all:20")))
    assert fake.last("answerCallbackQuery")["show_alert"] is True
    run(bot.handle_update(cb(ADMIN, "lgd")))
    assert fake.methods().count("editMessageText") == n_edits
    assert "sendDocument" not in fake.methods()

    # dev-админ: есть кнопка, экран логов и скачивание
    run(bot.handle_update(msg(DEV, "/start", 3)))
    assert 'data="lg:all:20"' in fake.last("sendRichMessage")["rich_message"]["html"]
    run(bot.handle_update(cb(DEV, "lg:err:50")))
    html = fake.last("editMessageText")["rich_message"]["html"]
    assert "<pre>" in html and "something broke" in html and "✓ ❌ ERROR" in html
    run(bot.handle_update(cb(DEV, "lgd")))
    assert "sendDocument" in fake.methods()
    assert b"something broke" in fake.last("sendDocument")["_multipart"]


def test_logs_screen_when_file_logging_off(tmp_path, logdir):
    setup_logging("INFO", None)
    fake = FakeTelegram()
    bot, _ = make_bot(tmp_path, fake)
    run(bot.handle_update(cb(DEV, "lg:all:20")))
    assert "выключена" in fake.last("editMessageText")["rich_message"]["html"]


def test_logs_classic_fallback_fits_limit(tmp_path, logdir):
    path = setup_logging("INFO", logdir, 10, 2)
    for i in range(300):
        logging.getLogger("birthdaybot.y").info("line %d %s", i, "y" * 100)
    for h in logging.getLogger().handlers:
        h.flush()
    fake = FakeTelegram(rich_supported=False)
    bot, _ = make_bot(tmp_path, fake, rich_mode="off")
    run(bot.handle_update(msg(DEV, "/logs")))
    text = fake.last("sendMessage")["text"]
    assert len(text) <= 4096 and "line 299" in text
    assert Path(path).exists()
