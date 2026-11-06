# Массовое удаление / Batch deletion — core 1.4.0, console 0.4.0

RU: Добавлены выбор галочками, выбор всех показанных подходящих, общий preview,
подтверждение УДАЛИТЬ N, пакетные лимиты и итог по каждому адресу. Удаляются
почтовые файлы; AD не изменяется. Автоматические правила сохранены.
EN: Reviewed checkbox cohort, visible eligible select-all, aggregate preview,
exact-count confirmation, root limits and per-address progress. Files only, no AD change.

[BATCH_DELETE.md — Полная инструкция RU/EN](BATCH_DELETE.md)

# Новые возможности / New operations — core 1.4.0, console 0.4.0

RU: Смена/сброс пароля консоли и явное безвозвратное удаление одного unknown-disabled/missing ящика. Удаление не создаёт архив и не меняет AD. Автоматическая политика сохранена.

EN: Console password change/recovery and explicit permanent deletion of one unknown-disabled/missing mailbox. No new archive or AD change; automatic policy retained.

[OPERATIONS.md — подробная инструкция RU/EN](OPERATIONS.md)

# iRedMail + Active Directory Mailbox Lifecycle
# Жизненный цикл почтовых ящиков iRedMail + Active Directory

Version / Версия: 1.4.0 (restore and persistent exclusions; SOGo UPN mapping retained)

**RU.** В конфигурации SOGo с UPN `MailFieldNames = (userPrincipalName)`, а также `IDFieldName`, `UIDFieldName`, `IMAPLoginFieldName` и `bindFields` используют UPN. Именно `MailFieldNames` определяет источник почтового адреса SOGo; раздел `mapping` в вашем примере настраивает фотографию. Поэтому основной адрес lifecycle должен быть `userPrincipalName`, даже если AD `mail` отличается или пуст. Версия 1.1.2 ошибочно предполагала AD `mail` для вашей установки; эта рекомендация заменена. Настройки Postfix/Dovecot этим пакетом не изменяются.

**EN.** A UPN-based SOGo configuration sets `MailFieldNames = (userPrincipalName)` and uses UPN for `IDFieldName`, `UIDFieldName`, `IMAPLoginFieldName` and `bindFields`. `MailFieldNames` defines SOGo's email source; the supplied `mapping` section handles the photo. Lifecycle therefore uses `userPrincipalName` as its primary address even when AD `mail` differs or is empty. Version 1.1.2 incorrectly assumed AD `mail` for this installation; that recommendation is superseded. This package does not edit Postfix/Dovecot configuration.

**После обновления измените сохранённый конфиг / After upgrading, update the preserved configuration:**

```bash
ADDRESS_ATTRIBUTE="userPrincipalName"
ADDRESS_INCLUDE_MAIL=0
ADDRESS_INCLUDE_SMTP_ALIASES=0
LDAP_FILTER='(&(objectClass=person)(objectCategory=user))'
```

**RU.** Установщик сохраняет `/etc/iredmail-ad-lifecycle.conf`; `.conf.dist` не применяет эти настройки автоматически. Если другая установка действительно использует LDAP `mail`, выберите `ADDRESS_ATTRIBUTE="mail"`. Не меняйте AD/SOGo ради скрипта. Перед включением timer сравните выбор адреса с рабочими LDAP-настройками Postfix/Dovecot и проверьте `--normal --report`. Для вашей предоставленной схемы используйте значения выше. Пересоздайте legacy-инвентаризацию после изменения сопоставления.

**EN.** The installer preserves `/etc/iredmail-ad-lifecycle.conf`; `.conf.dist` does not apply these values automatically. If a different installation actually uses LDAP `mail`, explicitly choose `ADDRESS_ATTRIBUTE="mail"`. Do not alter AD/SOGo to fit the script. Before enabling the timer, compare the address choice with the working Postfix/Dovecot LDAP configuration and inspect `--normal --report`. Use the values above for the supplied SOGo configuration. Regenerate legacy inventory after changing mapping.

**RU.** Дополнительные источники по умолчанию выключены. `ADDRESS_INCLUDE_MAIL=1` и/или `ADDRESS_INCLUDE_SMTP_ALIASES=1` включают дополнительные совпадения только для защиты: ящик, найденный исключительно по дополнительному адресу, получает `alias-only` и никогда не архивируется штатно. Единственное основание для штатной архивации — уникальное совпадение по основному атрибуту плюс disabled + `extensionAttribute6=0`. Legacy-поиск независимо от этих переключателей всегда проверяет настроенный атрибут, UPN, `mail` и SMTP-алиасы по широкому search base, чтобы не принять существующего пользователя за удалённого.

**EN.** Extra sources default to off. `ADDRESS_INCLUDE_MAIL=1` and/or `ADDRESS_INCLUDE_SMTP_ALIASES=1` enable additional presence protection only: an extra-only identity is `alias-only` and never authorizes normal archival. Normal archival requires a unique primary-attribute match plus disabled + `extensionAttribute6=0`. Regardless of these switches, broad legacy checks always search the configured attribute, UPN, `mail` and SMTP aliases to protect existing users.

**RU.** Повтор адреса между объектами AD больше не останавливает весь отчёт. Соответствующий ящик получает `ambiguous-ad` и остаётся на месте даже если все совпавшие объекты disabled + 0. Другие однозначные ящики обрабатываются по штатной политике. Дубликаты значений основного адреса и SMTP-алиаса внутри одного объекта считаются одним совпадением. В нормальном отчёте поле `ad_match_attributes` показывает источник; отдельный `ad-address-conflicts-*.csv` содержит email, число совпадений, DN каждого объекта, disabled, кадровый статус и источники. Без конфликтов CSV содержит только заголовок. Ошибки LDAP, referrals и повреждённая выдача по-прежнему блокируют обработку. Старое состояние ожидания увольнения для неоднозначного ящика сбрасывается при обычном запуске; `--report` не меняет state-файлы.

**EN.** Cross-object address conflicts no longer abort the entire report. The affected mailbox becomes `ambiguous-ad` and remains untouched even if every matching account is disabled + 0. Other unique mailboxes follow normal policy. Repeated primary/SMTP values within one object form one match. The normal report includes `ad_match_attributes`; separate `ad-address-conflicts-*.csv` lists email, match count, each object's DN, disabled flag, employee status and matching sources. Without conflicts, it contains a header only. LDAP errors, referrals and malformed results still fail closed. A normal run resets old termination waiting state for ambiguous mailboxes; `--report` does not change state files.

**RU.** `http/...` UPN не подходит для имени каталога ящика и пропускается с AD NOTICE; это не доказывает отсутствие учётной записи. Кириллические UPN поддерживаются. `EXPECTED_MIN_USERS` контролирует число объектов AD, а не адресов/алиасов.

**EN.** An `http/...` UPN cannot identify a mailbox directory and is skipped with AD NOTICE; it is not evidence of an absent account. Cyrillic UPNs are supported. `EXPECTED_MIN_USERS` counts AD objects, not addresses/aliases.

Чтобы выяснить, где встречается portal / To inspect portal's directory sources without editing AD:

