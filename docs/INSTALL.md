# Установка и обновление / Installation and upgrade

## RU — Новая установка

Целевая среда — Ubuntu 22.04/24.04, существующий iRedMail с AD, systemd, Python 3.10+. Проверьте структуру `VMAIL_ROOT/domain/login` с Maildir и соответствие адресов LDAP-маппингу почтового стека. Проект не преобразует существующее хранилище.

```bash
git clone https://github.com/stepm65/iredmail-ad-lifecycle.git
cd iredmail-ad-lifecycle
sudo bash install.sh
```

Общий установщик устанавливает зависимости, основной обработчик и веб-консоль. Укажите публичный URL, например `https://mail.example.org/lifecycle/`, имя сервера/цель CNAME и отдельный пароль консоли. Скрипт подключает маршрут к существующему Nginx/Apache. DNS, сертификат сайта и доверие к CA AD должны быть подготовлены; установщик не выпускает сертификаты. Таймер автоматически не включается.

Дальше работайте в браузере:

1. Войдите → **Настройки**.
2. Задайте AD, сервисный пароль, обе базы поиска, домены и реальные пути. Сохраните UPN-маппинг, если он используется SOGo. Замените `example.org`, проверьте минимум объектов AD и все сроки/лимиты.
3. Оставьте **Только проверка = Да**. Проверьте подключение и сохраните настройки. [Подробная инструкция](SETTINGS.md).
4. Создайте отчёт и проверьте пути, адреса, статусы. После проверки разрешите нужные операции и при необходимости включите таймер на той же странице. Изменение `DRY_RUN` влияет и на уже включённый таймер.

Для файловых операций выполняйте требования остановки записи и подтверждений конкретной операции. Сохранение настроек само по себе не переносит почту.

## RU — Обновление установленной консоли

Из новой копии репозитория запустите:

```bash
sudo bash upgrade.sh
```

Общий `install.sh` также передаёт управление обновлению, если обнаружена существующая консоль. Дождитесь завершения очереди. Обновление проверяет блокировки, сохраняет код/конфиги в `/var/backups/iredmail-lifecycle-upgrade`, обновляет обе части и подключает HTTPS. Пароль и URL, исходный конфиг, управляемые настройки, состояние, исключения и архивы сохраняются. Почтовые службы и ОС не обновляются и не останавливаются.

После обновления откройте **Настройки**: значения прочитаются из действующего конфига. Сохранение создаёт root-only управляемые переопределения; исходный файл не заменяется публичным шаблоном. Новые отчёты/проверки создавайте заново. Если сохранение ранее оборвалось, сначала дождитесь восстановления настроек службой worker; незавершённая запись блокирует обновление и обработку.

Для установки только CLI остаётся `core/install.sh` и [руководство core](../core/README.md); команды ручной конфигурации в руководствах компонентов относятся к этому варианту и аварийному обслуживанию.

## EN

Target an existing Ubuntu 22.04/24.04 iRedMail/AD host with systemd and Python 3.10+. Verify the `VMAIL_ROOT/domain/login` Maildir layout and mail-stack address mapping. Clone the repository and run **`sudo bash install.sh`**. This installs dependencies, core and console. Supply the public HTTPS URL, server/CNAME target and separate console password. Existing Nginx/Apache, DNS, certificate coverage and AD CA trust are prerequisites. The installer connects the HTTPS route but does not issue certificates or enable the core timer.

Log in, open **Настройки** (Settings), set AD/service password, both search bases, domains, storage, counts and retention/limits. Keep UPN when used by SOGo. Replace sample values, keep dry run enabled, test AD, save and generate a report. Enable required live permissions and the timer separately after review. See [web settings](SETTINGS.md). Changing `DRY_RUN` also changes future scheduled runs. File operations retain their writer-quiescence and confirmation requirements.

For an existing console run **`sudo bash upgrade.sh`** from a new checkout. `install.sh` detects an existing console and delegates to upgrade. Wait for queued jobs first. The script checks locks, backs up code/configuration under `/var/backups/iredmail-lifecycle-upgrade`, updates both components and reconnects HTTPS while preserving credentials, URL, base and managed configuration, state, exclusions and archives. Mail services and the OS are not upgraded or stopped.

Open Settings after upgrade: it loads existing values. Saving writes root-only managed overrides while retaining the base file. Regenerate reports and operation previews. Incomplete configuration recovery blocks processing/upgrades until the worker has recovered it. CLI-only installation and emergency maintenance remain documented in component manuals. Real-server integration still needs deployment validation.
