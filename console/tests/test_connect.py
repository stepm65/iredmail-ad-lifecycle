# Copyright (c) 2026, rslpro
# SPDX-License-Identifier: AGPL-3.0-only
"""Real config fixtures / atomic writes; native web server commands are mocked."""
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch, Mock

spec = importlib.util.spec_from_file_location("connector", Path(__file__).resolve().parents[1] / "connect-web.py")
m = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = m
spec.loader.exec_module(m)


class ConnectTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name).resolve()
        self.host = "mail.example.org"
        self.machine = "mailhost.example.org"

    def file(self, name, body):
        p = self.base / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body, encoding="utf-8", newline="\n")
        return p

    def nginx(self, body):
        p = self.file("site.conf", body)
        return p, m.parse_nginx(body, p)

    def select(self, nodes, **kw):
        return m.choose_nginx(nodes, self.host, 443, **kw)

    def apache(self, body):
        p = self.file("site.conf", body)
        return p, m.apache_servers("Included configuration files:\n  (1) " + p.as_posix())

    def test_url_normalizes_prefix_but_keeps_public_cname(self):
        self.assertEqual(m.public_url("https://MAIL.example.org/lifecycle/"), (self.host, 443, "/lifecycle"))

    def test_reject_urls_that_can_inject_config(self):
        for url in ("http://mail.ru/a/", "https://mail.ru/", "https://mail.ru/a;b", "https://x/a?b", "https://u:p@x/a", "https://x:bad/a", "https://x/a%0afoo"):
            with self.subTest(url=url), self.assertRaises(m.ConnectError):
                m.public_url(url)

    def test_exact_https_wins_over_default_and_http(self):
        _, nodes = self.nginx('server {listen 80; server_name mail.example.org;}\nserver {listen 443 ssl default_server; server_name _;}\nserver {listen 443 ssl; server_name mail.example.org;}')
        self.assertIs(self.select(nodes), nodes[2])

    def test_cname_target_named_site_wins_over_unrelated_default(self):
        _, nodes = self.nginx('server {listen 80; server_name mail.example.org;}\nserver {listen 443 ssl default_server; server_name unrelated.ru;}\nserver {listen 443 ssl; server_name mailhost.example.org;}')
        self.assertIs(m.select_site("nginx", nodes, self.host, 443, self.machine), nodes[2])

    def test_existing_public_site_wins_over_machine_site(self):
        _, nodes = self.nginx('server {listen 443 ssl; server_name mailhost.example.org;}\nserver {listen 443 ssl; server_name mail.example.org;}')
        self.assertIs(m.select_site("nginx", nodes, self.host, 443, self.machine), nodes[1])

    def test_single_iredmail_catchall(self):
        _, nodes = self.nginx('server { listen 443 ssl; server_name _; include templates/sogo.tmpl; }')
        self.assertIs(self.select(nodes), nodes[0])

    def test_wildcard_priority(self):
        _, nodes = self.nginx('server {listen 443 ssl; server_name *.ru;} server {listen 443 ssl; server_name *.example.org;}')
        self.assertIs(self.select(nodes), nodes[1])

    def test_ambiguous_sites_refused_noninteractive(self):
        _, nodes = self.nginx('server {listen 443 ssl; server_name one.ru;} server {listen 443 ssl; server_name two.ru;}')
        with patch.object(m.sys.stdin, "isatty", return_value=False), self.assertRaises(m.ConnectError):
            m.select_site("nginx", nodes, self.host, 443, self.machine)

    def test_ambiguous_interactive_site_menu(self):
        _, nodes = self.nginx('server {listen 443 ssl; server_name one.ru;}\nserver {listen 443 ssl; server_name two.ru;}')
        with patch.object(m.sys.stdin, "isatty", return_value=True), patch("builtins.input", return_value="2"):
            self.assertIs(m.select_site("nginx", nodes, self.host, 443, self.machine), nodes[1])

    def test_regex_requires_explicit_file(self):
        p, nodes = self.nginx('server {listen 443 ssl; server_name ~^mail.*;}')
        with self.assertRaises(m.ConnectError):
            self.select(nodes)
        self.assertIs(self.select(nodes, vhost=p), nodes[0])

    def test_redirecting_server_refused(self):
        body = 'server { listen 443 ssl; return 301 https://elsewhere.ru; }'
        _, nodes = self.nginx(body)
        with self.assertRaises(m.ConnectError):
            m.prepare_site(self.select(nodes), body, 'nginx', self.host, '/lifecycle')

    def test_iredmail_root_redirect_preserved_console_route_added(self):
        body = 'server {listen 443 ssl; server_name _; rewrite ^/$ /mail/ redirect;}'
        _, nodes = self.nginx(body)
        after = m.prepare_site(self.select(nodes), body, 'nginx', self.host, '/lifecycle')
        self.assertIn('rewrite ^/$ /mail/ redirect;', after)
        self.assertIn('location ^~ /lifecycle/', after)

    def test_other_app_rewrites_proven_disjoint(self):
        for pattern in ('^/iredadmin$', '^/iredadmin/?$', '^/mail/.*$', '^/SOGo(.*)', '^/mail/'):
            with self.subTest(pattern=pattern):
                self.assertTrue(m.rewrite_excludes_prefix(pattern, '/lifecycle'))
        self.assertFalse(m.rewrite_excludes_prefix('^/mail/.*$', '/mail/lifecycle'))

    def test_catchall_and_pcre_variants_refused(self):
        for pattern in ('^', '^/(.*)', '^/lifecycle', '^/lifecycle/.*', '^/life', '^/mail|/lifecycle', '^/lifecycleX?$', '^/lifecycleX{0,1}$', '(?i)^/LIFECYCLE', '^/other(?R)'):
            with self.subTest(pattern=pattern):
                self.assertFalse(m.rewrite_excludes_prefix(pattern, '/lifecycle'))

    def test_rule_error_identifies_source_file_and_line(self):
        body = 'server {\nlisten 443 ssl;\nrewrite ^ /mail/ last;\n}'
        p, nodes = self.nginx(body)
        with self.assertRaises(m.ConnectError) as error:
            m.prepare_site(self.select(nodes), body, 'nginx', self.host, '/lifecycle')
        self.assertIn(str(p) + ':3', str(error.exception))

    def test_location_returns_do_not_block_console(self):
        body = 'server {listen 443 ssl; location /mail {return 301 /SOGo;}}'
        _, nodes = self.nginx(body)
        after = m.prepare_site(self.select(nodes), body, 'nginx', self.host, '/lifecycle')
        self.assertIn(m.BEGIN, after)
        self.assertIn('location /mail {return 301 /SOGo;}', after)

    def test_included_root_rewrite_preserved_with_source_file(self):
        rules = self.file('redirect.tmpl', 'rewrite ^/$ /mail/ redirect;')
        site = self.file('site.conf', 'server {listen 443 ssl; include "' + rules.as_posix() + '";}')
        main = self.file('nginx.conf', 'http {include "' + site.as_posix() + '";}')
        dump = ''.join('# configuration file ' + p.as_posix() + ':\n' for p in (main, site, rules))
        nodes = m.nginx_servers(dump)
        after = m.prepare_site(self.select(nodes), site.read_text(), 'nginx', self.host, '/lifecycle')
        self.assertIn(rules.as_posix(), after)
        self.assertIn(m.BEGIN, after)
        self.assertEqual(rules.read_text(), 'rewrite ^/$ /mail/ redirect;')

    def test_server_if_return_requires_review(self):
        body = 'server {listen 443 ssl; if ($request_method = POST) {return 403;}}'
        _, nodes = self.nginx(body)
        with self.assertRaises(m.ConnectError):
            m.prepare_site(self.select(nodes), body, 'nginx', self.host, '/lifecycle')

    def test_reported_sogo_rules_preserved_and_console_attached(self):
        sogo = self.file('templates/sogo.tmpl', '''rewrite ^/.well-known/caldav /SOGo/dav permanent;
rewrite ^/.well-known/carddav /SOGo/dav permanent;
rewrite ^/principals /SOGo/dav permanent;
location = /SOGo { rewrite ^ /SOGo/; }
location /SOGo/ { proxy_pass http://127.0.0.1:20000; }
''')
        roundcube = self.file('templates/roundcube.tmpl', 'location = /mail { return 301 /mail/; }')
        site = self.file('00-default-ssl.conf', 'server {listen 443 ssl http2; listen [::]:443 ssl http2; server_name _; include "' + sogo.as_posix() + '"; include "' + roundcube.as_posix() + '";}')
        http = self.file('00-default.conf', 'server {listen 80; listen [::]:80; server_name _; return 301 https://$host$request_uri;}')
        main = self.file('nginx.conf', 'http {include "' + site.as_posix() + '"; include "' + http.as_posix() + '";}')
        saved = {p: p.read_bytes() for p in (sogo, roundcube, http, main)}
        dump = ''.join('# configuration file ' + p.as_posix() + ':\n' for p in (main, site, sogo, roundcube, http))
        node = self.select(m.nginx_servers(dump))
        after = m.prepare_site(node, site.read_text(), 'nginx', self.host, '/lifecycle')
        self.assertIn('location ^~ /lifecycle/', after)
        self.assertIn('listen [::]:443 ssl http2;', after)
        self.assertIn('server_name _ mail.example.org;', after)
        for p, before in saved.items():
            self.assertEqual(p.read_bytes(), before)

    def test_pcre_dot_is_wildcard_not_literal(self):
        self.assertFalse(m.rewrite_excludes_prefix('^/.ifecycle', '/lifecycle'))
        self.assertFalse(m.rewrite_excludes_prefix('^/life.ycle', '/lifecycle'))
        self.assertFalse(m.rewrite_excludes_prefix('^/.*', '/lifecycle'))
        self.assertFalse(m.rewrite_excludes_prefix('^/.well-known/caldav', '/xwell-known/caldav'))
        self.assertTrue(m.rewrite_excludes_prefix('^/.well-known/caldav', '/lifecycle'))

    def test_nginx_pcre_escapes_preserved_and_not_mistaken_for_literals(self):
        body = r'server {listen 443 ssl; rewrite ^/\Difecycle /mail/ last;}'
        _, nodes = self.nginx(body)
        self.assertEqual(nodes[0].children[1].words[1], r'^/\Difecycle')
        with self.assertRaises(m.ConnectError):
            m.prepare_site(self.select(nodes), body, 'nginx', self.host, '/lifecycle')
        self.assertTrue(m.rewrite_excludes_prefix(r'^/\.well-known/caldav', '/lifecycle'))

    def test_included_broad_rewrite_still_refused_with_template_location(self):
        rules = self.file('templates/redirect.tmpl', 'rewrite ^ /mail/ last;')
        site = self.file('site.conf', 'server {listen 443 ssl; include "' + rules.as_posix() + '";}')
        main = self.file('nginx.conf', 'http {include "' + site.as_posix() + '";}')
        dump = ''.join('# configuration file ' + p.as_posix() + ':\n' for p in (main, site, rules))
        with self.assertRaises(m.ConnectError) as error:
            m.prepare_site(self.select(m.nginx_servers(dump)), site.read_text(), 'nginx', self.host, '/lifecycle')
        self.assertIn(str(rules) + ':1', str(error.exception))

    def test_loaded_http_includes_not_stream_or_disabled_files(self):
        main = self.file("nginx.conf", 'stream {server {listen 443 ssl;}} http {include "' + self.base.as_posix() + '/enabled/*.conf";}')
        site = self.file("enabled/tls.conf", 'server {listen 443 ssl; server_name _; include "' + self.base.as_posix() + '/sogo.tmpl";}')
        sogo = self.file("sogo.tmpl", 'location /SOGo { proxy_pass http://127.0.0.1:20000; }')
        self.file("enabled/disabled.conf", 'server {listen 443 ssl; server_name mail.example.org;}')
        dump = ''.join('# configuration file ' + p.as_posix() + ':\n' for p in (main, site, sogo))
        nodes = m.nginx_servers(dump)
        self.assertEqual(len(nodes), 1)
        selected = self.select(nodes)
        result = m.prepare_site(selected, site.read_text(), "nginx", self.host, "/lifecycle")
        self.assertIn(sogo.as_posix(), result)
        self.assertEqual(sogo.read_text(), 'location /SOGo { proxy_pass http://127.0.0.1:20000; }')
        self.assertIn('server_name _ mail.example.org;', result)

    def test_comments_quoted_braces_and_variables(self):
        body = 'server # { fake brace\n { listen 443 ssl; set $x "${test} { привет }"; server_name _; }'
        p, nodes = self.nginx(body)
        after = m.prepare_site(self.select(nodes), body, "nginx", self.host, "/lifecycle")
        self.assertTrue(after.startswith('server # { fake brace\n {\n'))
        self.assertIn('${test} { привет }', after)
        m.parse_nginx(after, p)

    def test_nginx_patch_preserves_other_sites_and_is_idempotent(self):
        other = 'server {listen 80; return 301 https://mail.example.org$request_uri;}\n'
        body = other + 'server {listen 443 ssl; server_name mailhost.example.org; location /SOGo { proxy_pass http://127.0.0.1:20000; }}'
        p, nodes = self.nginx(body)
        after = m.prepare_site(self.select(nodes), body, "nginx", self.host, "/lifecycle")
        self.assertTrue(after.startswith(other))
        self.assertIn('location /SOGo { proxy_pass http://127.0.0.1:20000; }', after)
        p.write_text(after)
        again = m.prepare_site(self.select(m.parse_nginx(after, p)), after, "nginx", self.host, "/lifecycle")
        self.assertEqual(after, again)
        self.assertEqual(after.count(m.BEGIN), 1)

    def test_preexisting_conflicting_route_refused(self):
        body = 'server {listen 443 ssl; location /lifecycle/ {root /var/www;}}'
        _, nodes = self.nginx(body)
        with self.assertRaises(m.ConnectError):
            m.prepare_site(self.select(nodes), body, "nginx", self.host, "/lifecycle")

    def test_old_manual_nginx_route_reused(self):
        body = 'server {listen 443 ssl; server_name mail.example.org;' + m.snippet("nginx", "/lifecycle").replace(m.BEGIN, "# old manual").replace(m.END, "# old end") + '}'
        _, nodes = self.nginx(body)
        self.assertEqual(body, m.prepare_site(self.select(nodes), body, "nginx", self.host, "/lifecycle"))

    def test_malformed_managed_marker_refused(self):
        body = 'server {listen 443 ssl;\n' + m.BEGIN + '\n}'
        _, nodes = self.nginx(body)
        with self.assertRaises(m.ConnectError):
            m.patch_site(self.select(nodes), body, "nginx", "/lifecycle")

    def test_apache_alias_inserted_before_catchall_proxy(self):
        body = '<VirtualHost *:80>\n ServerName mail.example.org\n</VirtualHost>\n<VirtualHost *:443>\n ServerName mailhost.example.org\n SSLEngine on\n ProxyPass / http://elsewhere/\n</VirtualHost>\n<VirtualHost *:8443>\n ServerAlias mail.example.org\n</VirtualHost>'
        p, nodes = self.apache(body)
        node = m.choose_apache(nodes, self.host, 443)
        after = m.prepare_site(node, body, "apache2", self.host, "/lifecycle")
        self.assertLess(after.index('ProxyPass /lifecycle/'), after.index('ProxyPass / http'))
        self.assertEqual(after.count('ServerAlias mail.example.org'), 2)
        self.assertIn('ProxyAddHeaders Off', after)
        p.write_text(after)
        nodes = m.apache_servers(' (1) ' + p.as_posix())
        self.assertEqual(after, m.prepare_site(m.choose_apache(nodes, self.host, 443), after, "apache2", self.host, "/lifecycle"))

    def test_apache_only_native_active_blocks_are_candidates(self):
        p, nodes = self.apache('<VirtualHost *:443>\n ServerName disabled.ru\n</VirtualHost>\n<VirtualHost *:443>\n ServerName mail.example.org\n</VirtualHost>')
        active = m.active_apache(nodes, 'port 443 namevhost mail.example.org (' + p.as_posix() + ':4)')
        self.assertEqual(len(active), 1)
        self.assertIs(active[0][0], nodes[1][0])

    def test_apache_unknown_active_dump_refused(self):
        _, nodes = self.apache('<VirtualHost *:443>\n ServerName mail.example.org\n</VirtualHost>')
        with self.assertRaises(m.ConnectError):
            m.active_apache(nodes, 'unrecognized output')

    def test_apply_native_validation_failure_restores_and_keeps_backup(self):
        p = self.file('site.conf', 'original')
        validation = Mock(side_effect=[m.ConnectError('syntax bad'), None])
        reload = Mock()
        with self.assertRaises(m.ConnectError):
            m.apply_site(p, b'original', b'changed', self.base/'backups', validation, reload, protect=False)
        self.assertEqual(p.read_bytes(), b'original')
        backup = list((self.base/'backups').glob('*.bak'))
        self.assertEqual(len(backup), 1)
        self.assertEqual(backup[0].read_bytes(), b'original')
        self.assertEqual(reload.call_count, 1)

    def test_reload_failure_rolls_back_and_reloads_original(self):
        p = self.file('site.conf', 'original')
        reload = Mock(side_effect=[m.ConnectError('reload bad'), None])
        with self.assertRaises(m.ConnectError):
            m.apply_site(p, b'original', b'changed', self.base/'backups', Mock(), reload, protect=False)
        self.assertEqual(p.read_bytes(), b'original')
        self.assertEqual(reload.call_count, 2)

    def test_concurrent_admin_edit_preserved(self):
        p = self.file('site.conf', 'original')
        def validation():
            p.write_bytes(b'admin edit')
            raise m.ConnectError('syntax bad')
        with self.assertRaises(m.ConnectError):
            m.apply_site(p, b'original', b'changed', self.base/'backups', validation, Mock(), protect=False)
        self.assertEqual(p.read_bytes(), b'admin edit')

    def test_idempotent_apply_does_not_create_backup(self):
        p = self.file('site.conf', 'original')
        validate, reload = Mock(), Mock()
        self.assertIsNone(m.apply_site(p, b'original', b'original', self.base/'backups', validate, reload, protect=False))
        self.assertFalse((self.base/'backups').exists())
        validate.assert_called_once()
        reload.assert_called_once()

    def test_backend_probe_uses_public_host_and_overwrites_forwarding(self):
        conn = Mock()
        response = conn.getresponse.return_value
        response.status = 200
        response.read.return_value = b'IREDMAIL LIFECYCLE'
        with patch.object(m.http.client, 'HTTPConnection', return_value=conn):
            m.backend_probe(self.host, '/lifecycle')
        headers = conn.request.call_args.kwargs['headers']
        self.assertEqual(headers['Host'], self.host)
        self.assertEqual(headers['X-Forwarded-Prefix'], '/lifecycle')
        conn.close.assert_called_once()

    def test_backend_failure_bounded_no_route_mutation(self):
        conn = Mock()
        conn.request.side_effect = OSError('refused')
        with patch.object(m.http.client, 'HTTPConnection', return_value=conn), patch.object(m.time, 'monotonic', side_effect=[0, 0, 21]), patch.object(m.time, 'sleep'), self.assertRaises(m.ConnectError):
            m.backend_probe(self.host, '/lifecycle')
        conn.close.assert_called_once()

    def main_context(self, check=False):
        from contextlib import ExitStack
        stack = ExitStack()
        self.addCleanup(stack.close)
        p = self.file('site.conf', 'server {listen 443 ssl; server_name mailhost.example.org;}')
        main = self.file('nginx.conf', 'http {include "' + p.as_posix() + '";}')
        settings = self.file('settings.json', json.dumps(dict(public_url='https://mail.example.org/lifecycle/', username='admin', password_hash='unchanged', secret='unchanged', allow_live=False)))
        dump = ''.join('# configuration file ' + v.as_posix() + ':\n' for v in (main, p))
        args = ['connect-web.py', '--url', 'https://mail.example.org/lifecycle/', '--server-hostname', self.machine, '--server', 'nginx']
        if check:
            args += ['--check']
        stack.enter_context(patch.object(m.sys, 'argv', args))
        stack.enter_context(patch.object(m, 'SETTINGS_PATH', settings))
        stack.enter_context(patch.object(m, 'LOCK_PATH', self.base/'lock'))
        stack.enter_context(patch.object(m, 'BACKUP_PATH', self.base/'backups'))
        stack.enter_context(patch.object(m.os, 'geteuid', return_value=0, create=True))
        stack.enter_context(patch.object(m, 'secure_file'))
        stack.enter_context(patch.dict(sys.modules, {'fcntl': Mock(LOCK_EX=2, LOCK_NB=4)}))
        stack.enter_context(patch.object(m.shutil, 'which', return_value=None))
        checked = stack.enter_context(patch.object(m, 'checked', side_effect=lambda cmd: dump if cmd == ['nginx', '-T'] else ''))
        probe = stack.enter_context(patch.object(m, 'backend_probe'))
        return p, settings, checked, probe

    def test_main_check_is_readonly_and_does_not_start_services(self):
        p, settings, checked, probe = self.main_context(check=True)
        original, saved = p.read_bytes(), settings.read_bytes()
        self.assertEqual(m.main(), 0)
        self.assertEqual(p.read_bytes(), original)
        self.assertEqual(settings.read_bytes(), saved)
        checked.assert_called_once_with(['nginx', '-T'])
        probe.assert_not_called()

    def test_main_repair_preserves_credentials_and_validates_before_reload(self):
        p, settings, checked, probe = self.main_context()
        self.assertEqual(m.main(), 0)
        c = json.loads(settings.read_text())
        self.assertEqual(c['password_hash'], 'unchanged')
        self.assertEqual(c['secret'], 'unchanged')
        self.assertFalse(c['allow_live'])
        self.assertEqual(c['server_hostname'], self.machine)
        self.assertIn(m.BEGIN, p.read_text())
        self.assertIn('server_name mailhost.example.org mail.example.org;', p.read_text())
        calls = [call.args[0] for call in checked.call_args_list]
        self.assertLess(calls.index(['nginx', '-t']), calls.index(['systemctl', 'reload', 'nginx']))
        self.assertFalse(any('restart' in call for call in calls))
        probe.assert_called_once_with(self.host, '/lifecycle')


if __name__ == '__main__':
    unittest.main(verbosity=2)