```bash
source /etc/iredmail-ad-lifecycle.conf
ldapsearch -LLL -x -H "$LDAP_URI" "${LDAP_EXTRA_ARGS[@]}" \
  -D "$BIND_DN" -y "$BIND_PW_FILE" -b "$LEGACY_SEARCH_BASE" \
  -e '!1.2.840.113556.1.4.1339' -E "!pr=${LDAP_PAGE_SIZE:-1000}/noprompt" \
  '(|(userPrincipalName=portal@example.org)(mail=portal@example.org)(proxyAddresses=smtp:portal@example.org))' \
  dn userPrincipalName mail proxyAddresses userAccountControl extensionAttribute6
```

**Исправление 1.1.1:** кириллические UPN, включая `администратор@example.org`, обрабатываются в AD-выдаче. Для Unicode-имён каталогов и AD используется одна проверка и одинаковое приведение к нижнему регистру независимо от locale Bash. Небезопасные разделители путей и управляющие символы запрещены. Старые CSV-инвентаризации схемы 1.1 совместимы. ASCII state-файлы сохраняют имена, Unicode/`+` используют SHA256-ключ для исключения коллизий.

**Fix 1.1.1:** Cyrillic UPNs, including `администратор@example.org`, are accepted in AD output. Unicode directory names and AD share validation and lowercase conversion independent of Bash locale. Unsafe path delimiters and control characters remain forbidden. Existing schema 1.1 CSV inventories remain compatible. ASCII state filenames remain unchanged; Unicode/`+` use SHA256 keys to avoid collisions.

**Обновление 1.1:** отдельная инвентаризация и явное одобрение исторических missing-ящиков. Полная инструкция для шестилетнего накопления — раздел 18 ниже. Автоматическая штатная политика сохраняется.

**Update 1.1:** separate inventory and explicit approval for historical missing mailboxes. See section 18 below for the six-year backlog workflow. Normal automatic policy is preserved.

---

## 1. Purpose / Назначение

This package manages iRedMail mailbox archival using Active Directory account state plus the HR status stored in `extensionAttribute6`.

Комплект управляет архивацией почтовых ящиков iRedMail на основании состояния учётной записи Active Directory и кадрового статуса в `extensionAttribute6`.

**Only one condition permits automatic archive / Только одно условие разрешает автоматическую архивацию:**

```text
Unique match by ADDRESS_ATTRIBUTE / Однозначное совпадение основного атрибута
AND
AD account disabled (ACCOUNTDISABLE bit 0x2)
AND
extensionAttribute6 = 0
```

In normal mode everything else is report-only and the mailbox stays untouched. Explicit legacy commands follow the separate approval policy in section 18.

В штатном режиме во всех остальных случаях скрипт только пишет информацию в журнал/CSV и не трогает почтовый ящик. Явные legacy-команды используют отдельное одобрение из раздела 18.

---

## 2. Employee statuses / Кадровые статусы

| extensionAttribute6 | Русский | English |
|---:|---|---|
| 0 | Уволен(а) | Terminated |
| 1 | Работает | Working |
| 2 | Отпуск по уходу за ребёнком | Childcare leave |
| 3 | Отпуск | Vacation |
| 4 | Больничный | Sick leave |
| 5 | Командировка | Business trip |
| 6 | Отсутствует | Absent |
| 7 | Отпуск по беременности и родам | Maternity leave |
| 8 | Трудоустройство | Employment onboarding |

### Final mailbox classification / Итоговая классификация

| Classification | Rule / Условие | Action / Действие |
|---|---|---|
| `active` | AD account enabled / УЗ активна | Never archive / Не архивировать |
| `terminated` | Unique primary match + AD disabled + status `0` / Уникальный основной адрес | Eligible for archive / Можно архивировать |
| `protected-disabled` | AD disabled + status `1..8` | Never archive / Не архивировать |
| `ambiguous-ad` | Address matches multiple AD records / Адрес у нескольких объектов | Protected / Сохранять |
| `alias-only` | Extra source only / Только дополнительный источник | Protected / Сохранять |
| `unknown-disabled` | AD disabled + empty/unknown status | Warning only / Только WARNING |
| `missing` | Mailbox exists but AD object is missing | Warning only / Только WARNING |

`missing` is deliberately NOT archived automatically because after an AD object is deleted its `extensionAttribute6` can no longer be verified.

`missing` намеренно НЕ архивируется автоматически: после удаления объекта AD невозможно достоверно проверить его `extensionAttribute6`.

---

## 3. Package contents / Состав комплекта

```text
iredmail-ad-lifecycle/
├── iredmail-ad-lifecycle.sh          main program / основной скрипт
├── iredmail-ad-lifecycle.conf        bilingual configuration / двуязычный конфиг
├── legacy.py                        isolated legacy workflow + LDIF parser / legacy и LDIF
├── install.sh                        installer / установщик
├── uninstall.sh                      safe uninstaller / безопасное удаление
├── preflight.sh                      LDAP/storage preflight / предварительная проверка
├── postfix-query-filter.example      optional Postfix recipient filter / пример фильтра Postfix
├── README.md                         this manual / эта инструкция
├── CHANGELOG.md                      changes / изменения
├── legacy-approval.example.csv       approval template, NO by default / шаблон одобрения
├── legacy-approval.example.txt       commented allowlist template / шаблон списка
├── tests/                            reproducible safety tests / тесты защит
└── systemd/
    ├── iredmail-ad-lifecycle.service
    └── iredmail-ad-lifecycle.timer
```

---

## 4. Requirements / Требования

For Debian/Ubuntu:

```bash
apt update
apt install ldap-utils rsync util-linux python3 coreutils
```

Required commands / Необходимые команды:

```text
ldapsearch
ldapwhoami
rsync
flock
findmnt
find
du
awk
python3 (3.8+; standard library only / только стандартная библиотека)
realpath
tee
```

Expected mailbox layout / Ожидаемая структура:

```text
/var/vmail/vmail1/example.org/<login>/Maildir/
```

Current LDAP design / Текущая LDAP-схема:

```text
ldap://example.org:389 + STARTTLS (-ZZ)
BASE_DN=OU=Users,DC=example,DC=org
```

---

## 5. Installation / Установка

Extract the package:

```bash
tar xzf iredmail-ad-lifecycle-full.tar.gz
cd iredmail-ad-lifecycle
```

or unzip the ZIP and enter the directory.

Или распакуйте ZIP и перейдите в каталог.

Run:

```bash
chmod +x install.sh uninstall.sh preflight.sh
sudo ./install.sh
```

Installed files / Устанавливаемые файлы:

```text
/usr/local/sbin/iredmail-ad-lifecycle
/usr/local/lib/iredmail-ad-lifecycle/legacy.py
/etc/iredmail-ad-lifecycle.conf
/etc/systemd/system/iredmail-ad-lifecycle.service
/etc/systemd/system/iredmail-ad-lifecycle.timer
```

The installer does **not** enable the timer automatically.

Установщик **не включает** timer автоматически.

If `/etc/iredmail-ad-lifecycle.conf` already exists, it is preserved; the new template is written as:

