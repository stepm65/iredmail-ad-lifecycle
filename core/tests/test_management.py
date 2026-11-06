# Copyright (c) 2026, rslpro
# SPDX-License-Identifier: AGPL-3.0-only
"""Recovery/protection tests: real temporary trees, AD/rsync mocked."""
import csv
import importlib.util
import json
import os
from pathlib import Path
import shutil
import sys
import time
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import legacy as L
import manage as M
import test_legacy as fixtures
FakeAD = fixtures.FakeAD


class ManagementTests(unittest.TestCase):
    setUp = fixtures.LegacyTests.setUp
    tearDown = fixtures.LegacyTests.tearDown
    box = fixtures.LegacyTests.box
    inventory = fixtures.LegacyTests.inventory
    approve = fixtures.LegacyTests.approve
    copy_stub = fixtures.LegacyTests.copy_stub

    def setup_management(self):
        self.a.exclusions = str(self.base / 'policy/exclusions.json')
        self.a.actor = 'test-admin'
        self.a.reason = 'Protected for business'
        self.a.report = False
        self.a.output = str(self.base / 'output.csv')
        self.a.preview = str(self.base / 'preview.json')
        self.a.archive_id = ''

    def historical(self, login='dead'):
        self.setup_management()
        source = self.box(login)
        dest = self.normal / 'example.ru' / login / '20200101T010101'
        dest.parent.mkdir(parents=True)
        shutil.move(source, dest)
        row = M.archives(self.a)[0]
        self.a.archive_id = row['archive_id']
        self.a.output = self.a.preview
        M.preview(self.a)
        self.a.output = str(self.base / 'restored.json')
        return source, dest

    def restore_stub(self, cmd, timeout=600):
        if '-aHAXnci' in cmd:
            return ''
        source, dest = Path(cmd[-2]), Path(cmd[-1])
        ignored = [v.split('=/', 1)[1] for v in cmd if v.startswith('--exclude=/')]
        shutil.copytree(source, dest, dirs_exist_ok=True, copy_function=shutil.copy2,
                        ignore=lambda path, names: set(ignored) & set(names) if Path(path) == source else set())
        return ''

    def live(self):
        self.a.dry_run = 0
        self.a.writers_quiesced = True

    def test_historical_normal_restore_keeps_archive_and_adds_exclusion(self):
        source, dest = self.historical()
        self.live()
        with patch.object(L, 'run_checked', side_effect=self.restore_stub):
            M.restore(self.a)
        self.assertEqual((source/'Maildir/cur/message').read_bytes(), b'mail content\n')
        self.assertTrue(dest.exists())
        self.assertIn('dead@example.ru', L.load_exclusions(self.a.exclusions))
        self.assertTrue(json.loads(Path(self.a.output).read_text())['archive_kept'])

    def test_restore_dry_run_never_creates_source_or_policy(self):
        source, dest = self.historical()
        with patch.object(L, 'run_checked') as runner:
            M.restore(self.a)
        runner.assert_not_called()
        self.assertFalse(source.exists())
        self.assertFalse(Path(self.a.exclusions).exists())
        self.assertTrue(dest.exists())

    def test_restore_live_needs_writer_ack(self):
        source, dest = self.historical()
        self.a.dry_run = 0
        with self.assertRaises(L.SafetyError):
            M.restore(self.a)
        self.assertFalse(source.exists())

    def test_existing_empty_directory_never_overwritten(self):
        source, dest = self.historical()
        source.mkdir()
        self.live()
        with self.assertRaises(L.SafetyError):
            M.restore(self.a)
        self.assertEqual(list(source.iterdir()), [])

    def test_archive_change_after_preview_blocks_restore(self):
        source, dest = self.historical()
        (dest/'Maildir/cur/message').write_text('changed')
        self.live()
        with self.assertRaises(L.SafetyError):
            M.restore(self.a)
        self.assertFalse(source.exists())

    def test_tampered_preview_path_rejected(self):
        source, dest = self.historical()
        p = Path(self.a.preview)
        data = json.loads(p.read_text())
        data['archive']['source_path'] = str(self.base/'elsewhere')
        p.write_text(json.dumps(data))
        self.live()
        with self.assertRaises(L.SafetyError):
            M.restore(self.a)
        self.assertFalse(source.exists())

    def test_copy_error_preserves_archive_and_unpublished_staging(self):
        source, dest = self.historical()
        self.live()
        with patch.object(L, 'run_checked', side_effect=L.SafetyError('copy failure')), self.assertRaises(L.SafetyError):
            M.restore(self.a)
        self.assertFalse(source.exists())
        self.assertTrue(dest.exists())
        self.assertTrue(list((source.parent/'.lifecycle-staging').iterdir()))

    def test_checksum_difference_does_not_publish(self):
        source, dest = self.historical()
        self.live()
        def bad(cmd, timeout=600):
            if '-aHAXnci' in cmd:
                return '>fcs message'
            return self.restore_stub(cmd, timeout)
        with patch.object(L, 'run_checked', side_effect=bad), self.assertRaises(L.SafetyError):
            M.restore(self.a)
        self.assertFalse(source.exists())

    def test_destination_appearing_during_copy_preserved(self):
        source, dest = self.historical()
        self.live()
        def concurrent(cmd, timeout=600):
            value = self.restore_stub(cmd, timeout)
            if '-aHAXnci' in cmd:
                source.mkdir()
                (source/'keep').write_text('new data')
            return value
        with patch.object(L, 'run_checked', side_effect=concurrent), self.assertRaises(L.SafetyError):
            M.restore(self.a)
        self.assertEqual((source/'keep').read_text(), 'new data')

    def test_atomic_publish_refuses_existing_destination(self):
        a = self.base/'stage'; b = self.base/'existing'
        a.mkdir(); b.mkdir()
        (a/'mail').write_text('mail')
        with self.assertRaises(OSError):
            M.publish_no_replace(a, b)
        self.assertTrue((a/'mail').exists())
        self.assertEqual(list(b.iterdir()), [])

    def test_legacy_archive_restore_uses_original_recorded_path(self):
        self.setup_management()
        source = self.box()
        self.inventory()
        self.approve()
        self.live()
        with patch.object(L, 'run_checked', side_effect=self.copy_stub):
            L.archive(self.a, FakeAD())
        row = M.archives(self.a)[0]
        self.assertEqual(row['quality'], 'marked-complete')
        self.a.archive_id = row['archive_id']
        self.a.output = self.a.preview
        M.preview(self.a)
        self.a.output = str(self.base/'restored.json')
        with patch.object(L, 'run_checked', side_effect=self.restore_stub):
            M.restore(self.a)
        self.assertTrue((source/'Maildir/cur/message').exists())

    def test_incomplete_legacy_archive_not_restorable(self):
        self.setup_management()
        dest = self.arc/'example.ru/dead/20200101T010101Z-01234567/mailbox/Maildir'
        dest.mkdir(parents=True)
        row = M.archives(self.a)[0]
        self.assertEqual(row['restorable'], 'no')
        self.assertEqual(row['reason'], 'incomplete-archive')

    def test_forged_complete_marker_outside_source_layout_rejected(self):
        source, dest = self.historical()
        L.safe_json(dest/M.NORMAL_MARKER, dict(package='iredmail-ad-lifecycle', kind='normal', complete=True,
                   email='dead@example.ru', archive=str(dest), source=str(self.base/'outside'), archived_at_epoch=1))
        with self.assertRaises(L.SafetyError):
            M.archives(self.a)

    def test_unicode_protection_add_remove_and_list(self):
        self.setup_management()
        M.protection(self.a, 'СТАРЫЙ@example.ru')
        self.assertIn('старый@example.ru', L.load_exclusions(self.a.exclusions))
        M.exclusions_report(self.a)
        with Path(self.a.output).open(encoding='utf-8') as stream:
            self.assertEqual(list(csv.DictReader(stream))[0]['email'], 'старый@example.ru')
        M.protection(self.a, 'старый@example.ru', remove=True)
        self.assertEqual(L.load_exclusions(self.a.exclusions), {})

    def test_invalid_policy_fails_closed(self):
        self.setup_management()
        Path(self.a.exclusions).parent.mkdir()
        Path(self.a.exclusions).write_text('{bad JSON')
        self.box()
        with self.assertRaises(ValueError):
            self.inventory()

    def test_new_exclusion_blocks_previous_approved_legacy_snapshot(self):
        self.setup_management()
        source = self.box()
        self.inventory()
        self.approve()
        M.protection(self.a, 'dead@example.ru')
        self.live()
        with patch.object(L, 'run_checked') as runner:
            L.archive(self.a, FakeAD())
        runner.assert_not_called()
        self.assertTrue(source.exists())

    def test_exclusion_makes_inventory_ineligible(self):
        self.setup_management()
        self.box()
        M.protection(self.a, 'dead@example.ru')
        rows = self.inventory()
        self.assertEqual(rows['dead@example.ru']['eligible'], 'no')
        self.assertEqual(rows['dead@example.ru']['reason'], 'excluded')

    def test_exclusions_do_not_change_ad_protection_rules(self):
        self.setup_management()
        self.box()
        M.protection(self.a, 'unrelated@example.ru')
        self.assertEqual(self.inventory(FakeAD(broad=['dead@example.ru']))['dead@example.ru']['eligible'], 'no')

    def test_exclusion_protects_expired_legacy_archive_from_purge(self):
        self.setup_management()
        self.box(); self.inventory(); self.approve(); self.live()
        with patch.object(L,'run_checked',side_effect=self.copy_stub):
            L.archive(self.a,FakeAD())
        record = next(self.arc.glob('*/*/*/'+L.MARKER))
        data = json.loads(record.read_text(encoding='utf-8'))
        data['archived_at_epoch'] = int(self.old)
        record.write_text(json.dumps(data))
        M.protection(self.a,'dead@example.ru')
        self.a.retention = 1
        L.purge(self.a,FakeAD())
        self.assertTrue(record.exists())


if __name__ == '__main__':
    unittest.main(verbosity=2)
