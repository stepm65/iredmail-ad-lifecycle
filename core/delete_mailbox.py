#!/usr/bin/env python3
# Copyright (c) 2026, rslpro
# SPDX-License-Identifier: AGPL-3.0-only
"""Explicit reviewed single/batch deletion. No automatic policy is changed."""
import copy
import json
import os
from pathlib import Path
import re
import shutil
import sys
import time
import uuid
import legacy as L
import manage as M


def identity(a, email):
    directory = L.Directory(a)
    present = email in directory.broad()  # Includes minimum-count / domain guards.
    value = L.filter_escape(email)
    query = "(|(%s=%s)(userPrincipalName=%s)(mail=%s)(proxyAddresses=smtp:%s))" % (a.address, value, value, value, value)
    entries = directory.query(a.search_base, query,
                             [a.address, "userAccountControl", a.employee, "objectGUID", "objectClass"])
    if not entries:
        if present:
            raise L.SafetyError("Inconsistent broad AD response; refresh preview")
        return dict(classification="missing", dn="", guid="", disabled=True, employee="")
    if len(entries) != 1:
        raise L.SafetyError("Ambiguous AD identity; manual deletion is forbidden")
    e = entries[0]
    primary, uac, emp = e.get(a.address.lower(), []), e.get("useraccountcontrol", []), e.get(a.employee.lower(), [])
    guid = e.get("objectguid", [])
    if (len(primary) != 1 or L.email_value(primary[0]) != email
            or "user" not in [v.lower() for v in e.get("objectclass", [])]
            or len(uac) != 1 or not uac[0].isdigit() or not int(uac[0]) & 2
            or len(emp) > 1 or len(guid) != 1 or not re.fullmatch(r"[0-9a-f]{32}", guid[0])):
        raise L.SafetyError("Only a unique disabled primary AD account or truly missing mailbox can be deleted")
    status = emp[0] if emp else ""
    if status in {str(i) for i in range(9)}:
        raise L.SafetyError("Known HR status 0..8: use existing lifecycle rules; manual deletion forbidden")
    return dict(classification="unknown-disabled", dn=e["dn"][0], guid=guid[0], disabled=True, employee=status)


def snapshot(a):
    email = L.email_value(a.email)
    login, domain = email.split("@")
    if domain not in {d.lower() for d in a.domain}:
        raise L.SafetyError("Mailbox domain is not configured")
    if L.excluded(a, email):
        raise L.SafetyError("Mailbox is excluded; remove protection explicitly first")
    root = L.no_symlink_path(a.vmail_root)
    if not root.is_dir() or root == Path(root.anchor):
        raise L.SafetyError("Unsafe VMAIL_ROOT")
    source = L.no_symlink_path(root / domain / login)
    if source.parent.parent != root or not source.is_dir() or not (source / "Maildir").is_dir():
        raise L.SafetyError("Mailbox is absent or outside the supported flat Maildir layout")
    state = identity(a, email)
    info = L.activity(source)
    return dict(email=email, source=str(source), identity=state, size_bytes=info["size"],
                latest_mtime_utc=L.utc(info["latest_ns"] / 1e9), source_dev=info["dev"], source_ino=info["ino"],
                sha256=M.fingerprint(source))


def preview(a):
    plan = dict(schema=1, action="permanent-mailbox-delete", generated=int(time.time()), mailbox=snapshot(a))
    L.safe_json(L.no_symlink_path(a.output), plan)
    print("DELETE PREVIEW: %s; %s; no new archive will be made" % (plan["mailbox"]["email"], plan["mailbox"]["source"]))


