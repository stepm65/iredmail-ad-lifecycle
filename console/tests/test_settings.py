# Copyright (c) 2026, rslpro
# SPDX-License-Identifier: AGPL-3.0-only
"""Typed config, transactional rollback, secrets, auth and stale approvals."""
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import app as A
import config_model as M
import config_service as S
from common import atomic_json, read_json, result, validate_request
from werkzeug.security import generate_password_hash
import test_console as fixtures

TEMPLATE=Path(__file__).resolve().parents[2]/'core/iredmail-ad-lifecycle.conf'


class ModelTests(unittest.TestCase):
    def setUp(self):self.values=M.display(M.parse(TEMPLATE.read_text(encoding='utf-8')))

    def test_template_and_generated_config_roundtrip(self):
        parsed=M.validate(self.values)
        self.assertEqual(M.parse(M.render(parsed)),parsed)
        self.assertEqual(parsed['LDAP_EXTRA_ARGS'],['-ZZ'])

    def test_shell_commands_and_expansion_cannot_be_imported_or_saved(self):
        for text in ('BIND_DN=$(touch /tmp/pwn)','source /etc/evil','BIND_DN=abc;true','MAIL_DOMAINS=(a) ; true','BIND_DN=$USER','export BIND_DN=a','BIND_DN=a\nBIND_DN=b'):
            with self.subTest(text=text),self.assertRaises(ValueError):M.parse(text)
        for value in ('$(touch /tmp/pwn)','`id`','${HOME}','x\ny'):
            with self.subTest(value=value),self.assertRaises(ValueError):M.validate(dict(self.values,BIND_DN=value))

    def test_quoted_punctuation_stays_literal(self):
        values=M.validate(dict(self.values,BIND_DN="CN=O'Brien; user,DC=example,DC=org"))
        self.assertEqual(M.parse(M.render(values)),values)

    def test_bounds_choices_and_extra_parameters_fail_closed(self):
        for data in (dict(self.values,MANUAL_DELETE_MAX_COUNT='501'),dict(self.values,VERIFY_MIN_PERCENT='89'),dict(self.values,EXPECTED_MIN_USERS='0'),dict(self.values,DRY_RUN='true'),dict(self.values,LDAP_PAGE_SIZE='01'),dict(self.values,ADDRESS_ATTRIBUTE='proxyAddresses'),dict(self.values,LOG_FILE='/etc/passwd')):
            with self.subTest(data=data),self.assertRaises(ValueError):M.validate(data)

    def test_domains_tls_and_broad_base(self):
        for data in (dict(self.values,MAIL_DOMAINS='example.org example.org'),dict(self.values,MAIL_DOMAINS='../a'),dict(self.values,LDAP_URI='ldap://user:pass@ad/'),dict(self.values,LDAP_URI='https://ad/'),dict(self.values,LEGACY_SEARCH_BASE='OU=Other,DC=example,DC=org'),dict(self.values,EMPLOYEE_STATUS_ATTRIBUTE='mail)(x=*')):
            with self.subTest(data=data),self.assertRaises(ValueError):M.validate(data)
        self.assertEqual(M.validate(dict(self.values,LDAP_URI='ldaps://ad.example.org:636'))['LDAP_EXTRA_ARGS'],[])

    def test_storage_protected_paths_and_overlaps_rejected(self):
        for value in ('/etc/passwd','/root/mail','/var/log/mail','/mnt','/mnt/x/../y','/mnt//x/y'):
            with self.subTest(value=value),self.assertRaises(ValueError):M.validate(dict(self.values,ARCHIVE_ROOT=value))
        with self.assertRaises(ValueError):M.validate(dict(self.values,ARCHIVE_ROOT=self.values['VMAIL_ROOT']+'/archive'))


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        self.settings=self.root/'settings.json'
        self.base=self.root/'core.conf'
        self.base_bytes=TEMPLATE.read_text(encoding='utf-8').replace('/root/.ldap-vmail.pass',(self.root/'ldap-password.pass').as_posix()).encode()
        self.base.write_bytes(self.base_bytes)
        for name in ('jobs','inbox'):(self.root/name).mkdir()
        self.config=dict(core_config=str(self.base),jobs=str(self.root/'jobs'),inbox=str(self.root/'inbox'),
                         helper=str(TEMPLATE.parent/'legacy.py'),password_hash=generate_password_hash('console-password',method='pbkdf2:sha256:1000'),
                         public_url='https://mail.example.org/lifecycle/',allow_live=False,allow_delete=False,secret='unchanged-session-secret')
        atomic_json(self.settings,self.config)
        self.manager=S.ConfigManager(self.settings,enforce=False)
        self.form=M.display(M.parse(self.base.read_text(encoding='utf-8')))
        self.revision=self.manager.state()[-1]

    def request(self,operation='config-save',**changes):
        return dict(operation=operation,current='console-password',revision=self.revision,values=self.form,
                    ldap_password='',allow_live=False,allow_delete=False,**changes)

    def save(self,**changes):
        request=self.request();request.update(changes)
        return self.manager.dispatch(request)

    def test_read_has_no_hash_secret_or_password_and_no_writes_to_base(self):
        with patch.object(self.manager,'timer_status',return_value='off'):
            result=self.manager.dispatch({'operation':'config-read'})
        text=json.dumps(result)
        self.assertNotIn(self.config['secret'],text)
        self.assertNotIn('password_hash',text)
        self.assertNotIn('console-password',text)
        self.assertEqual(self.base.read_bytes(),self.base_bytes)

    def test_save_creates_backup_preserves_base_and_rotates_revision(self):
        before=self.settings.read_bytes()
        output=self.save(values=dict(self.form,MANUAL_DELETE_MAX_COUNT='30'))
        self.assertTrue(output['saved']);self.assertFalse(self.manager.pending.exists())
        self.assertEqual(self.base.read_bytes(),self.base_bytes)
        self.assertEqual(M.parse(self.manager.override.read_text())['MANUAL_DELETE_MAX_COUNT'],'30')
        backup=read_json(self.manager.history/(output['backup']+'.json'))
        self.assertEqual(backup['settings'].encode(),before)
        self.assertEqual(read_json(self.settings)['secret'],self.config['secret'])
        self.assertNotEqual(self.manager.state()[-1],self.revision)

    def test_bad_auth_extra_paths_and_stale_revision_leave_files_unchanged(self):
        before=self.settings.read_bytes()
        for changes in ({'current':'wrong'},{'revision':'stale'},{'path':'/etc/passwd'},{'allow_delete':'yes'}):
            with self.subTest(changes=changes),self.assertRaises(ValueError):self.save(**changes)
        self.assertEqual(self.settings.read_bytes(),before)
        self.assertFalse(self.manager.override.exists())

    def test_secret_written_once_not_returned_or_stored_in_backups(self):
        secret='AD-secret-123 ; $special'
        output=self.save(ldap_password=secret)
        merged=self.manager.state()[1]
        password=Path(merged['BIND_PW_FILE'])
        self.assertEqual(password.read_text(),secret)
        self.assertEqual(password.parent,self.manager.history)
        self.assertNotIn(secret,json.dumps(output))
        for file in self.manager.history.glob('*.json'):self.assertNotIn(secret,file.read_text())
        self.assertNotIn(secret,self.settings.read_text())
        if os.name=='posix':self.assertEqual(password.stat().st_mode & 0o777,0o600)

    def test_validate_is_readonly_and_test_password_is_temporary(self):
        before=self.settings.read_bytes()
        self.manager.dispatch(self.request('config-validate',))
        with patch.object(self.manager,'ldap_test',return_value=dict(primary_count=800,broad_count=900)) as ldap:
            request=self.request('config-test');request['ldap_password']='temporary-secret'
            result=self.manager.dispatch(request)
            self.assertTrue(result['tested']);ldap.assert_called_once()
        self.assertEqual(self.settings.read_bytes(),before)
        self.assertFalse(self.manager.override.exists())
        self.assertFalse(list(self.manager.history.glob('*.pass')))

    def test_live_settings_require_ldap_success_before_commit(self):
        with patch.object(self.manager,'ldap_test',side_effect=ValueError('AD unavailable')):
            with self.assertRaises(ValueError):self.save(values=dict(self.form,DRY_RUN='0'))
        self.assertFalse(self.manager.override.exists())
        with patch.object(self.manager,'ldap_test',return_value={}) as ldap:
            self.save(values=dict(self.form,DRY_RUN='0'),allow_live=True)
            ldap.assert_called_once()

    def test_queued_running_and_maintenance_block_save(self):
        atomic_json(self.root/'inbox'/'queued.json',{})
        with self.assertRaises(ValueError):self.save()
        (self.root/'inbox'/'queued.json').unlink()
        (self.root/'jobs'/'job').mkdir();atomic_json(self.root/'jobs'/'job'/'result.json',dict(status='running'))
        with self.assertRaises(ValueError):self.save()
        atomic_json(self.root/'jobs'/'job'/'result.json',dict(status='done'))
        atomic_json(self.settings,dict(self.config,maintenance=True))
        with self.assertRaises(ValueError):self.save()

    def test_interrupted_second_write_rolls_back_both_files(self):
        before=self.settings.read_bytes();writer=S.write_atomic
        failed=False
        def once(path,content,*args,**kwargs):
            nonlocal failed
            if Path(path)==self.settings and not failed:
                failed=True;raise OSError('fixture disk failure')
            return writer(path,content,*args,**kwargs)
        with patch.object(S,'write_atomic',side_effect=once):
            with self.assertRaises(ValueError):self.save()
        self.assertEqual(self.settings.read_bytes(),before)
        self.assertEqual(self.manager.override.read_bytes(),b'')
        self.assertFalse(self.manager.pending.exists())

    def test_crash_journal_recovered_on_restart(self):
        before=self.settings.read_bytes();writer=S.write_atomic
        def crash(path,content,*args,**kwargs):
            if Path(path)==self.settings:raise KeyboardInterrupt('fixture abrupt stop')
            return writer(path,content,*args,**kwargs)
        with patch.object(S,'write_atomic',side_effect=crash):
            with self.assertRaises(KeyboardInterrupt):self.save()
        self.assertTrue(self.manager.pending.exists())
        self.manager.recover()
        self.assertEqual(self.settings.read_bytes(),before)
        self.assertEqual(self.manager.override.read_bytes(),b'')

    def test_restore_is_safe_and_preserves_current_login(self):
        output=self.save(values=dict(self.form,MANUAL_DELETE_MAX_COUNT='40'))
        self.revision=self.manager.state()[-1]
        self.save(values=dict(self.form,MANUAL_DELETE_MAX_COUNT='50'),allow_delete=True)
        self.revision=self.manager.state()[-1]
        self.manager.dispatch(dict(operation='config-restore',current='console-password',revision=self.revision,backup=output['backup']))
        c,values,*_=self.manager.state()
        self.assertEqual(values['MANUAL_DELETE_MAX_COUNT'],'100')
        self.assertEqual(values['DRY_RUN'],'1');self.assertFalse(c['allow_live']);self.assertFalse(c['allow_delete'])
        self.assertEqual(c['password_hash'],self.config['password_hash'])

    def test_restore_path_tampering_and_base_change_rejected(self):
        output=self.save();self.revision=self.manager.state()[-1]
        with self.assertRaises(ValueError):self.manager.dispatch(dict(operation='config-restore',current='console-password',revision=self.revision,backup='../settings'))
        self.base.write_text(self.base.read_text(encoding='utf-8')+'\n# external change\n',encoding='utf-8')
        self.revision=self.manager.state()[-1]
        with self.assertRaises(ValueError):self.manager.dispatch(dict(operation='config-restore',current='console-password',revision=self.revision,backup=output['backup']))

    def test_timer_has_fixed_argv_and_never_stops_mail_services(self):
        with patch.object(S.subprocess,'run',return_value=type('R',(),{'returncode':0})()) as run:
            self.manager.dispatch(dict(operation='config-timer',current='console-password',revision=self.revision,enabled=False))
        self.assertEqual(run.call_args.args[0],['systemctl','disable','--now','iredmail-ad-lifecycle.timer'])

    @unittest.skipUnless(os.name=='posix','POSIX symlink semantics')
    def test_override_symlink_is_never_written(self):
        target=self.root/'unrelated';target.write_text('keep')
        self.manager.override.symlink_to(target)
        with self.assertRaises(ValueError):self.save()
        self.assertEqual(target.read_text(),'keep')


