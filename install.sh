#!/usr/bin/env bash
# BirthdayBot — установка на сервер (Debian/Ubuntu) как systemd-сервис.
#
#   curl -fsSL https://raw.githubusercontent.com/no-ddos/BirthdayBot/main/install.sh | sudo bash
#
# Команды:
#   install.sh [install]      установить (или переустановить) и запустить
#   install.sh update         git pull + зависимости + перезапуск
#   install.sh uninstall      удалить сервис (данные остаются; --purge — удалить всё)
#   install.sh status|logs|restart
#   install.sh config         заново ввести BOT_TOKEN / админов
#
# Опции/переменные окружения:
#   --here                    ставить из текущей папки (без git clone)
#   INSTALL_DIR=/opt/BirthdayBot  REPO_URL=...  BRANCH=main
#   BOT_TOKEN=... DEV_ADMIN=... ADMIN=... TZ=...   — для неинтерактивной установки
set -euo pipefail

REPO_URL="${REPO_URL:-https://github.com/no-ddos/BirthdayBot.git}"
BRANCH="${BRANCH:-main}"
INSTALL_DIR="${INSTALL_DIR:-/opt/BirthdayBot}"
SERVICE="birthdaybot"
SERVICE_USER="birthdaybot"
UNIT_FILE="/etc/systemd/system/${SERVICE}.service"

c_green=$'\e[32m'; c_red=$'\e[31m'; c_yellow=$'\e[33m'; c_bold=$'\e[1m'; c_off=$'\e[0m'
info() { echo "${c_green}==>${c_off} $*"; }
warn() { echo "${c_yellow}[!]${c_off} $*"; }
die()  { echo "${c_red}[x]${c_off} $*" >&2; exit 1; }

ACTION="install"
USE_HERE=0
PURGE=0
for arg in "$@"; do
  case "$arg" in
    install|update|uninstall|status|logs|restart|config) ACTION="$arg" ;;
    --here) USE_HERE=1 ;;
    --purge) PURGE=1 ;;
    -h|--help) sed -n '2,18p' "$0" 2>/dev/null || true; exit 0 ;;
    *) die "Неизвестный аргумент: $arg" ;;
  esac
done

if [[ $USE_HERE -eq 1 ]]; then
  INSTALL_DIR="$(pwd)"
  [[ -f "$INSTALL_DIR/birthdaybot/__main__.py" ]] || die "--here: в $INSTALL_DIR нет папки birthdaybot/"
fi

need_root() { [[ $EUID -eq 0 ]] || die "Нужны права root: запустите через sudo"; }

# чтение с терминала даже при `curl ... | bash`
if (exec </dev/tty) 2>/dev/null; then HAS_TTY=1; else HAS_TTY=0; fi
ask() {
  local prompt="$1" default="${2:-}" reply=""
  if [[ $HAS_TTY -eq 1 ]]; then
    if [[ -n "$default" ]]; then
      read -r -p "$prompt [$default]: " reply </dev/tty || true
    else
      read -r -p "$prompt: " reply </dev/tty || true
    fi
  fi
  echo "${reply:-$default}"
}

install_packages() {
  if command -v apt-get >/dev/null 2>&1; then
    info "Устанавливаю системные пакеты (python3, venv, git)…"
    export DEBIAN_FRONTEND=noninteractive
    apt-get update -qq
    apt-get install -y -qq python3 python3-venv python3-pip git ca-certificates curl >/dev/null
  else
    warn "apt-get не найден — убедитесь, что установлены python3 (>=3.9), python3-venv и git"
  fi
  python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' \
    || die "Нужен Python 3.9+, найден $(python3 --version 2>&1)"
}

fetch_code() {
  if [[ $USE_HERE -eq 1 ]]; then
    info "Использую код из $INSTALL_DIR"
  elif [[ -d "$INSTALL_DIR/.git" ]]; then
    info "Обновляю код в $INSTALL_DIR…"
    git -C "$INSTALL_DIR" fetch --quiet origin "$BRANCH"
    git -C "$INSTALL_DIR" checkout --quiet "$BRANCH"
    git -C "$INSTALL_DIR" reset --quiet --hard "origin/$BRANCH"
  elif [[ -e "$INSTALL_DIR" && -n "$(ls -A "$INSTALL_DIR" 2>/dev/null)" ]]; then
    die "$INSTALL_DIR уже существует и это не git-репозиторий. Удалите его или задайте INSTALL_DIR."
  else
    info "Клонирую $REPO_URL → $INSTALL_DIR"
    git clone --quiet --branch "$BRANCH" "$REPO_URL" "$INSTALL_DIR"
  fi
}

ensure_user() {
  if ! id "$SERVICE_USER" >/dev/null 2>&1; then
    info "Создаю системного пользователя $SERVICE_USER"
    useradd --system --no-create-home --home-dir "$INSTALL_DIR" --shell /usr/sbin/nologin "$SERVICE_USER"
  fi
}

setup_venv() {
  info "Готовлю виртуальное окружение…"
  [[ -x "$INSTALL_DIR/.venv/bin/python" ]] || python3 -m venv "$INSTALL_DIR/.venv"
  "$INSTALL_DIR/.venv/bin/pip" install --quiet --upgrade pip
  "$INSTALL_DIR/.venv/bin/pip" install --quiet -r "$INSTALL_DIR/requirements.txt"
}

valid_ids() { [[ -z "$1" || "$1" =~ ^[0-9]+([[:space:]]*,[[:space:]]*[0-9]+)*$ ]]; }

