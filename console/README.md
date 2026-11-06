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

[ADMINISTRATION.md — подробная инструкция RU/EN](ADMINISTRATION.md)

# iRedMail Lifecycle Web Console / Веб-консоль

Version / Версия: **0.4.0**, separate addon for working lifecycle **1.4.0**.

## RU — Восстановление и постоянные исключения (0.2.0)

Требуется lifecycle **1.4.0** с manage.py: одной замены файлов веб-консоли недостаточно. Единый пакет содержит `core/`, `console/` и `upgrade.sh`. Запустите `bash upgrade.sh` от root: он остановит только веб-процесс, убедится, что очередь пуста и worker ничего не выполняет, возьмёт основной flock, сохранит исходные файлы/конфиги, обновит core и console, затем подключит HTTPS. Если идут задания или основной CLI/timer держит lock, обновление прекращается и прежний web запускается снова. Запущенная архивация не прерывается. Существующий config, пароль консоли, AD mapping, состояния, audit и архивы сохраняются. Новые параметры core по умолчанию поддерживают прежний конфиг. Веб-установщик отдельно требует остановленных служб консоли; единый upgrade делает это после проверки заданий.

**Исключения.** Откройте «Исключения», введите адрес и причину, нажмите «Исключить из обработки», дождитесь задания. Можно открыть эту форму прямо из строки штатного отчёта. Добавление работает даже при выключенных реальных переносах. Защита хранится в root-owned `EXCLUSION_FILE` (по умолчанию `$STATE_DIR/exclusions.json`) и действует для штатной архивации, всех legacy approvals/archive-all и retention обоих видов архивов. Ящик виден в следующем штатном отчёте как `excluded`, в inventory имеет `eligible=no`, `reason=excluded`. Ранее одобренный список не обходит новое исключение. Снятие требует слова `СНЯТЬ` и пароля консоли; после завершения задания возвращаются штатные правила AD/retention. Пустой отсутствующий файл допускается для обновления; повреждённый файл блокирует обработку. CSV на странице — снимок: после восстановления/внешних CLI-изменений нажмите «Обновить список».

**Восстановление.** Откройте «Архивы и восстановление» → «Обновить список архивов». Видны штатные и legacy-архивы только настроенных доменов. Выберите один архив → «Проверить восстановление». Core определяет путь из своего layout/marker, проверяет Maildir и отсутствие исходного каталога и формирует SHA256-снимок файлов/метаданных. Web передаёт только ID архива, произвольный путь не принимается. У старых штатных архивов без marker полнота исходной переписки неизвестна — это отмечено отдельно. Незавершённые marked/legacy-копии недоступны. При существующем каталоге, даже пустом, восстановления поверх него нет. Объединение писем с живым ящиком не реализовано.

Для реального восстановления нужны `allow_live=yes` в консоли и `DRY_RUN=0` в core; этот DRY_RUN также влияет на штатный timer. Обеспечьте отсутствие записи в выбранный адрес, подтвердите это, введите `ВОССТАНОВИТЬ` и пароль консоли. Проверка одноразовая. Core повторно проверит снимок, скопирует данные в staging на целевой файловой системе, проверит rsync checksum/метаданные и опубликует каталог атомарным renameat2 без перезаписи. UID/GID/права/ACL/xattr копируются rsync. Архив сохраняется. До публикации автоматически создаётся постоянное исключение, поэтому restored disabled+0 не уедет в архив следующим timer. Проверяйте права и доступность ящика после восстановления; AD, SQL-получатель, пароли, SOGo, кадровый статус и доставка не создаются/не включаются автоматически. Снимок не подтверждает полноту старого непомеченного архива.

При ошибке/timeout архив сохраняется; staging остаётся в `<domain>/.lifecycle-staging/<uuid>/payload` для разбора и не обнаруживается как ящик. Если исключение уже было записано, оно сохраняется даже при ошибке публикации. Ошибка после публикации может означать восстановленный ящик: проверьте источник, `.restored.json` и журнал перед повтором. Ничего не повторяется автоматически. Если renameat2 не поддерживается, публикация запрещена; стандартные Ubuntu 22.04/24.04 должны проверяться при развёртывании. Временные каталоги удаляются оператором только после проверки данных.

