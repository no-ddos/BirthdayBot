# 🎂 BirthdayBot

Telegram-бот для удобного отслеживания дней рождения в группе потока.
Интерфейс построен на **Rich Messages** (Telegram Bot API 10.1+ / 10.3): заголовки, таблицы и кнопки
живут прямо внутри одного сообщения, а бот **редактирует** это сообщение, а не засыпает чат новыми.

## Возможности

- ➕ **Добавление человека** пошаговым мастером: имя → дата рождения → желаемый подарок → примерная цена →
  проверка карточки → кнопка **«💾 Сохранить»** (запись в SQLite). Подарок и цену можно пропустить.
- ✏️ **Карточка человека**: изменить имя, дату, подарок, цену; удаление с подтверждением.
- 📋 **Список дней рождения** таблицей с сортировкой: ⏳ ближайшие · 🔤 по имени · 📅 по календарю · 💰 по цене;
  постраничный вывод и сумма подарков.
- ⏰ **Напоминания** всем админам из `.env` — по умолчанию **за 7 дней и за 1 день** до ДР
  (имя, дата, сколько исполнится, подарок, цена).
- ⚙️ **Настройка алертов** прямо в боте: добавить / изменить / удалить смещения в днях, часах, минутах
  (`7d`, `3d`, `1d 12h`, `3h`, `30m`, `0` — в сам день; можно `7д`, `3ч`, `30м`), время отправки (по умолчанию 09:00).
- 🔐 **Доступ только админам** из `.env`; остальных бот молча игнорирует. Работает только в личных сообщениях.
- 🧪 **Тестовый алерт** — только для `DEV_ADMIN` (кнопка в меню или `/test_alert`).

### Роли

| Переменная  | Права                                   |
|-------------|-----------------------------------------|
| `DEV_ADMIN` | всё + тестовые алерты                   |
| `ADMIN`     | всё, кроме тестовых алертов             |

Обе переменные — списки Telegram ID через запятую. Свой ID можно узнать у [@userinfobot](https://t.me/userinfobot).

## Установка на сервер (Debian / Ubuntu) — одной командой

```bash
curl -fsSL https://raw.githubusercontent.com/no-ddos/BirthdayBot/main/install.sh | sudo bash
```

Скрипт:
1. поставит `python3`, `python3-venv`, `git`;
2. склонирует репозиторий в `/opt/BirthdayBot`;
3. создаст виртуальное окружение и установит зависимости;
4. если `.env` ещё нет — спросит `BOT_TOKEN`, `DEV_ADMIN`, `ADMIN`, часовой пояс;
5. создаст и запустит systemd-сервис `birthdaybot` (автозапуск после перезагрузки).

Без вопросов (например, из CI):

```bash
curl -fsSL https://raw.githubusercontent.com/no-ddos/BirthdayBot/main/install.sh \
  | sudo BOT_TOKEN=123:abc DEV_ADMIN=111111111 ADMIN=222222222,333333333 bash
```

Управление:

```bash
sudo bash /opt/BirthdayBot/install.sh update      # обновить до последней версии и перезапустить
sudo bash /opt/BirthdayBot/install.sh config      # заново ввести токен/админов
sudo bash /opt/BirthdayBot/install.sh uninstall   # удалить сервис (--purge — вместе с данными)
sudo bash /opt/BirthdayBot/install.sh status      # статус
sudo bash /opt/BirthdayBot/install.sh logs        # логи (journalctl -u birthdaybot -f)
```

Другая папка: `INSTALL_DIR=/srv/bbot`. Установка из уже скачанной папки: `sudo ./install.sh --here`.

## Docker

```bash
git clone https://github.com/no-ddos/BirthdayBot.git && cd BirthdayBot
cp .env.example .env && nano .env
docker compose up -d --build
```

База хранится в `./data`.

## Ручной запуск (для разработки)

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env   # заполнить BOT_TOKEN, DEV_ADMIN, ADMIN
python -m birthdaybot
pytest                 # тесты
```

## Настройки `.env`

| Переменная   | По умолчанию        | Описание |
|--------------|---------------------|----------|
| `BOT_TOKEN`  | —                   | токен от @BotFather |
| `DEV_ADMIN`  | —                   | ID dev-админов через запятую |
| `ADMIN`      | —                   | ID админов через запятую |
| `TZ`         | `Europe/Moscow`     | часовой пояс напоминаний |
| `DB_PATH`    | `data/birthdays.db` | путь к SQLite |
| `REMIND_AT`  | `09:00`             | время напоминаний при первом запуске (дальше меняется в боте) |
| `RICH_MODE`  | `auto`              | `auto` — rich с откатом на обычные сообщения, `on` — только rich, `off` — только обычные |
| `LOG_LEVEL`  | `INFO`              | уровень логов |

## Как работают напоминания

- Смещения, кратные суткам (`7d`, `1d`, `0`), срабатывают **в заданное время** (по умолчанию 09:00) за N дней до ДР.
- Смещения с часами/минутами (`3h`, `30m`, `1d 12h`) отсчитываются **от 00:00 дня рождения**: `3h` — в 21:00 накануне.
- Каждое напоминание уходит **один раз** (учёт по человеку / смещению / году ДР в БД) — рестарт не приводит к дублям.
- Если бот был выключен, он **догонит** пропущенные напоминания, пока день рождения не закончился;
  если «созрело» сразу несколько (например, 7d и 1d), придёт одно — самое актуальное.
- 29 февраля в невисокосный год празднуется 28 февраля. Переход через Новый год учитывается.
- Напоминание получают **все** админы из `DEV_ADMIN` и `ADMIN` (каждый должен хоть раз нажать /start у бота).

## Rich Messages

Экраны отправляются методом [`sendRichMessage`](https://core.telegram.org/bots/api/methods/sendrichmessage.md)
с `rich_message: {"html": "..."}` (InputRichMessage), а обновляются через
[`editMessageText`](https://core.telegram.org/bots/api/methods/editmessagetext.md) с полем `rich_message`.
Кнопки — теги `<tg-button type="callback_data" data="...">` внутри `<tg-button-row>` прямо в разметке;
нажатия приходят обычными `callback_query`. Готовые библиотеки пока не поддерживают Rich Messages,
поэтому используется тонкий клиент на `httpx` (`birthdaybot/telegram.py`).

Если Bot API вернёт ошибку на rich-вызов (например, старый self-hosted Bot API сервер), в режиме `RICH_MODE=auto`
бот автоматически покажет тот же экран обычным HTML-сообщением с inline-клавиатурой.

## Структура

```
birthdaybot/
  __main__.py   точка входа (python -m birthdaybot)
  bot.py        обработка команд, кнопок и ввода
  screens.py    экраны (меню, список, карточка, мастер, алерты, напоминание)
  ui.py         модель экрана и рендер: rich HTML / классический HTML + клавиатура
  telegram.py   клиент Bot API (sendRichMessage, editMessageText и т.д.)
  scheduler.py  фоновая отправка напоминаний
  dates.py      даты, смещения, расчёт напоминаний
  db.py         SQLite
  config.py     загрузка .env
tests/          pytest
install.sh      установка/обновление/удаление на сервере
```

## Лицензия

MIT
