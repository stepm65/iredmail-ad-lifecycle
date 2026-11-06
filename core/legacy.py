#!/usr/bin/env python3
# Copyright (c) 2026, rslpro
# SPDX-License-Identifier: AGPL-3.0-only
"""iRedMail lifecycle helper 1.4.0; stdlib only. Called under the Bash flock.
RU: отдельный операторский режим; штатная политика missing не изменяется.
EN: explicit operator workflow; normal missing policy is unchanged.
"""
import argparse
import base64
import csv
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import time
import uuid
import unicodedata

VERSION = "1.4.0"
SCHEMA_VERSION = "1.1"  # Existing reviewed inventories remain compatible.
FIELDS = ["schema_version", "inventory_id", "generated_at_epoch", "email",
          "size_bytes", "mailbox_path", "latest_mtime_epoch", "latest_mtime_utc",
          "inactive_days", "source_dev", "source_ino", "primary_status",
          "broad_ad_status", "eligible", "reason"]
ATTR_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9-]*$")
MARKER = ".iredmail-legacy.json"


class SafetyError(Exception):
    pass


def email_value(value):
    value = value.strip().lower()
    if value.count("@") != 1:
        raise SafetyError("Unsupported/unsafe mailbox email: " + repr(value))
    local, domain = value.split("@")
    # Unicode letters/numbers are identities, not path separators. Do not
    # transliterate, strip non-ASCII or apply NFC/casefold aliases: different
    # filesystem names must remain distinct. Lowercase is shared with Bash.
    local_ok = (bool(local) and local[0].isalnum() and all(
        char.isalnum() or unicodedata.category(char).startswith("M") or char in "_.+-"
        for char in local))
    domain_ok = (bool(domain) and domain[0].isalnum() and all(
        char.isalnum() or char in ".-" for char in domain))
    if not local_ok or not domain_ok:
        raise SafetyError("Unsupported/unsafe mailbox email: " + repr(value))
    if local in (".", "..") or domain in (".", "..") or ".." in domain:
        raise SafetyError("Unsafe email path component")
    return value


def ldif(text):
    """Decode folded/base64 LDIF. Referrals, URLs and malformed results fail closed."""
    lines = []
    for line in text.splitlines():
        if line.startswith(" "):
            if not lines:
                raise SafetyError("Invalid LDIF continuation")
            lines[-1] += line[1:]
        else:
            lines.append(line)
    entries, entry = [], {}
    for line in lines + [""]:
        if not line:
            if entry:
                if "dn" not in entry:
                    raise SafetyError("LDAP output without dn; possible partial result")
                entries.append(entry)
                entry = {}
            continue
        if line.startswith("#"):
            continue
        if ":" not in line:
            raise SafetyError("Malformed LDAP output")
        key, value = line.split(":", 1)
        key = key.lower()
        if key in ("ref", "result", "search", "numresponses"):
            raise SafetyError("LDAP referral/partial result; no changes permitted")
        if value.startswith(":"):
            try:
                decoded = base64.b64decode(value[1:].strip(), validate=True)
                if key == "objectguid":
                    if len(decoded) != 16:
                        raise SafetyError("Invalid AD objectGUID")
                    value = decoded.hex()
                else:
                    value = decoded.decode("utf-8")
            except (ValueError, UnicodeError) as exc:
                raise SafetyError("Invalid base64 LDIF") from exc
        elif value.startswith("<"):
            raise SafetyError("External URL in LDIF is unsupported")
        else:
            value = value.lstrip(" ")
        entry.setdefault(key, []).append(value)
    return entries