## RU — Назначение

Для вашего iRedMail на Ubuntu 22.04, с возможностью перенести дополнение на Ubuntu 24.04. Это отдельная консоль для lifecycle, а не замена SOGo/iRedAdmin. Она не меняет пользователей AD, почтовые SQL-записи, фильтры Postfix, конфиг SOGo или основной timer. Ваш рабочий lifecycle 1.4.0 остаётся установленным отдельно.

Возможности:

- Вход с отдельным паролем администратора консоли.
- Штатный безопасный отчёт, поиск и фильтры по классификации, выгрузка CSV и конфликтов AD.
- Legacy-инвентаризация: email, размер, путь в скачанном CSV, последняя файловая/каталожная активность и возраст.
- Выбор отдельных eligible-ящиков, создание approval CSV и проверка без переноса.
- Архивация только списка из успешно проверенного, неизменённого снимка. Повторное использование проверки запрещено.
- Очередь операций, состояние и полный журнал каждого задания. При перезапуске прерванное задание не запускается повторно автоматически.

**Штатная автоматическая архивация и legacy-retention выполняются прежним CLI/timer.** Из консоли нельзя запустить `archive-all`, purge, произвольную команду, менять shell-конфиг или остановить почтовые службы. Реальный перенос через консоль по умолчанию выключен. Службы доставки/IMAP нужно при необходимости останавливать и запускать средствами вашей действующей эксплуатации, а не кнопкой этого дополнения.

## RU — Как устроено

```text
Браузер → существующий HTTPS Nginx/Apache → 127.0.0.1:8127
                                          Flask/Gunicorn, пользователь lc-web
                                                  ↓ разрешённые задания
                                          root-worker → установленный lifecycle
```

Веб-процесс не имеет root и не читает пароль AD. Он записывает только очередь строго ограниченных операций. Root-worker повторно проверяет поля задания, снимок, одобрение и hashes; запускает фиксированные аргументы CLI без shell. Основные AD-проверки, mtime, writer quiescence, проверка копии и лимиты остаются в lifecycle. Ограничения основного конфига нельзя переопределить из HTTP-запроса. Один worker обрабатывает очередь последовательно; исходный flock lifecycle также действует против запуска по timer/CLI. Если flock занят, задание получает ошибку, а не обход блокировки.

Адреса согласуются с вашим SOGo `MailFieldNames=(userPrincipalName)` в **основном** `/etc/iredmail-ad-lifecycle.conf`:

```bash
ADDRESS_ATTRIBUTE="userPrincipalName"
ADDRESS_INCLUDE_MAIL=0
ADDRESS_INCLUDE_SMTP_ALIASES=0
```

Дополнение эти значения не переписывает. Disabled со статусами 1..8, unknown-disabled, ambiguous-ad, alias-only и missing по-прежнему защищены в штатном режиме. Legacy всегда повторно ищет по широкому base с UPN/mail/SMTP, независимо от штатных переключателей.

## RU — Исправление ошибки return/rewrite из 0.1.1

В 0.1.1 проверка ошибочно запрещала любой server-level rewrite. Ваш iRedMail включает глобальные правила SOGo `^/.well-known/caldav`, `^/.well-known/carddav`, `^/principals`; они не затрагивают `/lifecycle/`. Версия 0.1.2 допускает такие правила, сохраняет шаблоны SOGo/iRedAdmin/Roundcube и HTTP→HTTPS redirect. Location-level return/rewrite не мешают подключению консоли. Ограниченные правила для `/` или других приложений также допускаются, если доказано отсутствие пересечения с префиксом консоли.

Общие return, rewrite всего сайта, неизвестные PCRE-конструкции или условный server-level return по-прежнему требуют разбора: ошибка теперь показывает реальный файл, строку и тип/условие правила. Скрипт не удаляет правила и не отключает проверки. Это проверка ограниченной грамматики PCRE, а не совпадение с несколькими тестовыми URL. Escape-последовательности сохраняются; точка PCRE учитывается как wildcard.

