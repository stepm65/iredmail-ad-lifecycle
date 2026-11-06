# Copyright (c) 2026, rslpro
# SPDX-License-Identifier: AGPL-3.0-only
"""Irreversible operation tests use only temporary workspace mailbox trees."""
import base64
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import legacy as L
import manage as M
import delete_mailbox as D
import test_legacy as fixtures


class DeleteTests(unittest.TestCase):
    setUp = fixtures.LegacyTests.setUp
    tearDown = fixtures.LegacyTests.tearDown
    box = fixtures.LegacyTests.box

    def setup_delete(self, missing=False):
        self.source = self.box()
        self.a.email = 'dead@example.ru'
        self.a.output = str(self.base/'preview.json')
        self.a.preview = self.a.output
        self.a.report = False
        self.a.actor = 'admin'
        self.a.exclusions = str(self.base/'exclusions.json')
        self.a.reason = 'keep'
        self.a.confirm_delete = self.a.email
        self.a.writers_quiesced = True
        self.a.dry_run = 0
        self.entry = {'dn':['CN=dead,OU=OTHER,DC=example,DC=ru'], 'objectclass':['top','person','user'],
                      'userprincipalname':[self.a.email], 'objectguid':['a'*32], 'useraccountcontrol':['514']}
        self.entries = [] if missing else [self.entry]
        self.directory = unittest.mock.Mock()
        self.directory.broad.side_effect = lambda: set() if not self.entries else {self.a.email}
        self.directory.query.side_effect = lambda *args: self.entries
        self.ad_patch = patch.object(L, 'Directory', return_value=self.directory)
        self.ad_patch.start(); self.addCleanup(self.ad_patch.stop)

    def reviewed(self, missing=False):
        self.setup_delete(missing)
        D.preview(self.a)
        self.a.output = str(self.base/'result.json')

    def test_unknown_disabled_delete_keeps_other_mailbox_archives_and_ad(self):
        self.reviewed()
        other = self.box('other')
        self.arc.mkdir(); (self.arc/'old-archive').write_text('keep')
        D.delete(self.a)
        self.assertFalse(self.source.exists())
        self.assertTrue((other/'Maildir/cur/message').exists())
        self.assertEqual((self.arc/'old-archive').read_text(), 'keep')
        self.assertEqual(self.entry['useraccountcontrol'], ['514'])
        self.assertTrue(json.loads(Path(self.a.output).read_text())['permanent'])

    def test_missing_delete_requires_absence_in_wide_ad(self):
        self.reviewed(missing=True)
        D.delete(self.a)
        self.assertFalse(self.source.exists())
        self.assertEqual(self.directory.query.call_args[0][0], self.a.search_base)

    def test_known_statuses_active_and_bad_uac_are_blocked(self):
        self.setup_delete()
        for status in range(9):
            self.entry['extensionattribute6']=[str(status)]
            with self.assertRaises(L.SafetyError): D.preview(self.a)
        self.entry.pop('extensionattribute6')
        for uac in ('512','invalid'):
            self.entry['useraccountcontrol']=[uac]
            with self.assertRaises(L.SafetyError): D.preview(self.a)
        self.assertTrue(self.source.exists())

    def test_duplicate_and_alias_only_are_blocked(self):
        self.setup_delete()
        self.entries.append(dict(self.entry))
        with self.assertRaises(L.SafetyError): D.preview(self.a)
        self.entries.pop(); self.entry['userprincipalname']=['other@example.ru']
        with self.assertRaises(L.SafetyError): D.preview(self.a)

    def test_reappeared_guid_or_changed_status_prevents_delete(self):
        self.reviewed()
        self.entry['objectguid']=['b'*32]
        with self.assertRaises(L.SafetyError): D.delete(self.a)
        self.entry['extensionattribute6']=['2']
        with self.assertRaises(L.SafetyError): D.delete(self.a)
        self.assertTrue(self.source.exists())

    def test_modified_files_cancel_delete(self):
        self.reviewed()
        (self.source/'Maildir/cur/message').write_text('new mail')
        with self.assertRaises(L.SafetyError): D.delete(self.a)
        self.assertTrue(self.source.exists())

    def test_tampered_path_is_never_used(self):
        self.reviewed()
        p=Path(self.a.preview); plan=json.loads(p.read_text()); plan['mailbox']['source']=str(self.base)
        p.write_text(json.dumps(plan))
        with self.assertRaises(L.SafetyError): D.delete(self.a)
        self.assertTrue(self.source.exists())

    def test_new_exclusion_overrides_preview(self):
        self.reviewed()
        M.protection(self.a, self.a.email)
        with self.assertRaises(L.SafetyError): D.delete(self.a)
        self.assertTrue(self.source.exists())

    def test_dry_run_and_report_do_not_detach_or_remove(self):
        self.reviewed()
        for dry, report in ((1,False),(0,True)):
            self.a.dry_run, self.a.report = dry, report
            D.delete(self.a)
        self.assertTrue(self.source.exists())
        self.assertFalse((self.source.parent/'.lifecycle-deleting').exists())

    def test_exact_address_and_writer_ack_required(self):
        self.reviewed()
        for address, ack in (('other@example.ru',True),(self.a.email,False)):
            self.a.confirm_delete, self.a.writers_quiesced=address, ack
            with self.assertRaises(L.SafetyError): D.delete(self.a)
        self.assertTrue(self.source.exists())

    def test_ldap_error_or_invalid_guid_preserves_source(self):
        self.setup_delete()
        self.directory.broad.side_effect=L.SafetyError('LDAP unavailable')
        with self.assertRaises(L.SafetyError): D.preview(self.a)
        self.directory.broad.side_effect=lambda: {self.a.email}
        self.entry.pop('objectguid')
        with self.assertRaises(L.SafetyError): D.preview(self.a)
        self.assertTrue(self.source.exists())

    def test_rmtree_failure_records_staging_without_success(self):
        self.reviewed()
        with patch.object(D.shutil, 'rmtree', side_effect=OSError('fixture failure')):
            with self.assertRaises(OSError): D.delete(self.a)
        self.assertFalse(self.source.exists())
        stages=list((self.source.parent/'.lifecycle-deleting').glob('*/payload'))
        self.assertEqual(len(stages),1)
        self.assertTrue((stages[0]/'Maildir/cur/message').exists())
        self.assertFalse(Path(self.a.output).exists())

    def test_unconfigured_domain_and_unsafe_email_rejected(self):
        self.setup_delete()
        for email in ('dead@elsewhere.ru','../dead@example.ru'):
            self.a.email=email
            with self.assertRaises(L.SafetyError): D.preview(self.a)

    def test_guid_ldif_binary_is_decoded_without_relaxing_utf8(self):
        raw=bytes(range(16))
        entry=L.ldif('dn: CN=user\nobjectGUID:: '+base64.b64encode(raw).decode()+'\n')[0]
        self.assertEqual(entry['objectguid'],[raw.hex()])
        with self.assertRaises(L.SafetyError): L.ldif('dn: CN=user\nobjectGUID:: eA==\n')
        with self.assertRaises(L.SafetyError): L.ldif('dn: CN=user\nmail:: /w==\n')


if __name__=='__main__': unittest.main(verbosity=2)