def ad_table(entries, address, employee, include_sources=False,
             include_mail=False, include_smtp=False, conflicts=None):
    if not all(ATTR_RE.fullmatch(attr) for attr in (address, employee)):
        raise SafetyError("Invalid LDAP attribute")
    owners = {}
    for entry in entries:
        addresses = entry.get(address.lower(), [])
        if len(addresses) != 1:
            if len(addresses) > 1:
                raise SafetyError("Multiple primary address values in AD")
        candidates = [(address, value) for value in addresses]
        if include_mail and address.lower() != "mail":
            candidates += [("mail", value) for value in entry.get("mail", [])]
        if include_smtp:
            candidates += [("proxyAddresses", value[5:]) for value in entry.get("proxyaddresses", [])
                           if value.lower().startswith("smtp:")]
        identities = {}
        for attribute, value in candidates:
            try:
                mail = email_value(value)
            except SafetyError:
                # Directory login identifiers such as HTTP/... are legitimate
                # AD values but cannot identify a flat mailbox directory.
                print("AD NOTICE: ignored non-mailbox %s=%r dn=%r" %
                      (attribute, value, entry.get("dn", [""])[0]), file=sys.stderr)
                continue
            identities.setdefault(mail, set()).add(attribute)
        uac = entry.get("useraccountcontrol", [])
        emp = entry.get(employee.lower(), [])
        disabled = "unknown"
        if len(uac) == 1 and uac[0].isdigit():
            disabled = "1" if int(uac[0]) & 2 else "0"
        status = emp[0] if len(emp) == 1 else ""
        if any(c in status for c in "|\r\n\x00"):
            status = ""
        for mail, attributes in identities.items():
            owners.setdefault(mail, []).append((entry, attributes, disabled, status))
    rows = []
    for mail, matches in owners.items():
        attributes = set().union(*(m[1] for m in matches))
        if len(matches) > 1:
            disabled, status = "ambiguous", ""
            print("AD WARNING: ambiguous address %r; %s matching records; mailbox protected" %
                  (mail, len(matches)), file=sys.stderr)
            if conflicts is not None:
                for entry, sources, flag, emp in matches:
                    conflicts.append({"email": mail, "match_count": len(matches),
                        "dn": entry.get("dn", [""])[0], "ad_disabled": flag,
                        "employee_status": emp, "ad_match_attributes": ",".join(sorted(sources))})
        else:
            _, sources, disabled, status = matches[0]
            # Optional extra identities are presence protection only. They never
            # authorize moving a mailbox outside the selected SOGo identity.
            if address.lower() not in {s.lower() for s in sources}:
                disabled, status = "alias-only", ""
        row = (mail, disabled, status)
        if include_sources:
            row += (",".join(sorted(attributes)),)
        rows.append(row)
    return rows


def filter_escape(value):
    return "".join("\\%02x" % ord(c) if c in "*()\\\x00" else c for c in value)


def run_checked(cmd, timeout=600):
    try:
        result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                encoding="utf-8", errors="strict", timeout=timeout)
    except (OSError, subprocess.TimeoutExpired, UnicodeError) as exc:
        raise SafetyError("Command failed: " + cmd[0] + ": " + str(exc)) from exc
    if result.returncode:
        raise SafetyError("Command failed (%s): %s: %s" %
                          (result.returncode, cmd[0], result.stderr.strip()))
    return result.stdout


