from pathlib import Path

import pytest

from birthdaybot.config import load_config

KEYS = ["BOT_TOKEN", "DEV_ADMIN", "ADMIN", "LOG_DIR", "LOG_MAX_MB", "LOG_FILES", "TZ", "RICH_MODE"]


@pytest.fixture
def env(monkeypatch, tmp_path):
    for k in KEYS:
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("BOT_TOKEN", "1:x")
    monkeypatch.setenv("DEV_ADMIN", "1, 2")
    monkeypatch.setenv("ADMIN", "3")
    monkeypatch.setenv("TZ", "Europe/Moscow")
    return monkeypatch, tmp_path / "missing.env"


def test_defaults(env):
    _, f = env
    cfg = load_config(f)
    assert cfg.dev_admins == {1, 2} and cfg.admins == {3}
    assert cfg.log_dir == Path("logs") and cfg.log_max_mb == 100 and cfg.log_files == 10


def test_log_settings(env):
    mp, f = env
    mp.setenv("LOG_DIR", "/var/log/birthdaybot")
    mp.setenv("LOG_MAX_MB", "50")
    cfg = load_config(f)
    assert cfg.log_dir == Path("/var/log/birthdaybot") and cfg.log_max_mb == 50
    mp.setenv("LOG_DIR", "off")
    assert load_config(f).log_dir is None
    mp.setenv("LOG_MAX_MB", "abc")
    with pytest.raises(SystemExit):
        load_config(f)
