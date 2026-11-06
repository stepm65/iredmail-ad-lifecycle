#!/usr/bin/env bash
# Copyright (c) 2026, rslpro
# SPDX-License-Identifier: AGPL-3.0-only
set -Eeuo pipefail
umask 077

# iRedMail + Active Directory mailbox lifecycle manager
# Менеджер жизненного цикла почтовых ящиков iRedMail + Active Directory
#
# Archive rule / Правило архивации:
#   AD account disabled (ACCOUNTDISABLE bit 0x2)
#   AND extensionAttribute6 == TERMINATED_STATUS_VALUE (default: 0)
#
# Everything else is report-only.
# Все остальные состояния только отображаются в отчёте и НЕ архивируются.

CONFIG_FILE="${CONFIG_FILE:-/etc/iredmail-ad-lifecycle.conf}"
MODE="normal"
OPERATION=""
FORCE_REPORT=0
INVENTORY_ARG=""
APPROVAL_ARG=""
WRITERS_QUIESCED=0
OUTPUT_ARG=""
EMAIL_ARG=""
REASON_ARG=""
ARCHIVE_ID_ARG=""
PREVIEW_ARG=""
ACTOR_ARG="root"
CONFIRM_DELETE_ARG=""
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HELPER="${LEGACY_HELPER:-$SCRIPT_DIR/legacy.py}"
[[ -f "$HELPER" ]] || HELPER=/usr/local/lib/iredmail-ad-lifecycle/legacy.py

usage() {
    cat <<'EOF'
Usage:
  iredmail-ad-lifecycle [--normal] [--report]
  iredmail-ad-lifecycle --legacy-inventory --inventory FILE.csv
  iredmail-ad-lifecycle --legacy-archive --inventory FILE.csv [--approval FILE.csv|FILE.txt] [--report]
  iredmail-ad-lifecycle --legacy-archive-all --inventory FILE.csv [--report]
  iredmail-ad-lifecycle --legacy-purge [--report]
  iredmail-ad-lifecycle --delete-batch-preview --approval FILE.csv|FILE.txt --output batch-preview.json
  iredmail-ad-lifecycle --delete-batch --preview batch-preview.json --output result.json --confirm-delete "DELETE N" --writers-quiesced
  iredmail-ad-lifecycle --delete-preview --email EMAIL --output preview.json
  iredmail-ad-lifecycle --delete --email EMAIL --preview preview.json --output result.json --confirm-delete EMAIL --writers-quiesced
  iredmail-ad-lifecycle --archive-list --output archives.csv
  iredmail-ad-lifecycle --restore-preview --archive-id ID --output preview.json
  iredmail-ad-lifecycle --restore --archive-id ID --preview preview.json --output result.json --writers-quiesced
  iredmail-ad-lifecycle --exclude-add --email EMAIL --reason TEXT --output exclusions.csv
  iredmail-ad-lifecycle --exclude-remove --email EMAIL --output exclusions.csv
  iredmail-ad-lifecycle --exclusions-list --output exclusions.csv

Options:
  --report   Force safe report-only mode for this run, regardless of DRY_RUN.
             Принудительно включить безопасный режим отчёта без изменений.
  --help     Show this help.
  --normal   Force normal policy, ignoring LEGACY_MODE (used by systemd).
  --legacy-inventory   Write missing inventory; never archive or purge.
  --legacy-archive     Archive only explicitly approved inventory entries.
  --legacy-archive-all Explicit approval of ALL eligible entries in this inventory;
                       still subject to inactivity, AD rechecks and batch limits.
  --writers-quiesced   Required for live legacy archival: operator has stopped writers.
                       Для реальной legacy-архивации: доставка/доступ остановлены.
  --legacy-purge       Explicit legacy retention run; never part of normal timer.
EOF
}

set_operation() {
    [[ -z "$OPERATION" ]] || { echo "Conflicting operation flags" >&2; exit 2; }
    OPERATION="$1"
}
while (( $# )); do
    case "$1" in
        --report) FORCE_REPORT=1 ;;
        --normal) set_operation normal ;;
        --legacy-inventory) set_operation inventory ;;
        --legacy-archive) set_operation archive ;;
        --legacy-archive-all) set_operation archive-all ;;
        --legacy-purge) set_operation purge ;;
        --archive-list) set_operation archive-list ;;
        --restore-preview) set_operation restore-preview ;;
        --restore) set_operation restore ;;
        --delete-preview) set_operation delete-preview ;;
        --delete) set_operation delete ;;
        --delete-batch-preview) set_operation delete-batch-preview ;;
        --delete-batch) set_operation delete-batch ;;
        --exclude-add) set_operation exclude-add ;;
        --exclude-remove) set_operation exclude-remove ;;
        --exclusions-list) set_operation exclusions-list ;;
        --output|--email|--reason|--archive-id|--preview|--actor|--confirm-delete)
            [[ $# -ge 2 && "$2" != --* ]] || { echo "Missing argument for $1" >&2; exit 2; }
            case "$1" in
              --output) OUTPUT_ARG="$2" ;; --email) EMAIL_ARG="$2" ;; --reason) REASON_ARG="$2" ;;
              --archive-id) ARCHIVE_ID_ARG="$2" ;; --preview) PREVIEW_ARG="$2" ;; --actor) ACTOR_ARG="$2" ;; --confirm-delete) CONFIRM_DELETE_ARG="$2" ;;
            esac
            shift ;;
        --inventory|--approval)
            [[ $# -ge 2 && "$2" != --* ]] || { echo "Missing file argument for $1" >&2; exit 2; }
            if [[ "$1" == --inventory ]]; then INVENTORY_ARG="$2"; else APPROVAL_ARG="$2"; fi
            shift ;;
        --writers-quiesced) WRITERS_QUIESCED=1 ;;
        --help|-h) usage; exit 0 ;;
        *) echo "Unknown argument: $1" >&2; usage >&2; exit 2 ;;
    esac
    shift