class Directory:
    def __init__(self, args):
        self.a = args

    def query(self, base, query_filter, attributes):
        a = self.a
        cmd = ["ldapsearch", "-LLL", "-x", "-o", "ldif-wrap=no", "-H", a.ldap_uri,
               "-D", a.bind_dn, "-y", a.password_file, "-b", base, "-s", "sub",
               "-E", "!pr=%s/noprompt" % a.page_size,
               "-e", "!1.2.840.113556.1.4.1339"]
        # AD domain-scope: all OUs in one naming context, without DNS/child-NC
        # continuation referrals. Critical control fails on unsupported servers.
        cmd += a.ldap_extra + [query_filter] + attributes
        return ldif(run_checked(cmd, a.ldap_timeout))

    def primary(self):
        a = self.a
        entries = self.query(a.base, a.ldap_filter,
                            [a.address, "mail", "proxyAddresses", "userAccountControl", a.employee])
        rows = ad_table(entries, a.address, a.employee,
                        include_mail=a.include_mail, include_smtp=a.include_smtp)
        if len(entries) < a.min_users:
            raise SafetyError("Primary AD count below EXPECTED_MIN_USERS")
        return {row[0] for row in rows}

    def broad(self):
        a = self.a
        entries = self.query(a.search_base,
                    "(|(%s=*)(mail=*)(proxyAddresses=*)(userPrincipalName=*))" % a.address,
                    [a.address, "mail", "proxyAddresses", "userPrincipalName"])
        if len(entries) < a.min_users:
            raise SafetyError("Broad AD count below EXPECTED_MIN_USERS")
        values = set()
        for entry in entries:
            for key in (a.address.lower(), "mail", "proxyaddresses", "userprincipalname"):
                for value in entry.get(key, []):
                    if key == "proxyaddresses":
                        if not value.lower().startswith("smtp:"):
                            continue
                        value = value[5:]
                    values.add(value.strip().lower())
        return values

    def exists(self, email):
        a = self.a
        value = filter_escape(email)
        query = "(|(%s=%s)(mail=%s)(proxyAddresses=smtp:%s)(proxyAddresses=SMTP:%s)(userPrincipalName=%s))" % (
                a.address, value, value, value, value, value)
        # No primary LDAP_FILTER: enabled, disabled, contacts, aliases and other OUs protect.
        return bool(self.query(a.search_base, query, ["dn"]))


def no_symlink_path(path):
    path = Path(os.path.abspath(path))
    for component in [path] + list(path.parents):
        if component.is_symlink():
            raise SafetyError("Symlink path is forbidden: " + str(component))
    return path


def load_exclusions(path):
    if not path:
        return {}
    path = no_symlink_path(path)
    if not path.exists():
        return {}
    info = path.stat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > 4 * 1024 * 1024:
        raise SafetyError("Invalid exclusions file")
    if os.name == "posix":
        for part in [path] + list(path.parents):
            s = part.stat()
            if s.st_uid != 0 or s.st_mode & 0o022:
                raise SafetyError("Exclusions path must be root-owned without group/world write: " + str(part))
    data = json.loads(path.read_text(encoding="utf-8"))
    if set(data) != {"schema", "entries"} or data["schema"] != 1 or not isinstance(data["entries"], dict):
        raise SafetyError("Invalid exclusions schema")
    for email, row in data["entries"].items():
        if email_value(email) != email or not isinstance(row, dict) or set(row) != {"reason", "created", "actor"}:
            raise SafetyError("Invalid exclusion entry")
        if (not isinstance(row["reason"], str) or not 1 <= len(row["reason"]) <= 300
                or any(ord(c) < 32 or c == "|" for c in row["reason"])
                or not isinstance(row["actor"], str) or len(row["actor"]) > 100
                or not isinstance(row["created"], int)):
            raise SafetyError("Invalid exclusion metadata")
    return data["entries"]


def excluded(a, email):
    return email in load_exclusions(getattr(a, "exclusions", ""))


