# Copyright (c) 2026, rslpro
# SPDX-License-Identifier: AGPL-3.0-only
"""Bash integration: mocked LDAP/flock/rsync, real report/state/filesystem.
Run on Linux with python3 tests/test_normal.py; optional BASH_TEST_EXE on Windows.
Mocking rsync avoids requiring a production LDAP server or archive mount.
"""
import csv
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

PACKAGE = Path(__file__).resolve().parents[1]
BASH = os.environ.get("BASH_TEST_EXE") or shutil.which("bash")


def shell_path(path):
    value = Path(path).as_posix()
    if os.name == "nt" and len(value) > 2 and value[1:3] == ":/":
        return "/" + value[0].lower() + value[2:]
    return value


def write_lf(path, text):
    with Path(path).open("w", encoding="utf-8", newline="\n") as stream:
        stream.write(text)


@unittest.skipUnless(BASH, "Bash executable unavailable")
class NormalTests(unittest.TestCase):
    def setUp(self):
        scratch = PACKAGE.parent / "test-scratch"
        scratch.mkdir(exist_ok=True)
        self.tmp = tempfile.TemporaryDirectory(dir=scratch)
        self.base = Path(self.tmp.name).resolve()
        self.bin = self.base / "bin"
        self.bin.mkdir()
        self.root = self.base / "vmail"
        self.archive = self.base / "archive"
        self.legacy = self.archive / "legacy"
        self.state = self.base / "state"
        self.state.mkdir()
        (self.state / "sentinel").write_text("keep")
        self.reports = self.base / "reports"
        self.ldif = self.base / "ad.ldif"
        self.config = self.base / "config"
        self.sync = self.base / "rsync_fixture.py"
        self.sync.write_text("import sys,shutil\nfrom pathlib import Path\nsrc,dst=map(Path,sys.argv[-2:])\nshutil.copytree(src,dst,dirs_exist_ok=True,copy_function=shutil.copy2)\n")
        def stub(name, text):
            p = self.bin / name
            write_lf(p, "#!/bin/sh\n" + text + "\n")
            p.chmod(0o755)
        executable = shell_path(sys.executable)
        stub("python3", "exec '" + executable + "' \"$@\"")
        stub("ldapsearch", 'if [ "$TEST_FAIL_LDAP" = 1 ]; then exit 49; fi\ncat "$TEST_LDIF"')
        stub("flock", "exit 0")
        stub("rsync", "exec python3 '" + shell_path(self.sync) + "' \"$@\"")
        if os.name == "nt":
            # Native Python directory creation avoids MSYS mkdir -p checking
            # inaccessible ancestors above the sandbox's writable workspace.
            mkdir_helper = self.base / "mkdir.py"
            mkdir_helper.write_text("import sys\nfrom pathlib import Path\nfor p in sys.argv[1:]:\n if p != '-p': Path(p).mkdir(parents=True,exist_ok=True)\n")
            stub("mkdir", "exec python3 '" + shell_path(mkdir_helper) + "' \"$@\"")
            core_helper = self.base / "core.py"
            core_helper.write_text("import sys\nfrom pathlib import Path\ncmd=sys.argv[1]\np=Path(sys.argv[-1])\nif cmd=='du': print(str(sum(f.stat().st_size for f in p.rglob('*') if f.is_file()))+'\\t'+str(p))\nelif cmd=='realpath':\n s=p.resolve().as_posix();print('/'+s[0].lower()+s[2:] if s[1:3]==':/' else s)\nelif cmd=='tee':\n data=sys.stdin.read();sys.stdout.write(data)\n with p.open('a',encoding='utf-8') as f: f.write(data)\n")
            for name in ("du", "realpath", "tee"):
                stub(name, "exec python3 '" + shell_path(core_helper) + "' " + name + " \"$@\"")
        self.entries = []
        self.box("active", 512, "0")
        self.box("fired", 514, "0")
        for value in range(1, 9):
            self.box("protected" + str(value), 514, str(value))
        self.box("unknown", 514, "")
        self.box("unknown9", 514, "9")
        self.box("bad-uac", "invalid", "0")
        self.box("missing", None, None)
        self.values = dict(DRY_RUN=0, LEGACY_MODE=0, EXPECTED_MIN_USERS=1,
                           MAX_TERMINATED_PERCENT=100, ARCHIVE_RETENTION_DAYS=0)
        self.env = os.environ.copy()
        # MSYS sh understands a native Windows PATH when initialized; prepending
        # as a Windows path ensures it builds a correct POSIX search path.
        self.env["PATH"] = str(self.bin) + os.pathsep + str(Path(BASH).parent) + os.pathsep + self.env.get("PATH", "")
        self.env["CONFIG_FILE"] = shell_path(self.config)
        self.env["TEST_LDIF"] = shell_path(self.ldif)
        self.env["TEST_FAIL_LDAP"] = "0"

    def tearDown(self):
        self.tmp.cleanup()

    def box(self, name, uac, status):
        p = self.root / "example.ru" / name / "Maildir" / "cur"
        p.mkdir(parents=True)
        (p / "message").write_bytes(b"mail\n")
        if uac is not None:
            self.entries.append("dn: CN=" + name + ",DC=example,DC=ru\nuserPrincipalName: " + name +
                                "@example.ru\nmail: " + name + "@example.ru\nuserAccountControl: " + str(uac) +
                                "\nextensionAttribute6: " + status + "\n\n")

    def run_script(self, *args):
        (self.base / "password").write_text("FAKE-TEST-ONLY")
        values = dict(LDAP_URI="ldap://TEST-ONLY", BIND_DN="CN=TEST-ONLY",
                      BIND_PW_FILE=self.base / "password", BASE_DN="OU=people,DC=example,DC=ru",
                      VMAIL_ROOT=self.root, ARCHIVE_ROOT=self.archive, LEGACY_ARCHIVE_ROOT=self.legacy,
                      LEGACY_SEARCH_BASE="DC=example,DC=ru", STATE_DIR=self.state,
                      EXCLUSION_FILE=self.base / "exclusions.json",
                      LOG_FILE=self.base / "lifecycle.log", LOCK_FILE=self.base / "lock",
                      REPORT_DIR=self.reports, REQUIRE_SEPARATE_ARCHIVE_FS=0,
                      LEGACY_APPROVAL_FILE="", LEGACY_INVENTORY_FILE="")
        values.update(self.values)
        text = "MAIL_DOMAINS=('example.ru')\nLDAP_EXTRA_ARGS=()\n"
        for name, value in values.items():
            value = shell_path(value) if isinstance(value, Path) else str(value)
            text += name + "='" + value.replace("'", "'\\''") + "'\n"
        write_lf(self.config, text)
        write_lf(self.ldif, "".join(self.entries))
        return subprocess.run([BASH, shell_path(PACKAGE / "iredmail-ad-lifecycle.sh"), *args],
                              env=self.env, capture_output=True, encoding="utf-8", errors="replace")

    def test_report_preserves_mail_and_state_with_dry_run_zero(self):
        result = self.run_script("--report")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue((self.root / "example.ru/fired").exists())
        self.assertEqual(list(self.state.iterdir()), [self.state / "sentinel"])
        with next(self.reports.glob("mailboxes-*.csv")).open(encoding="utf-8", newline="") as f:
            classes = {row["email"]: row["classification"] for row in csv.DictReader(f)}
        self.assertEqual(classes["active@example.ru"], "active")
        self.assertEqual(classes["fired@example.ru"], "terminated")
        for v in range(1, 9):
            self.assertEqual(classes["protected%s@example.ru" % v], "protected-disabled")
        for name in ("unknown", "unknown9", "bad-uac"):
            self.assertEqual(classes[name + "@example.ru"], "unknown-disabled")
        self.assertEqual(classes["missing@example.ru"], "missing")

    def test_normal_excluded_terminated_mailbox_is_kept_and_reported(self):
        import json
        path = self.base / 'exclusions.json'
        path.write_text(json.dumps(dict(schema=1, entries={'fired@example.ru': dict(reason='Keep for recovery', actor='test', created=1)})))
        result = self.run_script('--normal')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue((self.root/'example.ru/fired').exists())
        with next(self.reports.glob('mailboxes-*.csv')).open(encoding='utf-8') as stream:
            rows = {r['email']: r for r in csv.DictReader(stream)}
        self.assertEqual(rows['fired@example.ru']['classification'], 'excluded')
        self.assertEqual(rows['fired@example.ru']['exclusion_reason'], 'Keep for recovery')

    def test_normal_malformed_exclusions_stop_before_archival(self):
        (self.base/'exclusions.json').write_text('broken')
        result = self.run_script('--normal')
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue((self.root/'example.ru/fired').exists())

    def test_retention_protects_excluded_and_incomplete_normal_archives(self):
        import json
        self.values['ARCHIVE_RETENTION_DAYS'] = 1
        (self.base/'exclusions.json').write_text(json.dumps(dict(schema=1,entries={'protected1@example.ru':dict(reason='Keep archive',actor='test',created=1)})))
        for login in ('protected1','protected2','protected3'):
            dest = self.archive/'example.ru'/login/'20200101T010101'
            (dest/'Maildir').mkdir(parents=True)
            if login == 'protected2':
                (dest/'.verified-copy.json').write_text('{}')
            os.utime(dest,(time.time()-100*86400,time.time()-100*86400))
        result = self.run_script('--normal')
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        self.assertTrue((self.archive/'example.ru/protected1/20200101T010101').exists())
        self.assertTrue((self.archive/'example.ru/protected2/20200101T010101').exists())
        self.assertFalse((self.archive/'example.ru/protected3/20200101T010101').exists())

    def test_management_cli_exclusion_crud_works_without_ldap_query(self):
        import json
        self.env['TEST_FAIL_LDAP'] = '1'
        out = self.base/'exclusions-out.csv'
        result = self.run_script('--exclude-add','--email','fired@example.ru','--reason','Protected by operator','--output',shell_path(out))
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        self.assertIn('fired@example.ru',json.loads((self.base/'exclusions.json').read_text())['entries'])
        result = self.run_script('--exclusions-list','--output',shell_path(self.base/'exclusions-list.csv'))
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        result = self.run_script('--exclude-remove','--email','fired@example.ru','--output',shell_path(self.base/'exclusions-after.csv'))
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        self.assertEqual(json.loads((self.base/'exclusions.json').read_text())['entries'],{})

    def test_normal_live_archives_only_disabled_status_zero(self):
        result = self.run_script("--normal")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse((self.root / "example.ru/fired").exists())
        self.assertTrue(list((self.archive / "example.ru/fired").glob("*/Maildir/cur/message")))
        for p in (self.root / "example.ru").iterdir():
            self.assertTrue((p / "Maildir/cur/message").exists())
        self.assertEqual(len(list((self.root / "example.ru").iterdir())), 13)

    def test_ldap_failure_aborts_without_moving_mail(self):
        self.env["TEST_FAIL_LDAP"] = "1"
        result = self.run_script("--normal")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("LDAP query failed", result.stdout)
        self.assertTrue((self.root / "example.ru/fired").exists())
        self.assertFalse(self.archive.exists())

    def test_normal_overrides_legacy_mode_and_retention_excludes_subtree(self):
        expired_normal = self.archive / "example.ru/old/20200101T000000"
        expired_legacy = self.legacy / "example.ru/dead/20200101T000000"
        timestamp_like_login = self.legacy / "example.ru/20200101T000000"
        for p in (expired_normal, expired_legacy, timestamp_like_login):
            p.mkdir(parents=True)
            (p / "message").write_text("keep legacy")
            os.utime(p, (time.time()-200*86400, time.time()-200*86400))
        self.values.update(LEGACY_MODE=1, ARCHIVE_RETENTION_DAYS=90)
        result = self.run_script("--normal")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse(expired_normal.exists())
        self.assertTrue(expired_legacy.exists())
        self.assertTrue(timestamp_like_login.exists())

    def test_legacy_mode_without_approval_inventory_aborts(self):
        self.values["LEGACY_MODE"] = 1
        result = self.run_script()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("First generate and review", result.stdout)
        self.assertTrue((self.root / "example.ru/fired").exists())
        self.assertTrue((self.root / "example.ru/missing").exists())

    def test_conflicting_operation_flags_rejected(self):
        result = self.run_script("--normal", "--legacy-archive-all")
        self.assertEqual(result.returncode, 2)

    def test_mass_termination_guard(self):
        self.values["MAX_TERMINATED_PERCENT"] = 1
        result = self.run_script("--normal")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("terminated mailboxes exceed", result.stdout)
        self.assertTrue((self.root / "example.ru/fired").exists())

    def test_cyrillic_ad_only_upn_does_not_abort_report(self):
        self.entries.append("dn: CN=admin\nuserPrincipalName:: 0LDQtNC80LjQvdC40YHRgtGA0LDRgtC+0YBAdWFjLXNzYy5ydQ==\nuserAccountControl: 512\n\n")
        self.env["LC_ALL"] = "C"
        result = self.run_script("--report")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("Finished successfully", result.stdout)
        self.assertTrue((self.root / "example.ru/missing").exists())

    def test_unicode_mailbox_matches_ad_in_c_locale_and_distinct_state(self):
        import hashlib
        self.box("УВОЛЕН", 514, "0")
        self.box("ДЕКРЕТ", 514, "2")
        self.env["LC_ALL"] = "C"
        self.values["TERMINATED_ARCHIVE_DAYS"] = 30
        result = self.run_script("--normal")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        with next(self.reports.glob("mailboxes-*.csv")).open(encoding="utf-8", newline="") as f:
            rows = {r["email"]: r["classification"] for r in csv.DictReader(f)}
        self.assertEqual(rows["уволен@example.ru"], "terminated")
        self.assertEqual(rows["декрет@example.ru"], "protected-disabled")
        key = "sha256-" + hashlib.sha256("уволен@example.ru".encode("utf-8")).hexdigest() + ".state"
        self.assertTrue((self.state / key).exists())
        self.assertTrue((self.root / "example.ru/УВОЛЕН").exists())
        self.assertTrue((self.root / "example.ru/ДЕКРЕТ").exists())

    def test_service_upn_old_config_is_not_a_fatal_mail_path(self):
        self.values["ADDRESS_ATTRIBUTE"] = "userPrincipalName"
        self.entries.append("dn: CN=http\nuserPrincipalName: http/internet-term.example.org@example.org\nuserAccountControl: 512\n\n")
        result = self.run_script("--report")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("AD NOTICE: ignored non-mailbox", result.stderr)
        self.assertIn("ADDRESS_ATTRIBUTE=userPrincipalName", result.stdout)
        self.assertNotIn("set mail", result.stdout)

    def test_mail_differs_from_upn_and_csv_records_mail_match(self):
        self.entries = [e.replace("userPrincipalName: active@example.ru", "userPrincipalName: other.login@example.ru") for e in self.entries]
        self.values["ADDRESS_ATTRIBUTE"] = "mail"
        result = self.run_script("--report")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        with next(self.reports.glob("mailboxes-*.csv")).open(encoding="utf-8", newline="") as f:
            rows = {r["email"]: r for r in csv.DictReader(f)}
        self.assertEqual(rows["active@example.ru"]["classification"], "active")
        self.assertEqual(rows["active@example.ru"]["ad_match_attributes"], "mail")

    def test_smtp_alias_of_existing_account_is_protected(self):
        self.values.update(ADDRESS_ATTRIBUTE="mail", ADDRESS_INCLUDE_SMTP_ALIASES=1)
        self.entries[0] = self.entries[0].replace("mail: active@example.ru", "mail: renamed@example.ru\nproxyAddresses: SMTP:active@example.ru")
        result = self.run_script("--report")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        with next(self.reports.glob("mailboxes-*.csv")).open(encoding="utf-8", newline="") as f:
            rows = {r["email"]: r for r in csv.DictReader(f)}
        self.assertEqual(rows["active@example.ru"]["classification"], "alias-only")
        self.assertEqual(rows["active@example.ru"]["ad_match_attributes"], "proxyAddresses")

    def test_conflicting_portal_report_contains_owners_and_preserves_state(self):
        self.values["ADDRESS_ATTRIBUTE"] = "mail"
        self.box("portal", 514, "0")
        self.entries.append("dn: CN=other-owner\nmail: portal@example.ru\nuserAccountControl: 514\nextensionAttribute6: 0\n\n")
        result = self.run_script("--normal", "--report")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        with next(self.reports.glob("mailboxes-*.csv")).open(encoding="utf-8", newline="") as f:
            rows = {r["email"]: r for r in csv.DictReader(f)}
        self.assertEqual(rows["portal@example.ru"]["classification"], "ambiguous-ad")
        self.assertEqual(rows["fired@example.ru"]["classification"], "terminated")
        with next(self.reports.glob("ad-address-conflicts-*.csv")).open(encoding="utf-8", newline="") as f:
            conflicts = list(csv.DictReader(f))
        self.assertEqual({r["dn"] for r in conflicts}, {"CN=portal,DC=example,DC=ru", "CN=other-owner"})
        self.assertEqual(list(self.state.iterdir()), [self.state / "sentinel"])
        self.assertTrue((self.root / "example.ru/portal").exists())

    def test_live_duplicate_is_kept_old_state_reset_and_unique_termination_archived(self):
        self.box("portal", 514, "0")
        self.entries.append("dn: CN=other-owner\nuserPrincipalName: portal@example.ru\nuserAccountControl: 512\n\n")
        (self.state / "portal@example.ru.state").write_text("CLASS=terminated\nFIRST_SEEN=1\n")
        result = self.run_script("--normal")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue((self.root / "example.ru/portal/Maildir/cur/message").exists())
        self.assertFalse((self.archive / "example.ru/portal").exists())
        self.assertFalse((self.root / "example.ru/fired").exists())
        self.assertFalse((self.state / "portal@example.ru.state").exists())

    def test_sogo_upn_ignores_shared_mail_and_smtp_with_different_uac(self):
        self.entries[0] = self.entries[0].replace("mail: active@example.ru", "mail: fired@example.ru\nproxyAddresses: SMTP:fired@example.ru")
        self.entries[1] = self.entries[1].replace("mail: fired@example.ru", "mail: unrelated@example.ru")
        result = self.run_script("--normal", "--report")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        with next(self.reports.glob("mailboxes-*.csv")).open(encoding="utf-8", newline="") as f:
            rows = {r["email"]: r for r in csv.DictReader(f)}
        self.assertEqual(rows["active@example.ru"]["classification"], "active")
        self.assertEqual(rows["fired@example.ru"]["classification"], "terminated")
        self.assertEqual(rows["fired@example.ru"]["ad_match_attributes"], "userPrincipalName")

    def test_optional_alias_of_terminated_account_never_archives_alias_box(self):
        self.values["ADDRESS_INCLUDE_SMTP_ALIASES"] = 1
        self.box("old-alias", None, None)
        self.entries[1] = self.entries[1].replace("mail: fired@example.ru", "mail: fired@example.ru\nproxyAddresses: SMTP:old-alias@example.ru")
        result = self.run_script("--normal")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue((self.root / "example.ru/old-alias/Maildir/cur/message").exists())
        self.assertFalse((self.root / "example.ru/fired").exists())

    def setup_delete_ldap(self):
        import base64
        self.entries = ['dn: CN=unknown,OU=other,DC=example,DC=ru\nobjectClass: user\nuserPrincipalName: unknown@example.ru\nuserAccountControl: 514\nobjectGUID:: ' + base64.b64encode(bytes(range(16))).decode() + '\n\n']
        helper = self.base/'delete-ldap.py'
        helper.write_text("import sys,os\nfrom pathlib import Path\nq=next(v for v in sys.argv if v.startswith('(|'))\nif '=*)' in q or 'unknown@example.ru' in q: print(Path(os.environ['TEST_LDIF']).read_text())\n",encoding='utf-8')
        write_lf(self.bin/'ldapsearch', '#!/bin/sh\nexec python3 '+"'"+shell_path(helper)+"' \"$@\"\n")
        if os.name == 'nt':
            # Native Python cannot exec MSYS shebang stubs. Substitute only the
            # LDAP process boundary in this test driver, never production code.
            driver=self.base/'delete-cli-driver.py'
            driver.write_text("import sys,runpy\nfrom pathlib import Path\nsys.path.insert(0,"+repr(str(PACKAGE))+")\nimport legacy as L\ndef fake(cmd,timeout=600):\n assert cmd[0]=='ldapsearch',cmd\n q=next(v for v in cmd if v.startswith('(|'))\n return Path("+repr(str(self.ldif))+").read_text() if '=*)' in q or 'unknown@example.ru' in q else ''\nL.run_checked=fake\nscript=sys.argv.pop(1)\nsys.argv[0]=script\nrunpy.run_path(script,run_name='__main__')\n",encoding='utf-8')
            executable=shell_path(sys.executable)
            write_lf(self.bin/'python3', '#!/bin/sh\ncase "$1" in\n *delete_mailbox.py) exec '+"'"+executable+"' '"+shell_path(driver)+"' \"$@\" ;;\n *) exec '"+executable+"' \"$@\" ;;\nesac\n")

    def test_delete_cli_preview_and_report_preserve_unknown_mailbox(self):
        self.setup_delete_ldap()
        plan=self.base/'delete-preview.json'
        r=self.run_script('--delete-preview','--email','unknown@example.ru','--output',shell_path(plan))
        self.assertEqual(r.returncode,0,r.stdout+r.stderr)
        self.assertTrue(plan.exists())
        r=self.run_script('--delete','--email','unknown@example.ru','--preview',shell_path(plan),'--output',shell_path(self.base/'delete-result.json'),'--report')
        self.assertEqual(r.returncode,0,r.stdout+r.stderr)
        self.assertIn('DRY-RUN DELETE:',r.stdout)
        self.assertTrue((self.root/'example.ru/unknown/Maildir/cur/message').exists())

    def test_delete_cli_live_requires_exact_confirmation_and_removes_only_selected(self):
        self.setup_delete_ldap()
        plan=self.base/'delete-preview.json'
        r=self.run_script('--delete-preview','--email','unknown@example.ru','--output',shell_path(plan))
        self.assertEqual(r.returncode,0,r.stdout+r.stderr)
        args=('--delete','--email','unknown@example.ru','--preview',shell_path(plan),'--output',shell_path(self.base/'delete-result.json'),'--writers-quiesced')
        r=self.run_script(*args,'--confirm-delete','active@example.ru')
        self.assertNotEqual(r.returncode,0)
        self.assertTrue((self.root/'example.ru/unknown').exists())
        r=self.run_script(*args,'--confirm-delete','unknown@example.ru')
        self.assertEqual(r.returncode,0,r.stdout+r.stderr)
        self.assertFalse((self.root/'example.ru/unknown').exists())
        self.assertTrue((self.root/'example.ru/active/Maildir/cur/message').exists())

    def test_batch_delete_cli_preview_report_and_live_are_isolated_from_normal(self):
        import json
        self.setup_delete_ldap()
        approval=self.base/'batch.txt';approval.write_text('unknown@example.ru\nmissing@example.ru\n')
        plan=self.base/'batch-preview.json'
        r=self.run_script('--delete-batch-preview','--approval',shell_path(approval),'--output',shell_path(plan))
        self.assertEqual(r.returncode,0,r.stdout+r.stderr)
        check=self.base/'batch-check.json'
        r=self.run_script('--delete-batch','--preview',shell_path(plan),'--output',shell_path(check),'--report')
        self.assertEqual(r.returncode,0,r.stdout+r.stderr)
        self.assertEqual(json.loads(check.read_text())['status'],'dry-run')
        self.assertTrue((self.root/'example.ru/missing').exists())
        final=self.base/'batch-result.json'
        r=self.run_script('--delete-batch','--preview',shell_path(plan),'--output',shell_path(final),'--confirm-delete','DELETE 2','--writers-quiesced')
        self.assertEqual(r.returncode,0,r.stdout+r.stderr)
        self.assertEqual(json.loads(final.read_text())['deleted'],['missing@example.ru','unknown@example.ru'])
        self.assertFalse((self.root/'example.ru/missing').exists());self.assertFalse((self.root/'example.ru/unknown').exists())
        self.assertTrue((self.root/'example.ru/fired').exists());self.assertTrue((self.root/'example.ru/active').exists())

    def test_object_count_guard_does_not_count_aliases_as_users(self):
        self.entries = ["dn: CN=one\nmail: active@example.ru\nproxyAddresses: SMTP:fired@example.ru\nuserAccountControl: 512\n\n"]
        self.values["EXPECTED_MIN_USERS"] = 2
        result = self.run_script("--report")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("AD returned 1 users", result.stdout)

    def test_no_mail_identifiers_still_reports_without_array_failure(self):
        self.entries = ["dn: CN=http\nuserPrincipalName: http/internet-term.example.org@example.org\nuserAccountControl: 512\n\n"]
        result = self.run_script("--report")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("mailbox identities loaded: 0", result.stdout)
        self.assertTrue((self.root / "example.ru/fired").exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
