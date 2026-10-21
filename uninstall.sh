#!/usr/bin/env bash
# BirthdayBot — ПОЛНОЕ удаление с сервера.
#
#   curl -fsSL https://raw.githubusercontent.com/no-ddos/BirthdayBot/main/uninstall.sh | sudo bash
#   curl -fsSL https://raw.githubusercontent.com/no-ddos/BirthdayBot/main/uninstall.sh | sudo bash -s -- -y
#
# Удаляет:
#   * systemd-сервис birthdaybot (остановка, отключение, unit-файл, daemon-reload);
#   * папку установки (по умолчанию /opt/BirthdayBot) — код, venv, .env, база данных;
#   * папку логов (LOG_DIR из .env, по умолчанию /var/log/birthdaybot);
#   * системного пользователя birthdaybot;
#   * Docker-контейнеры, образы и тома, если бот ставился через docker compose
#     (вместе с папкой проекта compose, где лежат .env, data/ и logs/).
#
# Опции:
#   -y, --yes        не спрашивать подтверждение
#   --keep-docker-dir  не удалять папку проекта docker compose (только контейнеры/образы/тома)
# Переменные: INSTALL_DIR, LOG_DIR, DOCKER_DIR — если ставили в нестандартные места.
set -euo pipefail

SERVICE="birthdaybot"
SERVICE_USER="birthdaybot"
UNIT_FILE="/etc/systemd/system/${SERVICE}.service"
DEFAULT_INSTALL_DIR="/opt/BirthdayBot"
DEFAULT_LOG_DIR="/var/log/birthdaybot"

c_green=$'\e[32m'; c_red=$'\e[31m'; c_yellow=$'\e[33m'; c_bold=$'\e[1m'; c_off=$'\e[0m'
info() { echo "${c_green}==>${c_off} $*"; }
warn() { echo "${c_yellow}[!]${c_off} $*"; }
die()  { echo "${c_red}[x]${c_off} $*" >&2; exit 1; }

# Безопасное удаление: не даём снести системные каталоги
safe_rm() {
  local p="$1"
  [[ -n "$p" ]] || return 0
  p="$(readlink -f -- "$p" 2>/dev/null || echo "$p")"
  case "$p" in
    /|/bin|/boot|/dev|/etc|/home|/lib|/lib64|/opt|/proc|/root|/run|/sbin|/srv|/sys|/tmp|/usr|/var|/var/log|/var/lib)
      warn "Пропускаю опасный путь: $p"; return 0 ;;
  esac
  if [[ -e "$p" ]]; then
    rm -rf -- "$p"
    info "Удалено: $p"
  fi
}

# значение переменной из .env (без source — файл может содержать что угодно)
env_value() {
  local file="$1" key="$2"
  [[ -f "$file" ]] || return 0
  grep -E "^[[:space:]]*${key}=" "$file" | tail -n1 | cut -d= -f2- | sed -e 's/^["'\'']//' -e 's/["'\'']$//' || true
}