Для уже установленной 0.1.1 достаточно нового connect-web.py: скопируйте `connect-web.py` на сервер и выполните:

```bash
python3 connect-web.py --server nginx \
  --vhost /etc/nginx/sites-enabled/00-default-ssl.conf
```

Скрипт спросит HTTPS URL и имя сервера. Если ранее был сохранён адрес `https://mailhost.example.org/lifecycle/`; если хотите открывать консоль через CNAME, укажите вместо него `https://mail.example.org/lifecycle/`. Настройка использует один выбранный публичный адрес. Имя сервера в обоих случаях `mailhost.example.org`. Для архивной поставки команды ниже.

## RU — Исправить 404 после установки 0.1.0/0.1.1

Распакуйте новый архив и подключите уже установленную консоль. Переустановка зависимостей и остановка worker не нужны:

```bash
cd /root
tar -xzf iredmail-lifecycle-web-console-0.2.0.tar.gz
cd iredmail-lifecycle-web-console
bash connect-web.sh
```

Скрипт запросит публичный HTTPS URL (по умолчанию сохранённый), имя сервера/цель CNAME (по умолчанию FQDN системы). Пример параметров для своего домена:

```text
Публичный HTTPS URL: https://mail.example.org/lifecycle/
Имя сервера / цель CNAME: mailhost.example.org
```

Тот же запуск с явными параметрами:

```bash
bash connect-web.sh --url https://mail.example.org/lifecycle/ \
  --server-hostname mailhost.example.org
```

CNAME определяет DNS-адрес сервера, но браузер передаёт Host/SNI `mail.example.org`. Скрипт сохраняет это публичное имя в настройках, отдельно сохраняет `server_hostname` и при необходимости добавляет публичное имя в server_name/ServerAlias выбранного сайта. Сертификат существующего сайта должен покрывать публичное имя; скрипт не выпускает сертификат и не меняет DNS. Существующий пароль/secret и разрешение live сохраняются.

## RU — Установка на существующий сервер

Требуются рабочий lifecycle 1.4.0, Python 3.10+, systemd, python3-venv и доступ к PyPI. Дополнение использует собственный `/opt/iredmail-lifecycle-web/venv`.

```bash
cd /root
tar -xzf iredmail-lifecycle-web-console-0.2.0.tar.gz
cd iredmail-lifecycle-web-console
apt-get update
apt-get install python3-venv
bash install-web.sh
```

Установщик запрашивает публичный HTTPS URL, создаёт отдельный пароль консоли (12+ символов с повтором; username по умолчанию admin), затем запрашивает имя сервера/цель CNAME и подключает консоль к существующему HTTPS-сайту. Не используйте пароль AD. Можно передать URL и имя сервера флагами, как в примере repair выше. Для другого пользователя задайте `configure.py --username NAME` локально.

**Подключение теперь выполняется автоматически по умолчанию:** скрипт определяет активный Nginx/Apache, читает загруженную конфигурацию и выбирает HTTPS virtual host по публичному имени; если оно пока не настроено, учитывает заданную цель CNAME. Один HTTPS catchall iRedMail также поддерживается. При неоднозначности показывает список файлов/блоков и спрашивает номер; при отсутствии терминала требует явные параметры. Другой веб-сервер требует ручного подключения.

Перед изменением создаётся резервная копия реального файла (symlink sites-enabled остаётся на месте) в `/var/backups/iredmail-lifecycle-web/*.bak`. В выбранный HTTPS-блок добавляется только маршрут консоли и, если нужно, публичное имя. Include SOGo/Roundcube/iRedAdmin сохраняются. Backend запускается на `127.0.0.1:8127` и проверяется по `/login`. Далее выполняется `nginx -t` либо `apache2ctl configtest` и reload работающего веб-сервера. Apache получает недостающие proxy/proxy_http/headers. При ошибке проверки/reload конфиг восстанавливается; параллельные правки администратора не перезаписываются. Почтовые службы, DNS, сертификаты и основной timer не меняются.