done

log() {
    local msg="$*"
    local line
    line="$(date '+%Y-%m-%d %H:%M:%S%z') $msg"
    printf '%s\n' "$line"
    if [[ -n "${LOG_FILE:-}" ]]; then
        mkdir -p "$(dirname "$LOG_FILE")" 2>/dev/null || true
        printf '%s\n' "$line" >> "$LOG_FILE" 2>/dev/null || true
    fi
}

die() {
    log "ERROR: $*"
    exit 1
}

require_cmd() {
    command -v "$1" >/dev/null 2>&1 || die "Required command not found: $1"
}

csv_escape() {
    local s="${1//\"/\"\"}"
    printf '"%s"' "$s"
}

human_bytes() {
    awk -v b="${1:-0}" 'BEGIN {
        if (b >= 1099511627776) printf "%.2f TB", b/1099511627776;
        else if (b >= 1073741824) printf "%.2f GB", b/1073741824;
        else if (b >= 1048576) printf "%.2f MB", b/1048576;
        else if (b >= 1024) printf "%.2f KB", b/1024;
        else printf "%d B", b;
    }'
}

state_file_for() {
    local email="$1"
    local safe
    if [[ "$email" =~ ^[A-Za-z0-9_.@-]+$ ]]; then
        safe="$email"
    else
        # Unicode and '+' must not collapse into the same underscore filename.
        # Unicode и '+' не должны сливаться в одинаковые имена с подчёркиваниями.
        safe="$(python3 "$HELPER" state-key --email "$email")"
    fi
    printf '%s/%s.state\n' "$STATE_DIR" "$safe"
}

get_or_set_first_seen() {
    local email="$1"
    local classification="$2"
    local f now old_class old_ts
    f="$(state_file_for "$email")"
    now="$(date +%s)"

    if [[ -f "$f" ]]; then
        old_class="$(awk -F= '$1=="classification"{print substr($0,index($0,"=")+1)}' "$f" 2>/dev/null || true)"
        old_ts="$(awk -F= '$1=="first_seen"{print substr($0,index($0,"=")+1)}' "$f" 2>/dev/null || true)"
        if [[ "$old_class" == "$classification" && "$old_ts" =~ ^[0-9]+$ ]]; then
            printf '%s\n' "$old_ts"
            return 0
        fi
    fi

    # In DRY_RUN/report mode, never create or modify state files.
    # В DRY_RUN/режиме отчёта state-файлы не создаются и не изменяются.
    if (( ${DRY_RUN:-1} == 1 )); then
        printf '%s\n' "$now"
        return 0
    fi

    {
        printf 'classification=%s\n' "$classification"
        printf 'first_seen=%s\n' "$now"
    } > "$f"
    printf '%s\n' "$now"
}

clear_state() {
    # Report-only mode must not mutate state.
    # Режим отчёта не должен изменять state.
    if (( ${DRY_RUN:-1} == 1 )); then
        return 0
    fi
    rm -f -- "$(state_file_for "$1")"
}

employee_label() {
    case "$1" in
        0) printf 'Уволен(а) / Terminated' ;;
        1) printf 'Работает / Working' ;;
        2) printf 'Отпуск по уходу за ребёнком / Childcare leave' ;;
        3) printf 'Отпуск / Vacation' ;;
        4) printf 'Больничный / Sick leave' ;;
        5) printf 'Командировка / Business trip' ;;
        6) printf 'Отсутствует / Absent' ;;
        7) printf 'Отпуск по беременности и родам / Maternity leave' ;;
        8) printf 'Трудоустройство / Employment onboarding' ;;
        "") printf 'Не задан / Not set' ;;
        *) printf 'Неизвестный статус / Unknown status' ;;
    esac
}

