# Copyright (c) 2026, rslpro
# SPDX-License-Identifier: AGPL-3.0-only
"""Batch console tests use real sessions/queues and a mock core runner."""
import csv
import hashlib
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from common import atomic_json,read_json,result,validate_request
import test_admin as fixtures


class BatchWebTests(unittest.TestCase):
    setUp=fixtures.AdminTests.setUp
    tearDown=fixtures.AdminTests.tearDown
    req=fixtures.AdminTests.req
    csrf=fixtures.AdminTests.csrf
    login=fixtures.AdminTests.login
    submit=fixtures.AdminTests.submit

    def fake_core(self,command,log,env,timeout):
        self.calls.append(command)
        if '--delete-batch-preview' in command:
            approved=Path(command[command.index('--approval')+1])
            with approved.open() as stream:emails=[r['email'] for r in csv.DictReader(stream)]
            mailboxes=[dict(email=e,source='/var/vmail/example.ru/'+e.split('@')[0],size_bytes=123,identity=dict(classification='missing' if e.startswith('missing@') else 'unknown-disabled')) for e in emails]
            atomic_json(Path(command[command.index('--output')+1]),dict(schema=2,action='permanent-mailbox-batch-delete',mailboxes=mailboxes,
                       limits=dict(mailboxes_total=10,max_count=100,max_percent=25),size_bytes=123*len(emails)))
            log.write_text('DELETE BATCH PREVIEW fixture');return 0
        if '--delete-batch' in command:
            plan=read_json(Path(command[command.index('--preview')+1]));emails=[row['email'] for row in plan['mailboxes']]
            record=dict(schema=2,selected=emails,deleted=emails,failed_email='',not_processed=[],status='done')
            atomic_json(Path(command[command.index('--output')+1]),record);log.write_text('COMPLETE fixture');return 0
        self.calls.pop()
        code=fixtures.AdminTests.fake_core(self,command,log,env,timeout)
        if '--normal' in command:
            with (Path(self.c['report_root'])/'mailboxes-20261001-120000.csv').open('a') as f:
                f.write('missing@example.ru,missing,Not set,\nprotected@example.ru,protected-disabled,Leave,userPrincipalName\n')
        return code

    def reviewed_batch(self):
        report=self.submit('report')
        preview=self.submit('delete-batch-preview',report=report,emails=['missing@example.ru','dead@example.ru'])
        self.assertEqual(result(self.c['jobs'],preview)['status'],'done')
        return preview

    def approved(self):
        preview=self.reviewed_batch();self.c['allow_delete']=True
        return preview

    def result_runner(self,status,code):
        def run(command,log,env,timeout):
            plan=read_json(command[command.index('--preview')+1]);emails=[r['email'] for r in plan['mailboxes']]
            if status=='dry-run':deleted=[];failed='';remaining=emails
            elif status=='partial':deleted=emails[:1];failed=emails[1];remaining=[]
            elif status in ('running','interrupted'):deleted=emails[:1];failed='';remaining=emails[1:]
            else:deleted=emails;failed='';remaining=[]
            atomic_json(Path(command[command.index('--output')+1]),dict(schema=2,selected=emails,deleted=deleted,failed_email=failed,not_processed=remaining,status=status))
            log.write_text('batch fixture')
            if code is None:raise ValueError('fixture timeout')
            return code
        return run

    def test_selection_requires_login_and_disables_protected_rows(self):
        self.assertEqual(self.req('/delete-selection').status_code,302)
        self.submit('report');self.login();page=self.req('/delete-selection').data
        self.assertIn(b'id="batch-select-visible"',page)
        self.assertIn(b'value="protected@example.ru" disabled',page)
        self.assertIn(b'value="active@example.ru" disabled',page)
        self.assertIn(b'value="dead@example.ru" ',page)

    def test_selection_page_without_report_offers_safe_report(self):
        self.login();page=self.req('/delete-selection').data
        self.assertIn('Сначала создайте штатный отчёт'.encode(),page)

    def test_preview_route_creates_exact_selected_allowlist_and_no_delete(self):
        report=self.submit('report');self.login()
        response=self.req('/jobs/'+report+'/delete-batch-preview',dict(csrf=self.csrf(),email=['missing@example.ru','dead@example.ru']))
        self.assertEqual(response.status_code,302)
        pending=next(Path(self.c['inbox']).glob('*.json'));self.w.handle(pending)
        approval=(Path(self.c['jobs'])/pending.stem/'approval.csv').read_text()
        self.assertEqual(approval,'email,approved\ndead@example.ru,YES\nmissing@example.ru,YES\n')
        self.assertIn('--delete-batch-preview',self.calls[-1]);self.assertNotIn('--delete-batch',self.calls[-1])

    def test_bad_or_protected_selection_rejected_before_cli(self):
        report=self.submit('report');before=len(self.calls)
        for email in ('active@example.ru','protected@example.ru','unknown@example.ru'):
            preview=self.submit('delete-batch-preview',report=report,emails=['dead@example.ru',email])
            self.assertEqual(result(self.c['jobs'],preview)['status'],'failed')
        self.assertEqual(len(self.calls),before)

    def test_protocol_rejects_empty_duplicate_large_path_or_secret_fields(self):
        good=dict(id='a'*32,operation='delete-batch-preview',created=1,actor='admin',report='b'*32,emails=['dead@example.ru'])
        for data in (dict(good,emails=[]),dict(good,emails=['x@example.ru']*2),dict(good,emails=[str(i)+'@example.ru' for i in range(501)]),dict(good,emails=['../x@example.ru']),dict(good,password='secret')):
            with self.assertRaises(ValueError):validate_request(data)

    def test_canonical_duplicate_selection_rejected(self):
        report=self.submit('report')
        preview=self.submit('delete-batch-preview',report=report,emails=['DEAD@example.ru','dead@example.ru'])
        self.assertEqual(result(self.c['jobs'],preview)['status'],'failed')

    def test_batch_confirmation_exact_count_password_and_all_writers(self):
        preview=self.approved();self.login()
        good=dict(confirm='УДАЛИТЬ 2',password='test-password-123',writers='yes')
        for key,value in (('confirm','УДАЛИТЬ 3'),('password','bad'),('writers','no')):
            data=dict(good);data[key]=value;data['csrf']=self.csrf()
            self.req('/jobs/'+preview+'/delete-batch',data)
            self.assertFalse(list(Path(self.c['inbox']).glob('*.json')))

    def test_deletion_permission_is_required(self):
        preview=self.reviewed_batch();self.login()
        self.assertEqual(self.req('/jobs/'+preview+'/delete-batch',dict(csrf=self.csrf(),confirm='УДАЛИТЬ 2',password='test-password-123',writers='yes')).status_code,403)

    def test_success_uses_count_from_preview_single_use_and_no_queued_password(self):
        preview=self.approved();self.login()
        self.req('/jobs/'+preview+'/delete-batch',dict(csrf=self.csrf(),confirm='УДАЛИТЬ 2',password='test-password-123',writers='yes'))
        pending=next(Path(self.c['inbox']).glob('*.json'));self.assertNotIn('password',read_json(pending));self.w.handle(pending)
        meta=result(self.c['jobs'],pending.stem);self.assertEqual(meta['status'],'done');self.assertEqual(meta['batch_result']['deleted'],['dead@example.ru','missing@example.ru'])
        self.assertEqual(self.calls[-1][self.calls[-1].index('--confirm-delete')+1],'DELETE 2')
        repeated=self.submit('delete-batch',preview=preview,confirmed=True,writers_quiesced=True)
        self.assertEqual(result(self.c['jobs'],repeated)['status'],'failed')

    def test_changed_allowlist_or_plan_hash_rejected(self):
        for name in ('approval.csv','delete-batch-preview.json'):
            preview=self.approved();(Path(self.c['jobs'])/preview/name).write_text('changed')
            before=len(self.calls);ident=self.submit('delete-batch',preview=preview,confirmed=True,writers_quiesced=True)
            self.assertEqual(result(self.c['jobs'],ident)['status'],'failed');self.assertEqual(len(self.calls),before)

    def test_partial_result_shows_deleted_failed_and_not_replayed(self):
        preview=self.approved();self.w.runner=self.result_runner('partial',3)
        ident=self.submit('delete-batch',preview=preview,confirmed=True,writers_quiesced=True);meta=result(self.c['jobs'],ident)
        self.assertEqual(meta['status'],'partial');self.assertEqual(meta['batch_result']['deleted'],['dead@example.ru'])
        self.login();page=self.req('/jobs/'+ident).data
        self.assertIn('Удалён безвозвратно'.encode(),page);self.assertIn('Ошибка: проверить'.encode(),page)
        self.assertTrue((Path(self.c['jobs'])/preview/'used.json').exists())

    def test_dry_run_is_not_reported_as_success(self):
        preview=self.approved();self.w.runner=self.result_runner('dry-run',0)
        ident=self.submit('delete-batch',preview=preview,confirmed=True,writers_quiesced=True)
        self.assertEqual(result(self.c['jobs'],ident)['status'],'dry-run')

    def test_timeout_preserves_confirmed_progress_and_marks_interrupted(self):
        preview=self.approved();self.w.runner=self.result_runner('running',None)
        ident=self.submit('delete-batch',preview=preview,confirmed=True,writers_quiesced=True);meta=result(self.c['jobs'],ident)
        self.assertEqual(meta['status'],'interrupted');self.assertEqual(meta['batch_result']['deleted'],['dead@example.ru'])

    def test_failure_after_deleted_member_keeps_exact_confirmed_result(self):
        preview=self.approved();self.w.runner=self.result_runner('interrupted',3)
        ident=self.submit('delete-batch',preview=preview,confirmed=True,writers_quiesced=True)
        self.assertEqual(result(self.c['jobs'],ident)['status'],'interrupted')

    def test_missing_or_inconsistent_result_can_never_be_success(self):
        preview=self.approved()
        def no_result(command,log,env,timeout):log.write_text('fixture without result');return 0
        self.w.runner=no_result
        ident=self.submit('delete-batch',preview=preview,confirmed=True,writers_quiesced=True)
        self.assertEqual(result(self.c['jobs'],ident)['status'],'failed')

    def test_batch_preview_and_result_downloads_require_login(self):
        preview=self.approved()
        self.assertEqual(self.req('/jobs/'+preview+'/download/delete-batch-preview.json').status_code,302)
        self.login();self.assertEqual(self.req('/jobs/'+preview+'/download/delete-batch-preview.json').status_code,200)

    def test_inconsistent_deleted_list_can_never_be_success(self):
        preview=self.approved()
        def invalid(command,log,env,timeout):
            atomic_json(Path(command[command.index('--output')+1]),dict(schema=2,selected=['dead@example.ru','missing@example.ru'],deleted=['outside@example.ru'],failed_email='',not_processed=[],status='done'))
            log.write_text('inconsistent result');return 0
        self.w.runner=invalid
        ident=self.submit('delete-batch',preview=preview,confirmed=True,writers_quiesced=True)
        self.assertNotEqual(result(self.c['jobs'],ident)['status'],'done')

    def test_restart_recovers_confirmed_progress_without_replay(self):
        preview=self.approved();self.w.runner=self.result_runner('running',3)
        ident=self.submit('delete-batch',preview=preview,confirmed=True,writers_quiesced=True)
        p=Path(self.c['jobs'])/ident/'result.json';meta=read_json(p);meta['status']='running';meta.pop('batch_result')
        atomic_json(p,meta)
        with patch.object(self.w,'runner') as runner:self.w.recover()
        runner.assert_not_called()
        meta=read_json(p);self.assertEqual(meta['status'],'interrupted');self.assertEqual(meta['batch_result']['deleted'],['dead@example.ru'])


if __name__=='__main__':unittest.main(verbosity=2)