class WebSettingsTests(unittest.TestCase):
    setUp=fixtures.ConsoleTests.setUp
    tearDown=fixtures.ConsoleTests.tearDown
    req=fixtures.ConsoleTests.req
    csrf=fixtures.ConsoleTests.csrf
    login=fixtures.ConsoleTests.login
    submit=fixtures.ConsoleTests.submit
    fake_core=fixtures.ConsoleTests.fake_core

    def view(self):return dict(values=M.display(M.parse(TEMPLATE.read_text(encoding='utf-8'))),revision='a'*64,
                              allow_live=False,allow_delete=False,password_set=True,history=[],public_url=self.c['public_url'],pending=False,protected={},timer='off')

    def form(self):return dict(self.view()['values'],csrf=self.csrf(),revision='a'*64,current='test-password-123',action='save',confirm='СОХРАНИТЬ')

    def test_settings_requires_login_and_csrf(self):
        with patch.object(A,'request_config') as rpc:
            self.assertEqual(self.req('/settings').status_code,302)
            self.login();self.assertEqual(self.req('/settings',{}).status_code,400)
            rpc.assert_not_called()

    def test_page_fields_and_no_secret_value(self):
        self.login()
        with patch.object(A,'request_config',return_value=self.view()):r=self.req('/settings')
        self.assertEqual(r.status_code,200)
        self.assertIn('Проверить подключение к AD'.encode(),r.data)
        self.assertNotIn(b'test-password-123',r.data)
        self.assertIn(b'name="MANUAL_DELETE_MAX_COUNT"',r.data)

    def test_save_sends_secrets_only_to_broker_not_job_files(self):
        self.login()
        with patch.object(A,'request_config',return_value={'saved':True}) as rpc:
            r=self.req('/settings',dict(self.form(),ldap_password='AD-secret'))
        self.assertEqual(r.status_code,302)
        self.assertEqual(rpc.call_args.args,('config-save',))
        self.assertEqual(rpc.call_args.kwargs['ldap_password'],'AD-secret')
        self.assertFalse(list(Path(self.c['inbox']).glob('*')))
        self.assertNotIn(b'AD-secret',r.data)

    def test_missing_confirmation_never_saves(self):
        self.login()
        with patch.object(A,'request_config',return_value=self.view()) as rpc:
            r=self.req('/settings',dict(self.form(),confirm='wrong'))
        self.assertEqual(r.status_code,200)
        self.assertTrue(all(call.args==('config-read',) for call in rpc.call_args_list))

    def test_ldap_errors_do_not_echo_submitted_password(self):
        self.login()
        with patch.object(A,'request_config',side_effect=[ValueError('Проверка AD не пройдена'),self.view()]):
            r=self.req('/settings',dict(self.form(),action='test',ldap_password='do-not-render-secret'))
        self.assertNotIn(b'do-not-render-secret',r.data)
        self.assertNotIn(b'test-password-123',r.data)

    def test_unrecognized_actions_and_cross_origin_rejected(self):
        self.login()
        with patch.object(A,'request_config') as rpc:
            self.assertEqual(self.req('/settings',dict(self.form(),action='shell')).status_code,400)
            self.assertEqual(self.req('/settings',self.form(),headers={'Origin':'https://evil.example'}).status_code,400)
            rpc.assert_not_called()

    def test_queued_old_revision_cannot_run_core(self):
        self.c['config_revision']='c'*32
        ident=self.submit('report',config_revision='b'*32)
        self.assertEqual(result(self.c['jobs'],ident)['status'],'failed')
        self.assertEqual(self.calls,[])

    def test_old_report_cannot_authorize_new_preview_after_settings_change(self):
        ident=self.submit('report')
        self.c['config_revision']='c'*32
        with self.assertRaises(ValueError):self.w.prerequisite(ident,'report')

    def test_revision_protocol_rejects_invalid_value(self):
        with self.assertRaises(ValueError):validate_request(dict(id='a'*32,operation='report',created=1,actor='admin',config_revision='../x'))


