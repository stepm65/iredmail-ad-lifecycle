# Validation 1.5.0 / console 0.5.0

Date: 2026-11-06. [Successful Linux run](https://github.com/stepm65/iredmail-ad-lifecycle/actions/runs/42216490160), tested implementation `f015b76e812b5bf062219cf2aa720d1ae3e82527`. Later release preparation adds documentation, screenshots and manifests.

Both **Ubuntu 22.04 / Python 3.10** and **Ubuntu 24.04 / Python 3.12** passed **273 tests per environment**:

| Suite | Cases |
| --- | ---: |
| Core legacy | 63 |
| Core restore/exclusions | 19 |
| Core single deletion | 14 |
| Core batch deletion | 15 |
| Core Bash/normal integration | 25 |
| HTTPS connector | 39 |
| Console jobs/auth | 33 |
| Console administration | 16 |
| Console batch actions | 18 |
| New web settings | 31 |

The settings suite covers typed values and bounds, unsupported shell syntax, storage paths, broad-base constraints, required TLS, authentication/CSRF/origin, atomic backups, failed-write and restart recovery, secret handling, safe restore, fresh AD checks before live settings, stale job/report rejection, fixed timer commands, real Linux flock and SO_PEERCRED acceptance/rejection. Core ownership-sensitive fixtures use isolated root-owned paths on ephemeral runners. The broker socket fixture uses test-owned configuration; production trusted-file checks remain enabled in deployed code.

Shell syntax, Python compilation and standard-library-only worker imports passed. The Windows portable test suites also passed; four settings cases requiring Linux/POSIX were skipped locally and passed on Linux. The older restore fixture needed a shorter Windows scratch path to avoid MAX_PATH; no production path rules were weakened.

Browser review used the actual Flask-rendered settings template with demo data, including desktop and 390px responsive viewport (no horizontal overflow). Screenshots: [settings](screenshots/settings.jpg), [save and timer](screenshots/settings-save.jpg). No real credentials or mailserver were used.

Not exercised: actual AD/TLS infrastructure, production systemd/HTTPS integration, real mailserver installation/upgrade, NAS/mount behavior, ACL/xattr fidelity and writer coordination. LDAP and external command boundaries use fixtures. Successful tests do not certify every iRedMail storage layout. First deployment remains dry run with a freshly reviewed report.

RU: На каждой из двух версий Ubuntu прошли 273 проверки, включая 31 новую для настроек. Проверены сохранение/откат, пароли, запрет старых подтверждений, Linux-блокировки и локальный сокет. Скриншоты сделаны на демонстрационных данных. Настоящий почтовый сервер, AD, systemd/HTTPS и обновление установленной системы этими тестами не проверялись; первый запуск выполняйте в режиме проверки.