def validate_roots(a):
    root = no_symlink_path(a.vmail_root)
    archive = no_symlink_path(a.archive_root)
    normal = no_symlink_path(a.normal_archive_root)
    if not root.is_dir() or root == Path(root.anchor):
        raise SafetyError("Invalid VMAIL_ROOT")
    if archive == Path(archive.anchor) or archive == normal:
        raise SafetyError("Legacy archive must be separate from normal archive root")
    if root == archive or root in archive.parents or archive in root.parents:
        raise SafetyError("Mailbox and legacy archive roots overlap")
    if normal == root or root in normal.parents or normal in root.parents:
        raise SafetyError("Mailbox and normal archive roots overlap")
    if archive in normal.parents:
        raise SafetyError("Legacy root cannot contain normal archive root")
    if normal in archive.parents:
        relative = archive.relative_to(normal)
        if len(relative.parts) != 1 or relative.name.lower() in [x.lower() for x in a.domain]:
            raise SafetyError("Nested legacy root must be a direct non-domain subdirectory of ARCHIVE_ROOT")
    if not a.search_base.strip():
        raise SafetyError("LEGACY_SEARCH_BASE is empty")
    # Configured domain/OU suffix must include the primary base.
    base = [part.strip().casefold() for part in a.base.split(",")]
    broad = [part.strip().casefold() for part in a.search_base.split(",")]
    if len(broad) > len(base) or base[-len(broad):] != broad:
        raise SafetyError("LEGACY_SEARCH_BASE must be an ancestor of BASE_DN")
    for attr in (a.address, a.employee):
        if not ATTR_RE.fullmatch(attr):
            raise SafetyError("Invalid LDAP attribute")
    if a.max_count < 1 or not 0 < a.max_percent <= 100:
        raise SafetyError("Legacy limits must be >0; percent <=100")
    if min(a.min_inactive, a.retention, a.min_users) < 0 or a.min_users < 1:
        raise SafetyError("Invalid inactivity/retention/minimum-users value")
    return root, archive


def discover(a):
    root, _ = validate_roots(a)
    mailboxes = {}
    for domain in a.domain:
        domain = email_value("validation@" + domain).split("@")[1]
        folder = no_symlink_path(root / domain)
        if not folder.is_dir():
            raise SafetyError("Configured domain directory is missing: " + str(folder))
        for path in sorted(folder.iterdir()):
            if path.is_symlink():
                raise SafetyError("Symlink in mailbox domain: " + str(path))
            if not (path / "Maildir").is_dir():
                continue
            no_symlink_path(path / "Maildir")
            email = email_value(path.name + "@" + domain)
            if email in mailboxes:
                raise SafetyError("Duplicate mailbox path for " + email)
            mailboxes[email] = path
    if not mailboxes:
        raise SafetyError("No mailboxes discovered")
    return mailboxes


def activity(path):
    """Scan files and directories without following links or crossing mounts.
    Directory mtimes catch delivery/removal/rename events and empty mailboxes.
    """
    path = no_symlink_path(path)
    root_stat = path.stat()
    size, latest, count = 0, root_stat.st_mtime_ns, 0
    stack = [path]
    while stack:
        folder = stack.pop()
        with os.scandir(folder) as entries:
            for entry in entries:
                info = os.stat(entry.path, follow_symlinks=False)
                if info.st_dev != root_stat.st_dev:
                    raise SafetyError("Nested filesystem in mailbox: " + entry.path)
                if stat.S_ISLNK(info.st_mode):
                    raise SafetyError("Symlink inside mailbox: " + entry.path)
                latest = max(latest, info.st_mtime_ns)
                if stat.S_ISDIR(info.st_mode):
                    stack.append(Path(entry.path))
                elif stat.S_ISREG(info.st_mode):
                    size += info.st_size
                    count += 1
                else:
                    raise SafetyError("Special file inside mailbox: " + entry.path)
    return {"size": size, "latest_ns": latest, "files": count,
            "dev": root_stat.st_dev, "ino": root_stat.st_ino}


def utc(epoch):
    return dt.datetime.fromtimestamp(epoch, dt.timezone.utc).isoformat()


def file_hash(path):
    with open(path, "rb") as stream:
        return hashlib.sha256(stream.read()).hexdigest()