Проверка публичного HTTPS URL выполняется curl с обычной проверкой сертификата. Если она не прошла, выводится отдельный WARNING: успешная проверка локального backend/конфига ещё не подтверждает доступность публичного адреса. Изменение маршрута при такой внешней ошибке не откатывается.

Просмотреть план без запуска служб/редактирования настроек или сайта:

```bash
bash connect-web.sh --check --url https://mail.example.org/lifecycle/ \
  --server-hostname mailhost.example.org
```

Явный выбор, если автопоиск неоднозначен (указывайте реальный файл своего HTTPS-сайта):

```bash
bash connect-web.sh --server nginx --vhost /etc/nginx/sites-available/00-default-ssl.conf
# При нескольких HTTPS-блоках в одном файле добавьте --block-line НОМЕР_СТРОКИ_SERVER
```

Для Apache аналогично `--server apache2 --vhost /etc/apache2/sites-available/ВАШ-САЙТ.conf`. Для общих/неизвестных server-level return/rewrite, regex server_name, включённого из другого файла server_name или уже занятого location скрипт может отказаться от автоматической вставки: он сообщает причину и не перезаписывает ручной маршрут. Старый стандартный Nginx snippet из 0.1.0 распознаётся и повторно не вставляется. Шаблоны ручного подключения остаются в `proxy/`. Явный `install-web.sh --no-proxy` устанавливает файлы/настройки без подключения и запуска служб. Для фактического обновления Python-файлов уже работающей консоли дождитесь заданий и остановите только две её службы перед переустановкой; для исправления 404 достаточно `connect-web.sh`.

Если `REPORT_DIR` основного lifecycle отличается от `/var/log/iredmail-ad-lifecycle-reports`, задайте такой же `--report-dir` через configure.py до создания отчётов. Не публикуйте порт 8127 наружу. Дополнительные ограничения доступа по административной сети можно добавить в location отдельно.

Откройте `https://mail.example.org/lifecycle/`, войдите и нажмите «Обновить отчёт». Консоль сначала пуста: она показывает отчёты своего worker, старые CLI CSV автоматически не импортируются. Задание продолжает выполняться при закрытии вкладки; время в интерфейсе указано в UTC.

Если 404 сохраняется, выполните connector и изучите его вывод: какой сайт выбран и прошла ли публичная проверка. Дополнительная диагностика:

```bash
systemctl status iredmail-lifecycle-web.service iredmail-lifecycle-worker.service --no-pager
curl -i http://127.0.0.1:8127/login -H 'Host: mail.example.org' \
  -H 'X-Forwarded-Proto: https' -H 'X-Forwarded-Prefix: /lifecycle'
```

Локальная проверка ожидает HTTP 200 и страницу IREDMAIL LIFECYCLE. Сообщение CONNECT ERROR требует исправления указанной причины; скрипт не утверждает, что сайт работает, до проверки. Не публикуйте settings.json или пароли в диагностике.

## RU — Очистка старых ящиков через веб

1. «Legacy-инвентаризация» создаёт новый снимок без переносов. Проверки выполняются рабочим CLI и широким `LEGACY_SEARCH_BASE`.
2. Откройте результат. Скачайте полный CSV, если нужно. Отметьте конкретные ящики «К проверке»; защищённые строки не выбираются. В интерфейсе максимум 500 выбранных адресов, но реальные `LEGACY_MAX_COUNT` и `LEGACY_MAX_PERCENT` в основном конфиге обычно значительно ниже и проверяются CLI. При необходимости обновите штатный отчёт отдельно.
3. «Проверить выбранные» создаёт root-owned approval CSV и запускает безопасную проверку. Это ещё не перенос. Не смешивайте снимки от разных инвентаризаций.
4. Просмотрите список и журнал. Реальный перенос появится только после явного включения live на сервере:

```bash
/opt/iredmail-lifecycle-web/venv/bin/python \
  /opt/iredmail-lifecycle-web/configure.py --allow-live yes
systemctl restart iredmail-lifecycle-worker.service iredmail-lifecycle-web.service
```

Основной lifecycle также требует `DRY_RUN=0`. **Изменение DRY_RUN влияет и на его штатный timer**, не только на консоль; согласуйте это с существующей эксплуатацией. Консоль не меняет DRY_RUN сама.