archive_mailbox() {
    local email="$1"
    local source="$2"
    local reason="$3"
    local domain login timestamp dest src_bytes dst_bytes min_bytes src_files dst_files
    [[ "$(python3 "$HELPER" exclusion-status --exclusions "$EXCLUSION_FILE" --email "$email")" == 0 ]] || { log "SKIP: excluded $email"; return 0; }

    domain="${email#*@}"
    login="${email%@*}"
    timestamp="$(date '+%Y%m%dT%H%M%S')"
    dest="$ARCHIVE_ROOT/$domain/$login/$timestamp"

    src_bytes="$(du -sb -- "$source" 2>/dev/null | awk '{print $1}')"
    src_bytes="${src_bytes:-0}"

    if (( DRY_RUN == 1 )); then
        log "DRY-RUN: would archive $email reason=$reason size=$(human_bytes "$src_bytes") source=$source destination=$dest"
        return 0
    fi

    [[ ! -e "$dest" ]] || die "Archive destination already exists: $dest"
    mkdir -p -- "$dest"
    log "ARCHIVE START: $email reason=$reason size=$(human_bytes "$src_bytes")"
    log "SOURCE: $source"
    log "DESTINATION: $dest"

    # First copy. If rsync fails, source is left untouched.
    # Сначала копирование. При ошибке rsync исходник не удаляется.
    if ! rsync -aHAX --numeric-ids -- "$source/" "$dest/"; then
        log "ERROR: rsync failed for $email; source was NOT removed"
        return 1
    fi

    # Second pass reduces the window for changes between copy and verification.
    # Второй проход уменьшает окно между копированием и проверкой.
    if ! rsync -aHAX --numeric-ids --delete -- "$source/" "$dest/"; then
        log "ERROR: second rsync pass failed for $email; source was NOT removed"
        return 1
    fi

    dst_bytes="$(du -sb -- "$dest" 2>/dev/null | awk '{print $1}')"
    dst_bytes="${dst_bytes:-0}"
    min_bytes=$(( src_bytes * VERIFY_MIN_PERCENT / 100 ))

    if (( src_bytes > 0 && dst_bytes < min_bytes )); then
        log "ERROR: size verification failed for $email: source=$src_bytes destination=$dst_bytes minimum=$min_bytes"
        log "ERROR: source was NOT removed"
        return 1
    fi

    src_files="$(find "$source" -xdev -type f -printf '.' 2>/dev/null | wc -c)"
    dst_files="$(find "$dest"   -xdev -type f -printf '.' 2>/dev/null | wc -c)"
    if [[ "$src_files" != "$dst_files" ]]; then
        log "ERROR: file-count verification failed for $email: source=$src_files destination=$dst_files"
        log "ERROR: source was NOT removed"
        return 1
    fi

    # Source is removed only after successful copy and verification.
    # Исходник удаляется только после успешного копирования и проверки.
    [[ "$(python3 "$HELPER" exclusion-status --exclusions "$EXCLUSION_FILE" --email "$email")" == 0 ]] || { log "SKIP: excluded during copy $email; source preserved"; return 0; }
    python3 "$MANAGEMENT_HELPER" normal-record --phase verified --email "$email" --source "$source" --archive-path "$dest" \
        --normal-archive-root "$ARCHIVE_ROOT" --vmail-root "$VMAIL_ROOT"
    rm -rf --one-file-system -- "$source"
    python3 "$MANAGEMENT_HELPER" normal-record --phase complete --email "$email" --source "$source" --archive-path "$dest" \
        --normal-archive-root "$ARCHIVE_ROOT" --vmail-root "$VMAIL_ROOT"
    clear_state "$email"
    log "ARCHIVED: $email -> $dest"
}

write_report_row() {
    local email="$1" class="$2" ad_enabled="$3" emp_status="$4" emp_label="$5" path="$6" size="$7"
    local ad_match="$8" exclusion_reason="${9:-}"
    {
        csv_escape "$email"; printf ','
        csv_escape "$class"; printf ','
        csv_escape "$ad_enabled"; printf ','
        csv_escape "$emp_status"; printf ','
        csv_escape "$emp_label"; printf ','
        printf '%s,' "$size"
        csv_escape "$(human_bytes "$size")"; printf ','
        csv_escape "$path"; printf ','
        csv_escape "$ad_match"; printf ','
        csv_escape "$exclusion_reason"; printf '\n'
    } >> "$REPORT_FILE"
}

# -----------------------------------------------------------------------------
# Configuration / Конфигурация
# -----------------------------------------------------------------------------
[[ -r "$CONFIG_FILE" ]] || { echo "ERROR: cannot read $CONFIG_FILE" >&2; exit 1; }
# shellcheck source=/dev/null
# Hold a stable shared lock BEFORE reading either configuration file. The web
# broker takes it exclusively for changes; pending recovery blocks all mutations.
exec 8>"${LIFECYCLE_SETTINGS_LOCK:-/run/iredmail-lifecycle-settings.lock}"
flock -s -n 8 || { echo 'Settings are being changed; retry later' >&2; exit 1; }
[[ ! -e /etc/iredmail-lifecycle-web/config-history/pending.json ]] || { echo 'Settings recovery required; no processing performed' >&2; exit 1; }
source "$CONFIG_FILE"
if [[ -e /etc/iredmail-lifecycle-web/core-overrides.conf ]]; then
    [[ ! -L /etc/iredmail-lifecycle-web/core-overrides.conf ]] || exit 1
    [[ "$(stat -c %u /etc/iredmail-lifecycle-web/core-overrides.conf)" == 0 ]] || exit 1
    [[ "$(stat -c %a /etc/iredmail-lifecycle-web/core-overrides.conf)" == 600 ]] || exit 1
    source /etc/iredmail-lifecycle-web/core-overrides.conf
fi