def write_inventory(a, directory):
    if not a.inventory:
        raise SafetyError("--inventory FILE is required")
    target = no_symlink_path(a.inventory)
    mailboxes = discover(a)
    if any(target == p or p in target.parents for p in mailboxes.values()):
        raise SafetyError("Inventory must be outside mailbox directories")
    primary, broad = directory.primary(), directory.broad()
    protections = load_exclusions(a.exclusions)
    generated, ident = int(time.time()), str(uuid.uuid4())
    rows = []
    for email, path in mailboxes.items():
        if email in primary:
            continue
        info = activity(path)
        epoch = info["latest_ns"] / 1_000_000_000
        age = (generated - epoch) / 86400
        absent = email not in broad
        eligible = absent and age >= a.min_inactive and email not in protections
        rows.append(dict(zip(FIELDS, [SCHEMA_VERSION, ident, generated, email, info["size"],
                    str(path), "%.9f" % epoch, utc(epoch), "%.6f" % age,
                    info["dev"], info["ino"], "missing", "absent" if absent else "present",
                    "yes" if eligible else "no",
                    "excluded" if email in protections else "missing-in-broad-base" if eligible else
                    "found-in-broad-base" if not absent else "recent-file-activity"])))
    target.parent.mkdir(parents=True, exist_ok=True)
    # Never overwrite an operator's approved snapshot.
    with target.open("x", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    print("LEGACY INVENTORY: %s; missing-primary=%s; eligible=%s; total=%s" % (
          target, len(rows), sum(r["eligible"] == "yes" for r in rows), len(mailboxes)))


def read_inventory(path):
    if not path:
        raise SafetyError("First generate and review --legacy-inventory; --inventory is required")
    rows = {}
    with no_symlink_path(path).open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        if not set(FIELDS).issubset(reader.fieldnames or []):
            raise SafetyError("Inventory schema mismatch")
        for row in reader:
            if row["schema_version"] != SCHEMA_VERSION or row["primary_status"] != "missing":
                raise SafetyError("Invalid inventory row")
            email = email_value(row["email"])
            if email in rows:
                raise SafetyError("Duplicate inventory email: " + email)
            rows[email] = row
    return rows


def read_approval(path):
    if not path:
        raise SafetyError("LEGACY_APPROVAL_FILE is required (CSV or TXT)")
    path = no_symlink_path(path)
    result = set()
    with path.open(encoding="utf-8-sig", newline="") as stream:
        if path.suffix.lower() == ".csv":
            reader = csv.DictReader(stream)
            if not {"email", "approved"}.issubset(reader.fieldnames or []):
                raise SafetyError("Approval CSV must have email,approved; approved=YES")
            for row in reader:
                email = email_value(row["email"])
                value = row["approved"].strip().upper()
                if value not in ("", "NO", "YES"):
                    raise SafetyError("Invalid approved value for " + email)
                if value == "YES":
                    if email in result:
                        raise SafetyError("Duplicate approved email: " + email)
                    result.add(email)
        elif path.suffix.lower() == ".txt":
            for line in stream:
                line = line.strip()
                if line and not line.startswith("#"):
                    email = email_value(line)
                    if email in result:
                        raise SafetyError("Duplicate approval email: " + email)
                    result.add(email)
        else:
            raise SafetyError("Approval file must end in .csv or .txt")
    if not result:
        raise SafetyError("Approval is empty; no mailbox changes")
    return result


def matches_snapshot(row, path, info):
    # A CSV path is never a source of authority: it must match fresh discovery.
    if no_symlink_path(row["mailbox_path"]) != path:
        raise SafetyError("Inventory path differs from discovery for " + row["email"])
    if (int(row["source_dev"]), int(row["source_ino"])) != (info["dev"], info["ino"]):
        raise SafetyError("Mailbox recreated/replaced since inventory: " + row["email"])
    if int(row["size_bytes"]) != info["size"] or abs(
            float(row["latest_mtime_epoch"]) - info["latest_ns"] / 1e9) > 0.000001:
        raise SafetyError("Mailbox activity changed since inventory; create fresh inventory: " + row["email"])


def check_limits(count, total, a):
    if count > a.max_count or count * 100 > a.max_percent * total:
        raise SafetyError("LEGACY safety stop: selected=%s total=%s; limits count=%s percent=%s" %
                          (count, total, a.max_count, a.max_percent))


def safe_json(path, data):
    with path.open("x", encoding="utf-8") as stream:
        json.dump(data, stream, ensure_ascii=False, indent=2)
        stream.flush()
        os.fsync(stream.fileno())


def archive_one(a, directory, email, source, row, baseline):
    # Recheck broad AD and actual age immediately before each archive.
    if excluded(a, email):
        print("LEGACY SKIP: excluded: " + email)
        return False
    if directory.exists(email):
        print("LEGACY SKIP: found in broad AD: " + email)
        return False
    info = activity(source)
    matches_snapshot(row, source, info)
    if (time.time() - info["latest_ns"] / 1e9) < a.min_inactive * 86400:
        print("LEGACY SKIP: recent filesystem activity: " + email)
        return False
    if a.dry_run:
        print("DRY-RUN LEGACY: would archive %s source=%s" % (email, source))
        return False
    _, root = validate_roots(a)
    root.mkdir(parents=True, exist_ok=True)
    no_symlink_path(root)
    if a.separate_fs and source.stat().st_dev == root.stat().st_dev:
        raise SafetyError("Legacy archive and source are on the same filesystem")
    if shutil.disk_usage(root).free < info["size"] + 64 * 1024 * 1024:
        raise SafetyError("Not enough free space in legacy archive")
    login, domain = email.split("@")
    parent = no_symlink_path(root / domain / login)
    parent.mkdir(parents=True, exist_ok=True)
    dest = parent / (dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8])
    dest.mkdir()
    # Data in its own subdirectory; incomplete archives have no completion marker.
    data = dest / "mailbox"
    data.mkdir()
    print("LEGACY COPY: %s -> %s" % (email, data))
    run_checked(["rsync", "-aHAX", "--numeric-ids", "--", str(source) + "/", str(data) + "/"], 86400)
    run_checked(["rsync", "-aHAX", "--numeric-ids", "--delete", "--", str(source) + "/", str(data) + "/"], 86400)
    changes = run_checked(["rsync", "-aHAXnci", "--numeric-ids", "--delete", "--out-format=%i %n%L",
                           "--", str(source) + "/", str(data) + "/"], 86400)
    if changes.strip():
        raise SafetyError("Checksum/metadata verification failed; source preserved: " + email)
    if activity(source) != baseline or activity(source) != info:
        raise SafetyError("Source changed during copy; source preserved: " + email)
    if excluded(a, email):
        raise SafetyError("Mailbox excluded during copy; source preserved: " + email)
    if directory.exists(email):
        raise SafetyError("AD object appeared during copy; source preserved: " + email)
    # Record verified recovery data before removing source. No marker is purgeable yet.
    record = {"package": "iredmail-ad-lifecycle", "version": VERSION,
              "kind": "legacy", "email": email, "source": str(source),
              "archive": str(dest), "inventory_sha256": file_hash(a.inventory),
              "approval_sha256": None if a.command == "archive-all" else file_hash(a.approval),
              "approval_kind": "explicit-archive-all" if a.command == "archive-all" else "allowlist",
              "search_base": a.search_base, "size_bytes": info["size"],
              "latest_mtime_epoch": info["latest_ns"] / 1e9,
              "archived_at_epoch": int(time.time()), "complete": True}
    safe_json(dest / ".verified-copy.json", record)
    if activity(source) != info:
        raise SafetyError("Source changed before removal; preserved: " + email)
    # Operator must quiesce writers: flock excludes lifecycle only, not Postfix/Dovecot.
    shutil.rmtree(source)
    record["archived_at_epoch"] = int(time.time())
    safe_json(dest / ".complete.json", record)
    os.replace(dest / ".complete.json", dest / MARKER)
    (dest / ".verified-copy.json").unlink()
    print("LEGACY ARCHIVED: %s -> %s" % (email, dest))
    return True


