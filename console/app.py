# Copyright (c) 2026, rslpro
# SPDX-License-Identifier: AGPL-3.0-only
"""Unprivileged Flask console. Only a fixed, validated JSON job protocol."""
from collections import Counter
from contextlib import closing
import datetime as dt
import functools
import hmac
import json
import os
from pathlib import Path
import secrets
import sqlite3
import time
from urllib.parse import urlsplit
import uuid
from flask import Flask, abort, flash, jsonify, redirect, render_template, request, send_file, session, url_for
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.exceptions import SecurityError
from werkzeug.security import check_password_hash
from password_service import request_change
from config_service import request_config
from config_model import SCHEMA, BOUNDS
from common import atomic_json, job_id, read_json, read_regular, result, rows, validate_request

LABELS = {"report": "Штатный отчёт", "inventory": "Legacy-инвентаризация",
          "preview": "Проверка выбранных ящиков", "archive": "Одобренная архивация",
          "archives": "Список архивов", "restore-preview": "Проверка восстановления", "restore": "Восстановление",
          "delete-batch-preview": "Проверка массового удаления", "delete-batch": "Массовое удаление файлов",
          "delete-preview": "Проверка безвозвратного удаления", "delete": "Безвозвратное удаление файлов",
          "exclusions": "Список исключений", "exclude-add": "Добавление исключения", "exclude-remove": "Снятие исключения"}
STATES = {"queued": "В очереди", "running": "Выполняется", "done": "Завершено",
          "failed": "Ошибка", "interrupted": "Прервано", "dry-run": "Только проверка",
          "partial": "Часть выполнена", "no-op": "Перенос не потребовался"}
CLASSES = {"active": "Активен", "terminated": "Уволен", "protected-disabled": "Защищён",
           "unknown-disabled": "Неизвестный статус", "missing": "Нет в основном AD",
           "ambiguous-ad": "Неоднозначный адрес", "alias-only": "Дополнительный адрес", "excluded": "Исключён"}


