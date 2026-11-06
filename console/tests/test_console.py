# Copyright (c) 2026, rslpro
# SPDX-License-Identifier: AGPL-3.0-only
"""Real queue/CSV/session tests; core command runner mocked, no mailserver contacted."""
import csv
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
import uuid
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from werkzeug.security import generate_password_hash
from app import create_app
from common import atomic_json, read_json, result
from worker import Worker


class ConsoleTests(unittest.TestCase):
    def setUp(self):
        scratch = Path(__file__).resolve().parents[2] / 'test-scratch'
        scratch.mkdir(exist_ok=True)
        self.tmp = tempfile.TemporaryDirectory(dir=scratch)
        self.base = Path(self.tmp.name)
        self.c = dict(public_url="https://mail.example.ru/lifecycle/", username="admin",
                      password_hash=generate_password_hash("test-password-123", method="pbkdf2:sha256:1000"),
                      secret="TEST-ONLY-" + "x"*40, allow_live=False,
                      core="/usr/local/sbin/iredmail-ad-lifecycle", core_config="/etc/iredmail-ad-lifecycle.conf",
                      helper="/usr/local/lib/iredmail-ad-lifecycle/legacy.py", job_timeout=100)
        for key in ("jobs", "inbox", "web_state", "report_root"):
            path = self.base / key
            path.mkdir()
            self.c[key] = str(path)
        self.app = create_app(self.c)
        self.app.config["TESTING"] = True
        self.client = self.app.test_client()
        self.calls = []
        self.w = Worker(self.c, self.fake_core, enforce_owner=False)

    def tearDown(self):
        # Windows scanners may temporarily retain a deleted directory entry.
        # Retry only these transient cleanup errors; test failures are not hidden.
        for attempt in range(5):
            try:
                self.tmp.cleanup()
                return
            except OSError as exc:
                if os.name != 'nt' or getattr(exc, 'winerror', None) not in (32, 145) or attempt == 4:
                    raise
                time.sleep(0.1 * (attempt + 1))

    def req(self, path="/", data=None, **kwargs):
        method = "post" if data is not None else "get"
        return getattr(self.client, method)(path, data=data, base_url="https://mail.example.ru/lifecycle/", **kwargs)

    def csrf(self):
        with self.client.session_transaction(base_url="https://mail.example.ru/lifecycle/") as s:
            return s["csrf"]

    def login(self):
        self.req("/login")
        response = self.req("/login", dict(csrf=self.csrf(), username="admin", password="test-password-123"))
        self.assertEqual(response.status_code, 302)

    def submit(self, op, **extra):
        ident = uuid.uuid4().hex
        data = dict(id=ident, operation=op, created=int(time.time()), actor="admin", **extra)
        p = Path(self.c["inbox"]) / (ident + ".json")
        atomic_json(p, data)
        self.w.handle(p)
        return ident

    def fake_core(self, command, log, env, timeout):
        self.calls.append(command)
        text = "Mock core\n"
        if '--archive-list' in command:
            target = Path(command[command.index('--output') + 1])
            fields = ['archive_id','email','kind','archived_at_utc','size_bytes','archive_path','source_path','quality','restorable','reason']
            with target.open('w',encoding='utf-8',newline='') as stream:
                writer = csv.DictWriter(stream,fieldnames=fields); writer.writeheader()
                writer.writerow(dict(zip(fields, ['a'*64,'старый@example.ru','normal','2020-01-01','123','/archive/example.ru/старый/20200101T010101','/var/vmail/example.ru/старый','historical-unverified','yes',''])))
                writer.writerow(dict(zip(fields, ['b'*64,'existing@example.ru','normal','2020-01-01','123','/archive/existing','/var/vmail/existing','historical-unverified','no','destination-exists'])))
        elif '--restore-preview' in command:
            target = Path(command[command.index('--output') + 1])
            target.write_text(json.dumps(dict(schema=1,archive=dict(archive_id='a'*64,email='старый@example.ru',archive_path='/archive/example.ru/старый/20200101T010101',source_path='/var/vmail/example.ru/старый'),archive_sha256='fake')))
        elif '--restore' in command:
            target = Path(command[command.index('--output') + 1])
            target.write_text(json.dumps(dict(archive_id='a'*64,email='старый@example.ru',excluded=True,archive_kept=True)))
            text += 'RESTORED: старый@example.ru -> /var/vmail/example.ru/старый\n'
        elif any(v in command for v in ('--exclusions-list','--exclude-add','--exclude-remove')):
            target = Path(command[command.index('--output') + 1])
            target.write_text('email,reason,created,actor\nстарый@example.ru,Keep for recovery,1,admin\n',encoding='utf-8')
        elif "--legacy-inventory" in command:
            target = Path(command[command.index("--inventory") + 1])
            fields = ["email", "eligible", "size_bytes", "latest_mtime_utc", "inactive_days", "broad_ad_status"]
            with target.open("w", encoding="utf-8", newline="") as stream:
                writer = csv.DictWriter(stream, fieldnames=fields)
                writer.writeheader()
                writer.writerow(dict(zip(fields, ["старый@example.ru", "yes", "1048576", "2020-01-01", "800", "absent"])))
                writer.writerow(dict(zip(fields, ["protected@example.ru", "no", "1024", "2026-01-01", "3", "present"])))
        elif "--normal" in command:
            root = Path(self.c["report_root"])
            report = root / "mailboxes-20261001-120000.csv"
            report.write_text('email,classification,employee_status_label,ad_match_attributes\nactive@example.ru,active,Working,userPrincipalName\n', encoding="utf-8")
            conflict = root / "ad-address-conflicts-20261001-120000-99.csv"
            conflict.write_text("email,dn\n", encoding="utf-8")
            text += "CSV report: " + str(report) + "\nAD address conflict details: " + str(conflict) + "\n"
        elif "--report" in command:
            text += "DRY-RUN LEGACY: would archive старый@example.ru\n"
        else:
            text += "LEGACY ARCHIVED: старый@example.ru -> archive\n"
        log.write_text(text, encoding="utf-8")
        return 0

    def reviewed(self):
        inventory = self.submit("inventory")
        preview = self.submit("preview", inventory=inventory, emails=["старый@example.ru"])
        self.assertEqual(result(self.c["jobs"], preview)["status"], "done")
        return preview

    def test_private_dashboard_and_download_require_login(self):
        for path in ("/", "/jobs/" + "a"*32 + "/download/run.log"):
            self.assertEqual(self.req(path).status_code, 302)

    def test_login_secure_cookie_headers_and_proxy_prefix(self):
        response = self.req("/login", headers={"X-Forwarded-Prefix": "/lifecycle"})
        cookie = response.headers["Set-Cookie"]
        self.assertIn("Secure", cookie)
        self.assertIn("HttpOnly", cookie)
        self.assertIn("SameSite=Strict", cookie)
        self.assertIn("Path=/lifecycle", cookie)
        self.assertIn(b'/lifecycle/static/console.css', response.data)
        self.assertEqual(response.headers["X-Frame-Options"], "DENY")

    def test_csrf_and_origin_reject_untrusted_posts(self):
        self.login()
        self.assertEqual(self.req("/new/report", {}).status_code, 400)
        self.assertEqual(self.req("/new/report", {"csrf": self.csrf()}, headers={"Origin": "https://evil.example"}).status_code, 400)
        self.assertFalse(list(Path(self.c["inbox"]).iterdir()))

    def test_bad_host_and_plain_http_rejected(self):
        self.assertEqual(self.client.get("/login", base_url="https://evil.example").status_code, 400)
        self.assertEqual(self.client.get("/login", base_url="http://mail.example.ru").status_code, 400)

    def test_login_lockout(self):
        self.req("/login")
        for _ in range(5):
            self.req("/login", dict(csrf=self.csrf(), username="admin", password="bad"))
        self.assertEqual(self.req("/login", dict(csrf=self.csrf(), username="admin", password="bad")).status_code, 429)

    def test_report_queue_worker_dashboard_and_download(self):
        self.login()
        response = self.req("/new/report", {"csrf": self.csrf()})
        ident = response.location.split("/")[-1]
        self.w.handle(Path(self.c["inbox"]) / (ident + ".json"))
        self.assertEqual(self.calls[0], [self.c["core"], "--normal", "--report"])
        self.assertIn(b'active@example.ru', self.req().data)
        download = self.req("/jobs/" + ident + "/download/report.csv")
        self.assertEqual(download.status_code, 200)
        self.assertIn("attachment", download.headers["Content-Disposition"])
        download.close()

    def test_unicode_inventory_preview_creates_exact_yes_allowlist(self):
        preview = self.reviewed()
        p = Path(self.c["jobs"]) / preview
        with (p / "approval.csv").open(encoding="utf-8", newline="") as stream:
            self.assertEqual(list(csv.DictReader(stream)), [{"email": "старый@example.ru", "approved": "YES"}])
        self.assertIn("--report", self.calls[-1])
        self.assertNotIn("--legacy-archive-all", self.calls[-1])

    def test_ineligible_selection_rejected_before_core(self):
        inventory = self.submit("inventory")
        before = len(self.calls)
        ident = self.submit("preview", inventory=inventory, emails=["protected@example.ru"])
        self.assertEqual(result(self.c["jobs"], ident)["status"], "failed")
        self.assertEqual(len(self.calls), before)

    def test_unknown_operation_and_path_fields_are_rejected(self):
        for op, extra in (([], {}), ("archive-all", {}), ("report", {"command": "rm -rf /"}), ("report", {"path": "/etc/shadow"})):
            ident = self.submit(op, **extra)
            self.assertFalse((Path(self.c["jobs"]) / ident).exists())
        self.assertEqual(self.calls, [])

    def test_live_disabled_enforced_by_both_app_and_worker(self):
        preview = self.reviewed()
        self.login()
        self.assertEqual(self.req("/jobs/" + preview + "/archive", {"csrf": self.csrf()}).status_code, 403)
        ident = self.submit("archive", preview=preview, confirmed=True, writers_quiesced=True)
        self.assertFalse((Path(self.c["jobs"]) / ident).exists())

    def test_live_requires_reauth_confirmation_and_writer_ack(self):
        self.c["allow_live"] = True
        preview = self.reviewed()
        self.login()
        self.req("/jobs/" + preview + "/archive", dict(csrf=self.csrf(), password="bad", confirm="АРХИВИРОВАТЬ", writers="yes"))
        self.assertFalse(list(Path(self.c["inbox"]).iterdir()))
        response = self.req("/jobs/" + preview + "/archive", dict(csrf=self.csrf(), password="test-password-123", confirm="АРХИВИРОВАТЬ", writers="yes"))
        self.assertEqual(response.status_code, 302)
        ident = response.location.split("/")[-1]
        self.w.handle(Path(self.c["inbox"]) / (ident + ".json"))
        self.assertIn("--writers-quiesced", self.calls[-1])
        self.assertNotIn("--report", self.calls[-1])
        self.assertEqual(result(self.c["jobs"], ident)["status"], "done")
        body = self.req("/jobs/" + ident).data.decode()
        self.assertIn("Перенесено 1 из 1", body)

    def test_edited_approval_is_rejected(self):
        self.c["allow_live"] = True
        preview = self.reviewed()
        (Path(self.c["jobs"]) / preview / "approval.csv").write_text("email,approved\nevil@example.ru,YES\n")
        before = len(self.calls)
        ident = self.submit("archive", preview=preview, confirmed=True, writers_quiesced=True)
        self.assertEqual(result(self.c["jobs"], ident)["status"], "failed")
        self.assertEqual(len(self.calls), before)

    def test_preview_is_single_use_even_with_new_job_id(self):
        self.c["allow_live"] = True
        preview = self.reviewed()
        first = self.submit("archive", preview=preview, confirmed=True, writers_quiesced=True)
        before = len(self.calls)
        second = self.submit("archive", preview=preview, confirmed=True, writers_quiesced=True)
        self.assertEqual(result(self.c["jobs"], first)["status"], "done")
        self.assertEqual(result(self.c["jobs"], second)["status"], "failed")
        self.assertEqual(len(self.calls), before)

    def test_same_job_id_cannot_run_twice(self):
        ident = self.submit("report")
        p = Path(self.c["inbox"]) / (ident + ".json")
        atomic_json(p, dict(id=ident, operation="report", created=1, actor="admin"))
        before = len(self.calls)
        self.w.handle(p)
        self.assertEqual(len(self.calls), before)
        self.assertFalse(p.exists())

    def test_core_dry_run_never_claims_live_success(self):
        self.c["allow_live"] = True
        preview = self.reviewed()
        def dry(command, log, env, timeout):
            log.write_text("DRY-RUN LEGACY: would archive старый@example.ru\n", encoding="utf-8")
            return 0
        self.w.runner = dry
        ident = self.submit("archive", preview=preview, confirmed=True, writers_quiesced=True)
        self.assertEqual(result(self.c["jobs"], ident)["status"], "dry-run")

    def test_reappeared_ad_skip_is_not_claimed_as_archived(self):
        self.c["allow_live"] = True
        preview = self.reviewed()
        def skip(command, log, env, timeout):
            log.write_text("LEGACY SKIP: found in AD: старый@example.ru\n", encoding="utf-8")
            return 0
        self.w.runner = skip
        ident = self.submit("archive", preview=preview, confirmed=True, writers_quiesced=True)
        meta = result(self.c["jobs"], ident)
        self.assertEqual(meta["status"], "no-op")
        self.assertEqual(meta["archived_emails"], [])
        self.assertEqual(meta["skipped_emails"], ["старый@example.ru"])

    def test_stale_atomic_temporary_does_not_block_update(self):
        path = self.base / "state.json"
        (self.base / "state.tmp").write_text("old interrupted temporary")
        atomic_json(path, {"value": 1})
        atomic_json(path, {"value": 2})
        self.assertEqual(read_json(path), {"value": 2})

    def test_core_failure_and_timeout_are_recorded(self):
        def fail(command, log, env, timeout):
            log.write_text("LDAP query failed\n")
            return 1
        self.w.runner = fail
        ident = self.submit("report")
        self.assertEqual(result(self.c["jobs"], ident)["status"], "failed")
        def timeout(*args):
            raise ValueError("timeout")
        self.w.runner = timeout
        ident = self.submit("report")
        self.assertEqual(result(self.c["jobs"], ident)["error"], "timeout")

    def test_recovery_marks_interruption_without_replaying(self):
        ident = uuid.uuid4().hex
        folder = Path(self.c["jobs"]) / ident
        folder.mkdir()
        atomic_json(folder / "result.json", dict(id=ident, status="running", created=1))
        self.w.recover()
        self.assertEqual(result(self.c["jobs"], ident)["status"], "interrupted")
        self.assertEqual(self.calls, [])

    def test_log_is_escaped_and_arbitrary_downloads_rejected(self):
        ident = self.submit("report")
        (Path(self.c["jobs"]) / ident / "run.log").write_text('<script>alert("x")</script>')
        self.login()
        body = self.req("/jobs/" + ident).data
        self.assertNotIn(b'<script>alert', body)
        self.assertIn(b'&lt;script&gt;', body)
        self.assertEqual(self.req("/jobs/" + ident + "/download/request.json").status_code, 404)

    def test_report_outside_allowed_root_fails(self):
        outside = self.base / "foreign.csv"
        outside.write_text("email,classification\n")
        def wrong(command, log, env, timeout):
            log.write_text("CSV report: " + str(outside) + "\n")
            return 0
        self.w.runner = wrong
        ident = self.submit("report")
        self.assertEqual(result(self.c["jobs"], ident)["status"], "failed")
        self.assertFalse((Path(self.c["jobs"]) / ident / "report.csv").exists())

    def test_inventory_page_has_only_eligible_selection(self):
        ident = self.submit("inventory")
        self.login()
        body = self.req("/jobs/" + ident).data.decode()
        self.assertIn('value="protected@example.ru" disabled', body)
        self.assertIn('value="старый@example.ru"  aria-label', body)

    def recovery_preview(self):
        catalog = self.submit('archives')
        preview = self.submit('restore-preview', catalog=catalog, archive_id='a'*64)
        self.assertEqual(result(self.c['jobs'], preview)['status'], 'done')
        return preview

    def test_archive_catalog_and_preview_ui(self):
        preview = self.recovery_preview()
        self.login()
        response = self.req('/archives')
        self.assertEqual(response.status_code,200)
        self.assertIn('Старый архив без подтверждения полноты', response.data.decode())
        body = self.req('/jobs/'+preview).data.decode()
        self.assertIn('полнота исходной переписки неизвестна', body)
        self.assertIn('/var/vmail/example.ru/старый', body)

    def test_restore_preview_rejects_nonrestorable_or_unknown_archive(self):
        catalog = self.submit('archives')
        for ident in ('b'*64,'c'*64):
            job = self.submit('restore-preview',catalog=catalog,archive_id=ident)
            self.assertEqual(result(self.c['jobs'],job)['status'],'failed')

    def test_restore_permissions_protocol_rejects_paths(self):
        from common import validate_request
        basic = dict(id=uuid.uuid4().hex, operation='restore-preview', created=1, actor='admin', catalog='a'*32, archive_id='b'*64)
        with self.assertRaises(ValueError):
            validate_request(dict(basic,path='/etc/passwd'))
        with self.assertRaises(ValueError):
            validate_request(dict(basic,archive_id='../../etc/passwd'))
        preview = self.recovery_preview()
        job = self.submit('restore',preview=preview,confirmed=True,writers_quiesced=True)
        self.assertFalse((Path(self.c['jobs'])/job).exists())

    def test_restore_requires_password_typed_confirmation_and_writers(self):
        self.c['allow_live'] = True
        preview = self.recovery_preview()
        self.login()
        for data in (dict(confirm='ВОССТАНОВИТЬ',writers='yes',password='wrong'),dict(confirm='x',writers='yes',password='test-password-123'),dict(confirm='ВОССТАНОВИТЬ',password='test-password-123')):
            response = self.req('/jobs/'+preview+'/restore', dict(csrf=self.csrf(),**data))
            self.assertEqual(response.status_code,302)
            self.assertFalse(list(Path(self.c['inbox']).glob('*.json')))
        response = self.req('/jobs/'+preview+'/restore',dict(csrf=self.csrf(),confirm='ВОССТАНОВИТЬ',writers='yes',password='test-password-123'))
        self.assertEqual(response.status_code,302)
        queued = read_json(next(Path(self.c['inbox']).glob('*.json')))
        self.assertNotIn('password',queued)
        self.assertEqual(queued['operation'],'restore')

    def test_restore_success_keeps_archive_and_is_single_use(self):
        self.c['allow_live'] = True
        preview = self.recovery_preview()
        restored = self.submit('restore',preview=preview,confirmed=True,writers_quiesced=True)
        meta = result(self.c['jobs'],restored)
        self.assertEqual(meta['status'],'done')
        self.assertTrue(meta['restore_result']['archive_kept'])
        self.assertTrue(meta['restore_result']['excluded'])
        replay = self.submit('restore',preview=preview,confirmed=True,writers_quiesced=True)
        self.assertEqual(result(self.c['jobs'],replay)['status'],'failed')
        self.assertEqual(sum('--restore' in c for c in self.calls),1)

    def test_changed_restore_preview_hash_rejected(self):
        self.c['allow_live'] = True
        preview = self.recovery_preview()
        (Path(self.c['jobs'])/preview/'restore-preview.json').write_text('tampered')
        job = self.submit('restore',preview=preview,confirmed=True,writers_quiesced=True)
        self.assertEqual(result(self.c['jobs'],job)['status'],'failed')

    def test_restore_dry_run_is_not_reported_as_recovered(self):
        self.c['allow_live'] = True
        preview = self.recovery_preview()
        def dry(command,log,env,timeout):
            log.write_text('DRY-RUN RESTORE: старый@example.ru',encoding='utf-8'); return 0
        self.w.runner = dry
        job = self.submit('restore',preview=preview,confirmed=True,writers_quiesced=True)
        self.assertEqual(result(self.c['jobs'],job)['status'],'dry-run')

    def test_exclusion_add_and_read_work_with_live_disabled(self):
        self.login()
        response = self.req('/exclusions/add',dict(csrf=self.csrf(),email='старый@example.ru',reason='Keep for recovery'))
        self.assertEqual(response.status_code,302)
        pending = next(Path(self.c['inbox']).glob('*.json'))
        self.w.handle(pending)
        body = self.req('/exclusions').data.decode()
        self.assertIn('Keep for recovery',body)
        self.assertIn('Снять исключение',body)
        self.assertIn('--exclude-add',self.calls[-1])

    def test_exclusion_removal_requires_password_and_confirmation(self):
        self.login()
        bad = self.req('/exclusions/remove',dict(csrf=self.csrf(),email='старый@example.ru',confirm='СНЯТЬ',password='wrong'))
        self.assertEqual(bad.status_code,302)
        self.assertFalse(list(Path(self.c['inbox']).glob('*.json')))
        ok = self.req('/exclusions/remove',dict(csrf=self.csrf(),email='старый@example.ru',confirm='СНЯТЬ',password='test-password-123'))
        self.assertEqual(ok.status_code,302)
        pending = next(Path(self.c['inbox']).glob('*.json'))
        data = read_json(pending)
        self.assertNotIn('password',data)
        self.w.handle(pending)
        self.assertIn('--exclude-remove',self.calls[-1])

    def test_exclusion_protocol_rejects_invalid_metadata(self):
        from common import validate_request
        data = dict(id='a'*32,operation='exclude-add',created=1,actor='admin',email='valid@example.ru',reason='Keep')
        for bad in (dict(data,email='../x@example.ru'),dict(data,reason='x\ny'),dict(data,reason=''),dict(data,command='rm -rf')):
            with self.assertRaises(ValueError):
                validate_request(bad)

    def test_upgrade_maintenance_blocks_posts_before_queue_write(self):
        self.login()
        self.c['maintenance'] = True
        response = self.req('/new/archives',dict(csrf=self.csrf()))
        self.assertEqual(response.status_code,503)
        self.assertFalse(list(Path(self.c['inbox']).glob('*.json')))


if __name__ == "__main__":
    unittest.main(verbosity=2)
