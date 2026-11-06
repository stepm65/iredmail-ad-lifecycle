# Copyright (c) 2026, rslpro
# SPDX-License-Identifier: AGPL-3.0-only
"""Password and manual deletion protocol tests; no production service touched."""
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import app as A
import password_service as P
from common import atomic_json, read_json, result, validate_request
from werkzeug.security import generate_password_hash, check_password_hash
import test_console as fixtures


class PasswordTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.path=Path(self.tmp.name)/'settings.json'
        self.c=dict(password_hash=generate_password_hash('original-pass-123',method='pbkdf2:sha256:1000'),
                    username='admin',public_url='https://mail.example.ru/lifecycle/',allow_delete=False,secret='preserve')
        atomic_json(self.path,self.c)

    def test_password_change_preserves_settings_and_mode_and_old_no_longer_authenticates(self):
        mode=self.path.stat().st_mode
        P.change(self.path,dict(current='original-pass-123',new='replacement-pass-456'))
        c=read_json(self.path)
        self.assertTrue(check_password_hash(c['password_hash'],'replacement-pass-456'))
        self.assertFalse(check_password_hash(c['password_hash'],'original-pass-123'))
        for key in ('username','public_url','allow_delete','secret'): self.assertEqual(c[key],self.c[key])
        self.assertEqual(self.path.stat().st_mode,mode)
        self.assertNotIn('replacement-pass-456',self.path.read_text())

    def test_bad_current_short_same_and_unknown_fields_do_not_change_settings(self):
        before=self.path.read_bytes()
        for d in (dict(current='bad',new='replacement-pass-456'),dict(current='original-pass-123',new='short'),
                  dict(current='original-pass-123',new='original-pass-123'),dict(current='original-pass-123',new='replacement-pass-456',path='/other')):
            with self.assertRaises(ValueError): P.change(self.path,d)
            self.assertEqual(self.path.read_bytes(),before)

    def test_maintenance_blocks_password_change(self):
        self.c['maintenance']=True; atomic_json(self.path,self.c)
        with self.assertRaises(ValueError): P.change(self.path,dict(current='original-pass-123',new='replacement-pass-456'))


