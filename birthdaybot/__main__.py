"""Точка входа: python -m birthdaybot"""

from __future__ import annotations

import asyncio
import logging
import signal

from .bot import BirthdayBot
from .config import load_config
from .db import Database
from .logs import setup_logging
from .telegram import TelegramAPI


def main() -> None:
    cfg = load_config()
    log_path = setup_logging(cfg.log_level, cfg.log_dir, cfg.log_max_mb, cfg.log_files,
                             secrets=[cfg.bot_token])
    if log_path:
        logging.getLogger(__name__).info("Логи: %s (до %s МБ, %s файлов)", log_path, cfg.log_max_mb, cfg.log_files)
    asyncio.run(_run(cfg))


async def _run(cfg) -> None:
    db = Database(cfg.db_path)
    api = TelegramAPI(cfg.bot_token, cfg.api_base)
    bot = BirthdayBot(cfg, db, api)
    loop = asyncio.get_running_loop()
    task = asyncio.current_task()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, lambda: task and task.cancel())
        except NotImplementedError:  # Windows
            pass
    try:
        await bot.run()
    except asyncio.CancelledError:
        logging.getLogger(__name__).info("Остановка…")
    finally:
        await api.close()
        db.close()


if __name__ == "__main__":
    main()
