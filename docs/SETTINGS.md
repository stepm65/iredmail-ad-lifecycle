# Настройки через веб-консоль / Web settings

## RU

С версии **core 1.5.0 / console 0.5.0** штатная настройка выполняется на странице **Настройки**. Терминал нужен для установки/обновления и аварийного восстановления доступа. Установка HTTPS-маршрута по-прежнему запрашивает публичный URL и имя сервера; DNS, сертификат и доверие к CA AD должны быть настроены в ОС.

### Первый запуск

1. Установите комплект по [инструкции](INSTALL.md) и войдите с отдельным паролем консоли.
2. Откройте **Настройки**. На существующем сервере форма читает действующие значения, а не подменяет их примером. На новой установке замените `example.org`.
3. Укажите LDAP URI, сервисную УЗ чтения AD, основную и широкую базы. Широкая база должна включать основную; обычно это корень домена. Для SOGo с `MailFieldNames=(userPrincipalName)` оставьте **userPrincipalName**. Дополнительные `mail` и SMTP-алиасы по умолчанию выключены; широкий поиск присутствия всё равно проверяет все три источника.
4. Задайте реальные домены, корень рабочих ящиков и два отдельных корня архивов. Изменение пути не переносит старые файлы. Прежние архивы после смены корня перестанут показываться в каталоге этого корня. Пути должны быть непересекающимися, без символических ссылок; принимаются отдельные подкаталоги `/var/vmail`, `/var/mail`, `/mnt`, `/srv`, `/media`, `/data`, `/mail`.
5. Проверьте минимум объектов AD, сроки хранения и ограничения. `EXPECTED_MIN_USERS` — число объектов, не число алиасов. В шаблоне стоит 700; подберите значение для своей организации. Хранение `0` отключает автоматическое удаление архивов. Для первого запуска оставьте **Только проверка = Да** и выключенные разрешения реальных действий.
6. Введите пароль сервисной УЗ AD и текущий пароль консоли. **Проверить значения** проверяет формат; **Проверить подключение к AD** выполняет запросы к обеим базам с постраничным чтением и проверяет минимальное число объектов. Пароли после отправки очищаются: перед следующим действием введите их снова, если меняете пароль AD.
7. Введите `СОХРАНИТЬ`, пароль консоли и нажмите **Сохранить настройки**. Создайте новый отчёт и проверьте адреса, пути и статусы. Перезапуск служб не требуется.

### Что настраивается

| Раздел | Параметры |
| --- | --- |
| AD | LDAP URI, bind DN/UPN и пароль, основная/широкая базы, основной адрес UPN или mail, дополнительные защитные совпадения, кадровый атрибут, фильтр, размер страницы, минимум объектов |
| Хранилище | Домены, корень рабочих ящиков, штатный и legacy-архивы, требование отдельной файловой системы |
| Штатная обработка | DRY_RUN, ожидание увольнения, хранение архивов, максимальная доля уволенных, проверка размера копии, размеры в отчёте |
| Legacy | Минимальная файловая неактивность, хранение, максимальное количество и процент пакета |
| Ручное удаление | Максимальное количество и процент пакета, отдельное разрешение на удаление |
| Администрирование | Разрешение подтверждённой архивации/восстановления, включение и выключение ежедневного таймера, восстановление настроек |

Каждое поле содержит подсказку и имя параметра. Полное описание исходных параметров RU/EN — [комментированный конфиг](../core/iredmail-ad-lifecycle.conf). Значение увольнения **0** закреплено политикой; статусы 1–8, unknown-disabled и missing в штатном автоматическом режиме защищены. Веб-консоль не меняет УЗ или пароли пользователей AD.

**Реальная обработка:** отключение «Только проверка» требует успешной повторной проверки AD при сохранении. `allow_live` разрешает подтверждённую архивацию/восстановление из консоли; `allow_delete` отдельно разрешает подтверждённое удаление. Эти разрешения не управляют штатным таймером: его реальную работу определяет `DRY_RUN`. При уже включённом таймере новое значение применяется со следующего запуска. Срок штатного хранения больше нуля разрешает очистку старых архивов по прежним правилам.

