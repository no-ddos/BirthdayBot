"""Точка входа: python -m birthdaybot"""

from __future__ import annotations

import asyncio
import logging
import signal
import sys

from .bot import BirthdayBot
from .config import load_config
from .db import Database
from .telegram import TelegramAPI


def main() -> None:
    cfg = load_config()
    logging.basicConfig(
        level=getattr(logging, cfg.log_level, logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stdout,
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
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
