#!/usr/bin/env bash
# Copyright (c) 2026, rslpro
# SPDX-License-Identifier: AGPL-3.0-only
# Combined upgrade. No running job is interrupted; mail services are untouched.
set -Eeuo pipefail
umask 077
[[ "${EUID:-$(id -u)}" == 0 ]] || { echo 'Run as root / Запустите от root'; exit 1; }
SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WEB_ACTIVE=0
WORKER_ACTIVE=0
systemctl is-active --quiet iredmail-lifecycle-web.service && WEB_ACTIVE=1 || true
systemctl is-active --quiet iredmail-lifecycle-worker.service && WORKER_ACTIVE=1 || true
BACKUP=""
MUTATING=0
finish() {
  local code=$?
  trap - EXIT
  if ((code != 0)); then
    if ((MUTATING == 1)) && [[ -f "$BACKUP/before.tar" ]]; then
      # The new worker is held in maintenance and cannot have started mail jobs.
      systemctl stop iredmail-lifecycle-web.service iredmail-lifecycle-worker.service || true
      echo 'Ошибка обновления; восстанавливаю исходные файлы из резервной копии.'
      tar -xpf "$BACKUP/before.tar" -C / || echo "ERROR: rollback failed; backup=$BACKUP"
      systemctl daemon-reload || true
    fi
    ((WORKER_ACTIVE == 0)) || systemctl start iredmail-lifecycle-worker.service || true
    ((WEB_ACTIVE == 0)) || systemctl start iredmail-lifecycle-web.service || true
    echo 'Обновление не завершено. Почтовые службы не останавливались.'
  fi
  exit "$code"
}
trap finish EXIT
((WEB_ACTIVE == 0)) || systemctl stop iredmail-lifecycle-web.service
exec 8>/run/iredmail-lifecycle-settings.lock
flock -n 8 || { echo 'Настройки или почта сейчас обрабатываются. Дождитесь завершения.'; exit 1; }
[[ ! -e /etc/iredmail-lifecycle-web/config-history/pending.json ]] || { echo 'Сначала требуется восстановление настроек службой worker; обновление остановлено.'; exit 1; }
# With frontend stopped no new HTTP jobs can enter. Worker writes running state
# before removing inbox entry, so checking inbox then running covers claim races.
python3 - <<'PY'
import json
from pathlib import Path
p=Path('/etc/iredmail-lifecycle-web/settings.json')
if p.exists():
    c=json.loads(p.read_text())
    if list(Path(c['inbox']).glob('*.json')):
        raise SystemExit('Очередь не пуста: дождитесь заданий и повторите upgrade. Worker не остановлен.')
    for f in Path(c['jobs']).glob('*/result.json'):
        if json.loads(f.read_text()).get('status')=='running':
            raise SystemExit('Есть выполняющееся задание: дождитесь завершения. Worker не остановлен.')
PY
LOCK_FILE="$(bash -c 'source /etc/iredmail-ad-lifecycle.conf; printf "%s" "${LOCK_FILE:-/run/iredmail-ad-lifecycle.lock}"')"
exec 9>"$LOCK_FILE"
flock -n 9 || { echo 'Основной lifecycle выполняется. Дождитесь завершения и повторите.'; exit 1; }
((WORKER_ACTIVE == 0)) || systemctl stop iredmail-lifecycle-worker.service
flock -u 8
BACKUP="/var/backups/iredmail-lifecycle-upgrade/$(date +%Y%m%d-%H%M%S)-$$"
install -d -m 0700 -o root -g root "$BACKUP"
FILES=()
for p in usr/local/sbin/iredmail-ad-lifecycle usr/local/lib/iredmail-ad-lifecycle \
  etc/iredmail-ad-lifecycle.conf etc/iredmail-ad-lifecycle.conf.dist \
  etc/iredmail-lifecycle-web etc/systemd/system/iredmail-ad-lifecycle.service \
  etc/systemd/system/iredmail-ad-lifecycle.timer etc/systemd/system/iredmail-lifecycle-web.service \
  etc/systemd/system/iredmail-lifecycle-worker.service opt/iredmail-lifecycle-web; do
  [[ ! -e "/$p" ]] || FILES+=("$p")
done
tar --exclude=opt/iredmail-lifecycle-web/venv --exclude='*/__pycache__' -cpf "$BACKUP/before.tar" -C / "${FILES[@]}"
echo "Резервная копия исходных файлов/конфигов: $BACKUP/before.tar"
MUTATING=1
MAINTENANCE_SCRIPT="$BACKUP/set-maintenance.py"
cat > "$MAINTENANCE_SCRIPT" <<'PY'
import json,os,sys,tempfile
from pathlib import Path
p=Path('/etc/iredmail-lifecycle-web/settings.json')
if not p.exists():
    raise SystemExit('Existing console settings required; use individual installers for a fresh install')
info=p.stat();c=json.loads(p.read_text());c['maintenance']=sys.argv[1]=='yes'
with tempfile.NamedTemporaryFile(dir=p.parent,delete=False,mode='w',encoding='utf-8') as f:
    tmp=Path(f.name);json.dump(c,f,ensure_ascii=False);f.flush();os.fsync(f.fileno())
os.chmod(tmp,info.st_mode & 0o777);os.chown(tmp,info.st_uid,info.st_gid);os.replace(tmp,p)
PY
python3 "$MAINTENANCE_SCRIPT" yes
bash "$SRC/core/install.sh"
bash "$SRC/console/install-web.sh" --no-proxy "$@"
# New code/settings are on disk. Drop the core lock before starting the worker.
flock -u 9
URL="$(python3 -c 'import json; print(json.load(open("/etc/iredmail-lifecycle-web/settings.json"))["public_url"])')"
python3 /opt/iredmail-lifecycle-web/connect-web.py --url "$URL" "$@"
python3 "$MAINTENANCE_SCRIPT" no
systemctl restart iredmail-lifecycle-worker.service iredmail-lifecycle-web.service
echo 'Обновлены lifecycle 1.5.0 и веб-консоль 0.5.0. Архивы, состояния и исключения сохранены.'
