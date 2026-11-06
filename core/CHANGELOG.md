# 1.5.0 — Web-managed settings / Настройки через веб

Managed overrides, shared configuration lock and interrupted-save guard. Existing mailbox processing policy is unchanged. See [release changes](../CHANGELOG.md) and [settings RU/EN](../docs/SETTINGS.md).

# 1.4.0 — Explicit reviewed batch deletion / Явное пакетное удаление

- Checkbox/CSV/TXT cohort only; new root count/percent guards. No automatic deletion.
- Entire cohort preflight plus per-member AD/GUID/data/exclusion rechecks;
  failures stop remaining members, exact confirmed progress is kept without replay.
- Web 0.4.0 validates report selections, immutable single-use preview/approval,
  exact УДАЛИТЬ N confirmation, password and all-writer acknowledgment.
- Root result supports partial/timeout/restart recovery. AD/archives unchanged.
- 34 new cases pass; baseline and affected regressions retained in VALIDATION.txt.

# 1.3.0 — Explicit permanent mailbox deletion / Явное безвозвратное удаление

- One reviewed unknown-disabled/missing mailbox; wide AD/GUID/UAC/status and
  data/metadata rechecks, exclusions, writer acknowledgment and exact address.
- No automatic unknown/missing deletion. Active/known statuses/ambiguous/alias
  accounts stay protected. No new archive, no AD change, old archives unchanged.
- Shared flock, atomic detach, fd-based recursive removal, staging progress audit;
  no automatic retry after failure/interruption. 120 core fixture checks passed.
- Console 0.3.0 adds separate deletion permission, reauthentication and single-use
  snapshots plus in-memory local password change and root/SSH recovery.

# 1.2.0 — Recovery and persistent exclusions / Восстановление и исключения

- RU: Проверяемый список архивов и явное восстановление одного ящика; архив
  сохраняется, существующий каталог не перезаписывается. EN: Reviewed one-mailbox
  restore keeps the archive and refuses existing destinations.
- RU: Постоянные исключения с причиной; защита normal, legacy и обоих retention,
  включая старые approval. Восстановленный адрес защищается автоматически.
  EN: Persistent reasoned protections cover normal/legacy/retention and older
  approvals; restored addresses are protected before publication.
- RU: Новые normal-архивы отмечаются pending/complete; незавершённые копии не
  восстанавливаются/не удаляются по retention. EN: New normal transfer completion
  markers protect incomplete copies from recovery and retention.
- RU/EN: SOGo userPrincipalName and disabled+extensionAttribute6=0 policy retained.
- RU/EN: Shared flock, checksum snapshot/copy checks, atomic non-overwrite publish,
  CLI management, core 104 distinct fixture tests; Ubuntu behavior not tested here.

# 1.1.3 — SOGo UPN identity / UPN и неоднозначные адреса

- RU: Основной адрес согласован с предоставленным SOGo MailFieldNames=(userPrincipalName). Рекомендация 1.1.2 для этой установки заменена. EN: Primary identity follows the supplied SOGo MailFieldNames=(userPrincipalName); supersedes the 1.1.2 recommendation for this installation.
- RU: ADDRESS_INCLUDE_MAIL/ADDRESS_INCLUDE_SMTP_ALIASES явно включают дополнительные источники, выключены по умолчанию. Дополнительное совпадение даёт только защиту alias-only. EN: Extra mail/SMTP sources are explicit, default off, and protect alias-only mailboxes without authorizing archival.
- RU: Неоднозначные адреса получают ambiguous-ad, не архивируются и не останавливают отчёт остальных ящиков. CSV конфликтов содержит DN/статусы/источники каждого совпадения. EN: Ambiguous identities are protected, do not abort other reports, and have a per-owner conflict CSV with DN/status/source details.
- RU: Legacy широкая проверка по UPN/mail/SMTP сохраняется независимо от переключателей штатного сопоставления. EN: Broad legacy UPN/mail/SMTP checks remain independent of normal extra-source switches.
- RU: Проверены конфликтующие активные/уволенные записи, все уволенные дубликаты, старое state, продолжение уникальной архивации, SOGo UPN при отличающихся mail/SMTP и защита alias-only. EN: Regression coverage includes conflicting enabled/terminated records, all-terminated duplicates, old state reset, independent unique archival, SOGo UPN with differing mail/SMTP and alias-only protection.

# 1.1.2 — Mail vs UPN / Почтовый адрес и имя входа