@unittest.skipUnless(sys.platform=='linux','Linux flock / Unix peer credentials')
class LinuxBoundaryTests(ServiceTests):
    # Reuse setup only; avoid counting inherited service cases twice.
    def test_worker_waits_for_exclusive_config_lock_and_pending_recovery(self):
        import fcntl
        from worker import Worker
        lockpath=self.root/'guard.lock'
        worker=Worker(dict(self.config,core='unused'),enforce_owner=True)
        with patch.object(S,'LOCK_PATH',str(lockpath)),patch.object(S,'PENDING',str(self.manager.pending)),patch.object(worker,'_handle') as run:
            with lockpath.open('w') as held:
                fcntl.flock(held,fcntl.LOCK_EX|fcntl.LOCK_NB)
                worker.handle(self.root/'inbox'/'pending.json')
                run.assert_not_called()
            self.manager.pending.write_text('{}')
            worker.handle(self.root/'inbox'/'pending.json');run.assert_not_called()
            self.manager.pending.unlink()
            worker.handle(self.root/'inbox'/'pending.json');run.assert_called_once()

    def socket_roundtrip(self,allowed):
        import multiprocessing
        import socket
        import time
        import password_service as P
        from types import SimpleNamespace
        sock=str(self.root/'broker.sock');lock=str(self.root/'broker.lock')
        def child():
            import pwd
            import worker
            P.SOCKET_PATH=sock;P.LOCK_PATH=lock
            worker.trusted=lambda path:None  # Fixture dirs belong to the CI user.
            pwd.getpwnam=lambda name:SimpleNamespace(pw_uid=os.getuid() if allowed else os.getuid()+1)
            manager_class=S.ConfigManager
            S.ConfigManager=lambda config:manager_class(config,enforce=False)
            P.serve(str(self.settings))
        process=multiprocessing.get_context('fork').Process(target=child)
        process.start()
        try:
            end=time.monotonic()+5
            while not Path(sock).exists() and process.is_alive() and time.monotonic()<end:time.sleep(.02)
            with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as client:
                client.settimeout(10);client.connect(sock)
                client.sendall(b'{"operation":"config-read"}\n')
                raw=b''
                while b'\n' not in raw:raw+=client.recv(65536)
            return json.loads(raw)
        finally:
            process.terminate();process.join(timeout=5)
            if process.is_alive():process.kill();process.join()

    def test_real_unix_socket_accepts_only_expected_peer(self):
        response=self.socket_roundtrip(True)
        self.assertTrue(response['ok']);self.assertIn('values',response['data'])
        self.assertNotIn(self.config['password_hash'],json.dumps(response))

    def test_real_unix_socket_rejects_wrong_peer_uid(self):
        response=self.socket_roundtrip(False)
        self.assertFalse(response['ok']);self.assertNotIn('data',response)


# Fixture inheritance provides helpers, not another copy of every service test.
for _name in list(ServiceTests.__dict__):
    if _name.startswith('test_'):setattr(LinuxBoundaryTests,_name,None)

if __name__=='__main__':unittest.main(verbosity=2)
