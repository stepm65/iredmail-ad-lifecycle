#!/usr/bin/env python3
# Copyright (c) 2026, rslpro
# SPDX-License-Identifier: AGPL-3.0-only
"""Root worker: fixed CLI operations only; web process cannot read AD secrets."""
import argparse
import csv
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import signal
import stat
import subprocess
import time
from common import ID, atomic_json, read_json, read_regular, result, rows, validate_request


def trusted(path):
    path = Path(path)
    for part in [path] + list(path.parents):
        info = part.lstat()
        if stat.S_ISLNK(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
            raise ValueError("Файл/каталог должен принадлежать root без записи группы: " + str(part))


def run_command(command, log, env, timeout):
    with log.open("xb") as output:
        process = subprocess.Popen(command, stdout=output, stderr=subprocess.STDOUT,
                                   env=env, start_new_session=True)
        try:
            return process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
            raise ValueError("Превышено время операции; проверьте журнал и состояние копии")


class Worker:
    def __init__(self, config, runner=run_command, enforce_owner=True, config_path=None):
        self.c, self.runner, self.enforce = config, runner, enforce_owner
        self.config_path = config_path
        self.store, self.inbox = Path(config["jobs"]), Path(config["inbox"])

    def prerequisite(self, ident, operation):
        meta = result(self.store, ident)
        if meta.get("operation") != operation or meta.get("status") != "done":
            raise ValueError("Сначала завершите " + operation + " без ошибок")
        return self.store / ident, meta

    def prepare(self, data, folder, meta):
        c = self.c
        if self.enforce:
            for key in ("core", "core_config", "helper"):
                trusted(c[key])
            spec = importlib.util.spec_from_file_location("lifecycle_helper", c["helper"])
            helper = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(helper)
            if helper.VERSION != "1.4.0":
                raise ValueError("Для восстановления/исключений установите lifecycle 1.4.0")
            canonical = helper.email_value
        else:
            canonical = lambda value: value.strip().lower()
        base = [c["core"]]
        op = data["operation"]
        if op in ("archives", "restore-preview", "restore", "exclusions", "exclude-add", "exclude-remove") and self.enforce:
            trusted(Path(c["helper"]).parent / "manage.py")
        if op in ("delete-batch-preview", "delete-batch"):
            if self.enforce:
                trusted(Path(c["helper"]).parent / "manage.py")
                trusted(Path(c["helper"]).parent / "delete_mailbox.py")
            if op == "delete-batch-preview":
                report, _ = self.prerequisite(data["report"], "report")
                emails = sorted({canonical(e) for e in data["emails"]})
                if len(emails) != len(data["emails"]):
                    raise ValueError("Повтор адреса после нормализации")
                entries = rows(report / "report.csv")
                for email in emails:
                    matches = [r for r in entries if r["email"] == email]
                    if len(matches) != 1 or matches[0].get("classification") not in ("unknown-disabled", "missing"):
                        raise ValueError("В списке защищённый/отсутствующий адрес: " + email)
                with (folder / "approval.csv").open("x", encoding="utf-8", newline="") as stream:
                    writer = csv.writer(stream); writer.writerow(["email", "approved"])
                    writer.writerows((email, "YES") for email in emails)
                meta.update(emails=emails, report=data["report"])
                return base + ["--delete-batch-preview", "--approval", str(folder / "approval.csv"), "--output", str(folder / "delete-batch-preview.json")]
            approved, preview = self.prerequisite(data["preview"], "delete-batch-preview")
            for name in ("delete-batch-preview.json", "approval.csv"):
                content = read_regular(approved / name)
                if hashlib.sha256(content).hexdigest() != preview["hashes"][name]:
                    raise ValueError("Список/снимок массового удаления изменён; создайте новую проверку")
                (folder / name).write_bytes(content)
            meta.update(emails=preview["emails"], preview=data["preview"])
            return base + ["--delete-batch", "--preview", str(folder / "delete-batch-preview.json"),
                           "--confirm-delete", "DELETE " + str(len(preview["emails"])), "--writers-quiesced", "--actor", data["actor"],
                           "--output", str(folder / "delete-batch-result.json")]
        if op in ("delete-preview", "delete"):
            if self.enforce:
                trusted(Path(c["helper"]).parent / "manage.py")
                trusted(Path(c["helper"]).parent / "delete_mailbox.py")
            if op == "delete-preview":
                report, _ = self.prerequisite(data["report"], "report")
                email = canonical(data["email"])
                matches = [r for r in rows(report / "report.csv") if r["email"] == email]
                if len(matches) != 1 or matches[0].get("classification") not in ("unknown-disabled", "missing"):
                    raise ValueError("Ручное удаление доступно только для unknown-disabled/missing; обновите отчёт")
                meta.update(email=email, report=data["report"])
                return base + ["--delete-preview", "--email", email, "--output", str(folder / "delete-preview.json")]
            approved, preview = self.prerequisite(data["preview"], "delete-preview")
            content = read_regular(approved / "delete-preview.json")
            if hashlib.sha256(content).hexdigest() != preview["hashes"]["delete-preview.json"]:
                raise ValueError("Снимок удаления изменён; создайте новую проверку")
            (folder / "delete-preview.json").write_bytes(content)
            meta.update(email=preview["email"], preview=data["preview"])
            return base + ["--delete", "--email", preview["email"], "--preview", str(folder / "delete-preview.json"),
                           "--confirm-delete", preview["email"], "--writers-quiesced", "--actor", data["actor"],
                           "--output", str(folder / "delete-result.json")]
        if op == "archives":
            return base + ["--archive-list", "--output", str(folder / "archives.csv")]
        if op in ("exclusions", "exclude-add", "exclude-remove"):
            args = ["--exclusions-list"] if op == "exclusions" else ["--" + op, "--email", canonical(data["email"]), "--actor", data["actor"]]
            if op == "exclude-add":
                args += ["--reason", data["reason"]]
            return base + args + ["--output", str(folder / "exclusions.csv")]
        if op == "restore-preview":
            catalog, _ = self.prerequisite(data["catalog"], "archives")
            matches = [r for r in rows(catalog / "archives.csv") if r["archive_id"] == data["archive_id"]]
            if len(matches) != 1 or matches[0].get("restorable") != "yes":
                raise ValueError("Архив отсутствует/не готов к восстановлению; обновите список")
            meta.update(archive_id=data["archive_id"], email=matches[0]["email"], quality=matches[0]["quality"])
            return base + ["--restore-preview", "--archive-id", data["archive_id"], "--output", str(folder / "restore-preview.json")]
        if op == "restore":
            approved, preview = self.prerequisite(data["preview"], "restore-preview")
            content = read_regular(approved / "restore-preview.json")
            if hashlib.sha256(content).hexdigest() != preview["hashes"]["restore-preview.json"]:
                raise ValueError("Снимок восстановления изменён; создайте новую проверку")
            (folder / "restore-preview.json").write_bytes(content)
            meta.update(archive_id=preview["archive_id"], email=preview["email"], quality=preview["quality"], preview=data["preview"])
            return base + ["--restore", "--archive-id", preview["archive_id"], "--preview", str(folder / "restore-preview.json"),
                           "--output", str(folder / "restore-result.json"), "--writers-quiesced", "--actor", data["actor"]]
        if op == "report":
            return base + ["--normal", "--report"]
        if op == "inventory":
            return base + ["--legacy-inventory", "--inventory", str(folder / "inventory.csv")]
        if op == "preview":
            original, source = self.prerequisite(data["inventory"], "inventory")
            entries = {r["email"]: r for r in rows(original / "inventory.csv")}
            emails = sorted({canonical(e) for e in data["emails"]})
            if len(emails) != len(data["emails"]):
                raise ValueError("Повтор адреса после нормализации")
            if any(e not in entries or entries[e].get("eligible") != "yes" for e in emails):
                raise ValueError("В списке есть отсутствующий или защищённый адрес")
            (folder / "inventory.csv").write_bytes(read_regular(original / "inventory.csv"))
            with (folder / "approval.csv").open("x", encoding="utf-8", newline="") as stream:
                writer = csv.writer(stream)
                writer.writerow(["email", "approved"])
                writer.writerows((e, "YES") for e in emails)
            meta.update(emails=emails, inventory=data["inventory"])
            return base + ["--legacy-archive", "--inventory", str(folder / "inventory.csv"),
                           "--approval", str(folder / "approval.csv"), "--report"]
        approved, preview = self.prerequisite(data["preview"], "preview")
        # A successful preview authorizes exactly this immutable allowlist.
        for name in ("inventory.csv", "approval.csv"):
            content = read_regular(approved / name)
            if hashlib.sha256(content).hexdigest() != preview["hashes"][name]:
                raise ValueError("Одобренный снимок изменён; создайте новую проверку")
            (folder / name).write_bytes(content)
        meta.update(emails=preview["emails"], inventory=preview["inventory"], preview=data["preview"])
        # DRY_RUN remains controlled by the operator's root-owned core config.
        return base + ["--legacy-archive", "--inventory", str(folder / "inventory.csv"),
                       "--approval", str(folder / "approval.csv"), "--writers-quiesced"]

    def collect_batch_result(self, folder, meta, exit_code):
        record = read_json(folder / "delete-batch-result.json")
        emails = meta["emails"]
        deleted, failed, remaining = record.get("deleted"), record.get("failed_email"), record.get("not_processed")
        if (record.get("schema") != 2 or record.get("selected") != emails
                or not isinstance(deleted, list) or deleted != emails[:len(deleted)]
                or not isinstance(remaining, list) or not isinstance(failed, str)):
            raise ValueError("Некорректный итог массового удаления")
        status = record.get("status")
        if status == "done":
            valid = deleted == emails and not failed and not remaining
        elif status == "dry-run":
            valid = not deleted and not failed and remaining == emails
        elif status in ("failed", "partial"):
            valid = (len(deleted) < len(emails) and failed == emails[len(deleted)] and remaining == emails[len(deleted)+1:]
                     and status == ("partial" if deleted else "failed"))
        elif status in ("running", "interrupted"):
            valid = not failed and remaining == emails[len(deleted):]
        else:
            valid = False
        if (not valid or (exit_code == 0 and status not in ("done", "dry-run"))
                or (exit_code is not None and exit_code != 0 and status in ("done", "dry-run"))):
            raise ValueError("Статус/код CLI не подтверждают результат пакета")
        meta["batch_result"] = record
        meta["status"] = "interrupted" if exit_code is None or status in ("running", "interrupted") else status
        if status in ("failed", "partial", "running", "interrupted"):
            meta["error"] = record.get("error", "Пакет прерван; проверьте оставшиеся пути и staging")

    def collect_report(self, folder, text):
        root = Path(self.c["report_root"]).resolve()
        for label, target in (("CSV report: ", "report.csv"),
                              ("AD address conflict details: ", "conflicts.csv")):
            lines = [line.split(label, 1)[1].strip() for line in text.splitlines() if label in line]
            if len(lines) != 1:
                raise ValueError("Не найден ожидаемый CSV отчёта")
            path = Path(lines[0])
            if path.is_symlink() or path.resolve().parent != root:
                raise ValueError("CSV находится вне разрешённого каталога отчётов")
            (folder / target).write_bytes(read_regular(path))

    def handle(self, path):
        path = Path(path)
        if not ID.fullmatch(path.stem) or path.suffix != ".json":
            return
        folder = self.store / path.stem
        if folder.exists():
            # Replay cannot overwrite an existing result or run it again.
            path.unlink(missing_ok=True)
            return
        try:
            data = read_json(path, limit=65536)
            if data.get("id") != path.stem:
                raise ValueError("Имя задания не совпадает")
            validate_request(data, self.c["allow_live"], delete=self.c.get("allow_delete", False))
        except (ValueError, OSError, AttributeError) as exc:
            print("Rejected queue item: " + path.name + ": " + str(exc), flush=True)
            path.unlink(missing_ok=True)
            return
        folder.mkdir(mode=0o750)
        meta = {k: data[k] for k in ("id", "operation", "created", "actor")}
        meta.update(status="running", started=int(time.time()))
        atomic_json(folder / "request.json", data)
        atomic_json(folder / "result.json", meta)
        path.unlink()
        try:
            command = self.prepare(data, folder, meta)
            if data["operation"] in ("archive", "restore", "delete", "delete-batch"):
                approved = self.store / data["preview"]
                if (approved / "used.json").exists():
                    raise ValueError("Эта проверка уже использована; сформируйте новую проверку")
                atomic_json(approved / "used.json", {"operation_job": data["id"], "at": int(time.time())})
            if data["operation"] == "delete-batch":
                atomic_json(folder / "result.json", meta)  # Retain exact list across a worker restart.
            env = {"PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
                   "CONFIG_FILE": self.c["core_config"], "LEGACY_HELPER": self.c["helper"],
                   "LANG": "C.UTF-8", "PYTHONIOENCODING": "utf-8"}
            code = self.runner(command, folder / "run.log", env, self.c["job_timeout"])
            # Limit log read for interpretation, without ever returning HTML.
            with (folder / "run.log").open("rb") as stream:
                stream.seek(max(0, (folder / "run.log").stat().st_size - 2000000))
                text = stream.read().decode("utf-8", errors="replace")
            meta.update(exit_code=code, status="done" if code == 0 else "failed")
            if code == 0 and data["operation"] == "report":
                self.collect_report(folder, text)
            if code == 0 and data["operation"] == "inventory":
                rows(folder / "inventory.csv")  # Require actual parseable output.
            if code == 0 and data["operation"] in ("archives", "exclusions", "exclude-add", "exclude-remove"):
                rows(folder / ("archives.csv" if data["operation"] == "archives" else "exclusions.csv"))
            if code == 0 and data["operation"] == "delete-batch-preview":
                plan = read_json(folder / "delete-batch-preview.json")
                if (plan.get("schema") != 2 or plan.get("action") != "permanent-mailbox-batch-delete"
                        or [row["email"] for row in plan["mailboxes"]] != meta["emails"]):
                    raise ValueError("Ответ CLI не совпадает с выбранным списком")
                meta["batch_plan"] = plan
                meta["hashes"] = {name: hashlib.sha256(read_regular(folder / name)).hexdigest()
                                  for name in ("delete-batch-preview.json", "approval.csv")}
            if data["operation"] == "delete-batch" and (folder / "delete-batch-result.json").exists():
                self.collect_batch_result(folder, meta, code)
            elif data["operation"] == "delete-batch" and code == 0:
                raise ValueError("Нет подтверждения массового удаления от CLI; проверьте журнал")
            if code == 0 and data["operation"] == "delete-preview":
                plan = read_json(folder / "delete-preview.json")
                if plan.get("action") != "permanent-mailbox-delete" or plan["mailbox"]["email"] != meta["email"]:
                    raise ValueError("Ответ CLI не совпадает с выбранным ящиком")
                meta["delete_plan"] = plan["mailbox"]
                meta["hashes"] = {"delete-preview.json": hashlib.sha256(read_regular(folder / "delete-preview.json")).hexdigest()}
            if code == 0 and data["operation"] == "delete":
                if "DRY-RUN DELETE:" in text:
                    meta.update(status="dry-run", error="Основной DRY_RUN=1: файлы не удалены")
                else:
                    record = read_json(folder / "delete-result.json")
                    if record.get("email") != meta["email"] or record.get("permanent") is not True:
                        raise ValueError("Нет подтверждения удаления; проверьте исходный путь и staging")
                    meta["delete_result"] = record
            if code == 0 and data["operation"] == "restore-preview":
                plan = read_json(folder / "restore-preview.json")
                if plan["archive"]["archive_id"] != data["archive_id"] or plan["archive"]["email"] != meta["email"]:
                    raise ValueError("Ответ CLI не совпадает с выбранным архивом")
                meta["hashes"] = {"restore-preview.json": hashlib.sha256(read_regular(folder / "restore-preview.json")).hexdigest()}
                meta["restore_plan"] = plan["archive"]
            if code == 0 and data["operation"] == "restore":
                if "DRY-RUN RESTORE:" in text:
                    meta.update(status="dry-run", error="Основной DRY_RUN=1: восстановление не выполнялось")
                else:
                    record = read_json(folder / "restore-result.json")
                    if record.get("archive_id") != meta["archive_id"] or record.get("email") != meta["email"] or record.get("excluded") is not True:
                        raise ValueError("Нет подтверждения восстановления от CLI; проверьте журнал/источник")
                    meta["restore_result"] = record
            if code == 0 and data["operation"] in ("preview", "archive"):
                meta["hashes"] = {name: hashlib.sha256(read_regular(folder / name)).hexdigest()
                                  for name in ("inventory.csv", "approval.csv")}
            if code == 0 and data["operation"] == "archive":
                if "DRY-RUN" in text:
                    meta["status"] = "dry-run"
                    meta["error"] = "Основной конфиг использует DRY_RUN=1: реального переноса не было"
                else:
                    meta["archived_emails"] = [line.split("LEGACY ARCHIVED: ", 1)[1].split(" -> ", 1)[0]
                                               for line in text.splitlines() if line.startswith("LEGACY ARCHIVED: ")]
                    meta["skipped_emails"] = sorted(set(meta["emails"]) - set(meta["archived_emails"]))
                    if not meta["archived_emails"]:
                        meta["status"] = "no-op"
                    elif meta["skipped_emails"]:
                        meta["status"] = "partial"
        except Exception as exc:
            meta.update(status="failed", error=str(exc))
            if data["operation"] == "delete-batch" and (folder / "delete-batch-result.json").exists():
                try:
                    self.collect_batch_result(folder, meta, None)
                except (ValueError, OSError, KeyError):
                    meta.update(status="failed", error="Неподтверждённый результат пакета; проверьте журнал/staging")
        finally:
            meta["finished"] = int(time.time())
            for file in folder.iterdir():
                if file.is_file():
                    file.chmod(0o640)
            atomic_json(folder / "result.json", meta)
            print(json.dumps(meta, ensure_ascii=False), flush=True)

    def recover(self):
        for path in self.store.glob("*/result.json"):
            meta = read_json(path)
            if meta.get("status") == "running":
                meta.update(status="interrupted", error="Обработчик перезапущен; автоматического повтора нет",
                            finished=int(time.time()))
                if meta.get("operation") == "delete-batch" and (path.parent / "delete-batch-result.json").exists():
                    try:
                        self.collect_batch_result(path.parent, meta, None)
                    except (ValueError, OSError, KeyError):
                        meta["error"] = "Пакет прерван; итог не подтверждён, проверьте журнал/staging"
                atomic_json(path, meta)

    def loop(self, broker=None):
        self.recover()
        while True:
            if broker is not None and broker.poll() is not None:
                raise RuntimeError("Password broker stopped; restarting worker service")
            if self.config_path:
                if self.enforce:
                    trusted(self.config_path)
                fresh = read_json(self.config_path)
                for name in ("allow_live", "allow_delete", "maintenance"):
                    self.c[name] = fresh.get(name, False)
            if self.c.get("maintenance"):
                time.sleep(2)
                continue
            for path in sorted(self.inbox.glob("*.json")):
                self.handle(path)
            time.sleep(2)


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--config", default="/etc/iredmail-lifecycle-web/settings.json")
    a = p.parse_args()
    if os.geteuid() != 0:
        raise SystemExit("Root worker only")
    trusted(a.config)
    os.umask(0o027)
    import fcntl
    with open("/run/iredmail-lifecycle-worker.lock", "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        password_service = Path(__file__).parent / "password_service.py"
        trusted(password_service)
        broker = subprocess.Popen([str(Path(__file__).parent / "venv/bin/python"), str(password_service), "--config", a.config])
        try:
            Worker(read_json(a.config), config_path=a.config).loop(broker)
        finally:
            broker.terminate()
            broker.wait(timeout=10)