main() {
  local assume_yes=0 keep_docker_dir=0
  for arg in "$@"; do
    case "$arg" in
      -y|--yes) assume_yes=1 ;;
      --keep-docker-dir) keep_docker_dir=1 ;;
      -h|--help) sed -n '2,22p' "${BASH_SOURCE[0]:-$0}" 2>/dev/null || true; exit 0 ;;
      *) die "Неизвестный аргумент: $arg" ;;
    esac
  done

  [[ $EUID -eq 0 ]] || die "Нужны права root: запустите через sudo"
  cd /   # на случай, если запустили изнутри удаляемой папки

  # --- что удалять -------------------------------------------------------
  local install_dir="${INSTALL_DIR:-}"
  if [[ -z "$install_dir" && -f "$UNIT_FILE" ]]; then
    install_dir="$(sed -n 's/^WorkingDirectory=//p' "$UNIT_FILE" | head -n1)"
  fi
  install_dir="${install_dir:-$DEFAULT_INSTALL_DIR}"

  local log_dir="${LOG_DIR:-}"
  if [[ -z "$log_dir" ]]; then
    log_dir="$(env_value "$install_dir/.env" LOG_DIR)"
  fi
  if [[ -n "$log_dir" && "$log_dir" != /* ]]; then
    log_dir="$install_dir/$log_dir"     # относительный путь — внутри папки установки
  fi
  local log_dirs=("$DEFAULT_LOG_DIR")
  [[ -n "$log_dir" && "$log_dir" != "$DEFAULT_LOG_DIR" ]] && log_dirs+=("$log_dir")

  # docker compose: ищем контейнеры по меткам compose
  local have_docker=0 containers="" docker_dirs=() projects=()
  if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
    have_docker=1
    containers="$(docker ps -a --filter "label=com.docker.compose.service=birthdaybot" --format '{{.ID}}' || true)"
    local named
    named="$(docker ps -a --filter "name=^/birthdaybot$" --format '{{.ID}}' || true)"
    containers="$(printf '%s\n%s\n' "$containers" "$named" | sort -u | sed '/^$/d')"
    local cid wd proj
    for cid in $containers; do
      wd="$(docker inspect -f '{{ index .Config.Labels "com.docker.compose.project.working_dir" }}' "$cid" 2>/dev/null || true)"
      proj="$(docker inspect -f '{{ index .Config.Labels "com.docker.compose.project" }}' "$cid" 2>/dev/null || true)"
      [[ -n "$wd" && "$wd" != "<no value>" ]] && docker_dirs+=("$wd")
      [[ -n "$proj" && "$proj" != "<no value>" ]] && projects+=("$proj")
    done
  fi
  [[ -n "${DOCKER_DIR:-}" ]] && docker_dirs+=("$DOCKER_DIR")
  # уникальные значения
  if ((${#docker_dirs[@]})); then mapfile -t docker_dirs < <(printf '%s\n' "${docker_dirs[@]}" | sort -u); fi
  if ((${#projects[@]})); then mapfile -t projects < <(printf '%s\n' "${projects[@]}" | sort -u); fi

  # --- подтверждение -------------------------------------------------------
  echo "${c_bold}Будет ПОЛНОСТЬЮ удалён BirthdayBot:${c_off}"
  echo "  • systemd-сервис:     $SERVICE ($UNIT_FILE)"
  echo "  • папка установки:    $install_dir (код, venv, .env, база данных)"
  echo "  • логи:               ${log_dirs[*]}"
  echo "  • пользователь:       $SERVICE_USER"
  if [[ $have_docker -eq 1 ]]; then
    echo "  • Docker-контейнеры:  ${containers:-не найдены}" | tr '\n' ' '; echo
    ((${#projects[@]})) && echo "  • compose-проекты:    ${projects[*]} (образы и тома)"
    if ((${#docker_dirs[@]})) && [[ $keep_docker_dir -eq 0 ]]; then
      echo "  • папки compose:      ${docker_dirs[*]} (.env, data/, logs/)"
    fi
  fi
  echo "${c_red}Данные (база дней рождения) восстановить будет нельзя.${c_off}"

  if [[ $assume_yes -eq 0 ]]; then
    local reply=""
    if (exec </dev/tty) 2>/dev/null; then
      read -r -p "Продолжить? Введите 'yes': " reply </dev/tty || true
    else
      die "Нет терминала для подтверждения. Запустите с -y: curl ... | sudo bash -s -- -y"
    fi
    [[ "$reply" == "yes" || "$reply" == "y" || "$reply" == "да" ]] || { info "Отменено."; exit 0; }
  fi

  # --- systemd -------------------------------------------------------------
  if command -v systemctl >/dev/null 2>&1; then
    if systemctl list-unit-files "${SERVICE}.service" >/dev/null 2>&1 || [[ -f "$UNIT_FILE" ]]; then
      info "Останавливаю сервис $SERVICE…"
      systemctl stop "$SERVICE" 2>/dev/null || true
      systemctl disable "$SERVICE" 2>/dev/null || true
    fi
    if [[ -f "$UNIT_FILE" ]]; then
      rm -f "$UNIT_FILE"
      info "Удалён unit-файл $UNIT_FILE"
    fi
    systemctl daemon-reload 2>/dev/null || true
    systemctl reset-failed "$SERVICE" 2>/dev/null || true
  fi

  # --- docker --------------------------------------------------------------
  if [[ $have_docker -eq 1 ]]; then
    local d
    for d in "${docker_dirs[@]}"; do
      if [[ -f "$d/docker-compose.yml" ]]; then
        info "docker compose down в $d…"
        (cd "$d" && docker compose down --rmi all --volumes --remove-orphans 2>/dev/null) \
          || (cd "$d" && docker-compose down --rmi all --volumes --remove-orphans 2>/dev/null) || true
      fi
    done
    if [[ -n "$containers" ]]; then
      # shellcheck disable=SC2086
      docker rm -f $containers >/dev/null 2>&1 || true
      info "Удалены контейнеры"
    fi
    local p ids
    for p in "${projects[@]}"; do
      ids="$(docker images -q --filter "label=com.docker.compose.project=$p" || true)"
      ids+=" $(docker images -q "${p}-birthdaybot" 2>/dev/null || true)"
      # shellcheck disable=SC2086
      [[ -n "${ids// /}" ]] && docker rmi -f $ids >/dev/null 2>&1 || true
      ids="$(docker volume ls -q --filter "label=com.docker.compose.project=$p" || true)"
      # shellcheck disable=SC2086
      [[ -n "$ids" ]] && docker volume rm -f $ids >/dev/null 2>&1 || true
    done
    # образы, собранные из этого репозитория под стандартными именами
    ids="$(docker images --format '{{.Repository}}:{{.Tag}}' | grep -Ei '(^|[-_/])birthdaybot([-_]birthdaybot)?:' || true)"
    # shellcheck disable=SC2086
    [[ -n "$ids" ]] && docker rmi -f $ids >/dev/null 2>&1 && info "Удалены образы: $(echo $ids)" || true
    if [[ $keep_docker_dir -eq 0 ]]; then
      for d in "${docker_dirs[@]}"; do
        # удаляем только то, что действительно похоже на папку BirthdayBot
        if [[ -d "$d/birthdaybot" && -f "$d/docker-compose.yml" ]]; then
          safe_rm "$d"
        else
          warn "Не похоже на папку BirthdayBot, не трогаю: $d"
        fi
      done
    fi
  fi

  # --- файлы ---------------------------------------------------------------
  if [[ -d "$install_dir" && ! -d "$install_dir/birthdaybot" && -n "$(ls -A "$install_dir" 2>/dev/null)" ]]; then
    warn "$install_dir не похож на папку BirthdayBot — не удаляю (задайте INSTALL_DIR)"
  else
    safe_rm "$install_dir"
  fi
  local l
  for l in "${log_dirs[@]}"; do safe_rm "$l"; done

  # --- пользователь ----------------------------------------------------------
  if id "$SERVICE_USER" >/dev/null 2>&1; then
    userdel "$SERVICE_USER" 2>/dev/null && info "Удалён пользователь $SERVICE_USER" || warn "Не удалось удалить пользователя $SERVICE_USER"
    getent group "$SERVICE_USER" >/dev/null 2>&1 && groupdel "$SERVICE_USER" 2>/dev/null || true
  fi

  info "${c_bold}BirthdayBot полностью удалён.${c_off}"
}

# весь код внутри main(): bash дочитает скрипт целиком до того, как удалится папка с ним
main "$@"
exit 0
