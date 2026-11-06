#!/usr/bin/env bash
# Copyright (c) 2026, rslpro
# SPDX-License-Identifier: AGPL-3.0-only
set -Eeuo pipefail
umask 022
[[ "${EUID:-$(id -u)}" == 0 ]] || { echo 'Run as root / Запустите от root'; exit 1; }
SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
URL=""
PROXY=1
CONNECT_ARGS=()
while (($#)); do
  case "$1" in
    --url) [[ $# -ge 2 ]] || { echo '--url requires a value'; exit 1; }; URL="$2"; shift 2 ;;
    --server|--server-hostname|--vhost|--block-line) [[ $# -ge 2 ]] || { echo "$1 requires a value"; exit 1; }; CONNECT_ARGS+=("$1" "$2"); shift 2 ;;
    --no-proxy) PROXY=0; shift ;;
    --help) echo 'install-web.sh [--url HTTPS_URL] [--server-hostname FQDN] [--server auto|nginx|apache2] [--vhost PATH] [--block-line N] [--no-proxy]'; exit 0 ;;
    *) echo "Unknown argument: $1"; exit 1 ;;
  esac
done
if [[ -z "$URL" ]]; then
  DEFAULT_URL="$(python3 -c 'import json,pathlib; p=pathlib.Path("/etc/iredmail-lifecycle-web/settings.json"); print(json.loads(p.read_text())["public_url"] if p.exists() else "")')"
  if [[ -t 0 ]]; then
    read -r -p "Публичный HTTPS URL консоли (например https://mail.example.org/lifecycle/) [$DEFAULT_URL]: " URL
    URL="${URL:-$DEFAULT_URL}"
  else
    URL="$DEFAULT_URL"
  fi
fi
[[ -n "$URL" ]] || { echo 'URL обязателен: укажите --url https://mail.example.org/lifecycle/'; exit 1; }
python3 - "$SRC/connect-web.py" "$URL" <<'PY'
import runpy, sys
runpy.run_path(sys.argv[1])["public_url"](sys.argv[2])
PY
if systemctl is-active --quiet iredmail-lifecycle-web.service || systemctl is-active --quiet iredmail-lifecycle-worker.service; then
  echo 'Stop console services after pending jobs finish before reinstalling / Дождитесь заданий и остановите службы консоли перед обновлением'; exit 1
fi
[[ -x /usr/local/sbin/iredmail-ad-lifecycle && -r /usr/local/lib/iredmail-ad-lifecycle/legacy.py ]] || {
  echo 'Install lifecycle 1.5.0 first / Сначала установите lifecycle 1.5.0'; exit 1; }
python3 -c 'import sys; assert sys.version_info >= (3, 10), "Python 3.10+ required"'
python3 -c 'import runpy; assert runpy.run_path("/usr/local/lib/iredmail-ad-lifecycle/legacy.py")["VERSION"] == "1.5.0", "This addon requires lifecycle 1.5.0"'
[[ -r /usr/local/lib/iredmail-ad-lifecycle/manage.py ]] || { echo 'Missing manage.py: reinstall lifecycle 1.5.0'; exit 1; }
for cmd in python3 systemctl install getent useradd groupadd; do command -v "$cmd" >/dev/null; done
getent group lc-web >/dev/null || groupadd --system lc-web
id lc-web >/dev/null 2>&1 || useradd --system --gid lc-web --home-dir /nonexistent --shell /usr/sbin/nologin lc-web
DEST=/opt/iredmail-lifecycle-web
install -d -m 0755 -o root -g root "$DEST" "$DEST/templates" "$DEST/static"
for file in common.py app.py worker.py configure.py config_model.py config_service.py password_service.py connect-web.py requirements.txt LICENSE NOTICE; do install -m 0644 -o root -g root "$SRC/$file" "$DEST/$file"; done
install -m 0750 -o root -g root "$SRC/connect-web.sh" "$DEST/connect-web.sh"
install -m 0750 -o root -g root "$SRC/reset-console-password.sh" "$DEST/reset-console-password.sh"
for file in "$SRC"/templates/*.html; do install -m 0644 -o root -g root "$file" "$DEST/templates/"; done
for file in "$SRC"/static/*; do install -m 0644 -o root -g root "$file" "$DEST/static/"; done
python3 -m venv "$DEST/venv"
"$DEST/venv/bin/pip" install -r "$DEST/requirements.txt"
install -d -m 0750 -o root -g lc-web /etc/iredmail-lifecycle-web /var/lib/iredmail-lifecycle-web
install -d -m 2750 -o root -g lc-web /var/lib/iredmail-lifecycle-web/jobs
install -d -m 0700 -o lc-web -g lc-web /var/lib/iredmail-lifecycle-web/inbox /var/lib/iredmail-lifecycle-web/web
for unit in "$SRC"/systemd/*.service; do install -m 0644 -o root -g root "$unit" /etc/systemd/system/; done
systemctl daemon-reload
"$DEST/venv/bin/python" "$DEST/configure.py" --url "$URL"
if ((PROXY)); then
  python3 "$DEST/connect-web.py" --url "$URL" "${CONNECT_ARGS[@]}"
else
  echo 'Установлены файлы и настройки. --no-proxy: службы не запущены, HTTPS-маршрут не добавлен.'
  echo 'После подготовки выполните /opt/iredmail-lifecycle-web/connect-web.sh'
fi
