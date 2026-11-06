#!/usr/bin/env python3
# Copyright (c) 2026, rslpro
# SPDX-License-Identifier: AGPL-3.0-only
"""Narrow local password broker; secrets stay in memory, never in job files."""
import argparse
import json
import os
from pathlib import Path
import socket
import stat
import struct
import time
from werkzeug.security import check_password_hash, generate_password_hash
from common import atomic_json, read_json

SOCKET_PATH = '/run/iredmail-lifecycle-web/password.sock'
LOCK_PATH = '/run/iredmail-lifecycle-settings.lock'


def validate(data):
    if not isinstance(data, dict) or set(data) != {'current', 'new'}:
        raise ValueError('Некорректный запрос смены пароля')
    if (not isinstance(data['current'], str) or len(data['current']) > 256
            or not isinstance(data['new'], str) or not 12 <= len(data['new']) <= 256
            or any(ord(c) < 32 for c in data['new'])):
        raise ValueError('Новый пароль: 12–256 символов без управляющих символов')


def change(path, data):
    validate(data)
    c = read_json(path)
    if c.get('maintenance'):
        raise ValueError('Выполняется обновление; повторите позже')
    if not check_password_hash(c['password_hash'], data['current']):
        raise ValueError('Неверный текущий пароль')
    if check_password_hash(c['password_hash'], data['new']):
        raise ValueError('Новый пароль должен отличаться от текущего')
    info = Path(path).stat()
    c['password_hash'] = generate_password_hash(data['new'], method='pbkdf2:sha256:600000')
    c['password_changed_at'] = int(time.time())
    atomic_json(path, c, mode=stat.S_IMODE(info.st_mode))
    if os.name == 'posix':
        os.chown(path, info.st_uid, info.st_gid)


def request_change(current, new):
    data = dict(current=current, new=new)
    validate(data)
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.settimeout(10)
        client.connect(SOCKET_PATH)
        client.sendall(json.dumps(data, ensure_ascii=False).encode('utf-8') + b'\n')
        response = b''
        while b'\n' not in response and len(response) <= 4096:
            chunk = client.recv(4096)
            if not chunk:
                raise ValueError('Служба смены пароля не ответила')
            response += chunk
    result = json.loads(response.decode('utf-8'))
    if result.get('ok') is not True:
        raise ValueError(result.get('error', 'Пароль не изменён'))


def serve(config):
    import fcntl
    import pwd
    from worker import trusted
    trusted(config)
    parent = Path(SOCKET_PATH).parent
    trusted(parent)
    allowed_uid = pwd.getpwnam('lc-web').pw_uid
    path = Path(SOCKET_PATH)
    if path.exists() or path.is_symlink():
        if not stat.S_ISSOCK(path.lstat().st_mode) or path.lstat().st_uid != 0:
            raise ValueError('Unsafe password socket')
        path.unlink()
    failures = []
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener:
        listener.bind(SOCKET_PATH)
        path.chmod(0o660)
        listener.listen(4)
        while True:
            connection, _ = listener.accept()
            with connection:
                connection.settimeout(5)
                try:
                    _, uid, _ = struct.unpack('3i', connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
                    if uid != allowed_uid:
                        raise ValueError('Доступ запрещён')
                    now = time.time()
                    failures[:] = [t for t in failures if now - t < 600]
                    if len(failures) >= 5:
                        raise ValueError('Слишком много попыток; повторите через 10 минут')
                    raw = b''
                    while b'\n' not in raw and len(raw) <= 4096:
                        chunk = connection.recv(4096)
                        if not chunk:
                            raise ValueError('Пустой запрос')
                        raw += chunk
                    if len(raw) > 4096:
                        raise ValueError('Запрос слишком большой')
                    data = json.loads(raw.decode('utf-8'))
                    with open(LOCK_PATH, 'a') as lock:
                        os.chmod(LOCK_PATH, 0o600)
                        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                        try:
                            change(config, data)
                        except ValueError:
                            failures.append(now)
                            raise
                    failures.clear()
                    print('PASSWORD CHANGED: local console account', flush=True)
                    response = dict(ok=True)
                except (ValueError, OSError) as exc:
                    # Never log the request, plaintext password, or exception repr.
                    message = str(exc) if isinstance(exc, ValueError) else 'Служба занята или недоступна; повторите позже'
                    response = dict(ok=False, error=message)
                try:
                    connection.sendall(json.dumps(response, ensure_ascii=False).encode('utf-8') + b'\n')
                except OSError:
                    pass


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--config', default='/etc/iredmail-lifecycle-web/settings.json')
    args = p.parse_args()
    if os.geteuid() != 0:
        raise SystemExit('Root password broker only')
    os.umask(0o027)
    serve(args.config)