**Таймер:** отдельные кнопки включают или выключают существующий `iredmail-ad-lifecycle.timer` с паролем и точной фразой. Показано состояние и следующий запуск. Расписание пакета — 03:20 по часовому поясу сервера, случайная задержка до 5 минут. Существующее пользовательское расписание не редактируется. Изменение таймера не прерывает текущую обработку.

**Шестилетний backlog:** в настройках задайте широкую базу, период неактивности и небольшие лимиты. Создайте legacy-инвентаризацию, изучите адреса/пути/размеры/mtime и одобрите конкретные ящики. Изменение лимита само по себе не является одобрением. После разовой очистки используйте обычные отчёты и штатный таймер; они запускаются с `--normal`. Веб-интерфейс не включает массовое автоматическое архивирование missing. CLI-параметры `LEGACY_MODE` и пути файлов approval/inventory остаются в исходном конфиге; веб-консоль передаёт собственные явные задания и списки.

### Сохранение, копии и пароль

Исходный `/etc/iredmail-ad-lifecycle.conf` сохраняется. Управляемые значения находятся в `/etc/iredmail-lifecycle-web/core-overrides.conf` (root, `0600`) и читаются после исходного файла, в том числе CLI и таймером. Поэтому последующее ручное изменение того же параметра в исходном файле не отменяет веб-настройку. Веб-форма поддерживает постоянные присваивания из штатного шаблона; конфиг с командами, вычислениями, неизвестными или повторными параметрами отклоняется с сообщением. Команды Bash из браузера не принимаются.

Пароль AD передаётся локальной службе через Unix-сокет и хранится в отдельном файле root `0600`. В HTML, отчёты, очередь заданий и журнал пароль не попадает. Пустое поле означает «оставить прежний». При проверке нового пароля используется временный файл, который затем удаляется. LDAP требует STARTTLS (`ldap://`, `-ZZ`) или LDAPS и доверенного сертификата; выключатель проверки TLS не предоставляется.

Перед сохранением создаётся копия прежних настроек в `/etc/iredmail-lifecycle-web/config-history` (root, `0700`). На странице показаны последние 20 копий и имена изменённых параметров. Файлы копий и старые файлы паролей сохраняются для отката; автоматического удаления истории нет. Они содержат чувствительные настройки и не должны попадать в Git или публичные архивы.

**Восстановить настройки** сохраняет текущий пароль входа в консоль, восстанавливает выбранную версию параметров и ссылку на прежний пароль AD, принудительно включает `DRY_RUN=1`, отключает `allow_live`/`allow_delete`. Состояние таймера не меняется: включённый таймер продолжит безопасные проверки. Если исходный конфиг изменён извне, несовместимая копия не восстанавливается. Почтовые данные эта операция не восстанавливает — для них есть **Архивы и восстановление**.

Во время заданий и при непустой очереди изменение настроек блокируется. После сохранения старые отчёты, инвентаризации и предварительные проверки больше не разрешают реальные действия: создайте свежие. Конкурирующая вкладка получает сообщение о смене версии. При сбое между записями служба восстанавливает прежнюю версию при запуске; пока восстановление не завершено, обработка почты заблокирована. При внешнем вмешательстве в незавершённую запись потребуется root-диагностика, автоматическое исправление неизвестных данных не выполняется.

Служебные пути состояния, исключений, журналов, очереди, блокировок и публичный HTTPS URL не редактируются этой формой. Они задаются при установке, чтобы обычное изменение политики не потеряло историю и защитные исключения.

## EN

Starting with **core 1.5.0 / console 0.5.0**, normal configuration is available on **Настройки** (Settings). The terminal is needed for installation/upgrades and emergency console access recovery. The installer still asks for the HTTPS URL and server/CNAME target; DNS, certificate coverage and AD CA trust remain operating-system prerequisites.

### Setup and operation