5. Обеспечьте остановку записи в выбранные ящики: доставка, IMAP/POP, очереди, локальные писатели. Если при подготовке изменился mtime, создайте новую инвентаризацию и новый preview. На странице успешной проверки отметьте подтверждение остановки записи, введите `АРХИВИРОВАТЬ` и пароль консоли. Worker использует только зафиксированный список; повторно выполняются все проверки lifecycle. Пароль в очередь и аудит не записывается.
6. Если основной DRY_RUN=1, результат будет «Только проверка», а не ложное сообщение об успешном переносе. Если операция прервана/ошибочна, проверьте журнал и реальное состояние ящиков/копий. Preview одноразовый даже при ошибке; для повтора нужен новый preview. Консоль не восстанавливает данные автоматически.
7. После очистки выключите live:

```bash
/opt/iredmail-lifecycle-web/venv/bin/python \
  /opt/iredmail-lifecycle-web/configure.py --allow-live no
systemctl restart iredmail-lifecycle-worker.service iredmail-lifecycle-web.service
```

Оставьте `LEGACY_MODE=0` в основном конфиге и штатный timer с `--normal`, как в рабочем пакете. Архивы и legacy-retention остаются под управлением CLI; веб-консоль не включает массовую архивацию missing.

## RU — Настройки и эксплуатация

| Параметр | Назначение |
|---|---|
| `server_hostname` | Имя машины/цель CNAME для подключения; не заменяет публичный Host |
| `public_url` | Точное HTTPS-имя и путь; Host проверяется; prefix должен совпадать с proxy |
| `username`, `password_hash` | Отдельная учётная запись консоли; пароль меняется только локальным configure.py |
| `secret` | Ключ подписи cookies; хранится в root-owned settings.json |
| `allow_live` | По умолчанию false; разрешение только одобренной legacy-архивации |
| `report_root` | Должен совпадать с REPORT_DIR основного lifecycle |
| `job_timeout` | 86400 секунд; при превышении завершается группа процессов задания |
| `jobs` | Root-owned результаты, CSV, approvals, logs; web имеет только чтение |
| `inbox` | Отдельная очередь web → root-worker; только фиксированный JSON-протокол |
| `web_state` | SQLite лимита попыток входа; writable только пользователем консоли |

Сессия действует один час с продлением при активности. Cookies Secure/HttpOnly/SameSite=Strict, CSRF для POST, Host allowlist и CSP. После пяти неверных входов с одного IP действует окно 10 минут. Для определения IP используйте snippets с перезаписью `X-Forwarded-For`, не передавайте клиентский заголовок без очистки. Это одиночная административная учётная запись; AD SSO, роли и MFA не реализованы.

Аудит хранится в `/var/lib/iredmail-lifecycle-web/jobs/<id>/`: `request.json`, `result.json`, `run.log` и соответствующие CSV. Содержит адреса и пути ящиков; сохраняйте резервную копию и ограничивайте доступ. Автоматического удаления аудита нет. `interrupted` не означает, что исходные ящики обязательно целы: изучите журнал и основной архивный marker перед повтором.

Диагностика:

```bash
journalctl -u iredmail-lifecycle-web.service -u iredmail-lifecycle-worker.service -n 100 --no-pager
```

502 — проверьте службу web и loopback порт. 400 Host — URL должен использовать hostname из settings.json. Пустые/ошибочные отчёты — смотрите журнал задания, REPORT_DIR/report_root и права основного config/helper. Ключевые файлы CLI/helper/config и родительские каталоги должны принадлежать root и не быть writable группой/остальными. Пароль AD с путём в `/root` доступен root-worker; web-процесс его не читает.

Резервируйте `/etc/iredmail-lifecycle-web/settings.json` и `/var/lib/iredmail-lifecycle-web`, вместе с рабочим lifecycle, его состоянием и архивами. `bash uninstall-web.sh` отключает только две службы консоли и сохраняет настройки/аудит; вручную удалите её proxy-location и проверьте конфиг веб-сервера.

## RU — Переход Ubuntu 22.04 → 24.04

