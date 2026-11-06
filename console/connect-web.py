#!/usr/bin/env python3
# Copyright (c) 2026, rslpro
# SPDX-License-Identifier: AGPL-3.0-only
"""Attach to a loaded HTTPS virtual host; stdlib only, no AD/mail changes."""
import argparse
from dataclasses import dataclass, field
import fnmatch
import glob
import http.client
import json
import os
from pathlib import Path
import re
import shutil
import socket
import stat
import subprocess
import sys
import tempfile
import time
from urllib.parse import urlsplit
import uuid

BEGIN = "# BEGIN IREDMAIL LIFECYCLE WEB MANAGED"
END = "# END IREDMAIL LIFECYCLE WEB MANAGED"
SETTINGS_PATH = Path("/etc/iredmail-lifecycle-web/settings.json")
LOCK_PATH = Path("/run/iredmail-lifecycle-connect.lock")
BACKUP_PATH = Path("/var/backups/iredmail-lifecycle-web")


class ConnectError(Exception):
    pass


def checked(cmd):
    p = subprocess.run(cmd, capture_output=True, encoding="utf-8", errors="replace", timeout=40)
    if p.returncode:
        raise ConnectError("Команда завершилась с ошибкой: " + " ".join(cmd) + "\n" + p.stderr[-4000:])
    return p.stdout + p.stderr