1. Follow [installation](INSTALL.md), log in and open Settings. Existing configuration is loaded; a fresh installation requires replacing example values.
2. Set the LDAP URI, read-only service identity, primary and broad bases, primary address mapping and minimum expected object count. The broad base must contain the primary base. SOGo `MailFieldNames=(userPrincipalName)` requires the UPN choice. Broad presence checks consider UPN, mail and SMTP aliases.
3. Set domains and distinct, non-overlapping mailbox/normal-archive/legacy-archive roots. Symlinks are rejected. Supported roots are dedicated subdirectories under `/var/vmail`, `/var/mail`, `/mnt`, `/srv`, `/media`, `/data` or `/mail`. Changing a root does not migrate data; old archives outside the new root will no longer be listed there.
4. Review delays, retention and batch limits. The sample minimum AD count is 700; adjust it to your organization. A retention value of 0 disables expiry deletion. Keep **Только проверка = Да** (dry run) and both live-operation permissions off for initial setup.
5. Enter the AD password and current console password. **Проверить значения** validates fields; **Проверить подключение к AD** queries both bases with paging and minimum-count checks. Password fields clear after submission; re-enter a new AD password before saving it.
6. Enter `СОХРАНИТЬ` and the console password, then save. Create a fresh report and inspect mapping, paths and classifications. Service restart is not required.

Editable fields cover LDAP/mapping/filter/paging, domains and archive roots, DRY_RUN, termination delay, retention, copy-size threshold, report sizes, legacy inactivity/count/percentage, deletion count/percentage and console permissions. Every field has a hint and its configuration key. See the [RU/EN configuration reference](../core/iredmail-ad-lifecycle.conf).

Normal automatic archiving remains limited to a unique primary match, disabled AD account and HR status **0**. Disabled 1–8, unknown-disabled and missing remain protected. The console does not modify AD accounts or their passwords. Saving `DRY_RUN=0` requires a successful fresh AD check. `allow_live` controls confirmed console archival/restore; `allow_delete` separately controls confirmed permanent deletion. These permissions do not control scheduled core runs: `DRY_RUN` does, including an already enabled timer. Positive normal archive retention retains its existing expiry-deletion meaning.

The timer controls require the console password and `ВКЛЮЧИТЬ` (enable) or `ВЫКЛЮЧИТЬ` (disable). They operate the existing lifecycle timer; the page shows its state and next run. The packaged schedule is 03:20 server time plus up to five minutes of jitter. Custom schedules are not edited. Running operations are not interrupted.

For a six-year backlog, configure a broad domain search, inactivity period and small limits, create legacy inventory, inspect addresses/paths/sizes/latest mtime and approve an exact list. Raising a limit is not approval. Afterwards use normal reports and the timer (`--normal`); missing accounts never enter automatic normal archiving. The web UI uses explicit legacy jobs/lists; CLI `LEGACY_MODE` and approval/inventory paths stay in the original file.

### Persistence and recovery

The original `/etc/iredmail-ad-lifecycle.conf` is retained. Typed, shell-quoted values are saved to root-only `core-overrides.conf` under `/etc/iredmail-lifecycle-web` and loaded after the original by CLI and scheduled runs. Editing an overridden value in the original file later will not supersede the web value. Only constant assignments from the supported template are imported; shell commands, computed values, unknown or duplicate keys are rejected.

AD passwords travel through the local UID-checked Unix socket, never the job queue, HTML or application log. They are kept in separate root `0600` files; a blank password preserves the current file. Connection tests use a temporary file for a new password and remove it afterwards. LDAP requires STARTTLS or LDAPS with certificate trust.

Each save backs up previous configuration under root-only `config-history`. The last 20 entries are shown with changed key names. Backup files and old credential files remain available for rollback; there is no automatic history purge. Do not publish this directory. Restore preserves the current console login password, restores the chosen parameters/AD credential reference and forces dry run with both live permissions disabled. It leaves the timer state unchanged; an enabled timer continues safe checks. Restore refuses a backup if the original base file was changed externally. Mailbox restore is a separate operation.

Settings cannot change during jobs or with a nonempty queue. Saves invalidate old reports/inventories/previews for further actions, and stale browser revisions are rejected. Interrupted writes are journaled and rolled back on broker restart; mail processing remains blocked until recovery completes. Unexpected external edits require root investigation. State, exclusions, log/queue/lock paths and HTTPS URL are installation settings and are not exposed as arbitrary writable paths.