def delete(a):
    plan = M.marker(L.no_symlink_path(a.preview))
    current = snapshot(a)
    if plan.get("schema") != 1 or plan.get("action") != "permanent-mailbox-delete" or plan.get("mailbox") != current:
        raise L.SafetyError("Mailbox/AD changed since preview; deletion cancelled")
    if a.report or a.dry_run:
        print("DRY-RUN DELETE: " + current["email"])
        return
    if not a.writers_quiesced or a.confirm_delete != current["email"]:
        raise L.SafetyError("Require --writers-quiesced and --confirm-delete with the exact mailbox address")
    output = L.no_symlink_path(a.output)
    if output.exists() or not output.parent.is_dir():
        raise L.SafetyError("Use a new result filename in an existing directory")
    source = Path(current["source"])
    staging = L.no_symlink_path(source.parent / ".lifecycle-deleting")
    staging.mkdir(mode=0o700, exist_ok=True)
    if os.name == "posix":
        info = staging.stat()
        if info.st_uid != 0 or info.st_mode & 0o077 or not shutil.rmtree.avoids_symlink_attacks:
            raise L.SafetyError("Unsafe deletion staging or missing fd-based recursive removal")
    stage = staging / uuid.uuid4().hex
    stage.mkdir(mode=0o700)
    data = stage / "payload"
    # Recheck immediately before mutation, including newly added protection.
    if snapshot(a) != current:
        raise L.SafetyError("Mailbox/AD changed during verification; source preserved")
    journal = dict(action="permanent-mailbox-delete", email=current["email"], source=str(source),
                   staging=str(data), started=int(time.time()), actor=a.actor, status="prepared")
    M.atomic(stage / "operation.json", journal)
    print("DELETE STAGING: " + str(stage), flush=True)
    M.publish_no_replace(source, data)
    journal["status"] = "detached"
    M.atomic(stage / "operation.json", journal)
    try:
        # Check the detached inode and data before irreversible unlinking.
        info = L.activity(data)
        if (info["dev"], info["ino"], M.fingerprint(data)) != (current["source_dev"], current["source_ino"], current["sha256"]):
            raise L.SafetyError("Detached mailbox changed; staging preserved, no recursive deletion started")
        shutil.rmtree(data)
    except Exception:
        print("DELETE INCOMPLETE: inspect " + str(stage) + "; do not retry automatically", file=sys.stderr)
        raise
    record = dict(email=current["email"], source=str(source), deleted_at_epoch=int(time.time()),
                  actor=a.actor, permanent=True, new_archive_created=False, existing_archives_kept=True)
    M.atomic(output, record)
    journal.update(status="deleted", finished=record["deleted_at_epoch"])
    M.atomic(stage / "operation.json", journal)
    print("PERMANENTLY DELETED: " + current["email"] + "; AD and existing archives unchanged")


def batch_limits(a, emails):
    count = getattr(a, "delete_max_count", 100)
    percent = getattr(a, "delete_max_percent", 25)
    if not 1 <= count <= 500 or not 1 <= percent <= 100:
        raise L.SafetyError("MANUAL_DELETE_MAX_COUNT must be 1..500 and MANUAL_DELETE_MAX_PERCENT 1..100")
    if not 1 <= len(emails) <= count:
        raise L.SafetyError("Batch has %s mailboxes; MANUAL_DELETE_MAX_COUNT=%s" % (len(emails), count))
    root = L.no_symlink_path(a.vmail_root)
    if not root.is_dir() or root == Path(root.anchor):
        raise L.SafetyError("Unsafe VMAIL_ROOT")
    total = 0
    for name in sorted({L.email_value("test@" + name).split("@")[1] for name in a.domain}):
        domain = L.no_symlink_path(root / name)
        if not domain.is_dir():
            continue
        for login in domain.iterdir():
            login = L.no_symlink_path(login)
            maildir = L.no_symlink_path(login / "Maildir")
            if login.is_dir() and maildir.is_dir():
                L.email_value(login.name + "@" + domain.name)
                total += 1
    if total < len(emails) or len(emails) * 100 > total * percent:
        raise L.SafetyError("Batch has %s of %s mailboxes; MANUAL_DELETE_MAX_PERCENT=%s" % (len(emails), total, percent))
    return dict(mailboxes_total=total, max_count=count, max_percent=percent)


def batch_preview(a):
    emails = sorted(L.read_approval(a.approval))
    limits = batch_limits(a, emails)
    selected = []
    for email in emails:
        item = copy.copy(a)
        item.email = email
        try:
            selected.append(snapshot(item))
        except (L.SafetyError, OSError, ValueError, KeyError) as exc:
            raise L.SafetyError("Batch rejected for %s: %s; no mailboxes deleted" % (email, exc)) from exc
    plan = dict(schema=2, action="permanent-mailbox-batch-delete", generated=int(time.time()),
                mailboxes=selected, limits=limits, size_bytes=sum(row["size_bytes"] for row in selected))
    L.safe_json(L.no_symlink_path(a.output), plan)
    print("DELETE BATCH PREVIEW: %s selected; %s bytes; no new archive will be made" % (len(selected), plan["size_bytes"]))