def secure_file(path):
    for part in [Path(path)] + list(Path(path).parents):
        info = part.lstat()
        if stat.S_ISLNK(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
            raise ConnectError("Требуется root-owned путь без записи группы/остальных: " + str(part))


def public_url(value):
    u = urlsplit(value)
    try:
        port = u.port or 443
    except ValueError as exc:
        raise ConnectError("Неверный HTTPS-порт") from exc
    if (u.scheme != "https" or not u.hostname or u.username or u.query or u.fragment
            or not re.fullmatch(r"[a-zA-Z0-9.-]+", u.hostname)
            or not re.fullmatch(r"(?:/[a-zA-Z0-9_-]+)+/?", u.path)):
        raise ConnectError("Нужен HTTPS URL с отдельным путём, например https://mail.example.org/lifecycle/")
    return u.hostname.lower(), port, u.path.rstrip("/")


@dataclass
class Node:
    words: list
    path: Path
    start: int
    close: int
    end: int
    children: list = field(default_factory=list)
    opening: int = 0


def nginx_tokens(text):
    # Preserve byte-independent character offsets; ignore quoted/comment braces.
    tokens, i = [], 0
    while i < len(text):
        if text[i].isspace():
            i += 1
            continue
        if text[i] == "#":
            end = text.find("\n", i)
            i = len(text) if end < 0 else end + 1
            continue
        start = i
        if text[i] in "{};":
            tokens.append((text[i], i, i + 1))
            i += 1
            continue
        value, quote = "", None
        while i < len(text):
            char = text[i]
            if char == "\\" and i + 1 < len(text):
                following = text[i + 1]
                # Match Nginx's config unescaping: keep PCRE escapes such as
                # \D and \.; stripping these can make a broad regex look safe.
                value += ({"t": "\t", "r": "\r", "n": "\n"}.get(following, following)
                          if following in "\"'\\trn" else "\\" + following)
                i += 2
                continue
            if quote:
                if char == quote:
                    quote = None
                else:
                    value += char
                i += 1
                continue
            if char in "\"'":
                quote = char
                i += 1
                continue
            if char == "$" and i + 1 < len(text) and text[i + 1] == "{":
                end = text.find("}", i + 2)
                if end < 0:
                    raise ConnectError("Незакрытая переменная Nginx")
                value += text[i:end + 1]
                i = end + 1
                continue
            if char.isspace() or char in "{};#":
                break
            value += char
            i += 1
        if quote:
            raise ConnectError("Незакрытая строка Nginx")
        tokens.append((value, start, i))
    return tokens


def parse_nginx(text, path):
    tokens = nginx_tokens(text)
    def parse(index, nested=False):
        nodes = []
        while index < len(tokens):
            if tokens[index][0] == "}":
                if not nested:
                    raise ConnectError("Лишняя закрывающая скобка Nginx")
                return nodes, index + 1
            start, words = tokens[index][1], []
            while index < len(tokens) and tokens[index][0] not in ("{", "}", ";"):
                words.append(tokens[index][0])
                index += 1
            if not words or index >= len(tokens) or tokens[index][0] == "}":
                raise ConnectError("Неполная директива Nginx")
            if tokens[index][0] == ";":
                nodes.append(Node(words, path, start, tokens[index][1], tokens[index][2]))
                index += 1
            else:
                opening = tokens[index][2]
                children, following = parse(index + 1, True)
                closing = tokens[following - 1]
                nodes.append(Node(words, path, start, closing[1], closing[2], children, opening))
                index = following
        if nested:
            raise ConnectError("Незакрытый блок Nginx")
        return nodes, index
    return parse(0)[0]


def nginx_servers(dump):
    raw_paths = re.findall(r"^# configuration file (.+):$", dump, re.M)
    if not raw_paths:
        raise ConnectError("nginx -T не перечислил активные файлы")
    loaded = {Path(p).as_posix(): Path(p).resolve() for p in raw_paths}
    main = Path(raw_paths[0])
    cache = {}
    def expand(path, stack=()):
        path = Path(path).resolve()
        if path in stack or len(stack) > 30:
            raise ConnectError("Циклический/слишком глубокий include")
        if path not in cache:
            cache[path] = parse_nginx(path.read_text(encoding="utf-8"), path)
        def replace(nodes):
            result = []
            for node in nodes:
                if node.words[0] == "include" and len(node.words) == 2:
                    pattern = node.words[1]
                    if not os.path.isabs(pattern):
                        pattern = str(main.parent / pattern)
                    pattern = Path(pattern).as_posix()
                    actual = {Path(p).as_posix() for p in glob.glob(pattern)}
                    matches = [real for raw, real in loaded.items() if raw in actual]
                    for match in matches:
                        result += expand(match, stack + (path,))
                else:
                    result.append(Node(node.words, node.path, node.start, node.close, node.end, replace(node.children), node.opening))
            return result
        return replace(cache[path])
    roots = expand(main)
    return [server for http in roots if http.words == ["http"]
            for server in http.children if server.words == ["server"]]


def port_matches(value, port):
    return value == str(port) or bool(re.search(r":%s$" % port, value))


def choose_nginx(servers, host, port, vhost=None, line=None):
    candidates = []
    for s in servers:
        listen = [n.words[1:] for n in s.children if n.words[0] == "listen"]
        if not any(v and port_matches(v[0], port) and "ssl" in v for v in listen):
            continue
        if vhost and s.path != Path(vhost).resolve():
            continue
        number = s.path.read_text(encoding="utf-8").count("\n", 0, s.start) + 1
        if line and number != line:
            continue
        names = [v.lower() for n in s.children if n.words[0] == "server_name" for v in n.words[1:]]
        exact = host in names
        wildcard = [n for n in names if n.startswith("*.") and host.endswith(n[1:]) or
                    n.startswith(".") and (host == n[1:] or host.endswith(n))]
        end_wildcard = [n for n in names if n.endswith(".*") and host.startswith(n[:-1])]
        default = any("default_server" in v or "default" in v for v in listen if v and port_matches(v[0], port))
        score = (4, len(host)) if exact else (3, max(map(len, wildcard))) if wildcard else (
                (2, max(map(len, end_wildcard))) if end_wildcard else (1, 0) if default else (0, 0))
        candidates.append((score, s, names, number))
    if not candidates:
        raise ConnectError("Не найден активный HTTPS server для порта %s" % port)
    if any(any(n.startswith("~") for n in c[2]) for c in candidates) and max(c[0] for c in candidates)[0] < 4 and not vhost:
        raise ConnectError("Есть regex server_name: укажите нужный --vhost и при необходимости --block-line")
    best = max(c[0] for c in candidates)
    matches = [c for c in candidates if c[0] == best]
    if len(matches) != 1:
        listing = "; ".join("%s:%s (%s)" % (c[1].path, c[3], ",".join(c[2])) for c in matches)
        raise ConnectError("Неоднозначный HTTPS virtual host; укажите --vhost/--block-line: " + listing)
    return matches[0][1]


def rewrite_excludes_prefix(pattern, prefix):
    # Prove disjointness for a deliberately small PCRE grammar. Do not use
    # Python re.match against sample URLs: PCRE and future URLs can differ.
    if pattern in ("^/$", "^/?$"):
        return True
    # Widen escaped literal dots to wildcard dots for a conservative proof.
    pattern = pattern.replace(r"\.", ".")
    matched = re.fullmatch(r"\^(/[a-zA-Z0-9_./-]+)(?:\$|\.\*\$?|\(\.\*\)\$?)?", pattern)
    if not matched:
        matched = re.fullmatch(r"\^(/[a-zA-Z0-9_./-]+)/\?\$", pattern)
    if not matched:
        return False
    literal = matched.group(1)
    # A bare '.' in PCRE consumes any one character. Treat it as a wildcard,
    # never as a literal: SOGo's ^/.well-known/... still has a fixed mismatch
    # with /lifecycle at the next character. Require a mismatch before either
    # prefix ends, conservatively protecting the bare URL and all descendants.
    return any(expected != "." and expected != actual for expected, actual in zip(literal, prefix))


def check_nginx_rules(server, prefix):
    # Server rewrite-phase directives run before location matching. Preserve
    # root/mail-app redirects that cannot touch the console; never delete or
    # bypass a genuinely broad redirect/security condition automatically.
    def phase(nodes):
        for n in nodes:
            if n.words[0] == "location":
                continue
            if n.words[0] in ("return", "rewrite"):
                yield n
            if n.words[0] == "if":
                yield from phase(n.children)
    for node in phase(server.children):
        if node.words[0] == "rewrite" and len(node.words) >= 3 and rewrite_excludes_prefix(node.words[1], prefix):
            continue
        number = node.path.read_text(encoding="utf-8").count("\n", 0, node.start) + 1
        detail = " ".join(node.words[:2])
        raise ConnectError("Правило HTTPS server может перехватить %s/: %s:%s (%s). "
                           "Нужен разбор этого правила; сайт не изменён" % (prefix, node.path, number, detail))


def apache_servers(dump):
    raw_paths = re.findall(r"^[ \t]*\([^)]*\)[ \t]+((?:/|[A-Za-z]:/).+?)[ \t]*$", dump, re.M)
    servers = []
    for raw in dict.fromkeys(raw_paths):
        path = Path(raw).resolve()
        text = path.read_text(encoding="utf-8")
        for match in re.finditer(r"^[ \t]*<VirtualHost\s+([^>]+)>.*?^[ \t]*</VirtualHost\s*>", text, re.M | re.S | re.I):
            opening = text.find(">", match.start()) + 1
            closing = text.lower().rfind("</virtualhost", match.start(), match.end())
            body = text[opening:closing]
            names = re.findall(r"^\s*Server(?:Name|Alias)\s+([^\n]+)", body, re.M | re.I)
            words = ["VirtualHost"] + match.group(1).split()
            servers.append((Node(words, path, match.start(), closing, match.end(), opening=opening), body,
                            [n.lower() for row in names for n in row.split()]))
    return servers


def choose_apache(servers, host, port, vhost=None, line=None):
    candidates = []
    for node, body, names in servers:
        if not any(port_matches(addr, port) for addr in node.words[1:]):
            continue
        if vhost and node.path != Path(vhost).resolve():
            continue
        number = node.path.read_text(encoding="utf-8").count("\n", 0, node.start) + 1
        if line and number != line:
            continue
        if host in names:
            rank = 2
        elif any(fnmatch.fnmatchcase(host, name) for name in names if "*" in name or "?" in name):
            rank = 1
        else:
            rank = 0
        candidates.append((rank, node, body))
    if not candidates:
        raise ConnectError("Не найден Apache VirtualHost на HTTPS-порту")
    rank = max(c[0] for c in candidates)
    best = [c for c in candidates if c[0] == rank]
    if len(best) != 1:
        raise ConnectError("Несколько Apache VirtualHost: укажите --vhost и при необходимости --block-line")
    return best[0][1]


def active_apache(servers, dump):
    loaded = {(Path(p).resolve(), int(line)) for p, line in re.findall(
              r"\(((?:/|[A-Za-z]:/)[^\n]+):(\d+)\)", dump)}
    active = [entry for entry in servers if (entry[0].path,
              entry[0].path.read_text(encoding="utf-8").count("\n", 0, entry[0].start) + 1) in loaded]
    if not active:
        raise ConnectError("apache2ctl -S не подтвердил активные VirtualHost; автоматическая вставка запрещена")
    return active


def snippet(server, prefix):
    if server == "nginx":
        return f'''    {BEGIN}
    location = {prefix} {{ return 301 {prefix}/; }}
    location ^~ {prefix}/ {{
        proxy_pass http://127.0.0.1:8127/;
        proxy_set_header Host $http_host;
        proxy_set_header X-Forwarded-For $remote_addr;
        proxy_set_header X-Forwarded-Proto https;
        proxy_set_header X-Forwarded-Prefix {prefix};
        proxy_set_header X-Forwarded-Host "";
        client_max_body_size 64k;
        proxy_read_timeout 60s;
    }}
    {END}
'''
    return f'''    {BEGIN}
    ProxyRequests Off
    RedirectMatch 301 ^{prefix}$ {prefix}/
    ProxyPass {prefix}/ http://127.0.0.1:8127/
    ProxyPassReverse {prefix}/ http://127.0.0.1:8127/
    <Location {prefix}/>
        ProxyPreserveHost On
        ProxyAddHeaders Off
        RequestHeader set X-Forwarded-Proto "https"
        RequestHeader set X-Forwarded-Prefix "{prefix}"
        RequestHeader set X-Forwarded-For "expr=%{{REMOTE_ADDR}}"
        RequestHeader unset X-Forwarded-Host
        LimitRequestBody 65536
    </Location>
    {END}
'''


def patch_site(node, original, server, prefix):
    segment = original[node.start:node.end]
    begin = segment.find(BEGIN)
    if begin >= 0:
        finish = segment.find(END, begin)
        if finish < 0 or segment.count(BEGIN) != 1:
            raise ConnectError("Повреждён managed-блок; автоматическая замена запрещена")
        left = node.start + begin
        left = original.rfind("\n", node.start, left) + 1
        right = node.start + finish + len(END)
        while right < len(original) and original[right] in " \t\r\n":
            if original[right] == "\n":
                right += 1
                break
            right += 1
        return original[:left] + snippet(server, prefix) + original[right:]
    if server == "nginx":
        existing = [n for n in node.children if n.words[0] == "location" and any(
                    v in (prefix, prefix + "/") for v in n.words[1:])]
        if existing:
            # Accept only the exact previously supplied proxy, without overwriting it.
            proxied = [n for n in existing if prefix + "/" in n.words]
            if len(proxied) == 1:
                directives = [n.words for n in proxied[0].children]
                if (["proxy_pass", "http://127.0.0.1:8127/"] in directives and
                        ["proxy_set_header", "X-Forwarded-Prefix", prefix] in directives and
                        ["proxy_set_header", "X-Forwarded-Proto", "https"] in directives):
                    return original
            raise ConnectError("Путь уже занят другим location; не перезаписываю ручную конфигурацию")
        start = node.opening
    else:
        if re.search(r"(?:ProxyPass\s+|<Location\s+)[\"']?" + re.escape(prefix) + r"/?(?:\s|>|[\"'])", segment, re.I):
            raise ConnectError("Путь уже настроен вручную в Apache; не создаю дубликат")
        start = node.opening
    return original[:start] + "\n" + snippet(server, prefix) + original[start:]


def replace_bytes(path, content, info):
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".lcweb-", delete=False) as stream:
        temporary = Path(stream.name)
        stream.write(content)
        stream.flush()
        os.fsync(stream.fileno())
    try:
        os.chmod(temporary, stat.S_IMODE(info.st_mode))
        if hasattr(os, "chown"):
            os.chown(temporary, info.st_uid, info.st_gid)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def apply_site(path, before, after, backups, validate, reload, protect=True):
    path = Path(path).resolve()
    if before == after:
        validate()
        reload()
        return None
    if protect:
        secure_file(path)
    if path.read_bytes() != before:
        raise ConnectError("Конфигурация изменилась после обнаружения; повторите подключение")
    info = path.stat()
    backups = Path(backups)
    backups.mkdir(parents=True, exist_ok=True, mode=0o700)
    if protect:
        secure_file(backups)
    backup = backups / (path.name + "." + time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:8] + ".bak")
    with backup.open("xb") as stream:
        stream.write(before)
    backup.chmod(0o600)
    replace_bytes(path, after, info)
    try:
        validate()
        reload()
    except Exception:
        if path.read_bytes() != after:
            raise ConnectError("Ошибка проверки; файл изменён другим процессом. Резервная копия: " + str(backup))
        replace_bytes(path, before, info)
        # A failed syntax check has not changed the running server. On a failed
        # reload, restoring and reloading the original also keeps disk consistent.
        try:
            validate()
            reload()
        except Exception:
            print("WARNING: повторная проверка/reload исходной конфигурации не прошла", flush=True)
        raise
    return backup


