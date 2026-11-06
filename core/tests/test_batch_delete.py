# Copyright (c) 2026, rslpro
# SPDX-License-Identifier: AGPL-3.0-only
"""Batch tests delete only explicitly constructed workspace fixture trees."""
import copy
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import delete_mailbox as D
import legacy as L
import manage as M
import test_delete as fixtures


class BatchTests(unittest.TestCase):
    setUp=fixtures.DeleteTests.setUp
    tearDown=fixtures.DeleteTests.tearDown
    box=fixtures.DeleteTests.box
    setup_delete=fixtures.DeleteTests.setup_delete

    def setup_batch(self):
        self.setup_delete()
        self.sources={'dead@example.ru':self.source,'dead2@example.ru':self.box('dead2'),
                      'missing@example.ru':self.box('missing'),'active@example.ru':self.box('active')}
        other=copy.deepcopy(self.entry);other['userprincipalname']=['dead2@example.ru'];other['objectguid']=['b'*32];other['dn']=['CN=other,DC=example,DC=ru']
        self.users={'dead@example.ru':self.entry,'dead2@example.ru':other}
        self.directory.broad.side_effect=lambda: set(self.users)
        self.directory.query.side_effect=lambda base,q,attrs: [e for email,e in self.users.items() if email in q]
        self.a.delete_max_count=100;self.a.delete_max_percent=100
        self.emails=['dead2@example.ru','dead@example.ru','missing@example.ru']
        self.emails.sort()
        self.a.approval=str(self.base/'selected.csv')
        Path(self.a.approval).write_text('email,approved\n'+''.join(e+',YES\n' for e in self.emails))
        self.a.output=str(self.base/'batch-preview.json');self.a.preview=self.a.output
        self.a.confirm_delete='DELETE 3'

    def reviewed_batch(self):
        self.setup_batch();D.batch_preview(self.a);self.a.output=str(self.base/'batch-result.json')

    def test_all_selected_removed_and_other_mailbox_archives_ad_kept(self):
        self.reviewed_batch();self.arc.mkdir();(self.arc/'archive').write_text('keep')
        self.assertEqual(D.batch_delete(self.a),0)
        result=json.loads(Path(self.a.output).read_text())
        self.assertEqual(result['deleted'],self.emails);self.assertEqual(result['status'],'done')
        for email in self.emails:self.assertFalse(self.sources[email].exists())
        self.assertTrue(self.sources['active@example.ru'].exists())
        self.assertEqual((self.arc/'archive').read_text(),'keep')
        self.assertEqual(self.entry['useraccountcontrol'],['514'])

    def test_changed_last_files_abort_entire_batch_before_first_delete(self):
        self.reviewed_batch();(self.sources[self.emails[-1]]/'Maildir/cur/message').write_text('new delivery')
        with self.assertRaises(L.SafetyError):D.batch_delete(self.a)
        for p in self.sources.values():self.assertTrue(p.exists())
        self.assertFalse(Path(self.a.output).exists())

    def test_protected_ad_state_in_second_member_aborts_all(self):
        self.reviewed_batch();self.users[self.emails[1]]['extensionattribute6']=['2']
        with self.assertRaises(L.SafetyError):D.batch_delete(self.a)
        for p in self.sources.values():self.assertTrue(p.exists())

    def test_new_exclusion_aborts_all_members(self):
        self.reviewed_batch();M.protection(self.a,self.emails[-1])
        with self.assertRaises(L.SafetyError):D.batch_delete(self.a)
        for p in self.sources.values():self.assertTrue(p.exists())

    def test_count_and_percent_limits_block_preview(self):
        self.setup_batch();self.a.delete_max_count=2
        with self.assertRaises(L.SafetyError):D.batch_preview(self.a)
        self.a.delete_max_count=100;self.a.delete_max_percent=25
        with self.assertRaises(L.SafetyError):D.batch_preview(self.a)
        self.assertFalse(Path(self.a.output).exists())

    def test_current_limits_override_preview(self):
        self.reviewed_batch();self.a.delete_max_count=2
        with self.assertRaises(L.SafetyError):D.batch_delete(self.a)
        for p in self.sources.values():self.assertTrue(p.exists())

    def test_invalid_limits_and_zero_percent_rejected(self):
        self.setup_batch()
        for count,percent in ((0,100),(501,100),(100,0),(100,101)):
            self.a.delete_max_count,self.a.delete_max_percent=count,percent
            with self.assertRaises(L.SafetyError):D.batch_preview(self.a)

    def test_duplicate_or_empty_approval_rejected(self):
        self.setup_batch()
        for content in ('email,approved\ndead@example.ru,YES\nDEAD@example.ru,YES\n','email,approved\ndead@example.ru,NO\n'):
            Path(self.a.approval).write_text(content)
            with self.assertRaises(L.SafetyError):D.batch_preview(self.a)

    def test_report_and_dry_run_produce_zero_deletion_result(self):
        self.reviewed_batch();self.a.report=True
        self.assertEqual(D.batch_delete(self.a),0)
        result=json.loads(Path(self.a.output).read_text());self.assertEqual(result['status'],'dry-run');self.assertEqual(result['deleted'],[])
        for p in self.sources.values():self.assertTrue(p.exists())
        self.assertFalse((self.root/'example.ru/.lifecycle-deleting').exists())

    def test_count_confirmation_and_all_writer_ack_required(self):
        self.reviewed_batch()
        for count,ack in (('DELETE 2',True),('DELETE 3',False)):
            self.a.confirm_delete,self.a.writers_quiesced=count,ack
            with self.assertRaises(L.SafetyError):D.batch_delete(self.a)
        for p in self.sources.values():self.assertTrue(p.exists())

    def test_changed_approved_path_is_never_used(self):
        self.reviewed_batch();p=Path(self.a.preview);plan=json.loads(p.read_text());plan['mailboxes'][-1]['source']=str(self.base)
        p.write_text(json.dumps(plan))
        with self.assertRaises(L.SafetyError):D.batch_delete(self.a)
        self.assertTrue(self.base.exists())
        for p in self.sources.values():self.assertTrue(p.exists())

    def test_partial_ad_change_stops_rest_and_records_exact_members(self):
        self.reviewed_batch();original=D.delete
        def mutate(item):
            original(item)
            self.users[self.emails[1]]['useraccountcontrol']=['512']
        with patch.object(D,'delete',side_effect=mutate):self.assertEqual(D.batch_delete(self.a),3)
        result=json.loads(Path(self.a.output).read_text())
        self.assertEqual(result['status'],'partial');self.assertEqual(result['deleted'],self.emails[:1])
        self.assertEqual(result['failed_email'],self.emails[1]);self.assertEqual(result['not_processed'],self.emails[2:])
        self.assertFalse(self.sources[self.emails[0]].exists());self.assertTrue(self.sources[self.emails[1]].exists());self.assertTrue(self.sources[self.emails[2]].exists())

    def test_partial_new_exclusion_overrides_batch_approval(self):
        self.reviewed_batch();original=D.delete
        def mutate(item):
            original(item);M.protection(self.a,self.emails[1])
        with patch.object(D,'delete',side_effect=mutate):self.assertEqual(D.batch_delete(self.a),3)
        result=json.loads(Path(self.a.output).read_text());self.assertEqual(result['deleted'],self.emails[:1])
        self.assertTrue(self.sources[self.emails[1]].exists());self.assertTrue(self.sources[self.emails[2]].exists())

    def test_failure_after_confirmed_single_delete_keeps_confirmed_progress(self):
        self.reviewed_batch();original=D.delete
        def fail_after(item):original(item);raise OSError('audit fixture failure')
        with patch.object(D,'delete',side_effect=fail_after):self.assertEqual(D.batch_delete(self.a),3)
        result=json.loads(Path(self.a.output).read_text());self.assertEqual(result['status'],'interrupted')
        self.assertEqual(result['deleted'],self.emails[:1]);self.assertEqual(result['not_processed'],self.emails[1:]);self.assertEqual(result['failed_email'],'')

    def test_existing_result_file_is_preserved_and_no_mail_deleted(self):
        self.reviewed_batch();Path(self.a.output).write_text('keep')
        with self.assertRaises(L.SafetyError):D.batch_delete(self.a)
        self.assertEqual(Path(self.a.output).read_text(),'keep')
        for p in self.sources.values():self.assertTrue(p.exists())


if __name__=='__main__':unittest.main(verbosity=2)
