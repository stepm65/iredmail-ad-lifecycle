# iRedMail lifecycle 1.4.0 / web console 0.4.0

## RU — Массовое удаление выбранных ящиков

В меню добавлено «Массовое удаление». Операция удаляет ПОЧТОВЫЕ ФАЙЛЫ нескольких
явно выбранных ящиков; учётные записи AD не удаляет. Новые архивные копии не
создаются. Старые архивы и внешние резервные копии не затрагиваются этой операцией.
Смена/сброс пароля консоли, восстановление и постоянные исключения сохранены.

1. Обновите штатный отчёт и откройте «Массовое удаление».
2. Выберите фильтр «Неизвестный статус» либо «Нет в основном AD», при необходимости
   найдите адреса. Отметьте галочки или нажмите «Выбрать все показанные подходящие».
   Активные, protected-disabled, alias-only, ambiguous-ad и excluded не выбираются.
3. Нажмите «Проверить выбранные». Это ничего не удаляет. Worker формирует точный
   approval CSV; core проверяет широкий AD, уникальную идентичность/GUID,
   disabled/HR, exclusions и SHA256/метаданные каждого ящика.
4. Просмотрите ВСЕ адреса, исходные пути, общий размер, количество и лимиты пакета.
5. Обеспечьте остановку SMTP/LMTP-доставки, IMAP и любых процессов записи во ВСЕ
   выбранные ящики. Введите «УДАЛИТЬ N», где N — показанное число, и пароль
   консоли; подтвердите остановку записи и нажмите удаление.

Поддерживаются unknown-disabled только с уникальным основным UPN и подтверждённым
ACCOUNTDISABLE, HR вне 0..8; missing — только при отсутствии во всём
LEGACY_SEARCH_BASE по основному атрибуту/UPN/mail/SMTP. Статусы 0..8, активные УЗ,
неизвестный UAC, контакты, дубликаты, алиасы и exclusions блокируют операцию.
«Кадровый статус не задан» сам по себе не означает, что ящик можно удалить.
Штатная автоматическая политика disabled+extensionAttribute6=0 не меняется.

Выбор сохраняется при смене фильтра; счётчик отдельно показывает скрытые выбранные
строки. Preview всегда перечисляет точный окончательный список. Показаны первые
5000 строк (подходящие первыми), выбор до 500 адресов; для большего объёма используйте
несколько явно одобренных пакетов/CLI. Ни кнопки «удалить всё хранилище», ни
автоматического одобрения по статусу нет.

Весь пакет повторно проверяется до первого удаления. Если любой адрес перестал
подходить/изменились данные — удаление не начинается. Перед каждым последующим
ящиком снова проверяются AD/файлы/исключения. Ошибка после начала останавливает
ОСТАЛЬНЫЕ ящики; уже удалённые не возвращаются автоматически. Итог показывает
подтверждённо удалённые, ошибочный и не обработанные адреса. При timeout/restart
сохраняется подтверждённый прогресс; неподтверждённые пути/staging нужно проверить.
Автоматического повтора нет. Повторное действие требует НОВОГО отчёта/preview.

Операция удаляет целый рабочий VMAIL_ROOT/domain/login, включая Maildir и остальные
файлы. Существующие каталоги не объединяются/не перезаписываются. Проверенный ящик
отсоединяется в domain/.lifecycle-deleting/UUID/payload, затем удаляется; при ошибке
там могут оставаться все/часть файлов. Это не архив. Root operation.json и
delete-batch-result.json фиксируют прогресс, per-item snapshots/results доступны
root в каталоге задания. После ошибки сначала проверьте их, исходный путь и журнал.
Доставка может вновь создать каталог, если отдельно не запретить приём адреса.

## RU — Обновить установленный комплект

Скопируйте TAR.GZ в /root на сервере:

```bash
cd /root
tar -xzf iredmail-lifecycle-management-1.4.0.tar.gz
cd iredmail-lifecycle-management-1.4.0
bash upgrade.sh
```

Сохраняются настройки AD/UPN, пароль/URL, состояния/audit, архивы и исключения.
Выполняющиеся задания не прерываются: очередь и основной flock должны быть
свободны. Backup/rollback кода/конфигов сохраняются в
/var/backups/iredmail-lifecycle-upgrade. Почтовые службы не останавливаются.
Для новой установки используйте core/install.sh, затем console/install-web.sh;
upgrade.sh рассчитан на существующую консоль. ОС/почтовый стек не обновляются.

Для реального удаления нужны отдельное allow_delete и DRY_RUN=0:

```bash
/opt/iredmail-lifecycle-web/venv/bin/python /opt/iredmail-lifecycle-web/configure.py --allow-delete yes
```

DRY_RUN=0 задаётся в /etc/iredmail-ad-lifecycle.conf и влияет также на штатный timer.
При DRY_RUN=1 пакет только проверяется, в интерфейсе это не считается удалением.
После разовой очистки рекомендуется --allow-delete no той же командой.
Разрешения применяются без прерывания заданий; allow_live не разрешает удаление.

