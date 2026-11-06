# Copyright (c) 2026, rslpro
# SPDX-License-Identifier: AGPL-3.0-only
"""Safety tests with temporary real mailbox trees, fake AD and rsync boundary.
No production server/credentials are contacted. Linux rsync ACL/xattr fidelity
requires deployment validation; the copy substitute tests lifecycle decisions.
"""
import base64
import csv
import importlib.util
import json
import os
from pathlib import Path
import shutil
import tempfile
import time
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location("legacy", Path(__file__).resolve().parents[1] / "legacy.py")
L = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(L)


class FakeAD:
    def __init__(self, primary=(), broad=(), queries=None):
        self.p, self.b = set(primary), set(broad)
        self.queries = iter(queries) if queries is not None else None
        self.calls = []

    def primary(self):
        return self.p

    def broad(self):
        return self.b

    def exists(self, email):
        self.calls.append(email)
        result = next(self.queries) if self.queries is not None else email in self.b
        if isinstance(result, Exception):
            raise result
        return result


class LegacyTests(unittest.TestCase):
    def setUp(self):
        scratch = Path(os.environ.get("LIFECYCLE_TEST_TMP", Path(__file__).resolve().parents[2] / "test-scratch"))
        scratch.mkdir(exist_ok=True)
        self.tmp = tempfile.TemporaryDirectory(dir=scratch)
        self.base = Path(self.tmp.name).resolve()
        self.root = self.base / "vmail"
        self.normal = self.base / "archive"
        self.arc = self.base / "legacy"
        (self.root / "example.ru").mkdir(parents=True)
        self.a = L.parser().parse_args(["inventory", "--vmail-root", str(self.root),
                     "--normal-archive-root", str(self.normal), "--archive-root", str(self.arc),
                     "--domain", "example.ru", "--base", "OU=people,DC=example,DC=ru",
                     "--search-base", "DC=example,DC=ru", "--inventory", str(self.base / "inventory.csv"),
                     "--max-count", "20", "--max-percent", "100", "--separate-fs", "0"])
        self.old = time.time() - 800 * 86400

    def tearDown(self):
        self.tmp.cleanup()

    def box(self, login="dead", old=True):
        p = self.root / "example.ru" / login
        (p / "Maildir" / "cur").mkdir(parents=True)
        (p / "Maildir" / "new").mkdir()
        (p / "Maildir" / "cur" / "message").write_bytes(b"mail content\n")
        if old:
            for q in sorted(p.rglob("*"), reverse=True) + [p]:
                os.utime(q, (self.old, self.old))
        return p

    def inventory(self, ad=None):
        L.write_inventory(self.a, ad or FakeAD())
        return L.read_inventory(self.a.inventory)

    def approve(self, content="dead@example.ru\n", suffix=".txt"):
        p = self.base / ("approval" + suffix)
        p.write_text(content, encoding="utf-8")
        self.a.approval = str(p)
        self.a.command = "archive"
        return p

    def copy_stub(self, cmd, timeout=600):
        if "-aHAXnci" in cmd:
            src, dest = Path(cmd[-2]), Path(cmd[-1])
            self.assertEqual((src / "Maildir/cur/message").read_bytes(),
                             (dest / "Maildir/cur/message").read_bytes())
            return ""
        src, dest = Path(cmd[-2]), Path(cmd[-1])
        shutil.copytree(src, dest, dirs_exist_ok=True, copy_function=shutil.copy2)
        return ""

    def test_inventory_includes_size_path_activity_age_and_no_moves(self):
        p = self.box()
        before = L.activity(p)
        rows = self.inventory()
        r = rows["dead@example.ru"]
        self.assertEqual(r["mailbox_path"], str(p))
        self.assertEqual(int(r["size_bytes"]), 13)
        self.assertGreater(float(r["inactive_days"]), 799)
        self.assertEqual(r["eligible"], "yes")
        self.assertEqual(L.activity(p), before)
        self.assertFalse(self.arc.exists())

    def test_inventory_never_overwrites_reviewed_csv(self):
        self.box()
        self.inventory()
        with self.assertRaises(FileExistsError):
            self.inventory()

    def test_user_moved_to_other_ou_is_inventory_protected(self):
        self.box()
        rows = self.inventory(FakeAD(broad=["dead@example.ru"]))
        self.assertEqual(rows["dead@example.ru"]["broad_ad_status"], "present")
        self.assertEqual(rows["dead@example.ru"]["eligible"], "no")

    def test_primary_present_is_not_legacy_candidate(self):
        self.box()
        self.assertEqual(self.inventory(FakeAD(primary=["dead@example.ru"])), {})

    def test_recent_directory_mtime_blocks_old_messages(self):
        p = self.box()
        os.utime(p / "Maildir/new", None)
        self.assertEqual(self.inventory()["dead@example.ru"]["eligible"], "no")

    def test_future_mtime_blocks(self):
        p = self.box()
        os.utime(p / "Maildir/cur/message", (time.time()+86400, time.time()+86400))
        self.assertEqual(self.inventory()["dead@example.ru"]["eligible"], "no")

    def test_empty_maildir_uses_directory_age(self):
        p = self.box()
        (p / "Maildir/cur/message").unlink()
        for q in list(p.rglob("*")) + [p]:
            os.utime(q, (self.old, self.old))
        rows = self.inventory()
        self.assertEqual(rows["dead@example.ru"]["size_bytes"], "0")
        self.assertEqual(rows["dead@example.ru"]["eligible"], "yes")

    def test_allowlist_and_explicit_csv_yes_only(self):
        p = self.approve("email,approved\ndead@example.ru,YES\nkeep@example.ru,NO\n", ".csv")
        self.assertEqual(L.read_approval(p), {"dead@example.ru"})

    def test_inventory_is_not_approval(self):
        self.box()
        self.inventory()
        with self.assertRaises(L.SafetyError):
            L.read_approval(self.a.inventory)

    def test_missing_approval_aborts(self):
        self.box()
        self.inventory()
        self.a.command = "archive"
        with self.assertRaises(L.SafetyError):
            L.archive(self.a, FakeAD())

    def test_approval_without_inventory_aborts(self):
        self.box()
        self.approve()
        with self.assertRaises(FileNotFoundError):
            L.archive(self.a, FakeAD())

    def test_unknown_approval_email_aborts(self):
        self.box()
        self.inventory()
        self.approve("typo@example.ru\n")
        with self.assertRaises(L.SafetyError):
            L.archive(self.a, FakeAD())

    def test_recent_approved_row_aborts(self):
        self.box(old=False)
        self.inventory()
        self.approve()
        with self.assertRaises(L.SafetyError):
            L.archive(self.a, FakeAD())

    def test_dry_run_keeps_mail_and_archive_untouched(self):
        p = self.box()
        self.inventory()
        self.approve()
        L.archive(self.a, FakeAD())
        self.assertTrue(p.exists())
        self.assertFalse(self.arc.exists())

    def test_live_needs_writer_quiescence_assertion(self):
        self.box()
        self.inventory()
        self.approve()
        self.a.dry_run = 0
        with self.assertRaises(L.SafetyError):
            L.archive(self.a, FakeAD())

    def test_count_guard_aborts_whole_batch(self):
        p, q = self.box("dead"), self.box("dead2")
        self.inventory()
        self.approve("dead@example.ru\ndead2@example.ru\n")
        self.a.max_count = 1
        with self.assertRaises(L.SafetyError):
            L.archive(self.a, FakeAD())
        self.assertTrue(p.exists() and q.exists())

    def test_percent_guard_does_not_round_down(self):
        for i in range(3):
            self.box("dead" + str(i))
        self.inventory()
        self.approve("dead0@example.ru\n")
        self.a.max_percent = 33
        with self.assertRaises(L.SafetyError):
            L.archive(self.a, FakeAD())

    def test_all_is_explicit_and_still_enforces_limits(self):
        self.box("dead")
        self.box("dead2")
        self.inventory()
        self.a.command = "archive-all"
        self.a.max_count = 1
        with self.assertRaises(L.SafetyError):
            L.archive(self.a, FakeAD())

    def test_ad_reappears_during_preflight_no_moves(self):
        p = self.box()
        self.inventory()
        self.approve()
        L.archive(self.a, FakeAD(queries=[True]))
        self.assertTrue(p.exists())

    def test_ad_reappears_immediately_before_copy_no_moves(self):
        p = self.box()
        self.inventory()
        self.approve()
        L.archive(self.a, FakeAD(queries=[False, True]))
        self.assertTrue(p.exists())

    def test_ldap_error_is_not_absence(self):
        p = self.box()
        self.inventory()
        self.approve()
        with self.assertRaises(L.SafetyError):
            L.archive(self.a, FakeAD(queries=[L.SafetyError("LDAP unavailable")]))
        self.assertTrue(p.exists())

    def test_changed_since_inventory_blocks_entire_plan(self):
        p = self.box()
        self.inventory()
        self.approve()
        (p / "Maildir/cur/message").write_bytes(b"new activity")
        with self.assertRaises(L.SafetyError):
            L.archive(self.a, FakeAD())

    def test_forged_csv_path_cannot_select_another_directory(self):
        p = self.box()
        rows = self.inventory()
        rows["dead@example.ru"]["mailbox_path"] = str(self.base)
        with self.assertRaises(L.SafetyError):
            L.matches_snapshot(rows["dead@example.ru"], p, L.activity(p))

    def test_roots_cannot_overlap(self):
        self.a.archive_root = str(self.root / "archive")
        with self.assertRaises(L.SafetyError):
            L.validate_roots(self.a)

    def test_direct_legacy_subdir_allowed_deeper_rejected(self):
        self.a.archive_root = str(self.normal / "legacy")
        L.validate_roots(self.a)
        self.a.archive_root = str(self.normal / "legacy" / "nested")
        with self.assertRaises(L.SafetyError):
            L.validate_roots(self.a)

    def test_search_base_must_include_primary_ou(self):
        self.a.search_base = "OU=other,DC=example,DC=ru"
        with self.assertRaises(L.SafetyError):
            L.validate_roots(self.a)

    def live_archive(self, ad=None, rsync=None):
        p = self.box()
        self.inventory()
        self.approve()
        self.a.dry_run = 0
        self.a.writers_quiesced = True
        with patch.object(L, "run_checked", side_effect=rsync or self.copy_stub):
            L.archive(self.a, ad or FakeAD())
        return p

    def test_live_archives_only_approved_and_marks_completion(self):
        keep = self.box("keep")
        p = self.live_archive()
        self.assertFalse(p.exists())
        self.assertTrue(keep.exists())
        markers = list(self.arc.glob("*/*/*/" + L.MARKER))
        self.assertEqual(len(markers), 1)
        data = json.loads(markers[0].read_text())
        self.assertEqual(data["approval_kind"], "allowlist")
        self.assertTrue((markers[0].parent / "mailbox/Maildir/cur/message").exists())
        self.assertGreater(data["archived_at_epoch"], time.time()-60)

    def test_failed_copy_preserves_source_and_not_purgeable(self):
        with self.assertRaises(L.SafetyError):
            self.live_archive(rsync=lambda *args, **kw: (_ for _ in ()).throw(L.SafetyError("rsync failed")))
        self.assertTrue((self.root / "example.ru/dead").exists())
        self.assertFalse(list(self.arc.glob("*/*/*/" + L.MARKER)))

    def test_checksum_mismatch_preserves_source(self):
        def copy(cmd, timeout=600):
            return "checksum mismatch" if "-aHAXnci" in cmd else self.copy_stub(cmd, timeout)
        with self.assertRaises(L.SafetyError):
            self.live_archive(rsync=copy)
        self.assertTrue((self.root / "example.ru/dead").exists())

    def test_ad_reappears_after_copy_preserves_source(self):
        with self.assertRaises(L.SafetyError):
            self.live_archive(ad=FakeAD(queries=[False, False, True]))
        self.assertTrue((self.root / "example.ru/dead").exists())

    def test_source_change_during_copy_preserves_source(self):
        changed = [False]
        def copy(cmd, timeout=600):
            result = self.copy_stub(cmd, timeout)
            if "-aHAXnci" in cmd and not changed[0]:
                (Path(cmd[-2]) / "Maildir/cur/message").write_bytes(b"changed during copy")
                changed[0] = True
            return result
        with self.assertRaises(L.SafetyError):
            self.live_archive(rsync=copy)
        self.assertTrue((self.root / "example.ru/dead").exists())

    def test_separate_fs_guard_preserves_source(self):
        self.a.separate_fs = 1
        with self.assertRaises(L.SafetyError):
            self.live_archive()
        self.assertTrue((self.root / "example.ru/dead").exists())

    def test_explicit_bulk_success_audits_flag_and_preserves_recent(self):
        old, recent = self.box(), self.box("recent", old=False)
        self.inventory()
        self.a.command, self.a.dry_run, self.a.writers_quiesced = "archive-all", 0, True
        with patch.object(L, "run_checked", side_effect=self.copy_stub):
            L.archive(self.a, FakeAD())
        self.assertFalse(old.exists())
        self.assertTrue(recent.exists())
        marker = next(self.arc.glob("*/*/*/" + L.MARKER))
        record = json.loads(marker.read_text())
        self.assertEqual(record["approval_kind"], "explicit-archive-all")
        self.assertIsNone(record["approval_sha256"])

    def test_retention_uses_archive_time_not_message_time(self):
        self.live_archive()
        self.a.retention = 90
        L.purge(self.a, FakeAD())
        self.assertEqual(len(list(self.arc.glob("*/*/*/" + L.MARKER))), 1)

    def test_purge_zero_keeps_old_archive(self):
        self.live_archive()
        self.a.retention = 0
        L.purge(self.a, FakeAD())
        self.assertEqual(len(list(self.arc.glob("*/*/*/" + L.MARKER))), 1)

    def test_explicit_purge_only_completed_expired(self):
        self.live_archive()
        marker = next(self.arc.glob("*/*/*/" + L.MARKER))
        data = json.loads(marker.read_text())
        data["archived_at_epoch"] = int(time.time()-100*86400)
        marker.write_text(json.dumps(data))
        incomplete = self.arc / "example.ru/dead/partial"
        incomplete.mkdir()
        self.a.retention = 90
        L.purge(self.a, FakeAD())
        self.assertFalse(marker.exists())
        self.assertTrue(incomplete.exists())

    def test_purge_dry_run_preserves_expired(self):
        self.live_archive()
        marker = next(self.arc.glob("*/*/*/" + L.MARKER))
        data = json.loads(marker.read_text())
        data["archived_at_epoch"] = int(time.time()-100*86400)
        marker.write_text(json.dumps(data))
        self.a.retention, self.a.dry_run = 90, 1
        L.purge(self.a, FakeAD())
        self.assertTrue(marker.exists())

    def test_base64_folded_and_case_insensitive_ldif(self):
        enc = base64.b64encode(b"dead@example.ru").decode()
        text = "dn: CN=dead\nUSERPRINCIPALNAME:: " + enc[:10] + "\n " + enc[10:] + "\nuserAccountControl: 514\nextensionAttribute6: 0\n\n"
        self.assertEqual(L.ad_table(L.ldif(text), "userPrincipalName", "extensionAttribute6"), [("dead@example.ru", "1", "0")])

    def test_duplicate_ad_addresses_are_protected(self):
        text = "dn: CN=x\nuserPrincipalName: dead@example.ru\n\ndn: CN=y\nuserPrincipalName: dead@example.ru\n\n"
        self.assertEqual(L.ad_table(L.ldif(text), "userPrincipalName", "extensionAttribute6"),
                         [("dead@example.ru", "ambiguous", "")])

    def test_missing_uac_does_not_become_terminated(self):
        text = "dn: CN=x\nuserPrincipalName: dead@example.ru\nextensionAttribute6: 0\n\n"
        self.assertEqual(L.ad_table(L.ldif(text), "userPrincipalName", "extensionAttribute6")[0][1], "unknown")

    def test_referral_and_malformed_ldif_fail_closed(self):
        for text in ("ref: ldap://other\n", "unexpected\n", "dn:< file:///tmp/x\n"):
            with self.assertRaises(L.SafetyError):
                L.ldif(text)

    def test_broad_exact_lookup_checks_aliases_no_ou_filter(self):
        d = L.Directory(self.a)
        with patch.object(d, "query", return_value=[]) as query:
            self.assertFalse(d.exists("dead@example.ru"))
        args = query.call_args.args
        self.assertEqual(args[0], "DC=example,DC=ru")
        self.assertIn("(mail=dead@example.ru)", args[1])
        self.assertIn("(proxyAddresses=smtp:dead@example.ru)", args[1])

    def test_broad_min_users_failure(self):
        self.a.min_users = 2
        d = L.Directory(self.a)
        with patch.object(d, "query", return_value=[{"dn": ["CN=x"]}]):
            with self.assertRaises(L.SafetyError):
                d.broad()

    def test_broad_alias_and_mail_presence_protect(self):
        d = L.Directory(self.a)
        entry = {"dn": ["CN=moved"], "userprincipalname": ["changed@example.ru"],
                 "mail": ["dead@example.ru"], "proxyaddresses": ["SMTP:alias@example.ru", "X500:ignored"]}
        with patch.object(d, "query", return_value=[entry]):
            self.assertEqual(d.broad(), {"changed@example.ru", "dead@example.ru", "alias@example.ru"})

    def test_service_upn_is_ignored_without_loosening_path_validation(self):
        entries = L.ldif("dn: CN=HTTP\nuserPrincipalName: http/internet-term.example.org@example.org\nuserAccountControl: 512\n\n")
        self.assertEqual(L.ad_table(entries, "userPrincipalName", "extensionAttribute6"), [])
        with self.assertRaises(L.SafetyError):
            L.email_value("http/internet-term.example.org@example.org")

    def test_mail_mapping_uses_email_instead_of_service_upn(self):
        entries = L.ldif("dn: CN=user\nuserPrincipalName: http/service@example.ru\nmail: dead@example.ru\nuserAccountControl: 514\nextensionAttribute6: 0\n\n")
        self.assertEqual(L.ad_table(entries, "mail", "extensionAttribute6", True),
                         [("dead@example.ru", "1", "0", "mail")])

    def test_valid_upn_different_from_mail_is_not_normal_key_in_mail_mode(self):
        entries = L.ldif("dn: CN=user\nuserPrincipalName: account.login@example.ru\nmail: real.address@example.ru\nuserAccountControl: 512\n\n")
        rows = L.ad_table(entries, "mail", "extensionAttribute6")
        self.assertEqual([r[0] for r in rows], ["real.address@example.ru"])

    def test_smtp_alias_dedup_within_one_account_and_source_trace(self):
        entries = L.ldif("dn: CN=user\nmail: dead@example.ru\nproxyAddresses: SMTP:dead@example.ru\nproxyAddresses: smtp:alias@example.ru\nproxyAddresses: X500:ignored\nuserAccountControl: 512\n\n")
        rows = L.ad_table(entries, "mail", "extensionAttribute6", True, include_smtp=True)
        self.assertEqual(rows[0], ("dead@example.ru", "0", "", "mail,proxyAddresses"))
        self.assertEqual(rows[1], ("alias@example.ru", "alias-only", "", "proxyAddresses"))

    def test_shared_smtp_alias_between_accounts_is_protected(self):
        entries = L.ldif("dn: CN=x\nproxyAddresses: SMTP:shared@example.ru\n\ndn: CN=y\nmail: shared@example.ru\n\n")
        self.assertEqual(L.ad_table(entries, "mail", "extensionAttribute6", include_smtp=True),
                         [("shared@example.ru", "ambiguous", "")])

    def test_all_terminated_duplicates_and_owner_order_are_protected(self):
        entries = L.ldif("dn: CN=a\nuserPrincipalName: dead@example.ru\nuserAccountControl: 514\nextensionAttribute6: 0\n\n"
                        "dn: CN=b\nuserPrincipalName: dead@example.ru\nuserAccountControl: 514\nextensionAttribute6: 0\n\n")
        for ordered in (entries, entries[::-1]):
            conflicts = []
            self.assertEqual(L.ad_table(ordered, "userPrincipalName", "extensionAttribute6", conflicts=conflicts),
                             [("dead@example.ru", "ambiguous", "")])
            self.assertEqual({c["dn"] for c in conflicts}, {"CN=a", "CN=b"})
            self.assertEqual({c["match_count"] for c in conflicts}, {2})

    def test_sogo_upn_ignores_differing_mail_and_shared_smtp_by_default(self):
        entries = L.ldif("dn: CN=a\nuserPrincipalName: dead@example.ru\nmail: old@example.ru\nproxyAddresses: SMTP:portal@example.ru\nuserAccountControl: 514\nextensionAttribute6: 0\n\n"
                        "dn: CN=b\nuserPrincipalName: other@example.ru\nmail: portal@example.ru\nproxyAddresses: smtp:portal@example.ru\nuserAccountControl: 512\n\n")
        self.assertEqual(L.ad_table(entries, "userPrincipalName", "extensionAttribute6"),
                         [("dead@example.ru", "1", "0"), ("other@example.ru", "0", "")])
        rows = L.ad_table(entries, "userPrincipalName", "extensionAttribute6", include_mail=True, include_smtp=True)
        self.assertIn(("portal@example.ru", "ambiguous", ""), rows)
        self.assertIn(("old@example.ru", "alias-only", ""), rows)

    def test_legacy_primary_duplicates_are_present_not_missing(self):
        entries = L.ldif("dn: CN=a\nuserPrincipalName: dead@example.ru\n\ndn: CN=b\nuserPrincipalName: dead@example.ru\n\n")
        d = L.Directory(self.a)
        with patch.object(d, "query", return_value=entries):
            self.assertEqual(d.primary(), {"dead@example.ru"})

    def test_duplicate_and_unique_addresses_are_independent(self):
        entries = L.ldif("dn: CN=a\nmail: duplicate@example.ru\n\ndn: CN=b\nmail: duplicate@example.ru\n\n"
                        "dn: CN=c\nmail: unique@example.ru\nuserAccountControl: 514\nextensionAttribute6: 0\n\n")
        self.assertEqual(L.ad_table(entries, "mail", "extensionAttribute6"),
                         [("duplicate@example.ru", "ambiguous", ""), ("unique@example.ru", "1", "0")])

    def test_primary_guard_counts_objects_not_mail_identifiers(self):
        self.a.min_users = 2
        entries = [{"dn": ["CN=a"], "mail": ["dead@example.ru"], "proxyaddresses": ["SMTP:alias@example.ru"]}]
        d = L.Directory(self.a)
        with patch.object(d, "query", return_value=entries), self.assertRaises(L.SafetyError):
            d.primary()

    def test_legacy_upn_presence_protects_even_when_mail_is_empty(self):
        self.a.address = "mail"
        d = L.Directory(self.a)
        with patch.object(d, "query", return_value=[{"dn": ["CN=a"], "userprincipalname": ["dead@example.ru"]}]):
            self.assertIn("dead@example.ru", d.broad())
        with patch.object(d, "query", return_value=[]) as query:
            d.exists("dead@example.ru")
        self.assertIn("(userPrincipalName=dead@example.ru)", query.call_args.args[1])

    def test_ldap_command_uses_critical_paging_domain_scope_and_subtree(self):
        self.a.ldap_extra = ["-ZZ"]
        with patch.object(L, "run_checked", return_value="") as run:
            L.Directory(self.a).query(self.a.search_base, "(mail=dead@example.ru)", ["dn"])
        cmd = run.call_args.args[0]
        self.assertIn("!1.2.840.113556.1.4.1339", cmd)
        self.assertIn("!pr=1000/noprompt", cmd)
        self.assertEqual(cmd[cmd.index("-s")+1], "sub")
        self.assertEqual(cmd[cmd.index("-b")+1], "DC=example,DC=ru")
        self.assertIn("-ZZ", cmd)

    def test_cyrillic_upn_from_server_error_is_valid(self):
        self.assertEqual(L.email_value("администратор@example.org"), "администратор@example.org")
        self.assertEqual(L.email_value("АДМИНИСТРАТОР@EXAMPLE.ORG"), "администратор@example.org")

    def test_cyrillic_base64_folded_ldif(self):
        enc = base64.b64encode("Администратор@example.org".encode("utf-8")).decode()
        text = "dn: CN=admin\nuserPrincipalName:: " + enc[:30] + "\n " + enc[30:] + "\nuserAccountControl: 512\nextensionAttribute6: 1\n\n"
        self.assertEqual(L.ad_table(L.ldif(text), "userPrincipalName", "extensionAttribute6"),
                         [("администратор@example.org", "0", "1")])

    def test_unicode_keeps_path_and_shell_injection_guards(self):
        for email in ("../админ@example.ru", "админ/other@example.ru", "админ\\other@example.ru",
                      "админ|0@example.ru", "админ\n@example.ru", "админ\x00@example.ru",
                      "$(touchX)@example.ru", "админ`x`@example.ru", "админ[1]@example.ru",
                      "админ\u202e@example.ru", "админ@../example.ru", "админ@@example.ru"):
            with self.subTest(email=email), self.assertRaises(L.SafetyError):
                L.email_value(email)

    def test_unicode_lower_does_not_transliterate_or_merge_names(self):
        self.assertNotEqual(L.email_value("администратор@example.ru"), L.email_value("administrator@example.ru"))
        self.assertNotEqual(L.email_value("Straße@example.ru"), L.email_value("strasse@example.ru"))
        self.assertNotEqual(L.email_value("é@example.ru"), L.email_value("e\u0301@example.ru"))

    def test_unicode_missing_inventory_and_approval(self):
        p = self.box("старый.ящик")
        rows = self.inventory()
        self.assertEqual(rows["старый.ящик@example.ru"]["mailbox_path"], str(p))
        self.assertEqual(rows["старый.ящик@example.ru"]["schema_version"], "1.1")
        self.approve("email,approved\nСТАРЫЙ.ЯЩИК@example.ru,YES\n", ".csv")
        L.archive(self.a, FakeAD())
        self.assertTrue(p.exists())

    def test_unicode_alias_in_broad_ad_protects(self):
        self.box("администратор")
        rows = self.inventory(FakeAD(broad=["администратор@example.ru"]))
        self.assertEqual(rows["администратор@example.ru"]["eligible"], "no")

    def test_unicode_live_archive_and_marker(self):
        p = self.box("старый.ящик")
        self.inventory()
        self.approve("старый.ящик@example.ru\n")
        self.a.dry_run, self.a.writers_quiesced = 0, True
        with patch.object(L, "run_checked", side_effect=self.copy_stub):
            L.archive(self.a, FakeAD())
        self.assertFalse(p.exists())
        marker = next(self.arc.glob("*/*/*/" + L.MARKER))
        self.assertEqual(json.loads(marker.read_text(encoding="utf-8"))["email"], "старый.ящик@example.ru")


if __name__ == "__main__":
    unittest.main(verbosity=2)
