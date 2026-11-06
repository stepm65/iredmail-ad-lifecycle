#!/usr/bin/env bash
# Copyright (c) 2026, rslpro
# SPDX-License-Identifier: AGPL-3.0-only
set -Eeuo pipefail

SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

[[ "${EUID:-$(id -u)}" -eq 0 ]] || {
    echo "Run as root / Запустите от root" >&2
    exit 1
}

echo "Installing iRedMail AD lifecycle manager..."
echo "Установка менеджера жизненного цикла iRedMail AD..."

for c in ldapsearch ldapwhoami rsync flock findmnt python3 realpath; do
    if ! command -v "$c" >/dev/null 2>&1; then
        echo "ERROR: missing command: $c"
        echo "Debian/Ubuntu: apt install ldap-utils rsync util-linux python3 coreutils"
        exit 1
    fi
done

install -m 0750 "$SRC_DIR/iredmail-ad-lifecycle.sh" /usr/local/sbin/iredmail-ad-lifecycle
install -d -m 0750 /usr/local/lib/iredmail-ad-lifecycle
install -m 0640 "$SRC_DIR/legacy.py" /usr/local/lib/iredmail-ad-lifecycle/legacy.py
install -m 0640 "$SRC_DIR/manage.py" /usr/local/lib/iredmail-ad-lifecycle/manage.py
install -m 0640 "$SRC_DIR/delete_mailbox.py" /usr/local/lib/iredmail-ad-lifecycle/delete_mailbox.py
install -d -m 0755 /usr/local/share/doc/iredmail-ad-lifecycle
install -m 0644 "$SRC_DIR/LICENSE" "$SRC_DIR/NOTICE" /usr/local/share/doc/iredmail-ad-lifecycle/

if [[ ! -e /etc/iredmail-ad-lifecycle.conf ]]; then
    install -m 0600 "$SRC_DIR/iredmail-ad-lifecycle.conf" /etc/iredmail-ad-lifecycle.conf
    echo "Created /etc/iredmail-ad-lifecycle.conf"
else
    install -m 0600 "$SRC_DIR/iredmail-ad-lifecycle.conf" /etc/iredmail-ad-lifecycle.conf.dist
    echo "Existing config preserved / Существующий конфиг сохранён"
    echo "New template / Новый шаблон: /etc/iredmail-ad-lifecycle.conf.dist"
fi

install -m 0644 "$SRC_DIR/systemd/iredmail-ad-lifecycle.service" /etc/systemd/system/iredmail-ad-lifecycle.service
install -m 0644 "$SRC_DIR/systemd/iredmail-ad-lifecycle.timer" /etc/systemd/system/iredmail-ad-lifecycle.timer

mkdir -p /var/lib/iredmail-ad-lifecycle /var/log/iredmail-ad-lifecycle-reports
chmod 0700 /var/lib/iredmail-ad-lifecycle
chmod 0750 /var/log/iredmail-ad-lifecycle-reports

systemctl daemon-reload

cat <<'EOF'

Installed. Timer is NOT enabled automatically.
Установлено. Таймер автоматически НЕ включён.

Next steps / Следующие шаги:
  1. Edit /etc/iredmail-ad-lifecycle.conf
     Отредактируйте /etc/iredmail-ad-lifecycle.conf

  2. Create /root/.ldap-vmail.pass and chmod 600
     Создайте /root/.ldap-vmail.pass и chmod 600

  3. Run safe report:
     Запустите безопасный отчёт:
       /usr/local/sbin/iredmail-ad-lifecycle --report

  4. Review log and CSV report.
     Проверьте журнал и CSV-отчёт.

  5. Only then set DRY_RUN=0.
     Только после проверки установите DRY_RUN=0.

  6. Enable timer when ready:
     Включите таймер после проверки:
       systemctl enable --now iredmail-ad-lifecycle.timer
EOF
