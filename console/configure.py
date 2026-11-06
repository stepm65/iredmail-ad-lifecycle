#!/usr/bin/env python3
# Copyright (c) 2026, rslpro
# SPDX-License-Identifier: AGPL-3.0-only
"""Local root configuration; never exposed as an HTTP operation."""
import argparse
import getpass
import grp
import os
from pathlib import Path
import re
import secrets
from urllib.parse import urlsplit
from werkzeug.security import generate_password_hash
from common import atomic_json, read_json

parser = argparse.ArgumentParser()
parser.add_argument("--url", help="https://mail.example.ru/lifecycle/")
parser.add_argument("--username")
parser.add_argument("--set-password", action="store_true")
parser.add_argument("--allow-live", choices=["yes", "no"])
parser.add_argument("--report-dir")
parser.add_argument("--allow-delete", choices=["yes", "no"])
args = parser.parse_args()
if os.geteuid() != 0:
    raise SystemExit("Run as root / Запустите от root")
os.umask(0o027)
import fcntl
settings_lock = open("/run/iredmail-lifecycle-settings.lock", "a")
os.chmod("/run/iredmail-lifecycle-settings.lock", 0o600)
fcntl.flock(settings_lock, fcntl.LOCK_EX)
path = Path("/etc/iredmail-lifecycle-web/settings.json")
new = not path.exists()
c = read_json(path) if not new else {
    "username": "admin", "secret": secrets.token_hex(32), "allow_live": False,
    "jobs": "/var/lib/iredmail-lifecycle-web/jobs", "inbox": "/var/lib/iredmail-lifecycle-web/inbox",
    "web_state": "/var/lib/iredmail-lifecycle-web/web", "core": "/usr/local/sbin/iredmail-ad-lifecycle",
    "core_config": "/etc/iredmail-ad-lifecycle.conf", "helper": "/usr/local/lib/iredmail-ad-lifecycle/legacy.py",
    "report_root": "/var/log/iredmail-ad-lifecycle-reports", "job_timeout": 86400}
if args.url:
    url = urlsplit(args.url)
    if (url.scheme != "https" or not url.hostname or url.username or url.query or url.fragment
            or not re.fullmatch(r"(?:/[a-zA-Z0-9_-]+)*/?", url.path)):
        raise SystemExit("Укажите HTTPS URL с обычным путём, без логина/query/fragment")
    c["public_url"] = args.url.rstrip("/") + "/"
if "public_url" not in c:
    raise SystemExit("For first setup --url is required / При первой настройке нужен --url")
if args.username:
    if not re.fullmatch(r"[a-zA-Z0-9_-]{1,40}", args.username):
        raise SystemExit("Unsupported username")
    c["username"] = args.username
if args.report_dir:
    report = Path(args.report_dir)
    if not report.is_absolute():
        raise SystemExit("Absolute report path required")
    c["report_root"] = str(report)
if args.allow_delete is not None:
    c["allow_delete"] = args.allow_delete == "yes"
if args.allow_live is not None:
    c["allow_live"] = args.allow_live == "yes"
if new or args.set_password:
    password = getpass.getpass("Новый пароль консоли (минимум 12 символов): ")
    if len(password) < 12 or password != getpass.getpass("Повторите пароль: "):
        raise SystemExit("Passwords differ or too short / Пароли различаются или слишком короткие")
    c["password_hash"] = generate_password_hash(password, method="pbkdf2:sha256:600000")
path.parent.mkdir(parents=True, exist_ok=True)
c['config_revision'] = secrets.token_hex(16)
atomic_json(path, c)
os.chown(path, 0, grp.getgrnam("lc-web").gr_gid)
print("Настройки сохранены. Пароль AD не используется. Пароль/разрешения применяются без перезапуска. После изменения URL/путей перезапустите службы консоли, дождавшись заданий.")