- RU: Исправлен источник сопоставления по умолчанию: ADDRESS_ATTRIBUTE=mail, а не userPrincipalName; фильтр всех пользователей не требует наличия UPN. EN: Default mapping uses mail instead of userPrincipalName; user search no longer requires UPN.
- RU: Штатная таблица также учитывает mail и SMTP proxyAddresses; несовместимые с именем ящика служебные UPN пропускаются с NOTICE, а не ломают отчёт. EN: Normal mapping also includes mail and SMTP aliases; non-mailbox service UPNs are skipped with NOTICE instead of aborting reports.
- RU: CSV ad_match_attributes и журнал показывают источник совпадения; существующий конфиг нужно исправить явно. EN: CSV ad_match_attributes and logs expose mapping source; the preserved config requires an explicit edit.
- RU: Минимум AD считается по объектам, а не адресам/алиасам. Межобъектные дубликаты адресов блокируют обработку. EN: AD minimum counts objects, not addresses/aliases; cross-object duplicate addresses stop processing.
- RU: Legacy-проверка продолжает защищать по mail, SMTP и UPN, включая пользователя с пустым mail. EN: Legacy presence checks protect by mail, SMTP aliases and UPN, including a user with empty mail.
- RU: Проверены точный http/... UPN, различающиеся mail/UPN, SMTP alias, пустая адресная таблица и количество объектов. EN: Regression cases include the exact http/... UPN, differing mail/UPN, SMTP aliases, empty address table and object counts.

# 1.1.1 — Unicode UPN fix / Исправление кириллических UPN

- RU: Устранён отказ --report на `администратор@example.org`: проверка адресов принимает Unicode-буквы/цифры. EN: Fixed --report abort on `администратор@example.org`; email validation accepts Unicode letters/digits.
- RU: AD и имена каталогов используют общую проверку и регистр, в том числе при LC_ALL=C; протокол helper → Bash всегда UTF-8. EN: AD and directory names share validation/lowercase, including LC_ALL=C; the helper-to-Bash protocol always uses UTF-8.
- RU: Защита от `/`, `\`, управляющих символов, shell-разделителей, подстановок и traversal сохраняется. Нет транслитерации/слияния разных Unicode-имён. EN: Path/control/shell/traversal guards remain; no transliteration or merging of distinct Unicode names.
- RU: Unicode/+ state-файлы получают уникальные SHA256-ключи; прежние ASCII имена сохраняются. EN: Unicode/+ state filenames use SHA256 identity keys; previous ASCII filenames remain.
- RU: Политика архивирования и формат inventory 1.1 сохраняются. Добавлены regression-тесты на кириллицу, base64 LDIF, locale C и Unicode legacy-архив. EN: Archive policy and inventory schema 1.1 remain; regression coverage includes Cyrillic/base64 LDIF, C locale and Unicode legacy archives.

# 1.1 — legacy cleanup / Очистка исторических missing

- RU: Штатное архивирование сохранено: только disabled + extensionAttribute6=0. Enabled, 1..8, unknown-disabled и missing сохраняются. EN: Normal archive remains disabled + extensionAttribute6=0 only; enabled, 1..8, unknown-disabled and missing remain.
- RU: Отдельные --legacy-inventory, --legacy-archive, --legacy-archive-all и --legacy-purge. Inventory не является одобрением. EN: Separate inventory, approved archive, explicit bulk archive and purge operations. Inventory does not constitute approval.
- RU: CSV/TXT allowlist, все запрошенные LEGACY-параметры и дополнительный LEGACY_INVENTORY_FILE. EN: CSV/TXT allowlist, all requested LEGACY settings and extra LEGACY_INVENTORY_FILE.
- RU: Широкие AD-проверки по UPN/настроенному атрибуту, mail и SMTP proxyAddresses; повтор перед каждым переносом и после копии. Ошибки LDAP/referrals блокируют работу. EN: Broad AD checks across configured address attribute, mail and SMTP aliases, repeated per mailbox and after copy; LDAP errors/referrals fail closed.
- RU: Защита от свежих/будущих mtime, замены каталога и изменения после inventory; два лимита до действий без округления процента вниз. EN: Recent/future activity, replaced directories and changed snapshots are protected; two batch limits apply before actions without percent rounding down.
- RU: Для legacy нужны остановленные процессы записи и --writers-quiesced; rsync проверяется checksum dry-run. EN: Legacy requires quiesced writers and --writers-quiesced; rsync is verified with a checksum dry-run.
- RU: Отдельный legacy-архив с completion/audit JSON; retention от времени успешного переноса, по умолчанию бессрочно, только явный purge. EN: Separate legacy archive with completion/audit JSON; retention starts at successful archival, defaults to forever, uses explicit purge only.
- RU: systemd всегда --normal; штатный purge исключает legacy-подкаталог. EN: systemd always uses --normal; normal purge excludes a nested legacy subdirectory.
- RU: Безопасный разбор folded/base64 LDIF, проверка дубликатов и неизвестного UAC, строгие имена/корни; исправлен процентный штатный guard. EN: Folded/base64 LDIF parsing, duplicate/unknown-UAC handling, strict names/roots; normal percent guard no longer rounds down.
- RU: Установка Python helper без перезаписи существующего конфига, новые примеры одобрения, инструкция для шестилетнего накопления на RU/EN и воспроизводимые тесты. EN: Python helper installation preserves existing config, with approval examples, RU/EN six-year backlog guidance and reproducible tests.

Existing 1.0 archives retain their existing layout and normal retention behavior.
Существующие архивы 1.0 сохраняют структуру и штатную политику хранения.
