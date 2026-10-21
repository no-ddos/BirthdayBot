"""Описание экранов и их отрисовка.

Экран (Screen) описывается один раз и рендерится двумя способами:
* rich  — HTML для Rich Messages (заголовки, таблицы, кнопки <tg-button> прямо в сообщении);
* classic — обычный HTML parse_mode + InlineKeyboardMarkup (фолбэк).
"""

from __future__ import annotations

import html as _html
import logging
import re
from dataclasses import dataclass, field
from typing import Optional, Union

from .telegram import TelegramAPI, TelegramError

log = logging.getLogger(__name__)


def esc(text: object) -> str:
    """Экранирование пользовательского текста (числовые сущности поддерживаются rich HTML)."""
    return _html.escape(str(text), quote=True)


# ---------------------------------------------------------------------------
# Блоки экрана
# ---------------------------------------------------------------------------

@dataclass
class Heading:
    text: str           # plain text
    level: int = 2


@dataclass
class Para:
    html: str           # inline-HTML: <b>, <i>, <u>, <s>, <code>, <tg-spoiler>


@dataclass
class Quote:
    html: str


@dataclass
class Table:
    headers: list[str]
    rows: list[list[str]]              # plain text в ячейках
    align: Optional[list[str]] = None  # left|center|right на колонку
    caption: Optional[str] = None
    header: bool = True                # первая строка — заголовок


@dataclass
class Footer:
    text: str


@dataclass
class Divider:
    pass


@dataclass
class Pre:
    text: str                 # plain text, моноширинный блок
    max_rich: int = 20000     # лимит rich-сообщения — 32768 символов
    max_classic: int = 3300   # лимит обычного сообщения — 4096


def _tail(text: str, limit: int) -> str:
    """Обрезает начало, оставляя самое свежее (конец)."""
    if len(text) <= limit:
        return text
    cut = text[len(text) - limit:]
    nl = cut.find("\n")
    return "…\n" + (cut[nl + 1:] if 0 <= nl < 200 else cut)


Block = Union[Heading, Para, Quote, Table, Footer, Divider, Pre]

STYLES = {"primary", "success", "danger", "link"}


@dataclass
class Button:
    text: str
    data: str
    style: Optional[str] = None

    def __post_init__(self) -> None:
        if len(self.data.encode()) > 64:
            raise ValueError(f"callback_data too long: {self.data!r}")
        if self.style and self.style not in STYLES:
            raise ValueError(f"bad style {self.style}")


@dataclass
class Screen:
    blocks: list[Block] = field(default_factory=list)
    buttons: list[list[Button]] = field(default_factory=list)

    def add(self, *blocks: Block) -> "Screen":
        self.blocks.extend(blocks)
        return self

    def row(self, *buttons: Button) -> "Screen":
        btns = [b for b in buttons if b is not None]
        for i in range(0, len(btns), 8):  # не больше 8 кнопок в ряду
            self.buttons.append(btns[i:i + 8])
        return self


# ---------------------------------------------------------------------------
# Rich HTML
# ---------------------------------------------------------------------------

def render_rich(screen: Screen) -> str:
    out: list[str] = []
    for b in screen.blocks:
        if isinstance(b, Heading):
            lvl = min(max(b.level, 1), 6)
            out.append(f"<h{lvl}>{esc(b.text)}</h{lvl}>")
        elif isinstance(b, Para):
            out.append(f"<p>{b.html}</p>")
        elif isinstance(b, Quote):
            out.append(f"<blockquote>{b.html}</blockquote>")
        elif isinstance(b, Footer):
            out.append(f"<footer>{esc(b.text)}</footer>")
        elif isinstance(b, Divider):
            out.append("<hr/>")
        elif isinstance(b, Pre):
            out.append(f"<pre>{esc(_tail(b.text, b.max_rich))}</pre>")
        elif isinstance(b, Table):
            out.append(_rich_table(b))
    for row in screen.buttons:
        btns = "".join(_rich_button(btn) for btn in row)
        out.append(f'<tg-button-row align="center">{btns}</tg-button-row>')
    return "\n".join(out)


def _rich_button(b: Button) -> str:
    style = f' style="{b.style}"' if b.style else ""
    return f'<tg-button type="callback_data"{style} data="{esc(b.data)}">{esc(b.text)}</tg-button>'


def _rich_table(t: Table) -> str:
    parts = ["<table bordered striped compact>"]
    if t.caption:
        parts.append(f"<caption>{esc(t.caption)}</caption>")

    def cell(tag: str, value: str, idx: int) -> str:
        al = ""
        if t.align and idx < len(t.align) and t.align[idx]:
            al = f' align="{t.align[idx]}"'
        return f"<{tag}{al}>{esc(value)}</{tag}>"

    if t.header and t.headers:
        parts.append("<tr>" + "".join(cell("th", h, i) for i, h in enumerate(t.headers)) + "</tr>")
    for r in t.rows:
        parts.append("<tr>" + "".join(cell("td", v, i) for i, v in enumerate(r)) + "</tr>")
    parts.append("</table>")
    return "".join(parts)


