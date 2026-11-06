# Установка / Installation

## RU — Новая установка

1. Проверьте рабочую структуру почты `VMAIL_ROOT/domain/login` с Maildir и совпадение адресов с LDAP-маппингом Postfix/Dovecot/SOGo. Проект не преобразует существующее хранилище.
2. Клонируйте репозиторий и установите зависимости:

   ```bash
   git clone https://github.com/stepm65/iredmail-ad-lifecycle.git
   cd iredmail-ad-lifecycle
   sudo apt-get update
   sudo apt-get install ldap-utils rsync util-linux python3 python3-venv coreutils
   sudo bash core/install.sh
   ```

3. Отредактируйте `/etc/iredmail-ad-lifecycle.conf`. Все `example.org` — примеры. Проверьте `LDAP_URI`, `BIND_DN`, `BIND_PW_FILE`, `BASE_DN`, `LEGACY_SEARCH_BASE`, `MAIL_DOMAINS`, `VMAIL_ROOT`, корни архивов, `EXPECTED_MIN_USERS`, задержки, сроки хранения и ограничения. `LEGACY_SEARCH_BASE` должен охватывать пользователей вне основного OU. Сопоставление адреса должно соответствовать рабочей почте; типичный UPN-вариант:

   ```bash
   ADDRESS_ATTRIBUTE="userPrincipalName"
   ADDRESS_INCLUDE_MAIL=0
   ADDRESS_INCLUDE_SMTP_ALIASES=0
   DRY_RUN=1
   LEGACY_MODE=0
   ```

4. Создайте указанный `BIND_PW_FILE`, доступный только root (`chmod 600`), с паролем сервисной УЗ чтения AD. Пароль не добавляется в репозиторий или командную строку. Настройте доверие к CA LDAP: шаблон использует обязательный STARTTLS (`-ZZ`). Не отключайте проверку сертификата ради подключения.
5. Выполните `sudo /usr/local/sbin/iredmail-ad-lifecycle --normal --report`. Сверьте реальные пути и статусы. При ошибке LDAP, неверном числе объектов или сомнительном соответствии адресов сначала исправьте конфигурацию.
6. Запустите `sudo bash console/install-web.sh`. Укажите публичный URL вида `https://mail.example.org/lifecycle/`, имя сервера, на который указывает CNAME, и отдельный пароль консоли. DNS и сертификат существующего HTTPS-сайта должны уже соответствовать публичному имени. Скрипт подключает маршрут к Nginx/Apache; не выпускает сертификаты.
7. Войдите в консоль, создайте отчёт и проверьте его. Включение реальных действий и таймера — отдельный осознанный шаг после проверки конфигурации. `DRY_RUN=0` влияет и на штатный timer. Для операций с файлами обеспечьте отсутствие всех процессов записи по инструкции конкретной операции.

Подробности: [core README](../core/README.md), [web README](../console/README.md), [конфиг с описанием параметров RU/EN](../core/iredmail-ad-lifecycle.conf).

## RU — Обновление

Из новой копии репозитория запустите `sudo bash upgrade.sh`. Это сценарий для **уже установленной консоли**, не для первой установки. Он сохраняет настройки и пароль, резервирует код/конфиги в `/var/backups/iredmail-lifecycle-upgrade`, проверяет очередь и блокировку основного обработчика, обновляет компоненты и подключает HTTPS. При занятых заданиях дождитесь их завершения. Почтовые службы и ОС он не обновляет и не останавливает.

Существующий основной конфиг сохраняется; новая версия шаблона записывается как `.conf.dist`. Сравните новые параметры вручную. Не заменяйте рабочие LDAP-параметры публичным шаблоном. Отчёты и архивы не включаются в Git.

## EN — Fresh installation

Use an existing Ubuntu 22.04/24.04 mail host with Python 3.10+, systemd, AD and a supported `VMAIL_ROOT/domain/login` Maildir layout. Clone this repository, install the apt dependencies above, and run `sudo bash core/install.sh`.

Edit `/etc/iredmail-ad-lifecycle.conf`: replace every `example.org` value, set read-only LDAP credentials, search bases, domains, storage/archive roots, directory-health thresholds and retention/delay limits. Select the primary address attribute used by your mail stack. Set a broad `LEGACY_SEARCH_BASE` to include moved users. Keep `DRY_RUN=1` and `LEGACY_MODE=0` during setup.

Create the configured LDAP password file with root-only mode `0600`. Configure LDAP CA trust; the example requires STARTTLS. Run `sudo /usr/local/sbin/iredmail-ad-lifecycle --normal --report` and inspect actual paths and AD states.

Run `sudo bash console/install-web.sh`. Supply the public HTTPS URL, the server/CNAME target and a separate console password. Existing DNS and certificate coverage are prerequisites. The installer adds the Nginx/Apache route; it does not issue certificates. Log in and create a fresh report. Real mutations and the core timer must be enabled separately after reviewing configuration; `DRY_RUN=0` also affects normal timer runs. Quiesce every writer before operations that require it.

## EN — Upgrade

For an existing console, run `sudo bash upgrade.sh` from a fresh checkout. The script checks jobs and the core lock, backs up code/configuration under `/var/backups/iredmail-lifecycle-upgrade`, preserves settings/password/state/archives/exclusions, updates both components and reconnects HTTPS. Busy jobs must finish first. Mail services and the OS are not upgraded or stopped.

Existing core configuration is preserved; a new `.conf.dist` template is provided. Review new options manually and do not replace live LDAP settings with the public example. Deployment against an actual Ubuntu/AD host still requires validation; published fixture tests do not exercise your server.
