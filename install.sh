#!/usr/bin/env bash
# Copyright (c) 2026, rslpro
# SPDX-License-Identifier: AGPL-3.0-only
set -Eeuo pipefail
[[ "${EUID:-$(id -u)}" == 0 ]] || { echo 'Запустите от root / Run as root'; exit 1; }
SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [[ -f /etc/iredmail-lifecycle-web/settings.json ]]; then
    exec bash "$SRC/upgrade.sh" "$@"
fi
if command -v apt-get >/dev/null; then
    apt-get update
    apt-get install -y ldap-utils rsync util-linux python3 python3-venv coreutils
fi
bash "$SRC/core/install.sh"
bash "$SRC/console/install-web.sh" "$@"
echo 'Откройте веб-консоль → Настройки. Укажите AD, пароль, домены и пути; проверьте подключение и сохраните. Таймер автоматически не включён.'
echo 'Open the console → Настройки. Configure AD/password/domains/storage, test and save. Timer is not enabled automatically.'