Если конфиг уже существует, он не перезаписывается; новый шаблон сохраняется как:

```text
/etc/iredmail-ad-lifecycle.conf.dist
```

---

## 6. LDAP password / Пароль LDAP

Create the password file without placing the password into shell history:

Создайте файл пароля без сохранения пароля в истории shell:

```bash
read -rsp 'LDAP password: ' LDAP_PW
printf '\n'
printf '%s' "$LDAP_PW" > /root/.ldap-vmail.pass
unset LDAP_PW
chmod 600 /root/.ldap-vmail.pass
```

Check:

```bash
ls -l /root/.ldap-vmail.pass
```

Expected / Ожидается:

```text
-rw------- 1 root root ...
```

---

## 7. Preflight / Предварительная проверка

Before the first run:

Перед первым запуском:

```bash
./preflight.sh /etc/iredmail-ad-lifecycle.conf
```

The preflight checks:

Предварительная проверка проверяет:

1. LDAP bind / LDAP-аутентификацию.
2. A sample LDAP search / пример LDAP-поиска.
3. VMAIL filesystem / файловую систему почты.
4. Archive filesystem / файловую систему архива.

Manual bind test / Ручная проверка bind:

```bash
ldapwhoami -x \
  -H 'ldap://example.org:389' \
  -ZZ \
  -D 'svc-lifecycle@example.org' \
  -y /root/.ldap-vmail.pass
```

Manual search / Ручной поиск:

```bash
ldapsearch -LLL -x \
  -H 'ldap://example.org:389' \
  -ZZ \
  -D 'svc-lifecycle@example.org' \
  -y /root/.ldap-vmail.pass \
  -b 'OU=Users,DC=example,DC=org' \
  '(&(objectClass=person)(objectCategory=user))' \
  mail proxyAddresses userPrincipalName userAccountControl extensionAttribute6 | head -60
```

---

## 8. Safe first run / Безопасный первый запуск

The configuration ships with:

Конфиг поставляется с:

```text
DRY_RUN=1
```

Run the explicit safe report mode:

Запустите принудительно безопасный режим:

```bash
/usr/local/sbin/iredmail-ad-lifecycle --report
```

`--report` forces report-only behavior even if somebody accidentally set `DRY_RUN=0` in the config.

`--report` принудительно запрещает изменения даже если в конфиге случайно стоит `DRY_RUN=0`.

Review:

```bash
tail -100 /var/log/iredmail-ad-lifecycle.log
```

CSV reports:

```bash
ls -lh /var/log/iredmail-ad-lifecycle-reports/
```

Typical output:

```text
Mailboxes: total=1014 active=... terminated=... protected-disabled=... unknown-disabled=... missing=...

DRY-RUN: would archive fired.user@example.org reason=AD disabled + extensionAttribute6=0 ...

REPORT: parent.leave@example.org disabled but extensionAttribute6=2 (...) mailbox is protected and will NOT be archived

WARNING: old.user@example.org mailbox exists but user is missing from AD; status cannot be verified, mailbox will NOT be archived automatically
```

Do not enable live mode until every `DRY-RUN: would archive` user has been checked.

Не включайте рабочий режим, пока не проверены все пользователи из `DRY-RUN: would archive`.

---

## 9. Live mode / Рабочий режим

Edit:

```bash
nano /etc/iredmail-ad-lifecycle.conf
```

Change:

```text
DRY_RUN=0
```

Before the first live run, check the archive filesystem:

Перед первым рабочим запуском проверьте файловые системы:

```bash
findmnt /var/vmail/vmail1
findmnt /mnt/mail-archive/iredmail

df -hT /var/vmail/vmail1 /mnt/mail-archive/iredmail
```

Recommended / Рекомендуется:

```text
REQUIRE_SEPARATE_ARCHIVE_FS=1
```

Then run manually:

```bash
/usr/local/sbin/iredmail-ad-lifecycle
```

Watch:

```bash
tail -f /var/log/iredmail-ad-lifecycle.log
```

Only after a successful manual run enable the timer:

Только после успешного ручного запуска включайте timer:

```bash
systemctl enable --now iredmail-ad-lifecycle.timer
systemctl list-timers | grep iredmail-ad-lifecycle
```

---

## 10. Archive algorithm / Алгоритм архивации

For a `terminated` mailbox:

Для ящика `terminated`:

1. The script performs `rsync -aHAX --numeric-ids`.
2. It performs a second rsync pass to reduce the change window.
3. Destination/source byte ratio is checked.
4. Regular-file count is checked.
5. Only after successful verification is the original directory removed.
6. The archive is stored under:

```text
ARCHIVE_ROOT/domain/login/YYYYMMDDTHHMMSS/
```

Example:

```text
/mnt/mail-archive/iredmail/example.org/ivanov/20261001T120000/
```

If rsync or verification fails, the source mailbox is not removed.

Если rsync или проверка не прошли, исходный ящик не удаляется.

### Important delivery warning / Важное предупреждение о доставке

Dovecot account disablement does not necessarily mean Postfix stops accepting new mail. Before live archival, configure recipient lookup so a terminated user (`disabled + extensionAttribute6=0`) is no longer accepted as a local mailbox.

Отключение УЗ в Dovecot не обязательно прекращает приём новых писем Postfix. Перед рабочей архивацией желательно исключить именно уволенных (`disabled + extensionAttribute6=0`) из recipient lookup.

See / См.:

```text
postfix-query-filter.example
```

The example intentionally continues accepting mail for disabled employees with status `1..8`, including childcare leave.

Пример намеренно продолжает принимать почту для отключённых сотрудников со статусами `1..8`, включая отпуск по уходу за ребёнком.

---

## 11. Configuration reference / Полное описание параметров конфигурации

The configuration file itself contains bilingual comments for every setting:

Сам конфигурационный файл содержит двуязычные комментарии для каждого параметра:

```text
/etc/iredmail-ad-lifecycle.conf
```

Summary / Краткий справочник:

| Parameter | Русское описание | English description |
|---|---|---|
| `LDAP_URI` | URI LDAP/AD | LDAP/AD server URI |
| `BIND_DN` | сервисная УЗ для чтения AD | service account used to read AD |
| `BIND_PW_FILE` | файл пароля сервисной УЗ | bind password file |
| `BASE_DN` | база LDAP-поиска | LDAP search base |
| `ADDRESS_ATTRIBUTE` | основной LDAP-атрибут адреса; для предоставленного SOGo `userPrincipalName` | primary LDAP address attribute; supplied SOGo uses `userPrincipalName` |
| `ADDRESS_INCLUDE_MAIL` | 0 по умолчанию; 1 добавляет защитные совпадения AD mail | default 0; 1 enables extra AD mail presence protection |
| `ADDRESS_INCLUDE_SMTP_ALIASES` | 0 по умолчанию; 1 добавляет защитные SMTP-совпадения | default 0; 1 enables extra SMTP presence protection |
| `EMPLOYEE_STATUS_ATTRIBUTE` | кадровый атрибут | employee status attribute |
| `TERMINATED_STATUS_VALUE` | значение «уволен»; в этой политике только `0` | terminated value; this policy permits only `0` |
| `LDAP_FILTER` | фильтр LDAP-пользователей | LDAP user filter |
| `LDAP_EXTRA_ARGS` | дополнительные параметры ldapsearch, здесь `-ZZ` | extra ldapsearch arguments, here `-ZZ` |
| `LDAP_PAGE_SIZE` | размер LDAP page | LDAP paging size |
| `VMAIL_ROOT` | корень почтового хранилища | mailbox root |
| `MAIL_DOMAINS` | обрабатываемые домены | processed mail domains |
| `ARCHIVE_ROOT` | корень архива | archive root |
| `REQUIRE_SEPARATE_ARCHIVE_FS` | требовать отдельную FS для архива | require separate archive filesystem |
| `DRY_RUN` | тестовый/рабочий режим | dry-run/live mode |
| `TERMINATED_ARCHIVE_DAYS` | задержка до архивации уволенного | wait before archiving terminated user |
| `ARCHIVE_RETENTION_DAYS` | срок хранения архива | archive retention period |
| `EXPECTED_MIN_USERS` | минимум объектов пользователей AD, а не адресов/алиасов | minimum AD user-object count, not addresses/aliases |
| `MAX_TERMINATED_PERCENT` | максимум % ящиков, которые могут одновременно стать terminated | max percentage simultaneously classified terminated |
| `VERIFY_MIN_PERCENT` | минимальное отношение размера копии к исходнику | minimum destination/source byte ratio |
| `REPORT_MAILBOX_SIZES` | считать размеры всех ящиков в CSV | calculate every mailbox size for CSV |
| `STATE_DIR` | состояние первого обнаружения terminated | first-seen state directory |
| `LOG_FILE` | основной журнал | main log |
| `REPORT_DIR` | каталог CSV-отчётов | CSV report directory |
| `LOCK_FILE` | защита от параллельного запуска | concurrent-run lock |

### Recommended production values / Рекомендуемые рабочие значения

For the current environment:

Для текущей среды:

```text
EXPECTED_MIN_USERS=700
MAX_TERMINATED_PERCENT=10
REQUIRE_SEPARATE_ARCHIVE_FS=1
TERMINATED_ARCHIVE_DAYS=0
```

For initial rollout consider:

Для первичного внедрения имеет смысл временно:

```text
ARCHIVE_RETENTION_DAYS=0
```

After restore procedures are tested, set the required retention, for example:

После проверки восстановления задайте нужный срок, например:

```text
ARCHIVE_RETENTION_DAYS=90
```

---

## 12. Retention / Удаление старых архивов

When:

```text
ARCHIVE_RETENTION_DAYS=90
```

archive timestamp directories older than the configured retention are permanently removed during a live run.

Архивные каталоги старше указанного срока безвозвратно удаляются во время рабочего запуска.

To disable automatic purge:

Чтобы отключить автоматическое удаление:

```text
ARCHIVE_RETENTION_DAYS=0
```

---

## 13. Restore and exclusions / Восстановление и исключения

RU: Версия 1.2.0 использует явную проверку восстановления и общий lifecycle flock; старый ручной rsync в существующий ящик заменён защищённой процедурой. EN: use the explicit preview/restore workflow under the shared lock; existing destinations are never merged/overwritten.

```bash
# Inventory archives; read the CSV and copy one archive_id (64 hex chars).
# Список архивов: возьмите один archive_id из CSV.
iredmail-ad-lifecycle --archive-list --output /root/archives.csv
iredmail-ad-lifecycle --restore-preview --archive-id ARCHIVE_ID --output /root/restore-preview.json
# Safe no-copy validation / Проверка без копирования:
iredmail-ad-lifecycle --restore --archive-id ARCHIVE_ID --preview /root/restore-preview.json --output /root/restore-result.json --report
# After stopping writers and setting DRY_RUN=0 / После остановки записи и DRY_RUN=0:
iredmail-ad-lifecycle --restore --archive-id ARCHIVE_ID --preview /root/restore-preview.json --output /root/restore-result.json --writers-quiesced
```

RU: Архив сохраняется; восстановленный адрес автоматически получает постоянное исключение. Восстанавливаются только файлы в исходный путь: AD, SQL, доступ SOGo и доставка не меняются. Повторная проверка SHA256/метаданных и rsync checksum обязательны; целевой каталог публикуется атомарно без перезаписи. У старого normal-архива без marker полнота неизвестна, это указано в CSV; незавершённый legacy/marked-архив запрещён. При ошибке сохраняются архив и staging `<domain>/.lifecycle-staging/<uuid>/payload`; если публикация уже прошла, проверьте исходный путь перед повтором. Права/владельцы/ACL/xattr копируются из архива; проверьте их после восстановления. Остановка timer необязательна для взаимного исключения: flock общий; остановка писателей обязательна для live.

EN: Archive stays intact; the restored address is automatically protected. Files only are restored to their original path, with no AD/SQL/SOGo/delivery changes. Preview binds checksums/metadata; rsync checksum verification and atomic non-overwrite publication are required. Historical unmarked normal archives have unknown original completeness. Incomplete legacy/marked copies are blocked. Failures keep staging and archive; inspect the target after any interruption before retrying. Ownership/ACL/xattrs are copied; validate them afterwards. Shared flock excludes timer races but does not replace writer quiescence.

```bash
iredmail-ad-lifecycle --exclude-add --email ivanov@example.org --reason 'Сохранить переписку' --output /root/exclusions.csv
iredmail-ad-lifecycle --exclusions-list --output /root/exclusions-current.csv
iredmail-ad-lifecycle --exclude-remove --email ivanov@example.org --output /root/exclusions-after.csv
```

RU: EXCLUSION_FILE по умолчанию `$STATE_DIR/exclusions.json`; отдельный root-owned JSON обновляется только под flock. Исключение действует для normal, legacy allowlist/archive-all и retention обоих архивов, в том числе старых одобрений. Адрес виден в отчётах (`excluded` и причина), missing inventory не допускает его к переносу (`eligible=no`, `reason=excluded`). Удаление исключения возвращает обычную политику AD/retention. EXCLUSION_FILE отсутствует при первом обновлении — список пуст; повреждённый файл блокирует обработку. `--report` не изменяет исключения; явные exclude-add/remove применяются даже при DRY_RUN=1, поскольку не переносят данные. Для CSV указывайте новое имя; snapshots не перезаписываются.

EN: EXCLUSION_FILE defaults to STATE_DIR/exclusions.json, a root-owned JSON changed under flock only. Protection covers normal/legacy archive and both retention flows, including existing approvals. Reports retain the address as excluded; missing inventory marks it ineligible. Removing protection resumes normal policy. Absent file means an initial empty list; corrupt policy blocks processing. --report does not alter protection; explicit add/remove work with DRY_RUN=1 since no mailbox is transferred. Use a new CSV filename for each snapshot.

---

## 14. Safety mechanisms / Защитные механизмы

### LDAP minimum

```text
EXPECTED_MIN_USERS=700
```

If LDAP returns fewer objects, no mailboxes are changed.