def backend_probe(host, prefix):
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        conn = http.client.HTTPConnection("127.0.0.1", 8127, timeout=2)
        try:
            conn.request("GET", "/login", headers={"Host": host, "X-Forwarded-Proto": "https",
                                                   "X-Forwarded-Prefix": prefix})
            response = conn.getresponse()
            body = response.read(262144)
            if response.status == 200 and b"IREDMAIL LIFECYCLE" in body:
                return
            raise ConnectError("Backend ответил HTTP %s; проверьте public_url/журнал web-службы" % response.status)
        except OSError:
            time.sleep(.5)
        finally:
            conn.close()
    raise ConnectError("Backend 127.0.0.1:8127 не отвечает; конфигурация сайта не изменена")


def ask(label, default=None):
    if not sys.stdin.isatty():
        if default is not None:
            return default
        raise ConnectError("Нет интерактивного терминала: задайте параметры явно. " + label)
    value = input(label + (" [%s]" % default if default else "") + ": ").strip()
    return value or default


def select_site(server, servers, host, port, hostname, vhost=None, line=None):
    choose = choose_nginx if server == "nginx" else choose_apache
    try:
        if not vhost and hostname != host:
            # Prefer an explicitly named CNAME target only if the public name
            # does not already select a named site.
            def names_for(n):
                return [v for d in n.children if d.words[0] == "server_name" for v in d.words[1:]] if server == "nginx" else next(e[2] for e in servers if e[0] is n)
            nodes = servers if server == "nginx" else [e[0] for e in servers]
            nodes = [n for n in nodes if (any(d.words[0] == "listen" and len(d.words) > 1 and
                    port_matches(d.words[1], port) and "ssl" in d.words for d in n.children)
                    if server == "nginx" else any(port_matches(v, port) for v in n.words[1:]))]
            public_named = any(any(host == name or ("*" in name and fnmatch.fnmatchcase(host, name)) or
                               (name.startswith(".") and (host == name[1:] or host.endswith(name)))
                               for name in names_for(n)) for n in nodes)
            if not public_named and any(hostname in names_for(n) for n in nodes):
                return choose(servers, hostname, port)
        return choose(servers, host, port, vhost, line)
    except ConnectError as primary:
        if vhost or not sys.stdin.isatty():
            raise
        choices = []
        for entry in servers:
            node = entry if server == "nginx" else entry[0]
            try:
                # Explicit file and line select just this loaded TLS block.
                number = node.path.read_text(encoding="utf-8").count("\n", 0, node.start) + 1
                choose(servers, host, port, str(node.path), number)
                choices.append((node, number))
            except ConnectError:
                continue
        if not choices:
            raise primary
        print(str(primary))
        print("Имя сервера: %s. Выберите HTTPS-сайт для публичного адреса %s:" % (hostname, host))
        for i, (node, number) in enumerate(choices, 1):
            if server == "nginx":
                names = [v for n in node.children if n.words[0] == "server_name" for v in n.words[1:]]
            else:
                names = next(e[2] for e in servers if e[0] is node)
            print("%s) %s:%s — %s" % (i, node.path, number, ", ".join(names)))
        answer = ask("Номер HTTPS-сайта")
        if not answer or not answer.isdigit() or not 1 <= int(answer) <= len(choices):
            raise ConnectError("Неверный номер; конфигурация не изменена")
        return choices[int(answer) - 1][0]