Обновление ОС/почтового стека не выполняется этим комплектом. Для iRedMail используйте его отдельную официальную инструкцию. После согласованного обновления Python пересоздайте **только venv этой консоли**. Дождитесь текущих заданий; остановите обе службы консоли, сохраните старый venv переименованием, создайте новый и установите зависимости:

```bash
systemctl stop iredmail-lifecycle-web.service iredmail-lifecycle-worker.service
cd /opt/iredmail-lifecycle-web
mv venv "venv.before-os-upgrade-$(date +%Y%m%d-%H%M%S)"
python3 -m venv venv
venv/bin/pip install -r requirements.txt
systemctl start iredmail-lifecycle-worker.service iredmail-lifecycle-web.service
```

Затем повторите вход, штатный отчёт и inventory/preview. Настройки и audit лежат вне venv. Системные virtualenv iRedMail/iRedAPD/iRedAdmin не затрагиваются этими командами. Разрешение зависимостей проверено для Linux x86_64 Python 3.10 и 3.12; фактическая установка служб на вашем Ubuntu ещё не проверена.

## EN — Restore and persistent exclusions (0.2.0)

Requires lifecycle 1.4.0 including manage.py. Use the combined package's upgrade.sh: it stops the frontend, refuses queued/running work or a busy core flock, backs up source/settings, upgrades core and console, then reconnects HTTPS. It never interrupts a running archival job. Existing configs/password/mapping/audit/state/archives are preserved. A failed update restores backed-up source/settings and previously running console services. Mail services are not stopped by the updater.

Persistent exclusion add/remove is available in the UI; add requires an address/reason, removal requires password and typed СНЯТЬ. Adding protection works even with allow_live disabled. The root-owned EXCLUSION_FILE defaults to STATE_DIR/exclusions.json and protects normal/legacy archival and both retention flows. Invalid policy fails closed; an absent file is an empty initial list. New protection overrides an old approval. Reports classify excluded; missing inventory marks it ineligible. Refresh snapshots after restore/CLI changes.

Archives → refresh → select one → restore preview. Core supplies canonical paths from configured roots/layout, binds exact data and metadata to a checksum snapshot, and rejects existing targets. Historical unmarked normal archives are supported with an explicit unknown-completeness warning; incomplete legacy/marked copies are blocked. No merge/overwrite is provided. The web passes only a 64-hex archive ID tied to a completed catalog job and an immutable preview.

Live restore requires allow_live=yes, core DRY_RUN=0, writer-quiescence acknowledgment, password and typed ВОССТАНОВИТЬ. The preview is single-use even on failure. Rsync preserves numeric ownership/ACL/xattrs; checksum validation precedes an atomic renameat2 RENAME_NOREPLACE from same-filesystem staging. Archive copy is kept. Permanent protection is written before publication so the timer cannot immediately rearchive a restored disabled+0 mailbox. AD/SQL accounts, passwords, SOGo, employment state and delivery are not re-enabled/created. Inspect ownership and mailbox accessibility afterwards.

Errors preserve archive and unpublished staging under domain/.lifecycle-staging/UUID/payload. A protection written before a failed publication remains. Failure after publication may still mean data was restored: inspect target/audit before retry. Unsupported renameat2 refuses publication. POSIX ownership, real rsync fidelity and Ubuntu deployment are not proven by Windows fixture tests.

## EN — Purpose and safety

Separate web addon **0.4.0** for working lifecycle **1.4.0**, designed for Ubuntu 22.04 and later migration to 24.04. It provides authenticated normal reports, classification search, CSV/conflict downloads, legacy inventory, per-mailbox selection, dry-run approval previews and explicitly approved archival. The UI is Russian; this guide includes English deployment instructions.

The existing lifecycle policy, SOGo UPN mapping, AD protections, broad legacy lookup, copy verification, inactivity thresholds and batch limits remain authoritative. The addon does not change AD, SQL recipients, SOGo, Postfix, mail services, or the normal lifecycle timer. It cannot execute arbitrary commands, archive-all or purge. Normal automatic runs and legacy retention remain CLI/timer tasks. It does not stop writers; restore is the explicit reviewed workflow described above.