# ---------------------------------------------------------------------------
# Классический HTML + inline keyboard (фолбэк)
# ---------------------------------------------------------------------------

_INLINE_ALLOWED = re.compile(r"</?(?:b|i|u|s|code|tg-spoiler)>")


def render_classic(screen: Screen) -> tuple[str, Optional[dict]]:
    out: list[str] = []
    for b in screen.blocks:
        if isinstance(b, Heading):
            out.append(f"<b>{esc(b.text)}</b>")
        elif isinstance(b, Para):
            out.append(b.html)
        elif isinstance(b, Quote):
            out.append(f"<blockquote>{b.html}</blockquote>")
        elif isinstance(b, Footer):
            out.append(f"<i>{esc(b.text)}</i>")
        elif isinstance(b, Divider):
            out.append("—————")
        elif isinstance(b, Pre):
            out.append(f"<pre>{esc(_tail(b.text, b.max_classic))}</pre>")
        elif isinstance(b, Table):
            out.append(_classic_table(b))
    text = "\n\n".join(out)
    if len(text) > 4000:
        text = text[:3990] + "…"
    markup = None
    if screen.buttons:
        markup = {"inline_keyboard": [
            [{"text": btn.text, "callback_data": btn.data} for btn in row] for row in screen.buttons
        ]}
    return text, markup


def _truncate(s: str, n: int) -> str:
    return s if len(s) <= n else s[: max(1, n - 1)] + "…"


def _classic_table(t: Table, max_col: int = 16) -> str:
    rows = ([t.headers] if t.header and t.headers else []) + t.rows
    if not rows:
        return ""
    ncols = max(len(r) for r in rows)
    rows = [[_truncate(str(c), max_col) for c in r] + [""] * (ncols - len(r)) for r in rows]
    widths = [max(len(r[i]) for r in rows) for i in range(ncols)]
    lines = []
    for idx, r in enumerate(rows):
        cells = []
        for i, c in enumerate(r):
            al = (t.align[i] if t.align and i < len(t.align) else None) or "left"
            cells.append(c.rjust(widths[i]) if al == "right" else
                         c.center(widths[i]) if al == "center" else c.ljust(widths[i]))
        lines.append(" │ ".join(cells).rstrip())
        if idx == 0 and t.header and t.headers:
            lines.append("─┼─".join("─" * w for w in widths))
    body = esc("\n".join(lines))
    cap = f"<i>{esc(t.caption)}</i>\n" if t.caption else ""
    return f"{cap}<pre>{body}</pre>"


# ---------------------------------------------------------------------------
# Отправка/редактирование с автоматическим фолбэком
# ---------------------------------------------------------------------------

class Renderer:
    """Отправляет экраны как Rich Messages; при ошибке — классикой (RICH_MODE=auto)."""

    def __init__(self, api: TelegramAPI, mode: str = "auto"):
        self.api = api
        self.mode = mode
        self.rich_ok = mode != "off"

    def _use_rich(self) -> bool:
        return self.mode == "on" or (self.mode == "auto" and self.rich_ok)

    def _on_rich_error(self, e: TelegramError) -> None:
        if self.mode == "on":
            raise e
        log.warning("Rich message failed (%s) — fallback to classic HTML", e)
        desc = e.description.lower()
        if e.code == 404 or "method not found" in desc or "unknown method" in desc:
            log.warning("Bot API не поддерживает Rich Messages — переключаюсь на классический режим")
            self.rich_ok = False

    async def send(self, chat_id: int, screen: Screen, *, silent: bool = False) -> int:
        if self._use_rich():
            try:
                msg = await self.api.send_rich_message(chat_id, render_rich(screen), disable_notification=silent)
                return int(msg["message_id"])
            except TelegramError as e:
                if e.code == 403:
                    raise
                self._on_rich_error(e)
        text, markup = render_classic(screen)
        msg = await self.api.send_message(chat_id, text, markup, disable_notification=silent)
        return int(msg["message_id"])

    async def edit(self, chat_id: int, message_id: Optional[int], screen: Screen) -> int:
        """Редактирует панель; если не получилось (удалена/слишком старая) — шлёт новую.

        Возвращает id сообщения, в котором теперь панель.
        """
        if message_id:
            try:
                await self._edit(chat_id, message_id, screen)
                return message_id
            except TelegramError as e:
                if e.not_modified:
                    return message_id
                log.info("edit failed (%s), sending new panel", e)
        return await self.send(chat_id, screen)

    async def _edit(self, chat_id: int, message_id: int, screen: Screen) -> None:
        if self._use_rich():
            try:
                await self.api.edit_rich_message(chat_id, message_id, render_rich(screen))
                return
            except TelegramError as e:
                if e.not_modified or e.code == 403:
                    raise
                if "not found" in e.description.lower() and "message" in e.description.lower():
                    raise
                self._on_rich_error(e)
        text, markup = render_classic(screen)
        await self.api.edit_message_text(chat_id, message_id, text, markup)
