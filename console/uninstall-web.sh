#!/usr/bin/env bash
# Copyright (c) 2026, rslpro
# SPDX-License-Identifier: AGPL-3.0-only
set -Eeuo pipefail
[[ "${EUID:-$(id -u)}" == 0 ]] || { echo 'Run as root'; exit 1; }
systemctl disable --now iredmail-lifecycle-web.service iredmail-lifecycle-worker.service || true
rm -f -- /etc/systemd/system/iredmail-lifecycle-web.service /etc/systemd/system/iredmail-lifecycle-worker.service
systemctl daemon-reload
echo 'Console stopped. Files, settings, audit data and the working lifecycle are preserved.'
echo 'Консоль отключена. Файлы, настройки, аудит и рабочий lifecycle сохранены.'
echo 'Remove its reverse-proxy location from your HTTPS site and reload the web server.'