Flask/Gunicorn runs as `lc-web`, behind the existing HTTPS server, binding only `127.0.0.1:8127`. A separate root worker revalidates a narrow JSON queue and invokes fixed CLI arguments without a shell. The web process cannot read AD credentials or modify root-owned results/approved snapshots. Preview hashes bind the exact inventory/approval; a preview is single-use even on failure. One worker serializes jobs; the existing lifecycle flock remains in force. Interrupted jobs are not replayed.

## EN — 0.1.2 rewrite fix

0.1.1 mistakenly rejected every server-level rewrite. 0.1.2 permits proven disjoint root/application redirects and the reported SOGo /.well-known/caldav, /.well-known/carddav and /principals rules while preserving their template files. Location-level redirects do not block console attachment. General returns, broad or unknown PCRE rewrites and conditional server returns remain rejected with source file/line diagnostics. The checker uses a small proven grammar, handles wildcard dots and preserves PCRE escapes; it does not approve a rule by testing sample URLs.

Use the stdlib-only console/connect-web.py from this repository for an existing installation, or extract the full package and run connect-web.sh. No dependency reinstall or worker shutdown is required. If the previously saved public URL is https://mailhost.example.org/lifecycle/; explicitly enter https://mail.example.org/lifecycle/ if the CNAME should be the public address. Machine FQDN remains mailhost.example.org.

## EN — Repair existing 0.1.0/0.1.1 installation / install

Extract the 0.1.2 archive and run `bash connect-web.sh` as root to attach an already installed console. No reinstall or worker shutdown is required for this repair. It prompts for the public HTTPS URL and machine FQDN/CNAME target, retaining saved values as defaults. For example, use `https://mail.example.org/lifecycle/` and `mailhost.example.org`. Explicit flags are available:

```bash
bash connect-web.sh --url https://mail.example.org/lifecycle/ \
  --server-hostname mailhost.example.org
```

The public name remains the HTTP Host/SNI even when DNS is a CNAME. It is stored separately from the machine name and added to the selected server_name/ServerAlias when needed. The existing certificate must cover the public name. Password/secret/live permission and jobs are preserved; no DNS/certificate changes are made.

For a new installation, working lifecycle 1.4.0, Python 3.10+, systemd, python3-venv and PyPI access are required. Extract, install python3-venv and run `bash install-web.sh`. It prompts for URL, a separate 12+ character console password and the machine name, then attaches the console automatically. The isolated venv does not affect iRedMail's Python environments. Username defaults to admin; use local configure.py for --username or a custom --report-dir matching core REPORT_DIR.

The connector detects active Nginx/Apache and inspects loaded files (Apache candidates also verified by apache2ctl -S). It selects by public hostname or an explicitly supplied CNAME target, supports a single iRedMail catchall and presents an interactive menu when ambiguous. Noninteractive ambiguity requires --server/--vhost/--block-line. It backs up the real file outside include trees in /var/backups/iredmail-lifecycle-web, keeps sites-enabled symlinks and existing mail-app includes, inserts a managed route and public name, starts/enables the console services and checks loopback /login. Native nginx -t/apache2ctl configtest precedes reload. Config/reload errors restore the original file; concurrent admin edits are not overwritten. Missing Apache proxy/proxy_http/headers modules are enabled. Mail services and the core timer are untouched.

`--check` shows the plan without edits or service startup. `--server nginx --vhost PATH` (or apache2) chooses an explicit loaded site; --block-line disambiguates multiple blocks. Existing conflicting manual routes, complex included server_name or server-wide redirects are rejected rather than overwritten. Reference snippets remain in proxy/. The old standard Nginx snippet is recognized. `install-web.sh --no-proxy` explicitly opts out of automatic attachment/startup. Updating running application files still requires waiting for jobs and stopping the two console services first; fixing only routing does not.

The final public curl probe verifies the existing certificate normally. A WARNING means external HTTPS was not confirmed, even if backend and config/reload checks passed; the valid local route is kept for investigation. See the RU section for repair, plan and diagnostics commands. Do not expose loopback port 8127. Open the URL and generate the first report; old CLI CSVs are not imported. Jobs continue after browser closure and UI timestamps use UTC.