class AdminTests(unittest.TestCase):
    setUp=fixtures.ConsoleTests.setUp
    tearDown=fixtures.ConsoleTests.tearDown
    req=fixtures.ConsoleTests.req
    csrf=fixtures.ConsoleTests.csrf
    login=fixtures.ConsoleTests.login
    submit=fixtures.ConsoleTests.submit

    def fake_core(self,command,log,env,timeout):
        if '--delete-preview' in command:
            email=command[command.index('--email')+1]
            atomic_json(Path(command[command.index('--output')+1]),dict(action='permanent-mailbox-delete',schema=1,mailbox=dict(
                email=email,source='/var/vmail/example.ru/dead',size_bytes=123,latest_mtime_utc='2020-01-01',
                identity=dict(classification='unknown-disabled',employee=''))))
            self.calls.append(command); log.write_text('DELETE PREVIEW fixture\n'); return 0
        if '--delete' in command:
            email=command[command.index('--email')+1]
            atomic_json(Path(command[command.index('--output')+1]),dict(email=email,permanent=True,new_archive_created=False))
            self.calls.append(command); log.write_text('PERMANENTLY DELETED fixture\n'); return 0
        code=fixtures.ConsoleTests.fake_core(self,command,log,env,timeout)
        if '--normal' in command:
            p=Path(self.c['report_root'])/'mailboxes-20261001-120000.csv'
            with p.open('a') as f: f.write('dead@example.ru,unknown-disabled,Not set,userPrincipalName\n')
        return code

    def reviewed_delete(self):
        report=self.submit('report')
        ident=self.submit('delete-preview',report=report,email='dead@example.ru')
        self.assertEqual(result(self.c['jobs'],ident)['status'],'done')
        return ident

    def test_password_form_requires_login_and_csrf(self):
        self.assertEqual(self.req('/account').status_code,302)
        self.login(); self.assertIn('Текущий пароль'.encode(),self.req('/account').data)
        self.assertEqual(self.req('/account',{}).status_code,400)

    def test_password_change_does_not_queue_or_log_secrets_and_logs_out(self):
        self.login()
        with patch.object(A,'request_change') as callback:
            r=self.req('/account',dict(csrf=self.csrf(),current='test-password-123',new='replacement-pass-456',repeat='replacement-pass-456'))
        callback.assert_called_once_with('test-password-123','replacement-pass-456')
        self.assertEqual(r.status_code,302)
        self.assertFalse(list(Path(self.c['inbox']).iterdir()))
        self.assertEqual(self.req('/').status_code,302)

    def test_password_mismatch_never_calls_broker(self):
        self.login()
        with patch.object(A,'request_change') as callback:
            self.req('/account',dict(csrf=self.csrf(),current='test-password-123',new='replacement-pass-456',repeat='different'))
        callback.assert_not_called()

    def test_root_password_reset_invalidates_old_session_on_next_request(self):
        self.login()
        # Exercise production reload branch; return updated settings from root.
        original=dict(self.c)
        new=dict(self.c,password_hash=generate_password_hash('replacement-pass-456',method='pbkdf2:sha256:1000'))
        with patch.object(A,'read_json',return_value=original):
            production=A.create_app()
        production.config['TESTING']=True; client=production.test_client()
        with client.session_transaction(base_url='https://mail.example.ru/lifecycle/') as s:
            s.update(user='admin',version=original['password_hash'][-32:])
        with patch.object(A,'read_json',return_value=new):
            self.assertEqual(client.get('/',base_url='https://mail.example.ru/lifecycle/').status_code,302)

    def test_delete_preview_dashboard_button_and_exact_plan(self):
        self.login(); ident=self.reviewed_delete()
        self.assertIn('Проверить удаление файлов'.encode(),self.req('/').data)
        page=self.req('/jobs/'+ident).data
        self.assertIn('безвозвратное'.encode(),page)
        self.assertIn(b'/var/vmail/example.ru/dead',page)
        self.assertIn('Ручное удаление отключено'.encode(),page)

    def test_delete_live_permission_is_separate_from_archival(self):
        ident=self.reviewed_delete(); self.login()
        r=self.req('/jobs/'+ident+'/delete',dict(csrf=self.csrf(),confirm='УДАЛИТЬ',email='dead@example.ru',password='test-password-123',writers='yes'))
        self.assertEqual(r.status_code,403)
        self.assertFalse(list(Path(self.c['inbox']).iterdir()))

    def test_delete_wrong_confirmation_password_address_or_writer_ack_do_not_queue(self):
        ident=self.reviewed_delete(); self.c['allow_delete']=True; self.login()
        good=dict(confirm='УДАЛИТЬ',email='dead@example.ru',password='test-password-123',writers='yes')
        for k,v in (('confirm','YES'),('email','active@example.ru'),('password','bad'),('writers','no')):
            self.req('/jobs/'+ident+'/delete',dict(good,csrf=self.csrf(),**{k:v}))
            self.assertFalse(list(Path(self.c['inbox']).iterdir()))

    def test_delete_success_single_use_and_no_password_in_job(self):
        ident=self.reviewed_delete(); self.c['allow_delete']=True; self.login()
        r=self.req('/jobs/'+ident+'/delete',dict(csrf=self.csrf(),confirm='УДАЛИТЬ',email='dead@example.ru',password='test-password-123',writers='yes'))
        self.assertEqual(r.status_code,302)
        pending=next(Path(self.c['inbox']).glob('*.json'))
        self.assertNotIn('password',read_json(pending)); self.w.handle(pending)
        self.assertEqual(result(self.c['jobs'],pending.stem)['status'],'done')
        self.assertIn('--confirm-delete',self.calls[-1])
        repeated=self.submit('delete',preview=ident,confirmed=True,writers_quiesced=True)
        self.assertEqual(result(self.c['jobs'],repeated)['status'],'failed')

    def test_active_address_or_unknown_address_never_gets_preview(self):
        report=self.submit('report')
        for address in ('active@example.ru','absent@example.ru'):
            ident=self.submit('delete-preview',report=report,email=address)
            self.assertEqual(result(self.c['jobs'],ident)['status'],'failed')

    def test_changed_preview_hash_rejected_without_cli_delete(self):
        ident=self.reviewed_delete(); self.c['allow_delete']=True
        (Path(self.c['jobs'])/ident/'delete-preview.json').write_text('{}')
        before=len(self.calls)
        failed=self.submit('delete',preview=ident,confirmed=True,writers_quiesced=True)
        self.assertEqual(result(self.c['jobs'],failed)['status'],'failed')
        self.assertEqual(len(self.calls),before)

    def test_delete_dry_run_never_reported_as_success(self):
        ident=self.reviewed_delete(); self.c['allow_delete']=True
        def dry(command,log,env,timeout): log.write_text('DRY-RUN DELETE: dead@example.ru'); return 0
        self.w.runner=dry
        result_id=self.submit('delete',preview=ident,confirmed=True,writers_quiesced=True)
        self.assertEqual(result(self.c['jobs'],result_id)['status'],'dry-run')

    def test_protocol_rejects_plaintext_password_or_arbitrary_delete_path(self):
        good=dict(id='a'*32,operation='delete',created=1,actor='admin',preview='b'*32,confirmed=True,writers_quiesced=True)
        for extra in ({'password':'secret'},{'path':'/var/vmail'},{'email':'other@example.ru'}):
            with self.assertRaises(ValueError): validate_request(dict(good,**extra),delete=True)

    def test_worker_refreshes_permissions_before_claiming_new_jobs(self):
        path=self.base/'settings.json'
        atomic_json(path,dict(self.c,allow_delete=True))
        self.w.config_path=path
        pending=Path(self.c['inbox'])/('a'*32+'.json'); pending.write_text('{}')
        import worker as W
        def inspect(p): self.assertTrue(self.w.c['allow_delete'])
        with patch.object(self.w,'recover'), patch.object(self.w,'handle',side_effect=inspect), patch.object(W.time,'sleep',side_effect=StopIteration):
            with self.assertRaises(StopIteration): self.w.loop()


if __name__=='__main__': unittest.main(verbosity=2)
