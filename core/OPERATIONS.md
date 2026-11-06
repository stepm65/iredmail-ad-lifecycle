# iRedMail lifecycle 1.4.0 / веб-консоль 0.4.0

## RU — Обновление существующей установки

В комплекте основной lifecycle и консоль. Скопируйте TAR.GZ на сервер в /root:

```bash
cd /root
tar -xzf iredmail-lifecycle-management-1.4.0.tar.gz
cd iredmail-lifecycle-management-1.4.0
bash upgrade.sh
```

Скрипт сохраняет рабочие настройки AD/UPN, пароль/URL консоли, audit/state,
исключения и архивы. Он отказывается прерывать выполняющиеся задания: дождитесь
пустой очереди и повторите при необходимости. Код/конфиги сохраняются в
/var/backups/iredmail-lifecycle-upgrade; при ошибке они восстанавливаются.
Консоль недоступна для новых действий во время обновления. Почтовые службы
не останавливаются. Резервная копия не включает venv; зависимости не изменены.
Публичный URL выбирается отдельно от FQDN/цели CNAME. Исправления подключения
существующего Nginx/Apache и штатных SOGo rewrite сохранены.

После установки выполните вход, безопасный отчёт и проверку одного тестового
ящика. Ubuntu 22.04/24.04 и реальный AD/systemd/Unix-сокет здесь не использовались.

## RU — Сменить или сбросить пароль консоли

Если пароль известен: «Пароль консоли» → текущий пароль → новый пароль и повтор.
Новый пароль должен содержать 12–256 символов. Все прежние сеансы потребуют
повторного входа. Пароль AD не меняется. Новые пароли не пишутся в очередь,
audit JSON или журналы; хранится только хеш. Root-служба обрабатывает узкую
операцию через локальный Unix-сокет, сама проверяет текущий пароль и ограничивает
неудачные попытки (5 за 10 минут). Root-настройки веб-процесс изменять не может.

Если пароль забыт, выполнить по SSH от root:

```bash
bash /opt/iredmail-lifecycle-web/reset-console-password.sh
```

Будет запрошен новый пароль и повтор. Перезапускается только frontend; worker
и выполняющиеся задания не останавливаются. AD и почтовые файлы не затрагиваются.
На уже установленной версии 0.2.0 сброс доступен без обновления:

```bash
/opt/iredmail-lifecycle-web/venv/bin/python /opt/iredmail-lifecycle-web/configure.py --set-password
systemctl restart iredmail-lifecycle-web.service
```

## RU — Безвозвратно удалить неизвестный или отсутствующий ящик

В 0.2.0 отдельного ручного удаления не было. В 0.4.0 это явная операция для
одного адреса, независимая от автоматической архивации. Поддержаны:

- unknown-disabled: единственное совпадение по основному UPN/ADDRESS_ATTRIBUTE,
  УЗ подтверждённо disabled, extensionAttribute6 пустой либо не входит в 0..8.
- missing: адрес отсутствует во всём LEGACY_SEARCH_BASE по основному атрибуту,
  userPrincipalName, mail и SMTP proxyAddresses.

Active, статусы 0..8, неизвестный UAC, alias-only, неоднозначные объекты,
контакты и постоянные исключения блокируют удаление. Если ящик уже исключён,
сначала явно снимите исключение с подтверждением. Статусы AD автоматически
не меняются; не присваивайте «0 / Уволен» только для обхода защиты.

Включите отдельное разрешение удаления по SSH от root:

```bash
/opt/iredmail-lifecycle-web/venv/bin/python /opt/iredmail-lifecycle-web/configure.py --allow-delete yes
```

Разрешение применяется без остановки заданий. allow_live относится к архивированию
и восстановлению; оно не включает удаление. В основном конфиге для реального
удаления требуется DRY_RUN=0. Эта настройка влияет и на штатный timer: учитывайте
его обычную политику disabled+0. При DRY_RUN=1 удаление остаётся проверкой.

Обновите штатный отчёт → у нужного ящика «Проверить удаление файлов» → дождитесь
проверки → проверьте адрес, размер, исходный путь и состояние AD. Обеспечьте
прекращение SMTP/LMTP-доставки, IMAP-доступа и любых процессов записи для этого
адреса. Подтвердите это в форме, введите УДАЛИТЬ, точный адрес и пароль консоли.

Удаляется весь рабочий каталог VMAIL_ROOT/domain/login, включая Maildir и прочие
файлы ящика. НОВАЯ АРХИВНАЯ КОПИЯ НЕ СОЗДАЁТСЯ. Старые архивы/внешние резервные
копии, AD, SQL и SOGo не удаляются. Если рабочие данные больше нигде не сохранены,
восстановление через консоль после удаления невозможно. Массовое удаление описано в BATCH_DELETE.md.
Штатная автоматическая политика unknown-disabled/missing остаётся защитной.

