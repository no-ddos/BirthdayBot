"""Фоновый планировщик напоминаний.

Каждые CHECK_INTERVAL секунд считает, какие алерты «созрели» (dates.due_alerts),
отправляет их всем админам и помечает в БД — повторно не уйдут даже после рестарта.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime

import httpx

from . import screens
from .config import Config
from .dates import parse_time_of_day, due_alerts
from .db import Database
from .telegram import TelegramError
from .ui import Renderer

log = logging.getLogger(__name__)

CHECK_INTERVAL = 30


class ReminderScheduler:
    def __init__(self, cfg: Config, db: Database, ui: Renderer):
        self.cfg = cfg
        self.db = db
        self.ui = ui
        self._running = True

    def stop(self) -> None:
        self._running = False

    async def run(self) -> None:
        log.info("Планировщик напоминаний запущен (проверка каждые %s с)", CHECK_INTERVAL)
        while self._running:
            try:
                await self.tick()
            except Exception:  # noqa: BLE001
                log.exception("Ошибка в планировщике")
            await asyncio.sleep(CHECK_INTERVAL)

    async def tick(self, now: datetime | None = None) -> int:
        """Одна проверка. Возвращает число отправленных напоминаний."""
        now = now or datetime.now(self.cfg.tz)
        try:
            remind_at = parse_time_of_day(self.db.get_setting("remind_at", self.cfg.default_remind_at) or "09:00")
        except ValueError:
            remind_at = parse_time_of_day("09:00")
        people = {p.id: p for p in self.db.list_people()}
        due = due_alerts(
            [(p.id, p.day, p.month) for p in people.values()],
            self.db.list_offsets(), now, remind_at, self.cfg.tz, self.db.sent_keys(),
        )
        sent = 0
        for alert in due:
            p = people[alert.person_id]
            screen = screens.reminder(p, alert.bday, alert.offset_minutes, now)
            delivered, network_fail = 0, False
            for admin_id in sorted(self.cfg.all_admins):
                try:
                    await self.ui.send(admin_id, screen)
                    delivered += 1
                except TelegramError as e:
                    # 403 — админ не запускал бота / заблокировал его
                    log.warning("Не удалось отправить напоминание админу %s: %s", admin_id, e)
                except httpx.HTTPError as e:
                    network_fail = True
                    log.warning("Сеть: напоминание админу %s не ушло: %s", admin_id, e)
            if delivered or not network_fail:
                # помечаем, чтобы не слать повторно (и поглощённые смещения тоже)
                for off in (alert.offset_minutes, *alert.also_mark):
                    self.db.mark_sent(alert.person_id, off, alert.bday_year)
                sent += 1
                log.info("Напоминание о %s (за %s мин) отправлено %s админам",
                         p.name, alert.offset_minutes, delivered)
        if due:
            self.db.cleanup_sent(now.year - 1)
        return sent
