"""Тонкий асинхронный клиент Telegram Bot API на httpx.

Библиотеки (aiogram, python-telegram-bot) пока не знают про Rich Messages
(Bot API 10.1+/10.3), поэтому все вызовы делаются «сырыми» HTTP-запросами.
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any, Optional

import httpx

log = logging.getLogger(__name__)


class TelegramError(Exception):
    def __init__(self, method: str, code: int, description: str, parameters: Optional[dict] = None):
        super().__init__(f"{method}: [{code}] {description}")
        self.method = method
        self.code = code
        self.description = description or ""
        self.parameters = parameters or {}

    @property
    def not_modified(self) -> bool:
        return "message is not modified" in self.description.lower()


class TelegramAPI:
    def __init__(self, token: str, base_url: str = "https://api.telegram.org",
                 transport: Optional[httpx.AsyncBaseTransport] = None):
        self._url = f"{base_url}/bot{token}/"
        self._client = httpx.AsyncClient(timeout=httpx.Timeout(70.0, connect=15.0), transport=transport)

    async def close(self) -> None:
        await self._client.aclose()

    async def call(self, method: str, **params: Any) -> Any:
        payload = {k: v for k, v in params.items() if v is not None}
        for attempt in range(3):
            resp = await self._client.post(self._url + method, json=payload)
            try:
                data = resp.json()
            except ValueError:
                raise TelegramError(method, resp.status_code, resp.text[:200]) from None
            if data.get("ok"):
                return data.get("result")
            code = int(data.get("error_code", resp.status_code))
            params_ = data.get("parameters") or {}
            if code == 429 and attempt < 2:
                delay = float(params_.get("retry_after", 1))
                log.warning("429 on %s, sleeping %.1fs", method, delay)
                await asyncio.sleep(delay)
                continue
            raise TelegramError(method, code, data.get("description", ""), params_)
        raise TelegramError(method, 429, "Too Many Requests")

    # -- обновления ---------------------------------------------------------
    async def get_updates(self, offset: Optional[int], timeout: int = 50) -> list[dict]:
        return await self.call("getUpdates", offset=offset, timeout=timeout,
                               allowed_updates=["message", "callback_query"])

    # -- Rich Messages (Bot API 10.1+, кнопки внутри — 10.3) --------------------
    async def send_rich_message(self, chat_id: int, html: str, *, disable_notification: bool = False) -> dict:
        """sendRichMessage с InputRichMessage{html=...}. Кнопки — теги <tg-button> внутри html."""
        return await self.call(
            "sendRichMessage",
            chat_id=chat_id,
            rich_message={"html": html, "skip_entity_detection": True},
            disable_notification=disable_notification or None,
        )

    async def edit_rich_message(self, chat_id: int, message_id: int, html: str) -> Any:
        """Редактирование rich-сообщения: editMessageText с полем rich_message вместо text."""
        return await self.call(
            "editMessageText",
            chat_id=chat_id,
            message_id=message_id,
            rich_message={"html": html, "skip_entity_detection": True},
        )

    # -- классические сообщения (фолбэк) ------------------------------------
    async def send_message(self, chat_id: int, text: str, reply_markup: Optional[dict] = None,
                           disable_notification: bool = False) -> dict:
        return await self.call("sendMessage", chat_id=chat_id, text=text, parse_mode="HTML",
                               reply_markup=reply_markup,
                               link_preview_options={"is_disabled": True},
                               disable_notification=disable_notification or None)

    async def edit_message_text(self, chat_id: int, message_id: int, text: str,
                                reply_markup: Optional[dict] = None) -> Any:
        return await self.call("editMessageText", chat_id=chat_id, message_id=message_id, text=text,
                               parse_mode="HTML", reply_markup=reply_markup,
                               link_preview_options={"is_disabled": True})

    # -- прочее -------------------------------------------------------------
    async def answer_callback(self, callback_id: str, text: Optional[str] = None,
                              show_alert: bool = False) -> None:
        try:
            await self.call("answerCallbackQuery", callback_query_id=callback_id, text=text,
                            show_alert=show_alert or None)
        except TelegramError as e:  # старые колбэки и т.п. — не критично
            log.debug("answerCallbackQuery failed: %s", e)

    async def delete_message(self, chat_id: int, message_id: int) -> bool:
        try:
            await self.call("deleteMessage", chat_id=chat_id, message_id=message_id)
            return True
        except TelegramError:
            return False

    async def set_my_commands(self, commands: list[tuple[str, str]], scope: Optional[dict] = None) -> None:
        await self.call("setMyCommands",
                        commands=[{"command": c, "description": d} for c, d in commands],
                        scope=scope)

    async def send_document(self, chat_id: int, path: str, filename: Optional[str] = None,
                            caption: Optional[str] = None) -> dict:
        """sendDocument (multipart): отправка файла, например текущего лога."""
        with open(path, "rb") as fh:
            content = fh.read()
        data = {"chat_id": str(chat_id)}
        if caption:
            data["caption"] = caption
        resp = await self._client.post(self._url + "sendDocument", data=data,
                                       files={"document": (filename or os.path.basename(path), content,
                                                           "text/plain")})
        body = resp.json()
        if not body.get("ok"):
            raise TelegramError("sendDocument", int(body.get("error_code", resp.status_code)),
                                body.get("description", ""))
        return body["result"]

    async def get_me(self) -> dict:
        return await self.call("getMe")