def batch_delete(a):
    plan = M.marker(L.no_symlink_path(a.preview))
    if plan.get("schema") != 2 or plan.get("action") != "permanent-mailbox-batch-delete" or not isinstance(plan.get("mailboxes"), list):
        raise L.SafetyError("Invalid batch deletion preview")
    selected = plan["mailboxes"]
    emails = [row["email"] for row in selected]
    if emails != sorted({L.email_value(email) for email in emails}):
        raise L.SafetyError("Noncanonical or duplicate batch identity")
    batch_limits(a, emails)  # Current root limits are authoritative.
    output = L.no_symlink_path(a.output)
    if output.exists() or not output.parent.is_dir():
        raise L.SafetyError("Use a new result filename in an existing directory")
    # Validate the ENTIRE batch before detaching the first mailbox.
    for row in selected:
        item = copy.copy(a)
        item.email = row["email"]
        try:
            fresh = snapshot(item)
        except (L.SafetyError, OSError, ValueError, KeyError) as exc:
            raise L.SafetyError("Batch rejected for %s: %s; no mailboxes deleted" % (item.email, exc)) from exc
        if fresh != row:
            raise L.SafetyError("Batch changed for %s; no mailbox deleted; create a new preview" % item.email)
    report = dict(schema=2, selected=emails, deleted=[], failed_email="", not_processed=emails.copy(),
                  status="dry-run" if a.dry_run or a.report else "running", actor=a.actor,
                  new_archive_created=False, existing_archives_kept=True, started=int(time.time()))
    if a.dry_run or a.report:
        report["finished"] = int(time.time())
        M.atomic(output, report)
        print("DRY-RUN DELETE BATCH: " + str(len(emails)))
        return 0
    if not a.writers_quiesced or a.confirm_delete != "DELETE " + str(len(emails)):
        raise L.SafetyError("Require --writers-quiesced and --confirm-delete 'DELETE N' with exact count")
    M.atomic(output, report)
    detail = output.parent / ("delete-items-" + uuid.uuid4().hex)
    detail.mkdir(mode=0o700)
    for index, row in enumerate(selected):
        item = copy.copy(a)
        item.email = row["email"]
        item.confirm_delete = item.email
        item.preview = str(detail / ("%04d-preview.json" % index))
        item.output = str(detail / ("%04d-result.json" % index))
        try:
            L.safe_json(Path(item.preview), dict(schema=1, action="permanent-mailbox-delete", mailbox=row))
            # Single-box operation rechecks AD/files/exclusions immediately before
            # detaching, even if an earlier batch member has already been deleted.
            delete(item)
            report["deleted"].append(item.email)
            report["not_processed"].remove(item.email)
            M.atomic(output, report)
        except (L.SafetyError, OSError, ValueError, KeyError) as exc:
            confirmed = False
            if Path(item.output).exists():
                try:
                    rec = M.marker(Path(item.output))
                    confirmed = rec.get("email") == item.email and rec.get("source") == row["source"] and rec.get("permanent") is True and not Path(row["source"]).exists()
                except (L.SafetyError, OSError, ValueError, KeyError):
                    pass
            if confirmed:
                if item.email not in report["deleted"]:
                    report["deleted"].append(item.email)
                report.update(status="interrupted", failed_email="", not_processed=emails[len(report["deleted"]):])
            else:
                report.update(status="partial" if report["deleted"] else "failed", failed_email=item.email, not_processed=emails[index+1:])
            report.update(error=str(exc), finished=int(time.time()), details=str(detail))
            M.atomic(output, report)
            print("DELETE BATCH STOPPED: %s confirmed deleted; failed=%s; no automatic retry" % (len(report["deleted"]), item.email), file=sys.stderr)
            return 3
    report.update(status="done", finished=int(time.time()), details=str(detail))
    M.atomic(output, report)
    print("PERMANENT DELETE BATCH COMPLETE: " + str(len(emails)))
    return 0


def main():
    os.umask(0o077)
    p = M.parser()
    next(v for v in p._actions if v.dest == "command").choices = ["delete-preview", "delete", "delete-batch-preview", "delete-batch"]
    p.add_argument("--confirm-delete", default="")
    p.add_argument("--delete-max-count", type=int, default=100)
    p.add_argument("--delete-max-percent", type=int, default=25)
    a = p.parse_args()
    try:
        if len(a.actor) > 100 or any(ord(c) < 32 for c in a.actor):
            raise L.SafetyError("Invalid actor")
        if a.command == "delete-batch-preview":
            batch_preview(a)
        elif a.command == "delete-batch":
            return batch_delete(a)
        else:
            (preview if a.command == "delete-preview" else delete)(a)
        return 0
    except (L.SafetyError, OSError, ValueError, KeyError) as exc:
        print("DELETE ERROR: " + str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