: "${LDAP_URI:?LDAP_URI is required}"
: "${BIND_DN:?BIND_DN is required}"
: "${BIND_PW_FILE:?BIND_PW_FILE is required}"
: "${BASE_DN:?BASE_DN is required}"
: "${ADDRESS_ATTRIBUTE:=userPrincipalName}"
: "${ADDRESS_INCLUDE_MAIL:=0}"
: "${ADDRESS_INCLUDE_SMTP_ALIASES:=0}"
: "${EMPLOYEE_STATUS_ATTRIBUTE:=extensionAttribute6}"
: "${LDAP_FILTER:=(&(objectClass=person)(objectCategory=user))}"
: "${TERMINATED_STATUS_VALUE:=0}"
: "${VMAIL_ROOT:?VMAIL_ROOT is required}"
: "${ARCHIVE_ROOT:?ARCHIVE_ROOT is required}"
: "${STATE_DIR:=/var/lib/iredmail-ad-lifecycle}"
: "${EXCLUSION_FILE:=$STATE_DIR/exclusions.json}"
MANAGEMENT_HELPER="$(dirname "$HELPER")/manage.py"
: "${LOG_FILE:=/var/log/iredmail-ad-lifecycle.log}"
: "${LOCK_FILE:=/run/iredmail-ad-lifecycle.lock}"
: "${REPORT_DIR:=/var/log/iredmail-ad-lifecycle-reports}"
: "${DRY_RUN:=1}"
: "${TERMINATED_ARCHIVE_DAYS:=0}"
: "${ARCHIVE_RETENTION_DAYS:=90}"
: "${EXPECTED_MIN_USERS:=1}"
: "${MAX_TERMINATED_PERCENT:=10}"
: "${REQUIRE_SEPARATE_ARCHIVE_FS:=1}"
: "${VERIFY_MIN_PERCENT:=99}"
: "${LDAP_PAGE_SIZE:=1000}"
: "${REPORT_MAILBOX_SIZES:=0}"
: "${LEGACY_MODE:=0}"
: "${LEGACY_SEARCH_BASE:=DC=example,DC=org}"
: "${LEGACY_MIN_INACTIVE_DAYS:=365}"
: "${LEGACY_APPROVAL_FILE:=}"
: "${LEGACY_INVENTORY_FILE:=}"
: "${LEGACY_ARCHIVE_ROOT:=${ARCHIVE_ROOT}-legacy}"
: "${LEGACY_RETENTION_DAYS:=0}"
: "${LEGACY_MAX_COUNT:=20}"
: "${LEGACY_MAX_PERCENT:=5}"

[[ "$LEGACY_MODE" == 0 || "$LEGACY_MODE" == 1 ]] || die "LEGACY_MODE must be 0 or 1"
if [[ -n "$OPERATION" ]]; then
    MODE="$OPERATION"
elif [[ "$LEGACY_MODE" == 1 ]]; then
    MODE=archive
fi
if (( FORCE_REPORT == 1 )); then
    DRY_RUN=1
fi
[[ "$DRY_RUN" == 0 || "$DRY_RUN" == 1 ]] || die "DRY_RUN must be 0 or 1"
[[ "$TERMINATED_STATUS_VALUE" == 0 ]] || die "This policy only permits TERMINATED_STATUS_VALUE=0"
for numeric in TERMINATED_ARCHIVE_DAYS ARCHIVE_RETENTION_DAYS EXPECTED_MIN_USERS MAX_TERMINATED_PERCENT LDAP_PAGE_SIZE; do
    [[ "${!numeric}" =~ ^(0|[1-9][0-9]*)$ ]] || die "$numeric must be a nonnegative integer without leading zeros"
done
(( EXPECTED_MIN_USERS > 0 && LDAP_PAGE_SIZE > 0 && MAX_TERMINATED_PERCENT <= 100 )) || die "Invalid AD/percentage safeguards"
for boolean in REQUIRE_SEPARATE_ARCHIVE_FS REPORT_MAILBOX_SIZES ADDRESS_INCLUDE_MAIL ADDRESS_INCLUDE_SMTP_ALIASES; do
    [[ "${!boolean}" == 0 || "${!boolean}" == 1 ]] || die "$boolean must be 0 or 1"
done
if [[ "$MODE" == normal && ( -n "$INVENTORY_ARG" || -n "$APPROVAL_ARG" || "$WRITERS_QUIESCED" == 1 || -n "$OUTPUT_ARG$EMAIL_ARG$REASON_ARG$ARCHIVE_ID_ARG$PREVIEW_ARG" ) ]]; then
    die "Legacy arguments require an explicit legacy operation"
fi

