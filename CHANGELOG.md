# Changes / Изменения

## 1.5.0 / web 0.5.0 — 2026-11-06

RU:

- Страница «Настройки»: AD/UPN, сервисный пароль, домены, хранилище, сроки, лимиты и разрешения операций.
- Проверка формата и подключения к обеим базам AD; обязательный TLS и повторная проверка перед сохранением рабочего режима.
- Управление ежедневным таймером, резервные копии и восстановление настроек в режиме проверки.
- Исходный конфиг сохраняется; управляемые значения читаются после него. Защита от параллельной обработки, незавершённых записей и старых подтверждений.
- Общий установщик `install.sh`, инструкция настройки через браузер; Linux-тесты для Ubuntu 22.04/24.04.
- Правила штатной архивации, legacy approval, исключений, восстановления и удаления не расширены.

EN:

- Settings page for AD/UPN, service password, domains, storage, retention, limits and operation permissions.
- Field validation and both-base AD connection checks; required TLS and fresh AD validation before saving live mode.
- Daily timer controls, configuration backups and restore into dry-run mode.
- Original configuration retained with managed overrides; processing locks, interrupted-write recovery and stale approval rejection.
- Combined `install.sh`, browser setup documentation and Ubuntu 22.04/24.04 fixture tests.
- Existing normal archival, explicit legacy approval, exclusion, restore and deletion policies remain in force.

## 1.4.0 / web 0.4.0

Explicit batch deletion with exact-list previews and independent limits. See [validation](docs/VALIDATION-1.4.0.txt). The `v1.4.0-agpl` publication changed the license to AGPL-3.0-only; see [licensing history](docs/LICENSING.md).