Если LDAP вернул меньше объектов, почта не изменяется.

### Mass-termination guard

```text
MAX_TERMINATED_PERCENT=10
```

If too many mailboxes suddenly qualify as `terminated`, the script stops.

Если слишком много ящиков внезапно классифицированы как `terminated`, скрипт останавливается.

### Unknown disabled

Disabled account + empty/unknown `extensionAttribute6` is never archived.

Отключённая УЗ + пустой/неизвестный `extensionAttribute6` никогда не архивируется автоматически.

### Missing AD object

A mailbox whose AD object is gone is never automatically archived.

Ящик, объект которого уже удалён из AD, автоматически не архивируется.

### Lock

`flock` prevents two copies from running simultaneously.

`flock` предотвращает параллельный запуск двух экземпляров.

---

## 15. systemd

Manual run:

```bash
systemctl start iredmail-ad-lifecycle.service
```

Status:

```bash
systemctl status iredmail-ad-lifecycle.service
```

Timer:

```bash
systemctl status iredmail-ad-lifecycle.timer
systemctl list-timers iredmail-ad-lifecycle.timer
```

Journal:

```bash
journalctl -u iredmail-ad-lifecycle.service
```

Application log:

```bash
tail -f /var/log/iredmail-ad-lifecycle.log
```

Default timer schedule:

Расписание по умолчанию:

```text
03:20 daily + RandomizedDelaySec=300
```

---

## 16. Troubleshooting / Диагностика

### Invalid credentials (49)

```bash
ldapwhoami -x \
  -H 'ldap://example.org:389' \
  -ZZ \
  -D 'svc-lifecycle@example.org' \
  -y /root/.ldap-vmail.pass
```

### Can't contact LDAP server

```bash
getent hosts example.org
openssl s_client -starttls ldap -connect example.org:389
```

### Check one user's actual attributes / Проверить атрибуты одного пользователя

```bash
ldapsearch -LLL -x \
  -H 'ldap://example.org:389' \
  -ZZ \
  -D 'svc-lifecycle@example.org' \
  -y /root/.ldap-vmail.pass \
  -b 'OU=Users,DC=example,DC=org' \
  '(|(mail=user@example.org)(proxyAddresses=smtp:user@example.org)(userPrincipalName=user@example.org))' \
  dn mail proxyAddresses userPrincipalName userAccountControl extensionAttribute6
```

### User on childcare leave / Сотрудник в отпуске по уходу

Expected:

```text
ACCOUNTDISABLE bit = set
extensionAttribute6: 2
```

Result:

```text
protected-disabled
```

No archive occurs.

Архивации не будет.

### Mailbox unexpectedly classified `missing`

Search from a wider base:

Если ящик неожиданно `missing`, попробуйте поиск от корня домена:

```bash
ldapsearch -LLL -x \
  -H 'ldap://example.org:389' \
  -ZZ \
  -D 'svc-lifecycle@example.org' \
  -y /root/.ldap-vmail.pass \
  -b 'DC=example,DC=org' \
  '(|(mail=user@example.org)(proxyAddresses=smtp:user@example.org)(userPrincipalName=user@example.org))' \
  dn mail proxyAddresses userPrincipalName userAccountControl extensionAttribute6
```

If found outside the configured `BASE_DN`, adjust the search base only after verifying the AD OU structure.

Если объект находится за пределами `BASE_DN`, расширяйте базу только после проверки структуры OU.

---

## 17. Uninstall / Удаление

Run:

```bash
sudo ./uninstall.sh
```

The uninstaller removes the executable and systemd units but deliberately keeps:

Скрипт удаления удаляет программу и systemd units, но намеренно сохраняет:

```text
/etc/iredmail-ad-lifecycle.conf
/root/.ldap-vmail.pass
/var/lib/iredmail-ad-lifecycle/
/var/log/iredmail-ad-lifecycle.log
/var/log/iredmail-ad-lifecycle-reports/
ARCHIVE_ROOT contents / содержимое ARCHIVE_ROOT
```

This prevents an uninstall command from deleting mailbox/archive data.

Это исключает удаление почтовых данных одной командой uninstall.

---

## 18. Six-year legacy backlog / Очистка ящиков, накопившихся за 6 лет

### 18.1 What happens to old missing mailboxes / Что происходит со старыми missing

**RU.** Наличие ящика шесть лет не означает шесть лет отсутствия активности. Скрипт не устанавливает дату удаления объекта из AD и не восстанавливает `extensionAttribute6`. В обычном запуске (`--normal`, в том числе timer) все `missing` остаются на месте. Отдельный legacy-процесс формирует список ящиков, которых нет в основном `BASE_DN`, затем проверяет корень домена AD и файловую активность. Ящики с найденным в другом OU пользователем или адресом в `mail`/SMTP `proxyAddresses` защищены. Это также помогает при различии UPN и почтового адреса.

**EN.** Six years of mailbox existence is not six years of inactivity. The script cannot infer when an AD object was deleted or recover `extensionAttribute6`. A normal run (`--normal`, including the timer) leaves every `missing` mailbox in place. The separate legacy workflow inventories mailboxes missing from the primary `BASE_DN`, then checks the domain-wide AD base and filesystem activity. A user/address found in another OU, `mail`, or SMTP `proxyAddresses` protects the mailbox, including cases where UPN differs from the mail address.

**RU.** Поиск использует ту же LDAP-службу и TLS, но не основной OU/HR-фильтр. Для широкой базы нужен доступ bind-УЗ ко всем нужным объектам. Отсутствие результата при недостаточных правах чтения нельзя считать доказательством удаления. Несколько доменов AD/лесов не обходятся автоматически: `LEGACY_SEARCH_BASE` должен охватывать нужную область выбранного LDAP-сервера. При недоступности LDAP, неполной выдаче, referrals, ошибке paging или недостаточном числе объектов выполнение прекращается. Referrals не обходятся автоматически; исправьте базу/сервер, чтобы выдача была полной.

**EN.** Searches use the same LDAP service and TLS but omit the primary OU/HR filter. Bind permissions must cover all relevant objects. An empty result with inadequate read permissions does not prove deletion. Multiple AD domains/forests are not traversed automatically: configure `LEGACY_SEARCH_BASE` to cover the relevant scope of the selected LDAP server. LDAP outages, incomplete output, referrals, paging failures, and insufficient object counts stop processing. Referrals are not followed automatically; fix the base/server for complete results.