Перед изменением проверяются широкий AD, GUID объекта, UAC/кадровый статус,
исключение, inode/device и SHA256/метаданные файлов. Изменение после preview
блокирует операцию; новая проверка обязательна. Symlink, special files и nested
mounts блокируют обход. Общий lifecycle flock исключает другие lifecycle-задания.

Проверенный каталог сначала атомарно отсоединяется в
VMAIL_ROOT/domain/.lifecycle-deleting/UUID/payload без перезаписи. Linux-удаление
использует fd-based shutil.rmtree. Root-журнал operation.json сохраняет состояние
подготовки/отсоединения/успеха. Если процесс прерван либо recursive unlink
завершился ошибкой, там могут остаться все или часть файлов, а исходный путь уже
отсутствовать. Это НЕ архив. Проверьте журнал и staging; повторов автоматически
нет. Не запускайте повторное удаление вслепую.

Удаление файлов само по себе не запрещает будущую доставку: если recipient lookup
всё ещё принимает адрес, сервер может создать каталог заново. Для окончательного
вывода ящика из эксплуатации отдельно настройте запрет доставки/доступа по своей
почтовой конфигурации. Консоль эти настройки не меняет.

После разового удаления выключите разрешение:

```bash
/opt/iredmail-lifecycle-web/venv/bin/python /opt/iredmail-lifecycle-web/configure.py --allow-delete no
```

Эквивалентный CLI, каждый snapshot/result — новый файл:

```bash
iredmail-ad-lifecycle --delete-preview --email old@example.org --output /root/delete-preview.json
iredmail-ad-lifecycle --delete --email old@example.org --preview /root/delete-preview.json --output /root/delete-result.json --report
# Только после проверки/остановки писателей, при DRY_RUN=0:
iredmail-ad-lifecycle --delete --email old@example.org --preview /root/delete-preview.json --output /root/delete-result.json --confirm-delete old@example.org --writers-quiesced
```

## EN — Upgrade, console password and explicit permanent deletion

The bundle contains core 1.4.0 and console 0.4.0. Extract in /root and run
`bash upgrade.sh` as root. Existing AD/UPN config, password/URL, audit/state,
protections and archives are preserved. Busy core or queued/running jobs refuse
upgrade; source/settings backup and rollback remain available. No mail services
are stopped. For a fresh installation configure/install core/install.sh first,
then python3-venv and console/install-web.sh; the combined upgrade needs existing
console settings. Core timer is not enabled automatically on fresh installation.

Change the console password through Пароль консоли with current/new/repeated
password. All previous sessions require login again. Secrets are passed only in
memory through a root-owned local Unix socket with peer-UID checking, root-side
authentication and rate limiting; only the hash is stored. Root settings are
not writable by the web process. Forgotten password recovery, as root over SSH:
`bash /opt/iredmail-lifecycle-web/reset-console-password.sh`. It prompts securely
and restarts only frontend, leaving worker jobs running. AD passwords are unchanged.

For explicit one-mailbox permanent deletion, enable configure.py --allow-delete
yes (separate from --allow-live). Permission changes are refreshed without restart.
Refresh a normal report, select Проверить удаление файлов on unknown-disabled or
missing, review paths/size/identity, quiesce all writers, then enter УДАЛИТЬ, the
exact email and console password. Core DRY_RUN=0 is also required; DRY_RUN=1
reports no deletion. This core switch also controls the normal lifecycle timer.
Disable --allow-delete no afterwards. New approvals are single-use, including
failed operations; changed snapshots are rejected. Reviewed batch deletion is documented in BATCH_DELETE.md.

Only unique primary disabled AD users with unset/unknown HR status outside 0..8,
or mailboxes absent from the wide AD search base, qualify. Known statuses 0..8,
active/unknown UAC, aliases, conflicts, contacts and exclusions block deletion.
Wide AD address lookup and objectGUID rechecks guard moved/recreated identities.
GUID, UAC/status, inode/device, checksums and metadata must match the preview;
new exclusions override approval. Protected accounts retain automatic policy.

The whole working mailbox directory, including Maildir and other files, is
deleted without making a new archive. Existing archives/backups and AD/SQL/SOGo
are unchanged. Recovery is impossible unless another copy already exists.
Atomic non-overwrite detach to domain/.lifecycle-deleting/UUID/payload precedes
fd-based recursive removal on Linux. Interrupted/failed removal may leave partial
staging and an absent original path. Root operation.json records progress; staging
is not an archive and work is never replayed automatically. Inspect before retry.
Disable future recipient acceptance separately, or delivery may recreate the box.

Validation: fixture tests and browser demo inspection only. Ubuntu deployment,
real LDAP, Unix peer credentials/systemd and native Linux rename/removal have
not been exercised. See VALIDATION.txt and detailed core/console RU/EN manuals.