## RU — Лимиты пакета

Новые параметры /etc/iredmail-ad-lifecycle.conf; существующий конфиг сохраняется,
при отсутствии параметров используются значения ниже. .conf.dist содержит описание.

```bash
MANUAL_DELETE_MAX_COUNT=100
MANUAL_DELETE_MAX_PERCENT=25
```

MAX_COUNT: 1..500 выбранных ящиков; MAX_PERCENT: 1..100 процентов от ВСЕХ рабочих
flat Maildir-ящиков в MAIL_DOMAINS, включая активные/защищённые. Оба ограничения
проверяются при preview и ПЕРЕД началом переноса первого ящика. Даже одно выбранное
имя в пакетном режиме подчиняется этим лимитам. Например, 2 из 4 ящиков = 50% и
не пройдут при 25%. Для согласованной большой очистки настройте значения явно,
затем создайте новую проверку. Данные не удаляются при превышении.

## RU — CLI для явного списка

approval CSV: email,approved, только YES разрешает адрес; либо TXT по одному адресу
на строку. Пустой файл и дубли отвергаются. Для результатов используйте новые имена.

```bash
iredmail-ad-lifecycle --delete-batch-preview --approval /root/selected.csv --output /root/batch-preview.json
iredmail-ad-lifecycle --delete-batch --preview /root/batch-preview.json --output /root/batch-check.json --report
# После проверки списка и остановки ВСЕХ писателей, при DRY_RUN=0:
iredmail-ad-lifecycle --delete-batch --preview /root/batch-preview.json --output /root/batch-result.json --confirm-delete 'DELETE 10' --writers-quiesced
```

Замените 10 точным числом адресов в preview. Общий lifecycle flock удерживается
на весь пакет. CLI не получает произвольных source paths из CSV — пути вычисляются
из конфигурации и сверяются с новой проверкой. Веб передаёт только root job ID;
неизменность snapshot/allowlist подтверждается SHA256, preview одноразовый.

## EN — Reviewed batch deletion

Core 1.4.0 / console 0.4.0 add Массовое удаление. Refresh the normal report, filter
unknown-disabled or missing, select checkboxes or all currently displayed eligible
rows, and preview the exact cohort. Hidden selections remain counted. Protected
rows are disabled; the worker also validates every address against a root-owned
report. Up to 5000 rows are shown, eligible first, with 500 selections maximum.
No discovery/filter automatically authorizes a deletion.

Review all addresses, computed paths, total size and current batch limits. Quiesce
all writers for all selected boxes, then type УДАЛИТЬ N with exact count and enter
the console password. Separate configure.py --allow-delete yes and core DRY_RUN=0
are required. DRY_RUN also controls the normal timer. Disable allow_delete after
one-off cleanup. Permission changes refresh without stopping running jobs.

This permanently deletes only working mailbox files, making no new archive and
leaving AD/SQL/SOGo/old archives/backups unchanged. Only unique primary disabled
accounts with HR outside 0..8 or truly broad-AD-absent mailboxes qualify. Known
0..8 statuses, active/unknown UAC, contacts, aliases, ambiguities and exclusions
remain protected. SOGo userPrincipalName mapping and automatic policies are kept.

The full cohort is revalidated before any deletion. Each member is rechecked
immediately before mutation, under the shared lifecycle lock. Changes before
start cancel all; failures during processing stop the remaining members. Confirmed
deleted, failed and remaining addresses are recorded. Timeout/restart retains
confirmed progress, with uncertain paths/staging needing inspection. Deleted data
is not rolled back; a failed/single-use preview is never replayed automatically.

Default MANUAL_DELETE_MAX_COUNT=100 (1..500) and MANUAL_DELETE_MAX_PERCENT=25
(1..100) apply at preview and before the first removal. Denominator is all working
flat Maildir boxes in MAIL_DOMAINS, including protected/active. Current config
limits override older previews. Existing config is preserved; absent keys use
these defaults. Adjust explicitly and preview again when cleaning a larger share.

Extract the combined TAR.GZ in /root and run bash upgrade.sh. Existing config,
password/URL, archives/state/audit/exclusions, HTTPS connector fix and backup/
rollback are preserved. Busy jobs/core lock refuse upgrade. Fresh installs use
individual installers. No mail services or OS upgrade are performed.

CLI accepts an explicit YES approval CSV or TXT, --delete-batch-preview, then
--delete-batch with --confirm-delete 'DELETE N' and --writers-quiesced. Use new
output names; paths are recomputed, not trusted from input. Atomic detach and
fd-based Linux recursive removal retain staging/progress audits on error. Staging
is not an archive. Prevent future recipient acceptance separately or delivery may
recreate a deleted box. Full prior single-delete/password/restore instructions are
in core/OPERATIONS.md and console/ADMINISTRATION.md.

See VALIDATION.txt for actual fixture scope: no production AD/mailserver, Ubuntu,
native Linux rename/removal or systemd/Unix broker deployment were exercised.
