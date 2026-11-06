# Contributing / Разработка

Core scripts are in `core/`; the Flask frontend, privileged worker, HTTPS connector and password broker are in `console/`. Keep the existing AD safety policy and explicit-approval boundaries when changing code. Never commit deployment settings, passwords, LDAP dumps, live reports or mailbox data. Use synthetic fixtures in bug reports and tests.

Основной обработчик — `core/`, веб-консоль и worker — `console/`. Не добавляйте автоматическое разрешение операций для `missing`/неизвестного статуса и не обходите исключения, повторную проверку AD или явное подтверждение. Для воспроизведения ошибок используйте искусственные данные.

Tests use Python's `unittest`. Run test files individually to keep their module imports isolated, for example:

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r console/requirements.txt
python core/tests/test_legacy.py
python core/tests/test_management.py
python core/tests/test_delete.py
python core/tests/test_batch_delete.py
python console/tests/test_connect.py
python console/tests/test_console.py
python console/tests/test_admin.py
python console/tests/test_batch.py
python console/tests/test_settings.py
```

The normal/CLI fixture in `core/tests/test_normal.py` additionally needs Bash. On Linux, core exclusion/restore fixtures require root-owned ancestors without group/world write; run them only on an isolated test VM with `LIFECYCLE_TEST_TMP` pointing to a dedicated root-owned directory under `/var/lib`. The CI workflow demonstrates this setup on ephemeral Ubuntu 22.04/24.04 runners. Do not run fixture tests with production configuration. On Windows a short writable `LIFECYCLE_TEST_TMP` avoids MAX_PATH limits. The new settings suite also exercises actual Linux flock and Unix peer credentials, while mocking AD/systemctl boundaries. Passing fixtures does not validate production LDAP, systemd integration, Nginx/Apache, ACL preservation or a server upgrade.

Public packaging replaces deployment-specific example domains with `example.org`; screenshots retain clearly labelled synthetic fixture addresses. The original release validation record is in `docs/VALIDATION-1.4.0.txt`; public-copy checks are recorded separately in `docs/PUBLICATION_CHECKS.md`.

## Pull requests / Отправка доработок

Мы просим направлять исправления и новые функции в
https://github.com/stepm65/iredmail-ad-lifecycle через pull request.

1. Создайте fork и отдельную ветку изменения.
2. Добавьте описание проблемы, изменения и выполненных проверок.
3. Для изменений обработки почты добавьте проверку соответствующей защиты на временных данных.
4. Откройте pull request в основную ветку этого репозитория.

Pull request доброволен. AGPL-3.0 не требует отправки изменений именно нам,
а принятие изменения в основную ветку остаётся решением сопровождающего.
Предлагая код для включения в проект, предоставляйте его на условиях
AGPL-3.0-only и указывайте сторонние компоненты/лицензии. Авторские права
на ваши изменения остаются у соответствующего правообладателя.
Не включайте пароли, рабочие LDAP-выгрузки, персональные данные или почту.

Please fork, create a focused branch, describe your changes and validation,
and open a pull request against this repository. Upstream submission is
voluntary; AGPL does not require submission to this particular repository.
Offer contributions for inclusion under AGPL-3.0-only and disclose third-party
code/licenses. Contributors retain copyright; maintainers decide what to merge.

If deploying a modified network version, provide your users with its actual
Corresponding Source as required by AGPL section 13. Update the console source
link to that version; a link to the unmodified upstream alone is insufficient
when your deployment contains changes.