def archive(a, directory):
    rows = read_inventory(a.inventory)
    selected = ({email for email, row in rows.items() if row["eligible"] == "yes"}
                if a.command == "archive-all" else read_approval(a.approval))
    if not selected:
        print("LEGACY: no eligible mailboxes")
        return
    unknown = selected - rows.keys()
    if unknown:
        raise SafetyError("Approval contains emails outside inventory: " + ", ".join(sorted(unknown)))
    mailboxes = discover(a)
    primary, broad = directory.primary(), directory.broad()
    # Limits apply to entire requested batch, BEFORE filtering reappeared/recent users.
    check_limits(len(selected), len(mailboxes), a)
    planned = []
    for email in sorted(selected):
        row = rows[email]
        if excluded(a, email):
            print("LEGACY SKIP: excluded: " + email)
            continue
        if row["eligible"] != "yes" or row["broad_ad_status"] != "absent":
            raise SafetyError("Approved row was not eligible at inventory: " + email)
        if email in primary or email in broad:
            print("LEGACY SKIP: found in AD: " + email)
            continue
        if email not in mailboxes:
            print("LEGACY SKIP: source no longer exists: " + email)
            continue
        source = mailboxes[email]
        info = activity(source)
        matches_snapshot(row, source, info)
        if time.time() - info["latest_ns"] / 1e9 < a.min_inactive * 86400:
            raise SafetyError("Mailbox below inactivity threshold: " + email)
        planned.append((email, source, row, info))
    # Complete batch preflight AD checks before any copy/remove operation.
    planned = [item for item in planned if not directory.exists(item[0])]
    if not a.dry_run:
        if not a.writers_quiesced:
            raise SafetyError("Live legacy archive requires --writers-quiesced after stopping delivery/access")
        _, root = validate_roots(a)
        root.mkdir(parents=True, exist_ok=True)
        if a.separate_fs and root.stat().st_dev == Path(a.vmail_root).stat().st_dev:
            raise SafetyError("Legacy archive must use separate filesystem")
        if shutil.disk_usage(root).free < sum(item[3]["size"] for item in planned) + 64 * 1024 * 1024:
            raise SafetyError("Insufficient archive free space for entire batch")
    for item in planned:
        archive_one(a, directory, *item)