def add_public_name(node, original, server, host):
    # DNS CNAME does not change HTTP Host/SNI. Make the selected site explicitly
    # serve the public hostname; do not substitute the machine's FQDN in settings.
    if server == "nginx":
        names = [n for n in node.children if n.words[0] == "server_name"]
        if any(host in n.words[1:] for n in names):
            return original
        if len(names) > 1 or any(n.path != node.path for n in names):
            raise ConnectError("server_name задан через include/несколько директив: добавьте публичное имя вручную")
        if names:
            name = names[0]
            return original[:name.close] + " " + host + original[name.close:]
        return original[:node.opening] + "\n    server_name " + host + ";" + original[node.opening:]
    segment = original[node.start:node.end]
    names = re.findall(r"^[ \t]*Server(?:Name|Alias)[ \t]+([^\n]+)", segment, re.M | re.I)
    if any(host in row.split() for row in names):
        return original
    return original[:node.opening] + "\n    ServerAlias " + host + original[node.opening:]


def prepare_site(node, original, server, host, prefix):
    if server == "nginx":
        check_nginx_rules(node, prefix)
    routed = patch_site(node, original, server, prefix)
    if server == "nginx":
        def walk(nodes):
            for n in nodes:
                yield n
                yield from walk(n.children)
        updated = next(n for n in walk(parse_nginx(routed, node.path)) if n.start == node.start and n.words == ["server"])
        updated.children += [n for n in node.children if n.path != node.path and n.words[0] == "server_name"]
    else:
        delta = len(routed) - len(original)
        updated = Node(node.words, node.path, node.start, node.close + delta, node.end + delta, opening=node.opening)
    return add_public_name(updated, routed, server, host)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url")
    parser.add_argument("--server-hostname", help="machine FQDN / CNAME target; never replaces public URL")
    parser.add_argument("--server", choices=["auto", "nginx", "apache2"], default="auto")
    parser.add_argument("--vhost")
    parser.add_argument("--block-line", type=int)
    parser.add_argument("--check", action="store_true", help="read-only plan; do not start services or edit files")
    args = parser.parse_args()
    if os.geteuid() != 0:
        raise ConnectError("Запустите от root")
    import fcntl
    with open(LOCK_PATH, "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return connect(args)


def connect(args):
    settings = SETTINGS_PATH
    if not settings.is_file():
        raise ConnectError("Сначала создайте настройки через configure.py --url https://mail.example.org/lifecycle/")
    secure_file(settings)
    c = json.loads(settings.read_text(encoding="utf-8"))
    url = args.url or ask("Публичный HTTPS URL консоли", c.get("public_url"))
    if not url:
        raise ConnectError("HTTPS URL обязателен")
    url = url.rstrip("/") + "/"
    host, port, prefix = public_url(url)
    hostname = args.server_hostname or ask("Имя сервера / цель CNAME", c.get("server_hostname", socket.getfqdn()))
    if not hostname or not re.fullmatch(r"[a-zA-Z0-9.-]+", hostname):
        raise ConnectError("Неверное имя сервера")
    server = args.server
    if server == "auto":
        active = [s for s in ("nginx", "apache2") if subprocess.run(
                  ["systemctl", "is-active", "--quiet", s], capture_output=True).returncode == 0]
        if len(active) != 1:
            server = ask("Веб-сервер: nginx или apache2")
            if server not in ("nginx", "apache2"):
                raise ConnectError("Укажите --server nginx или apache2")
        else:
            server = active[0]
    test = ["nginx", "-t"] if server == "nginx" else ["apache2ctl", "configtest"]
    if server == "nginx":
        servers = nginx_servers(checked(["nginx", "-T"]))
    else:
        servers = apache_servers(checked(["apache2ctl", "-t", "-D", "DUMP_INCLUDES"]))
        servers = active_apache(servers, checked(["apache2ctl", "-S"]))
    node = select_site(server, servers, host, port, hostname, args.vhost, args.block_line)
    path = node.path.resolve()
    secure_file(path)
    before = path.read_bytes()
    after = prepare_site(node, before.decode("utf-8"), server, host, prefix).encode("utf-8")
    print("HTTPS virtual host: %s; server=%s; URL=%s" % (path, server, url))
    if args.check:
        print("План: публичное имя %s и маршрут %s/ → 127.0.0.1:8127; изменений нет" % (host, prefix))
        return 0
    if c["public_url"] != url:
        # configure.py preserves password/secret/approvals and only updates URL.
        checked(["/opt/iredmail-lifecycle-web/venv/bin/python", "/opt/iredmail-lifecycle-web/configure.py", "--url", url])
        checked(["systemctl", "restart", "iredmail-lifecycle-web.service"])
    # Record the separately supplied machine name without altering password,
    # secret, live permission, or queued jobs. Works with installed 0.1.0 too.
    current = json.loads(settings.read_text(encoding="utf-8"))
    if current.get("server_hostname") != hostname:
        current["server_hostname"] = hostname
        replace_bytes(settings, (json.dumps(current, ensure_ascii=False, indent=2) + "\n").encode("utf-8"), settings.stat())
    checked(["systemctl", "enable", "--now", "iredmail-lifecycle-web.service", "iredmail-lifecycle-worker.service"])
    backend_probe(host if port == 443 else "%s:%s" % (host, port), prefix)
    new_modules = []
    try:
        if server == "apache2":
            enabled = checked(["apache2ctl", "-M"])
            for name in ("proxy", "proxy_http", "headers"):
                if name + "_module" not in enabled:
                    checked(["a2enmod", name])
                    new_modules.append(name)
        backup = apply_site(path, before, after, BACKUP_PATH,
                            lambda: checked(test), lambda: checked(["systemctl", "reload", server]))
    except Exception:
        for name in reversed(new_modules):
            subprocess.run(["a2dismod", name], capture_output=True)
        raise
    print("Маршрут подключён; backend HTTP 200; проверка конфигурации и reload успешны.")
    if backup:
        print("Резервная копия: " + str(backup))
    if shutil.which("curl"):
        # Do not bypass certificate verification. A DNS/certificate problem is
        # reported separately and does not undo an otherwise valid route.
        try:
            page = checked(["curl", "--fail", "--silent", "--show-error", "--max-time", "15", url + "login"])
            if "IREDMAIL LIFECYCLE" not in page:
                raise ConnectError("Публичный адрес не показал страницу консоли; проверьте DNS/virtual host")
            print("Публичный HTTPS URL: страница входа подтверждена.")
        except ConnectError as exc:
            print("WARNING: маршрут добавлен, но публичная HTTPS-проверка не подтверждена: " + str(exc))
    else:
        print("Откройте URL в браузере; curl отсутствует, внешняя HTTPS-проверка пропущена.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ConnectError, OSError, ValueError, subprocess.TimeoutExpired) as exc:
        print("CONNECT ERROR: " + str(exc))
        raise SystemExit(1)