write_env() {
  local env_file="$INSTALL_DIR/.env"
  local token="${BOT_TOKEN:-}" dev="${DEV_ADMIN:-}" adm="${ADMIN:-}" tz="${TZ:-Europe/Moscow}"

  echo
  echo "${c_bold}Настройка .env${c_off} (Telegram ID можно узнать у @userinfobot)"
  while [[ -z "$token" ]]; do
    token="$(ask 'BOT_TOKEN (от @BotFather)')"
    [[ -n "$token" ]] || { [[ $HAS_TTY -eq 1 ]] || die "BOT_TOKEN не задан (нет терминала — передайте BOT_TOKEN=... в окружении)"; warn "Токен обязателен"; }
  done
  while :; do
    dev="$(ask 'DEV_ADMIN — ID через запятую (всё + тестовые алерты)' "$dev")"
    valid_ids "$dev" && break; warn "Только числа через запятую"; dev=""
  done
  while :; do
    adm="$(ask 'ADMIN — ID через запятую (всё, кроме тестовых алертов)' "$adm")"
    valid_ids "$adm" && break; warn "Только числа через запятую"; adm=""
  done
  [[ -n "$dev$adm" ]] || die "Нужен хотя бы один админ (DEV_ADMIN или ADMIN)"
  tz="$(ask 'Часовой пояс' "$tz")"

  umask 077
  cat > "$env_file" <<ENV
BOT_TOKEN=$token
DEV_ADMIN=${dev// /}
ADMIN=${adm// /}
TZ=$tz
DB_PATH=data/birthdays.db
REMIND_AT=09:00
RICH_MODE=auto
LOG_LEVEL=INFO
ENV
  chown "$SERVICE_USER":"$SERVICE_USER" "$env_file"
  chmod 600 "$env_file"
  info ".env сохранён ($env_file)"
}

write_unit() {
  info "Создаю systemd-сервис $SERVICE"
  cat > "$UNIT_FILE" <<UNIT
[Unit]
Description=BirthdayBot - Telegram-бот дней рождения
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$SERVICE_USER
Group=$SERVICE_USER
WorkingDirectory=$INSTALL_DIR
Environment=BIRTHDAYBOT_ENV=$INSTALL_DIR/.env
ExecStart=$INSTALL_DIR/.venv/bin/python -m birthdaybot
Restart=always
RestartSec=5
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=full
ProtectHome=true

[Install]
WantedBy=multi-user.target
UNIT
  systemctl daemon-reload
}

fix_perms() {
  mkdir -p "$INSTALL_DIR/data"
  chown -R "$SERVICE_USER":"$SERVICE_USER" "$INSTALL_DIR/data"
  [[ -f "$INSTALL_DIR/.env" ]] && chown "$SERVICE_USER":"$SERVICE_USER" "$INSTALL_DIR/.env" && chmod 600 "$INSTALL_DIR/.env"
  chmod o+rx "$INSTALL_DIR"
  return 0
}

do_install() {
  need_root
  command -v systemctl >/dev/null 2>&1 || die "systemd не найден. Используйте Docker (см. README)."
  install_packages
  fetch_code
  ensure_user
  setup_venv
  if [[ -f "$INSTALL_DIR/.env" ]]; then
    info ".env уже есть — оставляю как есть (перенастроить: install.sh config)"
  else
    write_env
  fi
  fix_perms
  write_unit
  systemctl enable --quiet "$SERVICE"
  systemctl restart "$SERVICE"
  sleep 3
  if systemctl is-active --quiet "$SERVICE"; then
    info "${c_bold}BirthdayBot запущен!${c_off} Напишите боту /start в Telegram."
  else
    warn "Сервис не запустился. Логи: journalctl -u $SERVICE -n 50"
  fi
  echo
  echo "  Статус:     systemctl status $SERVICE"
  echo "  Логи:       journalctl -u $SERVICE -f"
  echo "  Обновить:   sudo bash $INSTALL_DIR/install.sh update"
  echo "  Удалить:    sudo bash $INSTALL_DIR/install.sh uninstall"
}

do_update() {
  need_root
  [[ -d "$INSTALL_DIR" ]] || die "$INSTALL_DIR не найден — сначала установите бота"
  fetch_code
  ensure_user
  setup_venv
  fix_perms
  write_unit
  systemctl restart "$SERVICE"
  info "Обновлено и перезапущено ($(git -C "$INSTALL_DIR" rev-parse --short HEAD 2>/dev/null || echo local))"
}

do_uninstall() {
  need_root
  info "Останавливаю и удаляю сервис…"
  systemctl disable --now "$SERVICE" 2>/dev/null || true
  rm -f "$UNIT_FILE"
  systemctl daemon-reload
  if [[ $PURGE -eq 0 ]]; then
    local ans
    ans="$(ask "Удалить также $INSTALL_DIR вместе с базой и .env? (y/N)" "N")"
    [[ "$ans" =~ ^[YyДд] ]] && PURGE=1
  fi
  if [[ $PURGE -eq 1 ]]; then
    [[ "$INSTALL_DIR" == "/" || -z "$INSTALL_DIR" ]] && die "Подозрительный INSTALL_DIR"
    rm -rf "$INSTALL_DIR"
    info "Удалён $INSTALL_DIR"
  else
    info "Код и данные оставлены в $INSTALL_DIR"
  fi
  id "$SERVICE_USER" >/dev/null 2>&1 && userdel "$SERVICE_USER" 2>/dev/null || true
  info "BirthdayBot удалён"
}

case "$ACTION" in
  install)   do_install ;;
  update)    do_update ;;
  uninstall) do_uninstall ;;
  status)    systemctl status "$SERVICE" --no-pager ;;
  logs)      journalctl -u "$SERVICE" -n 100 -f ;;
  restart)   need_root; systemctl restart "$SERVICE"; info "Перезапущено" ;;
  config)    need_root; ensure_user; write_env; fix_perms; systemctl restart "$SERVICE" 2>/dev/null || true ;;
esac