def purge(a, directory):
    _, root = validate_roots(a)
    if a.retention == 0:
        print("LEGACY RETENTION: disabled (0 = keep forever)")
        return
    directory.broad()  # fail closed on AD outage before purge
    if not root.exists():
        return
    candidates = []
    for marker in sorted(root.glob("*/*/*/" + MARKER)):
        no_symlink_path(marker)
        data = json.loads(marker.read_text(encoding="utf-8"))
        dest = marker.parent
        email = email_value(data["email"])
        if excluded(a, email):
            print("LEGACY PURGE SKIP: excluded: " + email)
            continue
        login, domain = email.split("@")
        if (data.get("package") != "iredmail-ad-lifecycle" or data.get("kind") != "legacy"
                or data.get("complete") is not True or data.get("archive") != str(dest)
                or dest.parent != root / domain / login or not (dest / "mailbox").is_dir()):
            raise SafetyError("Unrecognized legacy archive marker: " + str(marker))
        if time.time() - int(data["archived_at_epoch"]) < a.retention * 86400:
            continue
        source = Path(data["source"])
        source = no_symlink_path(source)
        vmail = no_symlink_path(a.vmail_root)
        if (source.parent.parent != vmail or source.parent.name.lower() != domain
                or source.name.lower() != login):
            raise SafetyError("Legacy marker source is outside configured mailbox layout")
        if source.exists() or directory.exists(email):
            print("LEGACY PURGE SKIP: source/AD exists: " + email)
            continue
        activity(dest)  # reject links and nested mounts before recursive removal
        candidates.append((email, dest, source))
    if len(candidates) > a.max_count:
        raise SafetyError("Purge exceeds LEGACY_MAX_COUNT")
    for email, dest, source in candidates:
        if excluded(a, email):
            raise SafetyError("Mailbox excluded before purge: " + email)
        if directory.exists(email) or source.exists():
            raise SafetyError("Source/AD reappeared before purge: " + email)
        print(("DRY-RUN LEGACY PURGE: " if a.dry_run else "LEGACY PURGE: ") + str(dest))
        if not a.dry_run:
            shutil.rmtree(dest)


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("command", choices=["inventory", "archive", "archive-all", "purge", "ad-table", "canonical-email", "state-key", "exclusion-table", "exclusion-status"])
    p.add_argument("--exclusions", default="")
    p.add_argument("--email", default="")
    p.add_argument("--ldap-uri", default="")
    p.add_argument("--bind-dn", default="")
    p.add_argument("--password-file", default="")
    p.add_argument("--base", default="")
    p.add_argument("--search-base", default="")
    p.add_argument("--address", default="userPrincipalName")
    p.add_argument("--include-mail", type=int, choices=[0, 1], default=0)
    p.add_argument("--include-smtp", type=int, choices=[0, 1], default=0)
    p.add_argument("--conflicts-csv", default="")
    p.add_argument("--include-count", action="store_true")
    p.add_argument("--include-sources", action="store_true")
    p.add_argument("--employee", default="extensionAttribute6")
    p.add_argument("--ldap-filter", default="(objectClass=user)")
    p.add_argument("--ldap-extra", action="append", default=[])
    p.add_argument("--page-size", type=int, default=1000)
    p.add_argument("--ldap-timeout", type=int, default=120)
    p.add_argument("--min-users", type=int, default=1)
    p.add_argument("--vmail-root", default="")
    p.add_argument("--archive-root", default="")
    p.add_argument("--normal-archive-root", default="")
    p.add_argument("--domain", action="append", default=[])
    p.add_argument("--min-inactive", type=int, default=365)
    p.add_argument("--retention", type=int, default=0)
    p.add_argument("--max-count", type=int, default=20)
    p.add_argument("--max-percent", type=float, default=5)
    p.add_argument("--separate-fs", type=int, choices=[0, 1], default=1)
    p.add_argument("--dry-run", type=int, choices=[0, 1], default=1)
    p.add_argument("--inventory", default="")
    p.add_argument("--approval", default="")
    p.add_argument("--writers-quiesced", action="store_true")
    p.add_argument("--ldif-file")
    return p