**RU.** Legacy-запросы используют критический AD domain-scope control `1.2.840.113556.1.4.1339`: он охватывает все OU одного naming context и предотвращает referrals в DNS/другие разделы. Это поиск внутри выбранного AD-домена, а не по дочерним доменам или всему лесу. Неподдерживаемый control блокирует выполнение. [Описание Microsoft](https://learn.microsoft.com/en-us/previous-versions/windows/desktop/ldap/ldap-server-domain-scope-oid).

**EN.** Legacy searches use critical AD domain-scope control `1.2.840.113556.1.4.1339` to search all OUs of one naming context without DNS/other-partition referrals. Scope is the selected AD domain, not child domains or the entire forest. Unsupported controls stop processing. [Microsoft documentation](https://learn.microsoft.com/en-us/previous-versions/windows/desktop/ldap/ldap-server-domain-scope-oid).

### 18.2 Upgrade and safe defaults / Обновление и безопасные значения

**RU.** Сначала остановите timer и дождитесь завершения уже запущенного service. Распакуйте новый архив и выполните `./install.sh` от root. Установщик сохраняет текущий конфиг, новый пишет в `.conf.dist`; перенесите новые параметры из шаблона в основной конфиг. Он также устанавливает Python helper. Запуск 1.1 со старым конфигом по умолчанию оставляет `LEGACY_MODE=0`. Старые state-файлы и архивы сохраняются. Предварительно проверьте доступность Python 3 и свободное место в архиве.

**EN.** Stop the timer and wait for any running service to finish. Extract the new archive and run `./install.sh` as root. The installer keeps the current config and writes the new template to `.conf.dist`; merge its new parameters into your active config. It also installs the Python helper. Version 1.1 defaults to `LEGACY_MODE=0` with an old config. Existing state and archives are preserved. Check Python 3 availability and archive free space first.

```bash
systemctl stop iredmail-ad-lifecycle.timer
systemctl status iredmail-ad-lifecycle.service
# Wait for an active run to finish / Дождитесь завершения активного запуска
chmod +x install.sh uninstall.sh preflight.sh
./install.sh
```

Starting settings / Начальные настройки:

```bash
DRY_RUN=1
LEGACY_MODE=0
LEGACY_SEARCH_BASE="DC=example,DC=org"
LEGACY_MIN_INACTIVE_DAYS=365
LEGACY_APPROVAL_FILE=""
LEGACY_INVENTORY_FILE=""
LEGACY_ARCHIVE_ROOT="/mnt/mail-archive/iredmail-legacy"
LEGACY_RETENTION_DAYS=0
LEGACY_MAX_COUNT=20
LEGACY_MAX_PERCENT=5
```

**RU.** Число 365 — выбранный консервативный порог, а не факт вашей кадровой политики. Выберите его по своей политике хранения. Оба лимита действуют одновременно. Например, при 1014 обнаруженных ящиках можно одобрить не более 20 за запуск; 403 исторических missing не переместятся одной командой с этими значениями. По мере уменьшения числа рабочих ящиков допустимый процент рассчитывается заново. Превышение лимита останавливает весь пакет, скрипт не обрезает его до первых N.

**EN.** 365 is a conservative starting threshold, not an asserted HR policy. Choose it according to your retention policy. Both batch limits apply together. With 1014 discovered mailboxes, at most 20 may be approved per run; a backlog of 403 missing mailboxes will not move in one command with these values. The percentage is recalculated as the working mailbox count falls. An exceeded limit aborts the entire batch; the script never silently truncates it to the first N.

### 18.3 Step 1: inventory only / Шаг 1: только инвентаризация

```bash
./preflight.sh /etc/iredmail-ad-lifecycle.conf
mkdir -p /root/legacy-review
chmod 700 /root/legacy-review
/usr/local/sbin/iredmail-ad-lifecycle --legacy-inventory \
  --inventory /root/legacy-review/missing-01.csv
```

**RU.** Эта команда никогда не архивирует и не удаляет, даже при `DRY_RUN=0`. Она создаёт только CSV и служебный журнал/lock. Нужен новый путь для каждого снимка; существующая инвентаризация не перезаписывается. Штатные state-файлы не изменяются. Размеры и возраст считаются для всех primary-missing независимо от `REPORT_MAILBOX_SIZES`. Обход большого хранилища может занять время и создать дисковую нагрузку.

**EN.** This command never archives or purges, even with `DRY_RUN=0`. It writes only the CSV and operational log/lock. Use a new path for each snapshot; existing inventories are never overwritten. Normal state files are unchanged. Size and age are calculated for every primary-missing mailbox regardless of `REPORT_MAILBOX_SIZES`. Scanning a large store can take time and cause disk I/O.

CSV fields / Поля CSV:

| Field | Русский | English |
|---|---|---|
| `email` | адрес из domain/login/Maildir | address derived from domain/login/Maildir |
| `size_bytes` | сумма логических размеров обычных файлов; не размер выделенных блоков | sum of regular-file logical sizes; not allocated disk blocks |
| `mailbox_path` | каталог пользователя, включая Maildir и другие его файлы | whole user directory, including Maildir and other files |
| `latest_mtime_epoch`, `latest_mtime_utc` | максимальный mtime файлов и каталогов в UTC | maximum file/directory mtime, UTC |
| `inactive_days` | возраст этой файловой активности в днях | age of this filesystem activity in days |
| `primary_status` | `missing` в основном BASE_DN | `missing` within primary BASE_DN |
| `broad_ad_status` | `absent` либо `present` в широкой базе | `absent` or `present` in broad base |
| `eligible`, `reason` | пригодность по AD и возрасту; это ещё не одобрение | AD/age eligibility; this is not approval |
| `schema_version`, `inventory_id`, `generated_at_epoch` | версия и идентификатор снимка | snapshot schema, ID, generation time |
| `source_dev`, `source_ino` | идентификатор исходного каталога для обнаружения замены | source identity used to detect replacement |

**RU.** Latest mtime — наблюдаемая файловая активность. Чтение письма может не менять mtime, восстановление из резервной копии может сохранять старые даты. Скрипт не объявляет ящик «уволенным» по этим датам. Mtime каталогов учитывается для пустых ящиков и событий удаления/переименования файлов. Любая свежая/будущая дата защищает ящик. Символические ссылки, специальные файлы и вложенные файловые системы блокируют небезопасный обход. Поддерживается исходная плоская структура `VMAIL_ROOT/domain/login/Maildir`; хешированную структуру iRedMail этот пакет не угадывает.

**EN.** Latest mtime is observed filesystem activity. Reading mail may not update it; backup restores may preserve old dates. These dates do not classify a mailbox as terminated. Directory mtimes cover empty mailboxes and file deletion/rename events. Any recent/future timestamp protects a mailbox. Symlinks, special files and nested filesystems block unsafe traversal. The original flat `VMAIL_ROOT/domain/login/Maildir` layout is supported; hashed iRedMail layouts are not guessed.

### 18.4 Step 2: explicit small allowlist / Шаг 2: явный небольшой список

**RU.** Проверьте каждую строку по кадровому/операционному учёту, назначению ящика и нужности старой переписки. Из `eligible=yes` выберите небольшой пакет. Создайте отдельный approval CSV: обязательны заголовки `email,approved`, только точное `YES` разрешает перенос; `NO`/пусто не разрешают. Сам файл инвентаризации нельзя использовать вместо approval CSV. Пример ниже содержит условный адрес: замените его фактически проверенным.

**EN.** Review each row against HR/operational records, mailbox purpose and correspondence retention needs. Choose a small batch from `eligible=yes`. Create a separate approval CSV with required `email,approved` headers; only `YES` permits archival, while `NO`/empty does not. Inventory CSV cannot be used as approval CSV. Replace the placeholder below with an actually reviewed email.

```csv
email,approved
reviewed.departed.user@example.org,YES
```

Or TXT, one approved email per line; `#` starts a whole-line comment / Или TXT: один одобренный адрес на строку, `#` — комментарий отдельной строкой:

```text
# Explicitly reviewed / Явно проверен
reviewed.departed.user@example.org
```

```bash
chmod 600 /root/legacy-review/approved-01.csv
/usr/local/sbin/iredmail-ad-lifecycle --legacy-archive \
  --inventory /root/legacy-review/missing-01.csv \
  --approval /root/legacy-review/approved-01.csv --report
```

**RU.** Неодобренные ящики сохраняются. Адрес вне снимка, неeligible-строка, пустой список или неверный CSV останавливает операцию. Перед любым переносом пересчитываются состав ящиков, лимиты, возраст и широкая AD-выдача. Перед каждым ящиком повторяется отдельный поиск в `LEGACY_SEARCH_BASE`; найденный пользователь пропускается. Размер/mtime и dev/inode должны совпадать со снимком: если после инвентаризации что-то изменилось, подготовьте новую инвентаризацию и перепроверьте одобрение.

**EN.** Unapproved mailboxes remain. An email outside the snapshot, an ineligible row, empty approval, or invalid CSV aborts the operation. Before any moves, mailbox discovery, batch limits, age and broad AD results are refreshed. Every mailbox gets an additional individual search in `LEGACY_SEARCH_BASE`; a found user is skipped. Size/mtime and device/inode must still match the snapshot; if the mailbox changed, generate a fresh inventory and review approval again.

### 18.5 Step 3: live archive in maintenance / Шаг 3: перенос в окно обслуживания

**RU.** Для гарантии согласованной копии прекратите запись в выбранные ящики: SMTP/LMTP доставку, очереди, IMAP/POP-доступ, локальные обработчики и прочие процессы записи. Можно остановить соответствующие службы в согласованное окно или применить проверенную изоляцию выбранных получателей. Один `flock` блокирует только другой lifecycle-скрипт; он не блокирует Postfix/Dovecot. Флаг `--writers-quiesced` фиксирует, что оператор обеспечил это условие; скрипт не останавливает службы сам. Если остановка служб изменила mtime, сначала создайте новый снимок. Установите `DRY_RUN=0` в конфиге и запустите одобренный пакет:

**EN.** For a consistent copy, stop writers for selected mailboxes: SMTP/LMTP delivery, queues, IMAP/POP access, local processors and other writers. Stop the relevant services in a maintenance window or use verified per-recipient isolation. `flock` excludes another lifecycle script only; it does not lock Postfix/Dovecot. `--writers-quiesced` asserts the operator has established this condition; the script does not stop services itself. If stopping services changed mtimes, generate a fresh snapshot first. Set `DRY_RUN=0` in the config and run the approved batch:

**RU.** Пакет переносит файлы; он не удаляет записи из iRedMail SQL/LDAP, aliases и внешних recipient maps. Перед возобновлением служб проверьте, что Postfix больше не принимает почту для выбранных missing-адресов и Dovecot не выдаёт их как рабочие учётные записи. Оставшийся маршрут доставки может заново создать папку. Изменения вашей базы/маршрутов выполните по действующей схеме управления iRedMail; данный пакет их не угадывает.

**EN.** The package moves files; it does not delete iRedMail SQL/LDAP accounts, aliases or external recipient-map entries. Before resuming services, ensure Postfix no longer accepts mail for selected missing addresses and Dovecot no longer exposes them as working accounts. A stale delivery route can recreate a directory. Manage database/routing changes using your existing iRedMail procedures; this package does not guess those settings.

```bash
/usr/local/sbin/iredmail-ad-lifecycle --legacy-archive \
  --inventory /root/legacy-review/missing-01.csv \
  --approval /root/legacy-review/approved-01.csv --writers-quiesced
```

**RU.** Два прохода `rsync -aHAX --numeric-ids` сохраняют дерево и метаданные, затем checksum dry-run должен показать отсутствие различий. После копирования повторно проверяются AD и неизменность источника; только после этого источник удаляется. Ошибка копирования/проверки оставляет источник и незавершённую копию для ручного разбора. Архивирование идёт последовательно: ошибка до обработки предотвращает всю партию, ошибка во время обработки прекращает дальнейшие действия, уже завершённые переносы сохраняются. Это не общая транзакция с AD; для параллельного создания AD-УЗ/ящиков также используйте согласованное окно изменений.

**EN.** Two `rsync -aHAX --numeric-ids` passes preserve the tree and metadata, followed by a checksum dry-run that must report no differences. AD and source stability are checked again after copying; only then is the source removed. A copy/verification failure leaves the source and incomplete copy for review. Archival is sequential: preflight failure prevents the batch, while a mid-run failure stops further operations and keeps already completed moves. This is not a distributed transaction with AD; coordinate concurrent AD/mailbox creation within the maintenance window too.

Archive layout / Структура архива:

```text
LEGACY_ARCHIVE_ROOT/domain/login/YYYYMMDDTHHMMSSZ-unique/
  mailbox/                     original user directory / исходный каталог
  .iredmail-legacy.json         completion + audit record / завершение и аудит
```

**RU.** Маркер содержит адрес, исходный путь, время переноса, широкую базу AD, вид одобрения и SHA256 файлов inventory/approval. Если процесс прерван после удаления источника, но до публикации маркера, `.verified-copy.json` и `mailbox/` позволяют восстановление; такой архив не удаляется retention. После каждого пакета проверьте журнал, архив и восстановление тестовой копии. Возобновите службы согласно своему окну обслуживания. Следующую партию готовьте из нового снимка, чтобы пересчитались знаменатель процента и файловая активность.

**EN.** The marker records email, source path, archive time, broad AD base, approval kind and inventory/approval SHA256 hashes. If interrupted after source removal but before completion marker publication, `.verified-copy.json` and `mailbox/` allow recovery; retention never deletes that incomplete archive. After each batch, inspect logs/archive and verify restoration of a test copy. Resume services according to your maintenance procedure. Use a new inventory for the next batch so percentage denominator and activity are current.

### 18.6 Optional explicit bulk approval / Необязательное явное массовое одобрение

**RU.** Если вы вручную проверили и одобрили весь `eligible=yes` список снимка, можно использовать отдельный `--legacy-archive-all`. Само включение `LEGACY_MODE=1` этого не делает. Режим не обходится без инвентаризации и не отменяет возраст, широкие AD-проверки, защиту путей и оба лимита. Если снимок содержит сотни eligible, сначала пересмотрите и явно задайте лимиты под это число; при обычных значениях команда остановится. Для накопившихся за 6 лет ящиков предпочтителен allowlist по небольшим партиям.

**EN.** After manually reviewing and approving the entire `eligible=yes` snapshot list, use the separate `--legacy-archive-all` flag. `LEGACY_MODE=1` does not enable it. Inventory remains mandatory, and age, broad AD rechecks, safe paths and both batch limits still apply. If the snapshot contains hundreds of eligible entries, explicitly review and configure limits for that number first; defaults will stop the run. Small allowlist batches are preferred for a six-year backlog.

```bash
/usr/local/sbin/iredmail-ad-lifecycle --legacy-archive-all \
  --inventory /root/legacy-review/missing-01.csv --report
# Only after explicit bulk review and writer isolation / После явного одобрения и остановки записи:
/usr/local/sbin/iredmail-ad-lifecycle --legacy-archive-all \
  --inventory /root/legacy-review/missing-01.csv --writers-quiesced
```

### 18.7 Separate retention and restore / Отдельное хранение и восстановление

**RU.** `LEGACY_RETENTION_DAYS=0` хранит legacy-архив бессрочно. Значение, например 180, разрешает удалять только завершённые legacy-архивы, которым исполнилось 180 дней **с момента переноса**, и только явной командой `--legacy-purge`. Старая дата писем не приводит к немедленному удалению. Незавершённые/чужие архивы не затрагиваются. Если AD-объект или исходный ящик снова существует, удаление пропускается. Количество удалений ограничено `LEGACY_MAX_COUNT`; `LEGACY_MAX_PERCENT` относится только к операциям переноса рабочих ящиков. Штатный timer legacy-retention не запускает; запланировать её можно отдельно после проверки восстановления.

**EN.** `LEGACY_RETENTION_DAYS=0` keeps legacy archives indefinitely. A value such as 180 permits deletion only of completed legacy archives 180 days **after archival**, and only via explicit `--legacy-purge`. Old message dates cannot cause immediate deletion. Incomplete/unrelated archives remain. Reappeared AD objects or source mailboxes protect an archive from purge. Deletion count is capped by `LEGACY_MAX_COUNT`; `LEGACY_MAX_PERCENT` applies only to moves of working mailboxes. The normal timer never runs legacy retention; schedule it separately only after testing restore.

```bash
/usr/local/sbin/iredmail-ad-lifecycle --legacy-purge --report
# For actual deletion / Для реального удаления: DRY_RUN=0 and explicit command
/usr/local/sbin/iredmail-ad-lifecycle --legacy-purge
```

Restore from the `mailbox/` subdirectory, with writers/timer paused and no existing destination / Восстановление из `mailbox/`, при остановленных записи/timer и отсутствии существующего назначения:

```bash
mkdir -p /var/vmail/vmail1/example.org/reviewed.departed.user
rsync -aHAX --numeric-ids \
  /mnt/mail-archive/iredmail-legacy/example.org/reviewed.departed.user/ACTUAL-ARCHIVE-ID/mailbox/ \
  /var/vmail/vmail1/example.org/reviewed.departed.user/
```

### 18.8 Return to normal policy / Вернуть штатную работу после очистки

Set in the active config / Установите в основном конфиге:

```bash
LEGACY_MODE=0
LEGACY_APPROVAL_FILE=""
LEGACY_INVENTORY_FILE=""
DRY_RUN=0
```

```bash
/usr/local/sbin/iredmail-ad-lifecycle --normal --report
# Review normal report, then / Проверьте штатный отчёт, затем:
systemctl enable --now iredmail-ad-lifecycle.timer
```

**RU.** Сохраните `LEGACY_SEARCH_BASE`, архив, отчёты и одобрения для аудита. Даже если забудете вернуть `LEGACY_MODE=0`, service версии 1.1 всегда запускает `--normal` и не архивирует missing. Новое увольнение обрабатывается только при AD disabled + `extensionAttribute6=0`. Пользователей со статусами 1..8, unknown-disabled и missing штатный режим сохраняет.

**EN.** Keep `LEGACY_SEARCH_BASE`, archives, inventories and approvals for audit. Even if `LEGACY_MODE=0` is not restored, the version 1.1 service always uses `--normal` and never archives missing mailboxes. New terminations require AD disabled + `extensionAttribute6=0`. Normal mode preserves status 1..8, unknown-disabled and missing mailboxes.

### 18.9 Legacy configuration reference / Справочник legacy-параметров

| Parameter | Default | Русский | English |
|---|---|---|---|
| `LEGACY_MODE` | `0` | 0 штатный; 1 ручной approved legacy, требует inventory+allowlist | 0 normal; 1 manual approved legacy, requires inventory+allowlist |
| `LEGACY_SEARCH_BASE` | `DC=example,DC=org` | общий предок BASE_DN и нужных OU | ancestor of BASE_DN and relevant OUs |
| `LEGACY_MIN_INACTIVE_DAYS` | `365` | минимум дней от latest файлового/каталожного mtime | minimum days since latest file/directory mtime |
| `LEGACY_APPROVAL_FILE` | пусто / empty | отдельный CSV с YES или TXT | separate YES CSV or TXT |
| `LEGACY_INVENTORY_FILE` | пусто / empty | просмотренный снимок; CLI --inventory имеет приоритет | reviewed snapshot; CLI --inventory overrides |
| `LEGACY_ARCHIVE_ROOT` | `/mnt/mail-archive/iredmail-legacy` | отдельный архив или прямой ARCHIVE_ROOT/legacy | separate archive or direct ARCHIVE_ROOT/legacy |
| `LEGACY_RETENTION_DAYS` | `0` | дни от переноса; 0 бессрочно; только --legacy-purge | days since archival; 0 forever; only --legacy-purge |
| `LEGACY_MAX_COUNT` | `20` | предел одобрений за запуск, также предел purge | per-run approval cap; also purge cap |
| `LEGACY_MAX_PERCENT` | `5` | % одобрений от текущих обнаруженных ящиков; >0..100 | % approved of currently discovered mailboxes; >0..100 |

`--report` is order-independent and forces DRY_RUN=1 for any operation. It may write logs/reports/lock but never copies/removes mail, purges archives or updates normal state. Conflicting operation flags are rejected.

`--report` действует независимо от порядка аргументов и включает DRY_RUN=1 для любой операции. Он может писать журнал/отчёты/lock, но не копирует/удаляет почту, не чистит архивы и не меняет штатное состояние. Противоречащие друг другу флаги операций запрещены.

## 19. Verification / Проверка

```bash
python3 tests/test_legacy.py
python3 tests/test_normal.py
for script in iredmail-ad-lifecycle.sh install.sh uninstall.sh preflight.sh; do
  bash -n "$script" || exit 1
done
```

See `VALIDATION.txt` for actual test scope and limits. Tests use temporary mailbox trees and mocked LDAP/rsync; they do not contact your AD or prove Linux ACL/xattr/NAS behavior. Validate preflight, safe reports and a test restore on the actual server before live operations.

См. `VALIDATION.txt`: что фактически проверено и ограничения проверки. Тесты используют временные ящики и подменённые LDAP/rsync; они не обращаются к вашему AD и не подтверждают работу Linux ACL/xattr/NAS. Перед реальной работой проверьте preflight, безопасные отчёты и тестовое восстановление на сервере.

Technical references / Технические источники: [OpenLDAP ldapsearch](https://www.openldap.org/software/man.cgi?query=ldapsearch&sektion=1), [rsync official manual](https://download.samba.org/pub/rsync/rsync.1).
