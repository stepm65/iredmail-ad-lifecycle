# Public-copy validation / Проверка публичной копии

Date / Дата: 2026-11-05.

This repository packages core 1.4.0 and console 0.4.0. Deployment-specific domain,
host and bind-user examples were replaced with generic values. No AD/mailbox policy
was changed. The BSD 3-Clause license from the initial repository was preserved.
Screenshots were visually reviewed and contain labelled demo data.

Результаты ниже получены заново на публичной копии. Временные ящики и подставная
LDAP-выдача; реальный сервер не подключался. Установленные ранее зависимости
тестов были недоступны, поэтому для проверок создана отдельная локальная среда.
Тестовые зависимости и локальные журналы не публикуются в репозитории.

| Test file | Cases | Result |
| --- | ---: | --- |
| `test_admin.py` | 16 | PASS |
| `test_batch.py` | 18 | PASS |
| `test_batch_delete.py` | 15 | PASS |
| `test_connect.py` | 39 | PASS |
| `test_console.py` | 33 | PASS |
| `test_delete.py` | 14 | PASS |
| `test_legacy.py` | 63 | PASS |
| `test_management.py` | 19 | PASS |

Total: **217 passing tests**, Windows / Python 3.12.10 and 3.12.14 with pinned console dependencies.
Public checkout/test paths were isolated from historical source copies.

Additional checks: all Python files parse with the Python 3.10 grammar; all Bash
scripts and the example core config pass `bash -n`; console JavaScript passes
`node --check`; relative Markdown links resolve; known private domain/hostname/local
path patterns and common private-key/GitHub-token patterns are absent from published
text. These checks are not a comprehensive security audit. Component manifests
were regenerated for public file contents; repository history retains the original license.

The earlier normal/CLI suites were not rerun for this packaging-only change.
See [the original release record](VALIDATION-1.4.0.txt) for their earlier evidence.
Real Ubuntu 22.04/24.04 deployment, Linux atomic removal, ACLs/ownership, live AD,
systemd, password broker, Gunicorn, Nginx/Apache and server upgrade/rollback have
not been exercised here. Validate on your target environment before enabling writes.