def main():
    os.umask(0o077)
    # UTF-8 protocol to the shell, independent of the host locale/code page.
    sys.stdout.reconfigure(encoding="utf-8", newline="\n")
    sys.stderr.reconfigure(encoding="utf-8", newline="\n")
    a = parser().parse_args()
    try:
        if a.command in ("exclusion-table", "exclusion-status"):
            entries = load_exclusions(a.exclusions)
            if a.command == "exclusion-status":
                print(1 if email_value(a.email) in entries else 0)
            else:
                for email, row in sorted(entries.items()):
                    print(email + "|" + row["reason"])
            return 0
        if a.command in ("canonical-email", "state-key"):
            email = email_value(a.email)
            print("sha256-" + hashlib.sha256(email.encode("utf-8")).hexdigest()
                  if a.command == "state-key" else email)
            return 0
        if a.command == "ad-table":
            entries = ldif(Path(a.ldif_file).read_text(encoding="utf-8"))
            conflicts = []
            rows = ad_table(entries, a.address, a.employee, a.include_sources,
                            a.include_mail, a.include_smtp, conflicts)
            if a.conflicts_csv:
                with Path(a.conflicts_csv).open("x", encoding="utf-8", newline="") as stream:
                    writer = csv.DictWriter(stream, fieldnames=["email", "match_count", "dn",
                        "ad_disabled", "employee_status", "ad_match_attributes"])
                    writer.writeheader()
                    writer.writerows(conflicts)
            if a.include_count:
                print("#objects|%s" % len(entries))
            for row in rows:
                print("|".join(row))
            return 0
        validate_roots(a)
        if not a.domain:
            raise SafetyError("MAIL_DOMAINS is empty")
        directory = Directory(a)
        if a.command == "inventory":
            write_inventory(a, directory)
        elif a.command == "purge":
            purge(a, directory)
        else:
            archive(a, directory)
        return 0
    except (SafetyError, OSError, ValueError, KeyError) as exc:
        print("LEGACY ERROR: " + str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