[[ ${#MAIL_DOMAINS[@]} -gt 0 ]] || die "MAIL_DOMAINS is empty"
[[ -r "$BIND_PW_FILE" ]] || die "Password file is not readable: $BIND_PW_FILE"
[[ -d "$VMAIL_ROOT" ]] || die "VMAIL_ROOT does not exist: $VMAIL_ROOT"
[[ "$VERIFY_MIN_PERCENT" =~ ^[0-9]+$ ]] || die "VERIFY_MIN_PERCENT must be numeric"
(( VERIFY_MIN_PERCENT >= 90 && VERIFY_MIN_PERCENT <= 100 )) || die "VERIFY_MIN_PERCENT must be between 90 and 100"

mkdir -p "$REPORT_DIR" "$(dirname "$LOG_FILE")"
if (( DRY_RUN == 0 )) && [[ "$MODE" == normal ]]; then mkdir -p "$STATE_DIR"; fi
touch "$LOG_FILE"

for cmd in ldapsearch rsync du find flock awk date python3 realpath; do
    require_cmd "$cmd"
done
if (( REQUIRE_SEPARATE_ARCHIVE_FS == 1 )); then
    require_cmd findmnt
fi

# -----------------------------------------------------------------------------
# Lock / Блокировка параллельного запуска
# -----------------------------------------------------------------------------
exec 9>"$LOCK_FILE"
flock -n 9 || die "Another instance is already running"
[[ -r "$HELPER" ]] || die "Missing helper: $HELPER; reinstall version 1.1"

case "$MODE" in
    archive-list|restore-preview|restore|exclude-add|exclude-remove|exclusions-list|delete-preview|delete|delete-batch-preview|delete-batch)
        [[ -r "$MANAGEMENT_HELPER" && -n "$OUTPUT_ARG" ]] || die "Management helper and --output FILE required"
        MANAGE_ARGS=("$MODE" --vmail-root "$VMAIL_ROOT" --normal-archive-root "$ARCHIVE_ROOT" \
            --archive-root "$LEGACY_ARCHIVE_ROOT" --base "$BASE_DN" --search-base "$LEGACY_SEARCH_BASE" \
            --exclusions "$EXCLUSION_FILE" --output "$OUTPUT_ARG" --email "$EMAIL_ARG" --reason "$REASON_ARG" \
            --archive-id "$ARCHIVE_ID_ARG" --preview "$PREVIEW_ARG" --actor "$ACTOR_ARG" --dry-run "$DRY_RUN")
        for domain in "${MAIL_DOMAINS[@]}"; do MANAGE_ARGS+=(--domain "$domain"); done
        (( FORCE_REPORT == 0 )) || MANAGE_ARGS+=(--report)
        (( WRITERS_QUIESCED == 0 )) || MANAGE_ARGS+=(--writers-quiesced)
        if [[ "$MODE" == delete-preview || "$MODE" == delete || "$MODE" == delete-batch-preview || "$MODE" == delete-batch ]]; then
            MANAGEMENT_HELPER="$(dirname "$HELPER")/delete_mailbox.py"
            [[ -r "$MANAGEMENT_HELPER" ]] || die "Missing delete_mailbox.py; install lifecycle 1.4.0"
            MANAGE_ARGS+=(--ldap-uri "$LDAP_URI" --bind-dn "$BIND_DN" --password-file "$BIND_PW_FILE"
                --address "$ADDRESS_ATTRIBUTE" --employee "$EMPLOYEE_STATUS_ATTRIBUTE" --ldap-filter "$LDAP_FILTER"
                --page-size "$LDAP_PAGE_SIZE" --min-users "$EXPECTED_MIN_USERS" --confirm-delete "$CONFIRM_DELETE_ARG"
                --approval "$APPROVAL_ARG" --delete-max-count "${MANUAL_DELETE_MAX_COUNT:-100}" --delete-max-percent "${MANUAL_DELETE_MAX_PERCENT:-25}")
            if declare -p LDAP_EXTRA_ARGS >/dev/null 2>&1; then
                for extra in "${LDAP_EXTRA_ARGS[@]}"; do MANAGE_ARGS+=("--ldap-extra=$extra"); done
            fi
        fi
        python3 "$MANAGEMENT_HELPER" "${MANAGE_ARGS[@]}" 2>&1 | tee -a "$LOG_FILE"
        exit 0 ;;
esac
[[ "$MODE" == normal || -z "$OUTPUT_ARG$EMAIL_ARG$REASON_ARG$ARCHIVE_ID_ARG$PREVIEW_ARG" ]] || die "Management arguments require a management operation"

# Explicit legacy workflow is isolated from normal state/archive/retention.
# Явный legacy-процесс отделён от штатной архивации и удаления архивов.
if [[ "$MODE" != normal ]]; then
    LEGACY_ARGS=("$MODE" --ldap-uri "$LDAP_URI" --bind-dn "$BIND_DN"
        --password-file "$BIND_PW_FILE" --base "$BASE_DN" --search-base "$LEGACY_SEARCH_BASE"
        --address "$ADDRESS_ATTRIBUTE" --employee "$EMPLOYEE_STATUS_ATTRIBUTE"
        --include-mail "$ADDRESS_INCLUDE_MAIL" --include-smtp "$ADDRESS_INCLUDE_SMTP_ALIASES"
        --ldap-filter "$LDAP_FILTER" --page-size "$LDAP_PAGE_SIZE" --min-users "$EXPECTED_MIN_USERS"
        --vmail-root "$VMAIL_ROOT" --normal-archive-root "$ARCHIVE_ROOT" --archive-root "$LEGACY_ARCHIVE_ROOT"
        --min-inactive "$LEGACY_MIN_INACTIVE_DAYS" --retention "$LEGACY_RETENTION_DAYS"
        --max-count "$LEGACY_MAX_COUNT" --max-percent "$LEGACY_MAX_PERCENT"
        --separate-fs "$REQUIRE_SEPARATE_ARCHIVE_FS" --dry-run "$DRY_RUN"
        --inventory "${INVENTORY_ARG:-$LEGACY_INVENTORY_FILE}" --approval "${APPROVAL_ARG:-$LEGACY_APPROVAL_FILE}" --exclusions "$EXCLUSION_FILE")
    for domain in "${MAIL_DOMAINS[@]}"; do LEGACY_ARGS+=(--domain "$domain"); done
    if declare -p LDAP_EXTRA_ARGS >/dev/null 2>&1; then
        for extra in "${LDAP_EXTRA_ARGS[@]}"; do LEGACY_ARGS+=("--ldap-extra=$extra"); done
    fi
    if (( WRITERS_QUIESCED == 1 )); then LEGACY_ARGS+=(--writers-quiesced); fi
    # Bash keeps fd 9/flock held until helper finishes; capture helper errors in log.
    python3 "$HELPER" "${LEGACY_ARGS[@]}" 2>&1 | tee -a "$LOG_FILE"
    exit 0
fi

# Canonical roots prevent nested archives, unsafe deletion paths and retention leaks.
# Проверка корней исключает вложенный архив и удаление чужих каталогов.
VMAIL_ROOT="$(realpath -e -- "$VMAIL_ROOT")"
ARCHIVE_ROOT="$(realpath -m -- "$ARCHIVE_ROOT")"
LEGACY_ARCHIVE_ROOT="$(realpath -m -- "$LEGACY_ARCHIVE_ROOT")"
[[ "$VMAIL_ROOT" != / && "$ARCHIVE_ROOT" != / && "$LEGACY_ARCHIVE_ROOT" != / ]] || die "Unsafe root path"
[[ "$ARCHIVE_ROOT" != "$VMAIL_ROOT" && "$ARCHIVE_ROOT" != "$VMAIL_ROOT/"* && "$VMAIL_ROOT" != "$ARCHIVE_ROOT/"* ]] || die "Mailbox/archive roots overlap"
[[ "$LEGACY_ARCHIVE_ROOT" != "$ARCHIVE_ROOT" && "$ARCHIVE_ROOT" != "$LEGACY_ARCHIVE_ROOT/"* ]] || die "Legacy root cannot equal or contain normal archive root"
[[ "$LEGACY_ARCHIVE_ROOT" != "$VMAIL_ROOT" && "$LEGACY_ARCHIVE_ROOT" != "$VMAIL_ROOT/"* && "$VMAIL_ROOT" != "$LEGACY_ARCHIVE_ROOT/"* ]] || die "Legacy/mailbox roots overlap"
if [[ "$LEGACY_ARCHIVE_ROOT" == "$ARCHIVE_ROOT/"* ]]; then
    legacy_relative="${LEGACY_ARCHIVE_ROOT#"$ARCHIVE_ROOT/"}"
    [[ "$legacy_relative" != */* ]] || die "Nested legacy root must be a direct subdirectory"
    for domain in "${MAIL_DOMAINS[@]}"; do
        [[ "${legacy_relative,,}" != "${domain,,}" ]] || die "Legacy subdirectory cannot be a mail domain"
    done
fi

# -----------------------------------------------------------------------------
# LDAP query / LDAP-запрос
# -----------------------------------------------------------------------------
declare -A EXCLUSIONS=()
EXCLUSION_TABLE="$(python3 "$HELPER" exclusion-table --exclusions "$EXCLUSION_FILE")" || die "Invalid exclusions policy; no changes"
while IFS='|' read -r excluded_email excluded_reason; do
    [[ -z "$excluded_email" ]] || EXCLUSIONS["$excluded_email"]="$excluded_reason"
done <<< "$EXCLUSION_TABLE"
LDAP_TMP="$(mktemp)"
trap 'rm -f "$LDAP_TMP"' EXIT

LDAP_ARGS=(
    -LLL -x -o ldif-wrap=no
    -H "$LDAP_URI"
    -D "$BIND_DN"
    -y "$BIND_PW_FILE"
    -b "$BASE_DN"
    -E "!pr=${LDAP_PAGE_SIZE}/noprompt"
)
if declare -p LDAP_EXTRA_ARGS >/dev/null 2>&1; then
    LDAP_ARGS+=("${LDAP_EXTRA_ARGS[@]}")
fi

log "Querying AD: $LDAP_URI"
log "LDAP mapping: ADDRESS_ATTRIBUTE=$ADDRESS_ATTRIBUTE; ADDRESS_INCLUDE_MAIL=$ADDRESS_INCLUDE_MAIL; ADDRESS_INCLUDE_SMTP_ALIASES=$ADDRESS_INCLUDE_SMTP_ALIASES"
log "LDAP filter: $LDAP_FILTER"
log "Mailbox identity must match the LDAP mapping used by SOGo/Postfix/Dovecot."
if ! ldapsearch "${LDAP_ARGS[@]}" \
    "$LDAP_FILTER" \
    "$ADDRESS_ATTRIBUTE" \
    mail proxyAddresses \
    userAccountControl \
    "$EMPLOYEE_STATUS_ATTRIBUTE" \
    > "$LDAP_TMP"; then
    die "LDAP query failed. No mailbox changes were made."
fi

# -----------------------------------------------------------------------------
# Parse AD / Разбор ответа AD
# -----------------------------------------------------------------------------
declare -A AD_DISABLED=()
declare -A AD_EMP_STATUS=()
declare -A AD_MATCH=()

# Duplicate identities protect the affected mailbox; malformed/referral data stops.
# Дубликаты защищают соответствующий ящик; ошибочный/неполный LDAP блокирует работу.
AD_CONFLICT_REPORT="$REPORT_DIR/ad-address-conflicts-$(date '+%Y%m%d-%H%M%S')-$$.csv"
AD_TABLE="$(python3 "$HELPER" ad-table --ldif-file "$LDAP_TMP" --address "$ADDRESS_ATTRIBUTE" --employee "$EMPLOYEE_STATUS_ATTRIBUTE" --include-mail "$ADDRESS_INCLUDE_MAIL" --include-smtp "$ADDRESS_INCLUDE_SMTP_ALIASES" --include-count --include-sources --conflicts-csv "$AD_CONFLICT_REPORT")" || die "Unsafe LDAP response"
log "AD address conflict details: $AD_CONFLICT_REPORT"
AD_COUNT=0
while IFS='|' read -r mail disabled emp matched; do
    [[ -n "$mail" ]] || continue
    if [[ "$mail" == '#objects' ]]; then AD_COUNT="$disabled"; continue; fi
    emp="${emp%$'\r'}"
    AD_DISABLED["$mail"]="$disabled"
    AD_EMP_STATUS["$mail"]="$emp"
    AD_MATCH["$mail"]="$matched"
done <<< "$AD_TABLE"

AD_IDENTITIES="${#AD_DISABLED[@]}"
log "AD user objects loaded: $AD_COUNT; mailbox identities loaded: $AD_IDENTITIES"
(( AD_COUNT >= EXPECTED_MIN_USERS )) || die "Safety stop: AD returned $AD_COUNT users; expected at least $EXPECTED_MIN_USERS"

# -----------------------------------------------------------------------------
# Discover/classify mailboxes / Поиск и классификация ящиков
# -----------------------------------------------------------------------------
declare -a MB_EMAIL MB_PATH MB_CLASS MB_ENABLED MB_EMP MB_MATCH
TOTAL=0
ACTIVE=0
TERMINATED=0
PROTECTED_DISABLED=0
UNKNOWN_DISABLED=0
AMBIGUOUS_AD=0
ALIAS_ONLY=0
MISSING=0

for domain in "${MAIL_DOMAINS[@]}"; do
    domain="${domain,,}"
    [[ "$domain" =~ ^[a-zA-Z0-9][a-zA-Z0-9.-]*$ && "$domain" != *..* ]] || die "Unsafe domain"
    domain_dir="$VMAIL_ROOT/$domain"
    [[ -d "$domain_dir" ]] || { log "WARNING: domain directory not found: $domain_dir"; continue; }

    for user_dir in "$domain_dir"/*; do
        [[ -d "$user_dir/Maildir" ]] || continue
        [[ ! -L "$domain_dir" && ! -L "$user_dir" && ! -L "$user_dir/Maildir" ]] || die "Symlink mailbox is unsupported"

        login="$(basename "$user_dir")"
        # Use the same Unicode/path-safe validation and lowercase as AD parsing.
        # Общая Unicode-проверка и регистр для имени каталога и адреса из AD.
        if [[ "$login" =~ ^[a-z0-9][a-z0-9_.+-]*$ ]]; then
            email="$login@$domain"
        else
            email="$(python3 "$HELPER" canonical-email --email "$login@$domain")" || die "Unsupported mailbox login: $login"
        fi

        if [[ ! -v "AD_DISABLED[$email]" ]]; then
            class="missing"
            enabled="unknown"
            emp=""
            (( MISSING += 1 ))
        else
            emp="${AD_EMP_STATUS[$email]:-}"
            if [[ "${AD_DISABLED[$email]}" == ambiguous ]]; then
                class="ambiguous-ad"
                enabled="unknown"
                (( AMBIGUOUS_AD += 1 ))
            elif [[ "${AD_DISABLED[$email]}" == alias-only ]]; then
                class="alias-only"
                enabled="unknown"
                (( ALIAS_ONLY += 1 ))
            elif [[ "${AD_DISABLED[$email]}" == "0" ]]; then
                class="active"
                enabled="yes"
                (( ACTIVE += 1 ))
            elif [[ "${AD_DISABLED[$email]}" == 1 && "$emp" == "$TERMINATED_STATUS_VALUE" ]]; then
                class="terminated"
                enabled="no"
                (( TERMINATED += 1 ))
            elif [[ "${AD_DISABLED[$email]}" == 1 && "$emp" =~ ^[1-8]$ ]]; then
                class="protected-disabled"
                enabled="no"
                (( PROTECTED_DISABLED += 1 ))
            else
                class="unknown-disabled"
                enabled="no"
                (( UNKNOWN_DISABLED += 1 ))
            fi
        fi

        if [[ -v "EXCLUSIONS[$email]" ]]; then
            if [[ "$class" == terminated ]]; then TERMINATED=$(( TERMINATED - 1 )); fi
            class="excluded"
        fi
        MB_EMAIL+=("$email")
        MB_PATH+=("$user_dir")
        MB_CLASS+=("$class")
        MB_ENABLED+=("$enabled")
        MB_EMP+=("$emp")
        MB_MATCH+=("${AD_MATCH[$email]:-}")
        (( TOTAL += 1 ))
    done
done

(( TOTAL > 0 )) || die "No mailboxes discovered"

log "Mailboxes: total=$TOTAL active=$ACTIVE terminated=$TERMINATED protected-disabled=$PROTECTED_DISABLED unknown-disabled=$UNKNOWN_DISABLED ambiguous-ad=$AMBIGUOUS_AD alias-only=$ALIAS_ONLY missing=$MISSING"

# Safety applies only to mailboxes that can actually be archived.
(( TERMINATED * 100 <= MAX_TERMINATED_PERCENT * TOTAL )) || \
    die "Safety stop: terminated mailboxes exceed ${MAX_TERMINATED_PERCENT}% ($TERMINATED/$TOTAL)"

# -----------------------------------------------------------------------------
# Archive filesystem safety / Проверка файловой системы архива
# -----------------------------------------------------------------------------
if (( DRY_RUN == 0 )); then
    mkdir -p "$ARCHIVE_ROOT"
    if (( REQUIRE_SEPARATE_ARCHIVE_FS == 1 )); then
        src_dev="$(findmnt -n -o SOURCE --target "$VMAIL_ROOT" 2>/dev/null || true)"
        dst_dev="$(findmnt -n -o SOURCE --target "$ARCHIVE_ROOT" 2>/dev/null || true)"
        [[ -n "$src_dev" && -n "$dst_dev" ]] || die "Cannot determine source/archive filesystems"
        [[ "$src_dev" != "$dst_dev" ]] || die "Safety stop: VMAIL_ROOT and ARCHIVE_ROOT are on the same filesystem: $src_dev"
    fi
fi

# -----------------------------------------------------------------------------
# CSV report / CSV-отчёт
# -----------------------------------------------------------------------------
REPORT_FILE="$REPORT_DIR/mailboxes-$(date '+%Y%m%d-%H%M%S').csv"
printf '"email","classification","ad_enabled","%s","employee_status_label","size_bytes","size_human","mailbox_path","ad_match_attributes","exclusion_reason"\n' \
    "$EMPLOYEE_STATUS_ATTRIBUTE" > "$REPORT_FILE"

for i in "${!MB_EMAIL[@]}"; do
    email="${MB_EMAIL[$i]}"
    source="${MB_PATH[$i]}"
    class="${MB_CLASS[$i]}"
    enabled="${MB_ENABLED[$i]}"
    emp="${MB_EMP[$i]}"
    if (( REPORT_MAILBOX_SIZES == 1 )); then
        size="$(du -sb -- "$source" 2>/dev/null | awk '{print $1}')"
        size="${size:-0}"
    else
        size=0
    fi
    write_report_row "$email" "$class" "$enabled" "$emp" "$(employee_label "$emp")" "$source" "$size" "${MB_MATCH[$i]}" "${EXCLUSIONS[$email]:-}"
done
log "CSV report: $REPORT_FILE"

# -----------------------------------------------------------------------------
# Process / Обработка
# -----------------------------------------------------------------------------
NOW="$(date +%s)"

for i in "${!MB_EMAIL[@]}"; do
    email="${MB_EMAIL[$i]}"
    source="${MB_PATH[$i]}"
    class="${MB_CLASS[$i]}"
    emp="${MB_EMP[$i]}"

    case "$class" in
        excluded)
            clear_state "$email"
            log "EXCLUDED: $email; ${EXCLUSIONS[$email]}"
            ;;
        active)
            clear_state "$email"
            ;;
        terminated)
            first_seen="$(get_or_set_first_seen "$email" "terminated")"
            age_days=$(( (NOW - first_seen) / 86400 ))
            if (( age_days < TERMINATED_ARCHIVE_DAYS )); then
                log "WAIT: $email is terminated for ${age_days}d; threshold=${TERMINATED_ARCHIVE_DAYS}d"
            else
                archive_mailbox "$email" "$source" "AD disabled + ${EMPLOYEE_STATUS_ATTRIBUTE}=${TERMINATED_STATUS_VALUE}"
            fi
            ;;
        protected-disabled)
            clear_state "$email"
            log "REPORT: $email disabled but ${EMPLOYEE_STATUS_ATTRIBUTE}=$emp ($(employee_label "$emp")); mailbox is protected and will NOT be archived"
            ;;
        unknown-disabled)
            clear_state "$email"
            log "WARNING: $email disabled but ${EMPLOYEE_STATUS_ATTRIBUTE}='${emp:-<empty>}' is empty/unknown; mailbox will NOT be archived"
            ;;
        ambiguous-ad|alias-only)
            clear_state "$email"
            log "WARNING: $email is $class; identity is not a unique primary match; mailbox will NOT be archived"
            ;;
        missing)
            clear_state "$email"
            log "WARNING: $email mailbox exists but user is missing from AD; status cannot be verified, mailbox will NOT be archived automatically"
            ;;
    esac
done

# -----------------------------------------------------------------------------
# Retention / Срок хранения архива
# -----------------------------------------------------------------------------
if (( ARCHIVE_RETENTION_DAYS > 0 )); then
    if (( DRY_RUN == 1 )); then
        log "DRY-RUN: archive purge disabled; retention=${ARCHIVE_RETENTION_DAYS} days"
    else
        log "Checking archive directories older than ${ARCHIVE_RETENTION_DAYS} days"
        while IFS= read -r -d '' d; do
            # Enforce exactly domain/login/timestamp and an independent legacy exclusion.
            # Ровно domain/login/timestamp; независимая защита legacy-дерева.
            relative="${d#"$ARCHIVE_ROOT/"}"
            [[ "$relative" == */*/* && "${relative#*/*/}" != */* ]] || continue
            [[ "$d" != "$LEGACY_ARCHIVE_ROOT" && "$d" != "$LEGACY_ARCHIVE_ROOT/"* ]] || continue
            [[ ! -f "$d/.verified-copy.json" ]] || { log "PURGE SKIP: incomplete normal transfer $d"; continue; }
            purge_domain="${relative%%/*}"
            purge_login="${relative#*/}"; purge_login="${purge_login%%/*}"
            purge_email="$(python3 "$HELPER" canonical-email --email "$purge_login@$purge_domain")" || die "Unsafe retention email"
            [[ "$(python3 "$HELPER" exclusion-status --exclusions "$EXCLUSION_FILE" --email "$purge_email")" == 0 ]] || { log "PURGE SKIP: excluded $purge_email"; continue; }
            log "PURGE: deleting expired archive: $d"
            rm -rf --one-file-system -- "$d"
        done < <(
            find "$ARCHIVE_ROOT" -maxdepth 3 \
                -path "$LEGACY_ARCHIVE_ROOT" -prune -o \
                -type d -name '20??????T??????' \
                -mtime "+$ARCHIVE_RETENTION_DAYS" -print0
        )
    fi
fi

log "Finished successfully"
