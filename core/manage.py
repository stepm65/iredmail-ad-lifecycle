#!/usr/bin/env python3
# Copyright (c) 2026, rslpro
# SPDX-License-Identifier: AGPL-3.0-only
"""Archive recovery and persistent protections. Called under lifecycle flock."""
import argparse
import csv
import ctypes
import datetime as dt
import errno
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import tempfile
import time
import uuid
import legacy as L

NORMAL_MARKER = ".iredmail-normal.json"
META_FILES = {NORMAL_MARKER, ".verified-copy.json", ".restored.json"}
ARCHIVE_FIELDS = ["archive_id", "email", "kind", "archived_at_utc", "size_bytes",
                  "archive_path", "source_path", "quality", "restorable", "reason"]
EXCLUSION_FIELDS = ["email", "reason", "created", "actor"]


def atomic(path, value):
    path = L.no_symlink_path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".lifecycle-", delete=False) as stream:
        tmp = Path(stream.name)
        stream.write((json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
        stream.flush()
        os.fsync(stream.fileno())
    try:
        tmp.chmod(0o600)
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def protection(a, email, remove=False, reason=None):
    email = L.email_value(email)
    path = L.no_symlink_path(a.exclusions)
    if not a.exclusions:
        raise L.SafetyError("EXCLUSION_FILE must be configured")
    entries = L.load_exclusions(path)
    if remove:
        entries.pop(email, None)
    else:
        reason = reason or a.reason
        if not 1 <= len(reason) <= 300 or any(ord(c) < 32 or c == "|" for c in reason):
            raise L.SafetyError("Specify a reason (1..300 chars, no controls or pipe)")
        entries[email] = dict(reason=reason, created=int(time.time()), actor=a.actor)
    # Shell/worker holds the shared lifecycle lock for the whole transaction.
    if not a.report:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if os.name == "posix":
            for part in [path.parent] + list(path.parent.parents):
                info = part.stat()
                if info.st_uid != 0 or info.st_mode & 0o022:
                    raise L.SafetyError("Unsafe exclusions parent: " + str(part))
        atomic(path, dict(schema=1, entries=entries))
        L.load_exclusions(path)  # Verify persisted schema/permissions.
    print(("DRY-RUN " if a.report else "") + ("EXCLUSION REMOVED: " if remove else "EXCLUDED: ") + email)
    return entries


def csv_output(path, fields, rows):
    p = L.no_symlink_path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("x", encoding="utf-8", newline="") as stream:
        w = csv.DictWriter(stream, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)


def exclusions_report(a):
    entries = L.load_exclusions(a.exclusions)
    csv_output(a.output, EXCLUSION_FIELDS, [dict(email=e, **v) for e, v in sorted(entries.items())])


def marker(path):
    L.no_symlink_path(path)
    s = path.stat()
    if not stat.S_ISREG(s.st_mode) or s.st_nlink != 1 or s.st_size > 1048576:
        raise L.SafetyError("Invalid archive marker")
    return json.loads(path.read_text(encoding="utf-8"))


def archives(a):
    vmail, legacy = L.validate_roots(a)
    normal = L.no_symlink_path(a.normal_archive_root)
    rows = []
    allowed = {L.email_value("test@" + d).split("@")[1] for d in a.domain}
    for kind, root in (("normal", normal), ("legacy", legacy)):
        if not root.exists():
            continue
        for domain in sorted(root.iterdir()):
            if domain.name.lower() not in allowed:
                continue
            L.no_symlink_path(domain)
            if not domain.is_dir():
                continue
            for login in sorted(domain.iterdir()):
                L.no_symlink_path(login)
                if not login.is_dir():
                    continue
                email = L.email_value(login.name + "@" + domain.name)
                for dest in sorted(login.iterdir()):
                    L.no_symlink_path(dest)
                    if not dest.is_dir() or not re.fullmatch(r"20\d{6}T\d{6}(?:Z-[0-9a-f]{8})?", dest.name):
                        continue
                    source = L.no_symlink_path(vmail / domain.name / login.name)
                    payload = dest / "mailbox" if kind == "legacy" else dest
                    quality = "historical-unverified"
                    record_file = dest / (L.MARKER if kind == "legacy" else NORMAL_MARKER)
                    reason = ""
                    archived = dest.stat().st_mtime
                    if record_file.exists():
                        rec = marker(record_file)
                        if (rec.get("package") != "iredmail-ad-lifecycle" or rec.get("kind") != kind
                                or rec.get("complete") is not True or rec.get("email") != email
                                or rec.get("archive") != str(dest)):
                            raise L.SafetyError("Archive marker/layout mismatch: " + str(record_file))
                        source = L.no_symlink_path(rec["source"])
                        if (source.parent.parent != vmail or source.parent.name.lower() != domain.name.lower()
                                or source.name.lower() != login.name.lower()):
                            raise L.SafetyError("Archive source is outside configured mailbox layout")
                        archived = int(rec["archived_at_epoch"])
                        quality = "marked-complete"
                    elif kind == "legacy" or (dest / ".verified-copy.json").exists():
                        reason = "incomplete-archive"
                    if not payload.is_dir() or not (payload / "Maildir").is_dir():
                        reason = "no-maildir"
                        size = 0
                    else:
                        info = L.activity(payload)
                        size = info["size"]
                    if source.exists():
                        reason = "destination-exists"
                    ident = hashlib.sha256((kind + "\0" + str(dest)).encode("utf-8")).hexdigest()
                    rows.append(dict(archive_id=ident, email=email, kind=kind, archived_at_utc=L.utc(archived),
                                     size_bytes=size, archive_path=str(dest), source_path=str(source), quality=quality,
                                     restorable="no" if reason else "yes", reason=reason))
    return rows


def find_archive(a):
    if not re.fullmatch(r"[0-9a-f]{64}", a.archive_id):
        raise L.SafetyError("Invalid archive ID")
    matches = [row for row in archives(a) if row["archive_id"] == a.archive_id]
    if len(matches) != 1:
        raise L.SafetyError("Archive not found; refresh list")
    row = matches[0]
    if row["restorable"] != "yes":
        raise L.SafetyError("Cannot restore: " + row["reason"])
    return row


def payload(row):
    p = Path(row["archive_path"])
    return p / "mailbox" if row["kind"] == "legacy" else p


def fingerprint(path, ignore_meta=False):
    L.activity(path)  # Reject symlinks, special files, nested mounts.
    h = hashlib.sha256()
    for p in [path] + sorted(path.rglob("*")):
        rel = p.relative_to(path).as_posix()
        if ignore_meta and p.parent == path and p.name in META_FILES:
            continue
        s = p.stat()
        h.update(json.dumps([rel, stat.S_IMODE(s.st_mode), s.st_uid, s.st_gid, s.st_size if p.is_file() else 0,
                             s.st_mtime_ns], ensure_ascii=False).encode("utf-8"))
        if p.is_file():
            with p.open("rb") as stream:
                while block := stream.read(1048576):
                    h.update(block)
    return h.hexdigest()


def preview(a):
    row = find_archive(a)
    content = payload(row)
    info = L.activity(content)
    plan = dict(schema=1, archive=row, archive_sha256=fingerprint(content, row["kind"] == "normal"),
                archive_dev=info["dev"], archive_ino=info["ino"], generated=int(time.time()))
    L.safe_json(L.no_symlink_path(a.output), plan)
    print("RESTORE PREVIEW: %s -> %s; archive is kept; automatic exclusion will be added" % (row["email"], row["source_path"]))


def publish_no_replace(source, destination):
    if os.name == "nt":
        os.rename(source, destination)  # Windows fails even for an existing empty dir.
        return
    libc = ctypes.CDLL(None, use_errno=True)
    rename = getattr(libc, "renameat2", None)
    if rename is None:
        raise L.SafetyError("Atomic non-overwrite renameat2 unavailable; staging and archive kept")
    rename.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    rename.restype = ctypes.c_int
    if rename(-100, os.fsencode(source), -100, os.fsencode(destination), 1):
        code = ctypes.get_errno()
        raise OSError(code, os.strerror(code), str(destination))


def restore(a):
    row = find_archive(a)
    plan = marker(L.no_symlink_path(a.preview))
    content = payload(row)
    info = L.activity(content)
    if (plan.get("schema") != 1 or plan.get("archive") != row or plan.get("archive_dev") != info["dev"]
            or plan.get("archive_ino") != info["ino"]
            or plan.get("archive_sha256") != fingerprint(content, row["kind"] == "normal")):
        raise L.SafetyError("Archive/snapshot changed; create a new restore preview")
    source = L.no_symlink_path(row["source_path"])
    if a.dry_run or a.report:
        print("DRY-RUN RESTORE: " + row["email"])
        return
    if not a.writers_quiesced:
        raise L.SafetyError("Live restore requires --writers-quiesced; stop all writers to this mailbox")
    if not source.parent.is_dir():
        raise L.SafetyError("Mailbox domain directory is missing")
    if shutil.disk_usage(source.parent).free < info["size"] + 64 * 1024 * 1024:
        raise L.SafetyError("Insufficient destination free space")
    staging = L.no_symlink_path(source.parent / ".lifecycle-staging")
    staging.mkdir(mode=0o700, exist_ok=True)
    stage = staging / uuid.uuid4().hex
    stage.mkdir(mode=0o700)
    data = stage / "payload"
    data.mkdir()
    ignore = ["--exclude=/" + n for n in sorted(META_FILES)] if row["kind"] == "normal" else []
    # On any error keep staging for diagnosis; never touch/archive-delete source.
    print("RESTORE STAGING: " + str(stage))
    L.run_checked(["rsync", "-aHAX", "--numeric-ids"] + ignore + ["--", str(content) + "/", str(data) + "/"], 86400)
    differences = L.run_checked(["rsync", "-aHAXnci", "--numeric-ids", "--delete", "--out-format=%i %n%L"]
                                + ignore + ["--", str(content) + "/", str(data) + "/"], 86400)
    if differences.strip() or fingerprint(content, row["kind"] == "normal") != plan["archive_sha256"]:
        raise L.SafetyError("Copy verification failed or archive changed; destination not published")
    L.activity(data)
    if source.exists():
        raise L.SafetyError("Destination appeared during copy; never overwrite it")
    protection(a, row["email"], reason="Восстановлен из архива / Restored from archive")
    publish_no_replace(data, source)
    record = dict(archive_id=row["archive_id"], email=row["email"], source=str(source),
                  restored_at_epoch=int(time.time()), actor=a.actor, archive_kept=True, excluded=True)
    atomic(Path(row["archive_path"]) / ".restored.json", record)
    atomic(Path(a.output), record)
    stage.rmdir()
    print("RESTORED: %s -> %s; archive kept; excluded from processing" % (row["email"], source))


def normal_record(a):
    email = L.email_value(a.email)
    login, domain = email.split("@")
    dest = L.no_symlink_path(a.archive_path)
    root = L.no_symlink_path(a.normal_archive_root)
    source = L.no_symlink_path(a.source)
    if (dest.parent != root / domain / login or source.parent.parent != L.no_symlink_path(a.vmail_root)
            or source.parent.name.lower() != domain or source.name.lower() != login):
        raise L.SafetyError("Normal marker layout mismatch")
    rec = dict(package="iredmail-ad-lifecycle", version=L.VERSION, kind="normal", email=email,
               source=str(source), archive=str(dest), archived_at_epoch=int(time.time()), complete=a.phase == "complete")
    if a.phase == "verified":
        L.safe_json(dest / ".verified-copy.json", rec)
    else:
        pending = marker(dest / ".verified-copy.json")
        if any(pending.get(k) != rec[k] for k in ("email", "source", "archive", "kind")) or source.exists():
            raise L.SafetyError("Normal transfer not complete; archive preserved")
        L.safe_json(dest / NORMAL_MARKER, rec)
        (dest / ".verified-copy.json").unlink()


def parser():
    p = L.parser()
    # Extend the shared configuration grammar; these commands need no AD query.
    next(action for action in p._actions if action.dest == "command").choices = [
        "archive-list", "restore-preview", "restore", "exclude-add", "exclude-remove", "exclusions-list", "normal-record"]
    p.add_argument("--output", default="")
    p.add_argument("--archive-id", default="")
    p.add_argument("--preview", default="")
    p.add_argument("--reason", default="")
    p.add_argument("--actor", default="root")
    p.add_argument("--report", action="store_true")
    p.add_argument("--archive-path", default="")
    p.add_argument("--source", default="")
    p.add_argument("--phase", choices=["verified", "complete"], default="complete")
    return p


def main():
    os.umask(0o077)
    a = parser().parse_args()
    try:
        if len(a.actor) > 100 or any(ord(c) < 32 for c in a.actor):
            raise L.SafetyError("Invalid actor")
        if a.command == "normal-record":
            normal_record(a)
        elif a.command == "archive-list":
            csv_output(a.output, ARCHIVE_FIELDS, archives(a))
        elif a.command == "restore-preview":
            preview(a)
        elif a.command == "restore":
            restore(a)
        else:
            if a.command != "exclusions-list":
                protection(a, a.email, a.command == "exclude-remove")
            exclusions_report(a)
        return 0
    except (L.SafetyError, OSError, ValueError, KeyError) as exc:
        print("MANAGEMENT ERROR: " + str(exc), file=__import__('sys').stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