def create_app(settings=None):
    c = settings or read_json(os.environ.get("LIFECYCLE_WEB_CONFIG", "/etc/iredmail-lifecycle-web/settings.json"))
    origin = urlsplit(c["public_url"])
    if origin.scheme != "https" or not origin.hostname or origin.username or origin.query or origin.fragment:
        raise ValueError("public_url must be an HTTPS URL")
    prefix = origin.path.rstrip("/") or ""
    app = Flask(__name__)
    app.config.update(SECRET_KEY=c["secret"], TRUSTED_HOSTS=[origin.hostname],
                      SESSION_COOKIE_NAME="lifecycle_session", SESSION_COOKIE_SECURE=True,
                      SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Strict",
                      SESSION_COOKIE_PATH=prefix or "/", MAX_CONTENT_LENGTH=65536,
                      PERMANENT_SESSION_LIFETIME=dt.timedelta(minutes=60))
    # Exactly one local reverse proxy; examples overwrite proto and prefix.
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_prefix=1, x_host=0, x_port=0)
    store, inbox = Path(c["jobs"]), Path(c["inbox"])
    version = c["password_hash"][-32:]
    database = Path(c["web_state"]) / "login.sqlite"

    def csrf():
        if "csrf" not in session:
            session["csrf"] = secrets.token_urlsafe(32)
        return session["csrf"]

    def login_required(fn):
        @functools.wraps(fn)
        def wrapped(*args, **kwargs):
            if session.get("user") != c["username"] or session.get("version") != version:
                return redirect(url_for("login"))
            return fn(*args, **kwargs)
        return wrapped

    @app.before_request
    def checks():
        nonlocal version
        if settings is None:
            fresh = read_json(os.environ.get("LIFECYCLE_WEB_CONFIG", "/etc/iredmail-lifecycle-web/settings.json"))
            c["password_hash"] = fresh["password_hash"]
            c["maintenance"] = fresh.get("maintenance", False)
            c["allow_live"] = fresh.get("allow_live", False)
            c["allow_delete"] = fresh.get("allow_delete", False)
            c["config_revision"] = fresh.get("config_revision", "")
            version = c["password_hash"][-32:]
        if not request.is_secure:
            abort(400, "Используйте HTTPS через настроенный reverse proxy")
        if c.get("maintenance") and request.method == "POST":
            abort(503, "Выполняется обновление. Дождитесь завершения и обновите страницу.")
        if request.method == "POST":
            if not hmac.compare_digest(session.get("csrf", ""), request.form.get("csrf", "")) or not session.get("csrf"):
                abort(400, "Неверный CSRF-токен; обновите страницу")
            if request.headers.get("Origin") not in (None, origin.scheme + "://" + origin.netloc):
                abort(400, "Неверный источник запроса")

    @app.after_request
    def headers(response):
        response.headers.update({"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff",
            "X-Frame-Options": "DENY", "Referrer-Policy": "same-origin",
            "Content-Security-Policy": "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"})
        return response

    @app.context_processor
    def context():
        return dict(csrf=csrf, labels=LABELS, states=STATES, classes=CLASSES,
                    live=c["allow_live"], deletion_enabled=c.get("allow_delete", False), public_url=c["public_url"], username=session.get("user"))

    @app.template_filter("stamp")
    def stamp(value):
        return dt.datetime.fromtimestamp(value, dt.timezone.utc).strftime("%d.%m.%Y %H:%M UTC") if value else "—"

    @app.template_filter("size")
    def size(value):
        n = float(value or 0)
        for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
            if n < 1024 or unit == "TiB":
                return "%.1f %s" % (n, unit)
            n /= 1024

    def history():
        found = []
        for p in store.glob("*/result.json"):
            try:
                found.append(read_json(p))
            except (ValueError, OSError):
                continue
        for p in inbox.glob("*.json"):
            try:
                item = read_json(p)
                if not any(r["id"] == item["id"] for r in found):
                    found.append(dict(item, status="queued"))
            except (ValueError, OSError, KeyError):
                continue
        return sorted(found, key=lambda r: r["created"], reverse=True)

    def get_job(ident):
        try:
            ident = job_id(ident)
            try:
                return result(store, ident)
            except FileNotFoundError:
                return dict(read_json(inbox / (ident + ".json")), status="queued")
        except (ValueError, OSError):
            abort(404)

    def enqueue(operation, **extra):
        ident = uuid.uuid4().hex
        data = dict(id=ident, operation=operation, created=int(time.time()), actor=c["username"], **extra)
        data['config_revision'] = c.get('config_revision', '')
        validate_request(data, c["allow_live"], delete=c.get("allow_delete", False))
        if len(json.dumps(data, ensure_ascii=False).encode("utf-8")) > 65536:
            raise ValueError("Слишком большой список; уменьшите пакет")
        if len(list(inbox.glob("*.json"))) >= 10:
            raise ValueError("Очередь заполнена. Дождитесь завершения заданий")
        atomic_json(inbox / (ident + ".json"), data, mode=0o600)
        return redirect(url_for("job", ident=ident))

    @app.route("/login", methods=["GET", "POST"])
    def login():
        if request.method == "POST":
            database.parent.mkdir(parents=True, exist_ok=True)
            with closing(sqlite3.connect(database)) as connection, connection as db:
                db.execute("CREATE TABLE IF NOT EXISTS failures (ip TEXT, at INTEGER)")
                now, ip = int(time.time()), request.remote_addr or "unknown"
                db.execute("DELETE FROM failures WHERE at < ?", (now - 600,))
                attempts = db.execute("SELECT count(*) FROM failures WHERE ip=?", (ip,)).fetchone()[0]
                if attempts >= 5:
                    abort(429, "Слишком много попыток; повторите через 10 минут")
                valid = check_password_hash(c["password_hash"], request.form.get("password", ""))
                if valid and request.form.get("username") == c["username"]:
                    db.execute("DELETE FROM failures WHERE ip=?", (ip,))
                    session.clear()
                    session.update(user=c["username"], version=version, csrf=secrets.token_urlsafe(32))
                    session.permanent = True
                    return redirect(url_for("dashboard"))
                db.execute("INSERT INTO failures VALUES (?, ?)", (ip, now))
            flash("Неверное имя пользователя или пароль", "error")
        return render_template("login.html")

    @app.post("/logout")
    @login_required
    def logout():
        session.clear()
        return redirect(url_for("login"))

    @app.get("/")
    @login_required
    def dashboard():
        jobs = history()
        latest = next((j for j in jobs if j["operation"] == "report" and j["status"] == "done"), None)
        data = rows(store / latest["id"] / "report.csv") if latest else []
        counts = Counter(r.get("classification", "") for r in data)
        query, category = request.args.get("q", "")[:200].lower(), request.args.get("category", "")
        selected = [r for r in data if query in r.get("email", "").lower()
                    and (not category or r.get("classification") == category)]
        try:
            page = max(1, int(request.args.get("page", "1")))
        except ValueError:
            page = 1
        return render_template("dashboard.html", jobs=jobs[:20], latest=latest, counts=counts,
                               total=len(data), selected=selected[(page-1)*100:page*100],
                               matching=len(selected), page=page, query=query, category=category)

    @app.post("/new/<operation>")
    @login_required
    def new(operation):
        if operation not in ("report", "inventory", "archives", "exclusions"):
            abort(404)
        try:
            return enqueue(operation)
        except ValueError as exc:
            flash(str(exc), "error")
            return redirect(url_for("dashboard"))

    @app.get("/jobs/<ident>")
    @login_required
    def job(ident):
        item = get_job(ident)
        folder = store / ident
        data = rows(folder / "inventory.csv") if item["operation"] == "inventory" and item["status"] == "done" else []
        text = ""
        if (folder / "run.log").is_file():
            with (folder / "run.log").open("rb") as stream:
                stream.seek(max(0, (folder / "run.log").stat().st_size - 100000))
                text = stream.read().decode("utf-8", errors="replace")
        return render_template("job.html", item=item, inventory=data[:5000], inventory_count=len(data), log=text)

    @app.route("/account", methods=["GET", "POST"])
    @login_required
    def account():
        if request.method == "POST":
            try:
                if request.form.get("new") != request.form.get("repeat"):
                    raise ValueError("Новые пароли не совпадают")
                request_change(request.form.get("current", ""), request.form.get("new", ""))
                session.clear()
                flash("Пароль изменён. Войдите с новым паролем.", "success")
                return redirect(url_for("login"))
            except (ValueError, OSError):
                flash("Пароль не изменён: проверьте текущий пароль, повтор и длину 12–256 символов. После 5 неудачных попыток подождите 10 минут. Если служба недоступна, используйте сброс через SSH.", "error")
        return render_template("account.html")

    @app.route('/settings', methods=['GET','POST'])
    @login_required
    def settings_page():
        view = None
        outcome = None
        submitted = None
        try:
            if request.method == 'POST':
                action = request.form.get('action', '')
                if action not in ('validate','test','save','restore','timer-on','timer-off'):
                    abort(400)
                common = dict(current=request.form.get('current',''), revision=request.form.get('revision',''))
                if action in ('validate','test','save'):
                    submitted = {key:request.form.get(key,'') for key in SCHEMA}
                    if action == 'save' and request.form.get('confirm') != 'СОХРАНИТЬ':
                        raise ValueError('Для сохранения введите СОХРАНИТЬ и пароль консоли')
                    outcome = request_config('config-'+action, **common, values=submitted,
                                             ldap_password=request.form.get('ldap_password',''),
                                             allow_live=request.form.get('allow_live')=='yes',
                                             allow_delete=request.form.get('allow_delete')=='yes')
                elif action == 'restore':
                    if request.form.get('confirm') != 'ВОССТАНОВИТЬ НАСТРОЙКИ':
                        raise ValueError('Введите ВОССТАНОВИТЬ НАСТРОЙКИ и пароль консоли')
                    outcome = request_config('config-restore', **common, backup=request.form.get('backup',''))
                else:
                    phrase='ВКЛЮЧИТЬ' if action=='timer-on' else 'ВЫКЛЮЧИТЬ'
                    if request.form.get('confirm') != phrase:raise ValueError('Введите '+phrase+' и пароль консоли')
                    outcome = request_config('config-timer', **common, enabled=action=='timer-on')
                if action in ('save','restore','timer-on','timer-off'):
                    flash('Настройки сохранены. Старые отчёты и проверки операций нужно создать заново.' if action in ('save','restore') else 'Настройка таймера применена.', 'success')
                    return redirect(url_for('settings_page'))
            view = request_config('config-read')
            if submitted:
                view['values'] = submitted
                view['allow_live'] = request.form.get('allow_live')=='yes'
                view['allow_delete'] = request.form.get('allow_delete')=='yes'
        except (ValueError, OSError) as exc:
            flash(str(exc) if isinstance(exc,ValueError) else 'Служба настроек недоступна или занята. Дождитесь задания и повторите.', 'error')
            if view is None:
                try:
                    view=request_config('config-read')
                    if submitted:view['values']=submitted
                except (ValueError,OSError):pass
        return render_template('settings.html', view=view, fields=SCHEMA.values(), bounds=BOUNDS, outcome=outcome)

    @app.get("/delete-selection")
    @login_required
    def delete_selection():
        latest = next((j for j in history() if j["operation"] == "report" and j["status"] == "done"), None)
        entries = rows(store / latest["id"] / "report.csv") if latest else []
        entries.sort(key=lambda row: (row.get("classification") not in ("unknown-disabled", "missing"), row.get("email", "")))
        eligible = sum(r.get("classification") in ("unknown-disabled", "missing") for r in entries)
        return render_template("delete_selection.html", latest=latest, entries=entries[:5000], total=len(entries), eligible=eligible)

    @app.post("/jobs/<ident>/delete-batch-preview")
    @login_required
    def delete_batch_preview(ident):
        item = get_job(ident)
        if item["operation"] != "report" or item["status"] != "done":
            abort(400)
        try:
            return enqueue("delete-batch-preview", report=ident, emails=request.form.getlist("email"))
        except ValueError as exc:
            flash(str(exc), "error")
            return redirect(url_for("delete_selection"))

    @app.post("/jobs/<ident>/delete-batch")
    @login_required
    def delete_batch_job(ident):
        item = get_job(ident)
        if not c.get("allow_delete") or item["operation"] != "delete-batch-preview" or item["status"] != "done":
            abort(403)
        if (request.form.get("confirm") != "УДАЛИТЬ " + str(len(item["emails"])) or request.form.get("writers") != "yes"
                or not check_password_hash(c["password_hash"], request.form.get("password", ""))):
            flash("Введите УДАЛИТЬ и точное количество, пароль консоли; подтвердите остановку записи во все выбранные ящики", "error")
            return redirect(url_for("job", ident=ident))
        try:
            return enqueue("delete-batch", preview=ident, confirmed=True, writers_quiesced=True)
        except ValueError as exc:
            flash(str(exc), "error")
            return redirect(url_for("job", ident=ident))

    @app.post("/jobs/<ident>/delete-preview")
    @login_required
    def delete_preview(ident):
        item = get_job(ident)
        if item["operation"] != "report" or item["status"] != "done":
            abort(400)
        try:
            return enqueue("delete-preview", report=ident, email=request.form.get("email", ""))
        except ValueError as exc:
            flash(str(exc), "error")
            return redirect(url_for("dashboard"))

    @app.post("/jobs/<ident>/delete")
    @login_required
    def delete_job(ident):
        item = get_job(ident)
        if not c.get("allow_delete") or item["operation"] != "delete-preview" or item["status"] != "done":
            abort(403)
        if (request.form.get("confirm") != "УДАЛИТЬ" or request.form.get("email") != item["email"]
                or request.form.get("writers") != "yes" or not check_password_hash(c["password_hash"], request.form.get("password", ""))):
            flash("Введите УДАЛИТЬ, точный адрес и пароль консоли; подтвердите остановку записи", "error")
            return redirect(url_for("job", ident=ident))
        try:
            return enqueue("delete", preview=ident, confirmed=True, writers_quiesced=True)
        except ValueError as exc:
            flash(str(exc), "error")
            return redirect(url_for("job", ident=ident))

    @app.get("/archives")
    @login_required
    def archives_page():
        latest = next((j for j in history() if j["operation"] == "archives" and j["status"] == "done"), None)
        data = rows(store / latest["id"] / "archives.csv") if latest else []
        query = request.args.get("q", "")[:200].lower()
        return render_template("archives.html", latest=latest, entries=[r for r in data if query in r["email"].lower()][:1000], query=query, total=len(data))

    @app.post("/jobs/<ident>/restore-preview")
    @login_required
    def restore_preview(ident):
        item = get_job(ident)
        if item["operation"] != "archives" or item["status"] != "done":
            abort(400)
        try:
            return enqueue("restore-preview", catalog=ident, archive_id=request.form.get("archive_id", ""))
        except ValueError as exc:
            flash(str(exc), "error")
            return redirect(url_for("archives_page"))

    @app.post("/jobs/<ident>/restore")
    @login_required
    def restore_job(ident):
        item = get_job(ident)
        if not c["allow_live"] or item["operation"] != "restore-preview" or item["status"] != "done":
            abort(403)
        if (request.form.get("confirm") != "ВОССТАНОВИТЬ" or request.form.get("writers") != "yes"
                or not check_password_hash(c["password_hash"], request.form.get("password", ""))):
            flash("Подтвердите остановку записи, введите ВОССТАНОВИТЬ и пароль консоли", "error")
            return redirect(url_for("job", ident=ident))
        try:
            return enqueue("restore", preview=ident, confirmed=True, writers_quiesced=True)
        except ValueError as exc:
            flash(str(exc), "error")
            return redirect(url_for("job", ident=ident))

    @app.get("/exclusions")
    @login_required
    def exclusions_page():
        latest = next((j for j in history() if j["operation"] in ("exclusions", "exclude-add", "exclude-remove") and j["status"] == "done"), None)
        data = rows(store / latest["id"] / "exclusions.csv") if latest else []
        return render_template("exclusions.html", latest=latest, entries=data[:5000], total=len(data))

    @app.post("/exclusions/<action>")
    @login_required
    def exclusion_action(action):
        if action not in ("add", "remove"):
            abort(404)
        try:
            if action == "add":
                return enqueue("exclude-add", email=request.form.get("email", ""), reason=request.form.get("reason", ""))
            if (request.form.get("confirm") != "СНЯТЬ" or
                    not check_password_hash(c["password_hash"], request.form.get("password", ""))):
                raise ValueError("Для снятия исключения введите СНЯТЬ и пароль консоли")
            return enqueue("exclude-remove", email=request.form.get("email", ""), confirmed=True)
        except ValueError as exc:
            flash(str(exc), "error")
            return redirect(url_for("exclusions_page"))

    @app.get("/jobs/<ident>/status")
    @login_required
    def status(ident):
        item = get_job(ident)
        return jsonify(status=item["status"])

    @app.post("/jobs/<ident>/preview")
    @login_required
    def preview(ident):
        item = get_job(ident)
        if item["operation"] != "inventory" or item["status"] != "done":
            abort(400)
        try:
            return enqueue("preview", inventory=ident, emails=request.form.getlist("email"))
        except ValueError as exc:
            flash(str(exc), "error")
            return redirect(url_for("job", ident=ident))

    @app.post("/jobs/<ident>/archive")
    @login_required
    def archive(ident):
        item = get_job(ident)
        if not c["allow_live"] or item["operation"] != "preview" or item["status"] != "done":
            abort(403)
        password_ok = check_password_hash(c["password_hash"], request.form.get("password", ""))
        if not password_ok or request.form.get("confirm") != "АРХИВИРОВАТЬ" or request.form.get("writers") != "yes":
            flash("Подтвердите остановку записи, введите АРХИВИРОВАТЬ и пароль администратора", "error")
            return redirect(url_for("job", ident=ident))
        try:
            return enqueue("archive", preview=ident, confirmed=True, writers_quiesced=True)
        except ValueError as exc:
            flash(str(exc), "error")
            return redirect(url_for("job", ident=ident))

    @app.get("/jobs/<ident>/download/<name>")
    @login_required
    def download(ident, name):
        get_job(ident)
        if name not in ("report.csv", "conflicts.csv", "inventory.csv", "approval.csv", "run.log", "archives.csv", "exclusions.csv", "restore-preview.json", "restore-result.json", "delete-preview.json", "delete-result.json", "delete-batch-preview.json", "delete-batch-result.json"):
            abort(404)
        path = store / ident / name
        if not path.is_file() or path.is_symlink():
            abort(404)
        return send_file(path, as_attachment=True, download_name=ident[:8] + "-" + name,
                         mimetype="application/json" if name.endswith(".json") else "text/plain" if name.endswith(".log") else "text/csv")

    @app.errorhandler(400)
    @app.errorhandler(403)
    @app.errorhandler(404)
    @app.errorhandler(429)
    @app.errorhandler(503)
    def error(exc):
        if isinstance(exc, SecurityError):
            return "Недопустимый Host", 400, {"Content-Type": "text/plain; charset=utf-8"}
        return render_template("error.html", error=exc), exc.code

    return app
