#!/usr/bin/env bash
# Copyright (c) 2026, rslpro
# SPDX-License-Identifier: AGPL-3.0-only
set -Eeuo pipefail

CONFIG="${1:-/etc/iredmail-ad-lifecycle.conf}"
[[ -r "$CONFIG" ]] || { echo "Cannot read config: $CONFIG" >&2; exit 1; }
# shellcheck source=/dev/null
exec 8>"${LIFECYCLE_SETTINGS_LOCK:-/run/iredmail-lifecycle-settings.lock}"
flock -s -n 8 || { echo 'Settings are being changed'; exit 1; }
[[ ! -e /etc/iredmail-lifecycle-web/config-history/pending.json ]] || exit 1
source "$CONFIG"
if [[ -e /etc/iredmail-lifecycle-web/core-overrides.conf ]]; then
    [[ ! -L /etc/iredmail-lifecycle-web/core-overrides.conf ]] || exit 1
    [[ "$(stat -c %u /etc/iredmail-lifecycle-web/core-overrides.conf)" == 0 ]] || exit 1
    [[ "$(stat -c %a /etc/iredmail-lifecycle-web/core-overrides.conf)" == 600 ]] || exit 1
    source /etc/iredmail-lifecycle-web/core-overrides.conf
fi
SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TMP="$(mktemp)"
trap 'rm -f "$TMP"' EXIT

echo "1/5 LDAP bind / LDAP bind"
ldapwhoami -x -H "$LDAP_URI" "${LDAP_EXTRA_ARGS[@]}" -D "$BIND_DN" -y "$BIND_PW_FILE"

echo
echo "2/5 LDAP sample / Пример LDAP"
ldapsearch -LLL -x -H "$LDAP_URI" "${LDAP_EXTRA_ARGS[@]}" \
  -D "$BIND_DN" -y "$BIND_PW_FILE" -b "$BASE_DN" \
  -E "!pr=${LDAP_PAGE_SIZE:-1000}/noprompt" -o ldif-wrap=no \
  "$LDAP_FILTER" "$ADDRESS_ATTRIBUTE" mail proxyAddresses userAccountControl "$EMPLOYEE_STATUS_ATTRIBUTE" > "$TMP"
head -60 "$TMP"
python3 "$SRC_DIR/legacy.py" ad-table --ldif-file "$TMP" --address "$ADDRESS_ATTRIBUTE" --employee "$EMPLOYEE_STATUS_ATTRIBUTE" \
  --include-mail "${ADDRESS_INCLUDE_MAIL:-0}" --include-smtp "${ADDRESS_INCLUDE_SMTP_ALIASES:-0}" >/dev/null

echo
echo "3/5 Broad legacy search / Широкий legacy-поиск"
ldapsearch -LLL -x -H "$LDAP_URI" "${LDAP_EXTRA_ARGS[@]}" \
  -D "$BIND_DN" -y "$BIND_PW_FILE" -b "${LEGACY_SEARCH_BASE:-DC=example,DC=org}" \
  -E "!pr=${LDAP_PAGE_SIZE:-1000}/noprompt" -e '!1.2.840.113556.1.4.1339' -o ldif-wrap=no \
  "(|($ADDRESS_ATTRIBUTE=*)(mail=*)(proxyAddresses=*)(userPrincipalName=*))" \
  "$ADDRESS_ATTRIBUTE" mail proxyAddresses userPrincipalName > "$TMP"
head -60 "$TMP"

echo
echo "4/5 Mail storage / Почтовое хранилище"
findmnt "$VMAIL_ROOT" || true
df -hT "$VMAIL_ROOT"

echo
echo "5/5 Archive storage / Архив"
mkdir -p "$ARCHIVE_ROOT"
findmnt "$ARCHIVE_ROOT" || true
df -hT "$ARCHIVE_ROOT"
mkdir -p "${LEGACY_ARCHIVE_ROOT:-${ARCHIVE_ROOT}-legacy}"
df -hT "${LEGACY_ARCHIVE_ROOT:-${ARCHIVE_ROOT}-legacy}"

echo
echo "Preflight completed / Предварительная проверка завершена"
