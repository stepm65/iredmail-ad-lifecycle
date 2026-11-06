# Copyright (c) 2026, rslpro
# SPDX-License-Identifier: AGPL-3.0-only
"""Shared protocol. No user-supplied command, filesystem path or shell string."""
import csv
import json
import os
from pathlib import Path
import re
import stat
import tempfile

ID = re.compile(r"^[0-9a-f]{32}$")
OPS = {"report", "inventory", "preview", "archive", "archives", "restore-preview", "restore", "exclusions", "exclude-add", "exclude-remove", "delete-preview", "delete", "delete-batch-preview", "delete-batch"}
ARCHIVE_ID = re.compile(r"^[0-9a-f]{64}$")


def job_id(value):
    if not isinstance(value, str) or not ID.fullmatch(value):
        raise ValueError("Некорректный номер задания")
    return value


def read_regular(path, limit=10485760):
    path = Path(path)
    if path.is_symlink():
        raise ValueError("Символические ссылки запрещены")
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > limit:
            raise ValueError("Неподходящий файл или превышен размер")
        content = stream.read(limit + 1)
        if len(content) > limit:
            raise ValueError("Превышен размер")
        return content


def read_json(path, limit=1048576):
    return json.loads(read_regular(path, limit).decode("utf-8"))


def atomic_json(path, data, mode=0o640):
    path = Path(path)
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                     prefix=path.name + ".", suffix=".tmp", delete=False) as stream:
        temporary = Path(stream.name)
        json.dump(data, stream, ensure_ascii=False)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    try:
        temporary.chmod(mode)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def rows(path):
    import io
    return list(csv.DictReader(io.StringIO(read_regular(path).decode("utf-8-sig"))))


def result(root, ident):
    return read_json(Path(root) / job_id(ident) / "result.json")


def validate_request(data, live=False, delete=False):
    if not isinstance(data, dict):
        raise ValueError("Ожидалось задание")
    job_id(data.get("id"))
    operation = data.get("operation")
    if not isinstance(operation, str) or operation not in OPS:
        raise ValueError("Операция не разрешена")
    fields = {"id", "operation", "created", "actor"}
    if "config_revision" in data:
        fields.add("config_revision")
        if data["config_revision"] != "":
            job_id(data["config_revision"])
    if operation == "delete-batch-preview":
        fields |= {"report", "emails"}
        job_id(data.get("report"))
        emails = data.get("emails")
        if (not isinstance(emails, list) or not 1 <= len(emails) <= 500
                or any(not isinstance(e, str) or not 3 <= len(e) <= 320 or e.count("@") != 1 or any(ord(c)<32 or c in "/\\|" for c in e) for e in emails)
                or len(set(emails)) != len(emails)):
            raise ValueError("Выберите от 1 до 500 разных подходящих адресов")
    if operation == "delete-preview":
        fields |= {"report", "email"}
        job_id(data.get("report"))
        value = data.get("email")
        if not isinstance(value, str) or not 3 <= len(value) <= 320 or value.count("@") != 1 or any(ord(c)<32 or c in "/\\|" for c in value):
            raise ValueError("Некорректный адрес ящика")
    if operation in ("delete", "delete-batch"):
        fields |= {"preview", "confirmed", "writers_quiesced"}
        job_id(data.get("preview"))
        if not delete or data.get("confirmed") is not True or data.get("writers_quiesced") is not True:
            raise ValueError("Не разрешены/не подтверждены безвозвратное удаление и остановка записи")
    if operation == "restore-preview":
        fields |= {"catalog", "archive_id"}
        job_id(data.get("catalog"))
        if not isinstance(data.get("archive_id"), str) or not ARCHIVE_ID.fullmatch(data["archive_id"]):
            raise ValueError("Некорректный номер архива")
    if operation in ("exclude-add", "exclude-remove"):
        fields |= {"email"}
        value = data.get("email")
        if not isinstance(value, str) or not 3 <= len(value) <= 320 or value.count("@") != 1 or any(ord(c)<32 or c in "/\\|" for c in value):
            raise ValueError("Некорректный адрес исключения")
        if operation == "exclude-add":
            fields |= {"reason"}
            reason = data.get("reason")
            if not isinstance(reason, str) or not 1 <= len(reason) <= 300 or any(ord(c)<32 or c == "|" for c in reason):
                raise ValueError("Укажите причину (до 300 символов)")
        else:
            fields |= {"confirmed"}
            if data.get("confirmed") is not True:
                raise ValueError("Снятие исключения не подтверждено")
    if operation == "preview":
        fields |= {"inventory", "emails"}
        job_id(data.get("inventory"))
        emails = data.get("emails")
        if (not isinstance(emails, list) or not 1 <= len(emails) <= 500
                or any(not isinstance(e, str) or len(e) > 320 for e in emails)
                or len(set(emails)) != len(emails)):
            raise ValueError("Выберите от 1 до 500 разных адресов")
    if operation in ("archive", "restore"):
        fields |= {"preview", "confirmed", "writers_quiesced"}
        job_id(data.get("preview"))
        if not live:
            raise ValueError("Реальные переносы выключены в настройках консоли")
        if data.get("confirmed") is not True or data.get("writers_quiesced") is not True:
            raise ValueError("Не подтверждены перенос и остановка записи")
    if set(data) != fields:
        raise ValueError("Неожиданные поля задания")
    if (not isinstance(data.get("created"), int) or
            not isinstance(data.get("actor"), str) or len(data["actor"]) > 100):
        raise ValueError("Некорректные поля задания")
    return data
