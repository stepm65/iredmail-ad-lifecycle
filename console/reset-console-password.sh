#!/usr/bin/env bash
# Copyright (c) 2026, rslpro
# SPDX-License-Identifier: AGPL-3.0-only
# Forgotten password recovery: root/SSH only, no archival worker interruption.
set -Eeuo pipefail
[[ "${EUID:-$(id -u)}" == 0 ]] || { echo 'Run as root / Запустите от root'; exit 1; }
/opt/iredmail-lifecycle-web/venv/bin/python /opt/iredmail-lifecycle-web/configure.py --set-password
systemctl restart iredmail-lifecycle-web.service
echo 'Пароль консоли изменён. Войдите заново. Почтовые данные и задания не затронуты.'
