#!/usr/bin/env bash
# Copyright (c) 2026, rslpro
# SPDX-License-Identifier: AGPL-3.0-only
set -Eeuo pipefail

[[ "${EUID:-$(id -u)}" -eq 0 ]] || {
    echo "Run as root / Запустите от root" >&2
    exit 1
}

systemctl disable --now iredmail-ad-lifecycle.timer 2>/dev/null || true
rm -f /etc/systemd/system/iredmail-ad-lifecycle.timer
rm -f /etc/systemd/system/iredmail-ad-lifecycle.service
systemctl daemon-reload
rm -f /usr/local/sbin/iredmail-ad-lifecycle
rm -f /usr/local/lib/iredmail-ad-lifecycle/legacy.py
rmdir /usr/local/lib/iredmail-ad-lifecycle 2>/dev/null || true

cat <<'EOF'

Program files removed.
Файлы программы удалены.

For safety, these are NOT removed automatically:
В целях безопасности НЕ удаляются автоматически:

  /etc/iredmail-ad-lifecycle.conf
  /root/.ldap-vmail.pass
  /var/lib/iredmail-ad-lifecycle/
  /var/log/iredmail-ad-lifecycle.log
  /var/log/iredmail-ad-lifecycle-reports/
  archive data under ARCHIVE_ROOT / данные архива в ARCHIVE_ROOT

Delete them manually only after verification.
Удаляйте их вручную только после проверки.
EOF