## EN — Legacy workflow

Generate an inventory, inspect it, select only specific eligible mailboxes, then run “Проверить выбранные”. The worker creates an immutable YES approval CSV and invokes CLI report mode. Protected selections are rejected again server-side. The UI cap of 500 selections does not relax core MAX_COUNT/MAX_PERCENT (usually much lower). Inspect the preview log and exact list.

Live archival defaults to disabled. To enable it deliberately:

```bash
/opt/iredmail-lifecycle-web/venv/bin/python /opt/iredmail-lifecycle-web/configure.py --allow-live yes
systemctl restart iredmail-lifecycle-worker.service iredmail-lifecycle-web.service
```

The core also requires DRY_RUN=0. Changing that core setting affects its normal timer too; the console never changes it. Quiesce all delivery/IMAP/other writers for selected mailboxes through your operating procedures. If preparation changes mtimes, regenerate inventory/preview. Confirm writer quiescence in the UI, type `АРХИВИРОВАТЬ`, and re-enter your console password. No password is queued/logged. If core DRY_RUN remains 1, the UI marks “Только проверка”, not archival success. After cleanup set `--allow-live no`, restart both console services and keep core LEGACY_MODE=0 and normal timer `--normal`. Legacy archive/purge remains the CLI's responsibility.

## EN — Operations, upgrade and limits

Settings are root-owned `/etc/iredmail-lifecycle-web/settings.json`, readable by lc-web. Jobs/snapshots/approval/logs are root-owned `/var/lib/iredmail-lifecycle-web/jobs`; only inbox and login-rate state are writable by the web account. Protect and back up audit data, which contains mailbox identities/paths. Audit retention is manual. The administrator account is local and single-user; no AD SSO, roles or MFA. Sessions last one hour with activity refresh; cookies are Secure/HttpOnly/Strict, requests have CSRF protection, Host restriction and CSP. Five failed logins per IP trigger a 10-minute window. Proxy snippets must overwrite client-supplied forwarding headers.

Use journalctl for both services and each job's full log. A timed-out/interrupted job may have copied or moved some mailboxes: inspect actual core markers and sources before trying again; it is never automatically replayed. Key executable/helper/core config and their ancestors must be root-owned without group/world write. Root worker can access a bind secret in /root; the web process cannot.

After your separately managed Ubuntu/Python upgrade, stop console services after jobs finish, rename this addon's old venv, create it again with the new python3, install requirements.txt, then restart and recheck login/report/inventory/preview. Keep settings/audit outside the venv. See the RU command block above. This procedure does not touch iRedMail's own virtualenvs. Linux dependency resolution was checked for x86_64 Python 3.10 and 3.12; real Ubuntu/systemd/proxy installation still requires validation on your server.

`uninstall-web.sh` stops/disables only the two console services and preserves files/settings/audit. Remove its proxy location yourself and test/reload your web server.

## Sources / Источники

- [iRedMail: existing Nginx virtual host and web application templates](https://docs.iredmail.org/subdomain.web.apps.html)
- [iRedMail: Ubuntu 22.04 → 24.04 upgrade](https://docs.iredmail.org/upgrade.ubuntu.22.04-24.04.html)
- [Flask: Gunicorn deployment as a non-root process](https://flask.palletsprojects.com/en/stable/deploying/gunicorn/)
- [Flask: proxy trust configuration](https://flask.palletsprojects.com/en/stable/deploying/proxy_fix/)
- [Flask: web security and CSRF](https://flask.palletsprojects.com/en/stable/web-security/)
- [Nginx: rewrite phase and server/location order](https://nginx.org/en/docs/http/ngx_http_rewrite_module.html)
- [Nginx: proxy module](https://nginx.org/en/docs/http/ngx_http_proxy_module.html)
- [Apache: mod_proxy](https://httpd.apache.org/docs/current/mod/mod_proxy.html)
- [Gunicorn release metadata](https://pypi.org/project/gunicorn/26.2.0/)
