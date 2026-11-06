# Copyright (c) 2026, rslpro
# SPDX-License-Identifier: AGPL-3.0-only
"""Root-only configuration RPC, called under the shared settings EX lock.

No shell evaluation, no user-selected output path, and no secret in a job/log.
"""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import tempfile
import time
from types import SimpleNamespace
import uuid
from common import read_json, read_regular
from config_model import SCHEMA, display, parse, render, validate

LOCK_PATH='/run/iredmail-lifecycle-settings.lock'
HISTORY='/etc/iredmail-lifecycle-web/config-history'
PENDING=HISTORY+'/pending.json'
OVERRIDE='/etc/iredmail-lifecycle-web/core-overrides.conf'


def digest(data):
    return hashlib.sha256(data).hexdigest()


def write_atomic(path, content, mode=0o600, owner=None):
    path=Path(path)
    if path.is_symlink(): raise ValueError('Символические ссылки для записи запрещены')
    with tempfile.NamedTemporaryFile(dir=path.parent,delete=False) as f:
        tmp=Path(f.name)
        f.write(content);f.flush();os.fsync(f.fileno())
    try:
        tmp.chmod(mode)
        if owner and os.name=='posix':os.chown(tmp,*owner)
        os.replace(tmp,path)
        if os.name=='posix':
            fd=os.open(path.parent,os.O_RDONLY|os.O_DIRECTORY)
            try:os.fsync(fd)
            finally:os.close(fd)
    finally:tmp.unlink(missing_ok=True)


class ConfigManager:
    def __init__(self, settings, enforce=True):
        self.path=Path(settings)
        self.enforce=enforce
        self.override=self.path.parent/'core-overrides.conf'
        self.history=self.path.parent/'config-history'
        self.pending=self.history/'pending.json'
        if enforce:
            from worker import trusted
            trusted(self.path)
            trusted(self.path.parent)
        self.history.mkdir(mode=0o700,exist_ok=True)
        if enforce:trusted(self.history)

    def state(self):
        c=read_json(self.path)
        if self.enforce:
            from worker import trusted
            trusted(c['core_config'])
            if self.override.exists():trusted(self.override)
        raw=read_regular(c['core_config'],262144)
        overlay=read_regular(self.override,65536) if self.override.exists() else b''
        values=parse(raw.decode('utf-8'))
        overrides=parse(overlay.decode('utf-8'))
        if set(overrides)-set(SCHEMA)-{'LDAP_EXTRA_ARGS','BIND_PW_FILE'}:
            raise ValueError('Неподдерживаемые служебные переопределения')
        values.update(overrides)
        signature=digest(raw+b'\0'+overlay+b'\0'+json.dumps({k:c.get(k) for k in ('password_hash','allow_live','allow_delete','config_revision')},sort_keys=True).encode())
        return c,values,raw,overlay,signature

    def backup_list(self):
        result=[]
        for p in sorted(self.history.glob('backup-*.json'),reverse=True)[:20]:
            item=read_json(p)
            result.append(dict(id=p.stem,at=item['at'],changed=item['changed']))
        return result

    def read(self):
        c,v,raw,overlay,revision=self.state()
        return dict(values=display(v),revision=revision,allow_live=c.get('allow_live',False),
                    allow_delete=c.get('allow_delete',False),password_set=bool(v.get('BIND_PW_FILE') and Path(v['BIND_PW_FILE']).is_file()),
                    history=self.backup_list(),public_url=c['public_url'],pending=self.pending.exists(),timer=self.timer_status(),
                    protected={k:v.get(k,'по умолчанию') for k in ('STATE_DIR','EXCLUSION_FILE','REPORT_DIR','LOCK_FILE','LEGACY_MODE')})

    def timer_status(self):
        try:
            result=subprocess.run(['systemctl','show','iredmail-ad-lifecycle.timer','--property=ActiveState,UnitFileState,NextElapseUSecRealtime'],capture_output=True,text=True,timeout=5)
            if result.returncode:return 'Состояние таймера недоступно'
            fields=dict(line.split('=',1) for line in result.stdout.splitlines() if '=' in line)
            active='активен' if fields.get('ActiveState')=='active' else 'не активен'
            enabled='автозапуск включён' if fields.get('UnitFileState')=='enabled' else 'автозапуск выключен'
            return active+'; '+enabled+'; следующий запуск: '+(fields.get('NextElapseUSecRealtime') or 'не назначен')
        except (OSError,subprocess.TimeoutExpired):return 'Состояние таймера недоступно'

    def idle(self,c):
        if c.get('maintenance'):raise ValueError('Выполняется обновление; повторите позже')
        if list(Path(c['inbox']).glob('*.json')):raise ValueError('Дождитесь завершения очереди заданий')
        for p in Path(c['jobs']).glob('*/result.json'):
            if read_json(p).get('status')=='running':raise ValueError('Дождитесь выполняющегося задания')

    def authenticate(self,c,current):
        from werkzeug.security import check_password_hash
        if not isinstance(current,str) or len(current)>256 or not check_password_hash(c['password_hash'],current):
            raise ValueError('Неверный пароль консоли')

    def recover(self):
        """Rollback an interrupted two-file commit before allowing new work."""
        if not self.pending.exists():return
        item=read_json(self.pending)
        if set(item)!={'backup','new_overlay','new_settings'} or not re.fullmatch(r'backup-[0-9]+-[0-9a-f]{32}',item['backup']):
            raise ValueError('Некорректная запись восстановления настроек')
        old=read_json(self.history/(item['backup']+'.json'))
        active=read_regular(self.override) if self.override.exists() else b''
        settings=read_regular(self.path)
        old_overlay=old['overlay'].encode()
        old_settings=old['settings'].encode()
        if digest(active) not in (digest(old_overlay),item['new_overlay']) or digest(settings) not in (digest(old_settings),item['new_settings']):
            raise ValueError('После сбоя файлы изменены извне; автоматическое восстановление остановлено')
        info=self.path.stat()
        write_atomic(self.override,old_overlay)
        write_atomic(self.path,old_settings,stat.S_IMODE(info.st_mode),(info.st_uid,info.st_gid))
        self.pending.unlink()

    def check_paths(self,values):
        for key in ('VMAIL_ROOT','ARCHIVE_ROOT','LEGACY_ARCHIVE_ROOT'):
            p=Path(values[key])
            for parent in [p]+list(p.parents):
                if parent.is_symlink():raise ValueError('Путь хранилища содержит символическую ссылку: '+key)
            if p.exists() and not p.is_dir():raise ValueError('Путь хранилища не является каталогом: '+key)

    def ldap_test(self,values,password_file,c):
        if not password_file or not Path(password_file).is_file():
            raise ValueError('Укажите пароль сервисной учётной записи AD')
        if self.enforce:
            from worker import trusted
            trusted(c['helper'])
            trusted(password_file)
        spec=importlib.util.spec_from_file_location('config_ldap_helper',c['helper'])
        helper=importlib.util.module_from_spec(spec);spec.loader.exec_module(helper)
        args=SimpleNamespace(ldap_uri=values['LDAP_URI'],bind_dn=values['BIND_DN'],password_file=str(password_file),
                             page_size=int(values['LDAP_PAGE_SIZE']),ldap_extra=values['LDAP_EXTRA_ARGS'],ldap_timeout=20)
        directory=helper.Directory(args)
        try:
            primary=directory.query(values['BASE_DN'],values['LDAP_FILTER'],['dn'])
            broad=directory.query(values['LEGACY_SEARCH_BASE'],'(|(userPrincipalName=*)(mail=*)(proxyAddresses=*))',['dn'])
            if min(len(primary),len(broad))<int(values['EXPECTED_MIN_USERS']):
                raise ValueError('count')
        except Exception:
            # Never reflect LDAP output, account DN, credentials or subprocess arguments.
            raise ValueError('Проверка AD не пройдена: проверьте TLS/CA, пароль, базы, фильтр и минимальное число объектов') from None
        return dict(primary_count=len(primary),broad_count=len(broad))

    def transact(self,c,values,permissions,changed,raw,overlay):
        identifier='backup-%s-%s' % (time.time_ns(),uuid.uuid4().hex)
        old_settings=read_regular(self.path)
        backup=dict(at=int(time.time()),changed=changed,base_sha256=digest(raw),overlay=overlay.decode(),settings=old_settings.decode())
        write_atomic(self.history/(identifier+'.json'),json.dumps(backup,ensure_ascii=False).encode())
        updated=dict(c,**permissions,config_revision=uuid.uuid4().hex)
        new_overlay=render(values).encode()
        new_settings=(json.dumps(updated,ensure_ascii=False)+'\n').encode()
        info=self.path.stat()
        journal=dict(backup=identifier,new_overlay=digest(new_overlay),new_settings=digest(new_settings))
        write_atomic(self.pending,json.dumps(journal).encode())
        try:
            write_atomic(self.override,new_overlay)
            write_atomic(self.path,new_settings,stat.S_IMODE(info.st_mode),(info.st_uid,info.st_gid))
            self.pending.unlink()
        except Exception:
            self.recover()
            raise ValueError('Не удалось сохранить настройки; прежняя версия восстановлена') from None
        return dict(saved=True,backup=identifier,changed=changed)

    def dispatch(self,data):
        if not isinstance(data,dict) or data.get('operation') not in ('config-read','config-validate','config-test','config-save','config-restore','config-timer'):
            raise ValueError('Неизвестная операция настроек')
        op=data['operation']
        if op=='config-read':
            if set(data)!={'operation'}:raise ValueError('Лишние поля запроса')
            return self.read()
        common={'operation','current','revision'}
        fields=common|({'values','ldap_password','allow_live','allow_delete'} if op in ('config-validate','config-test','config-save') else {'backup'} if op=='config-restore' else {'enabled'})
        if set(data)!=fields:raise ValueError('Лишние или недостающие поля запроса')
        c,old,raw,overlay,revision=self.state()
        self.authenticate(c,data['current'])
        if self.pending.exists():raise ValueError('Восстановление настроек после сбоя ещё не завершено')
        if data['revision']!=revision:raise ValueError('Настройки изменились: обновите страницу и повторите')
        self.idle(c)
        if op=='config-timer':
            if type(data['enabled']) is not bool:raise ValueError('Неверное значение таймера')
            # Fixed argv, no schedule/unit/shell from the web request.
            try:
                result=subprocess.run(['systemctl','enable' if data['enabled'] else 'disable','--now','iredmail-ad-lifecycle.timer'],capture_output=True,timeout=15)
            except subprocess.TimeoutExpired:
                raise ValueError('Таймер не ответил вовремя; обновите страницу и проверьте его состояние') from None
            if result.returncode:raise ValueError('Не удалось изменить таймер; настройки почтовых служб не менялись')
            return dict(timer_enabled=data['enabled'])
        if op=='config-restore':
            if not isinstance(data['backup'],str) or not re.fullmatch(r'backup-[0-9]+-[0-9a-f]{32}',data['backup']):raise ValueError('Неизвестная резервная копия')
            saved=read_json(self.history/(data['backup']+'.json'))
            if saved['base_sha256']!=digest(raw):raise ValueError('Основной конфиг изменён извне; эта копия несовместима')
            values=parse(raw.decode());values.update(parse(saved['overlay']))
            restored=validate(display(values));restored['DRY_RUN']='1'
            if values.get('BIND_PW_FILE'):restored['BIND_PW_FILE']=values['BIND_PW_FILE']
            self.check_paths(restored)
            return self.transact(c,restored,dict(allow_live=False,allow_delete=False),['restore-safe'],raw,overlay)
        if type(data['allow_live']) is not bool or type(data['allow_delete']) is not bool:raise ValueError('Неверные разрешения операций')
        values=validate(data['values']);self.check_paths(values)
        password=data['ldap_password']
        if not isinstance(password,str) or len(password)>1024 or any(ord(ch)<32 for ch in password):raise ValueError('Некорректный пароль AD')
        changed=[k for k in SCHEMA if display(values)[k]!=display(old)[k]]
        changed += [k for k in ('allow_live','allow_delete') if data[k]!=c.get(k,False)]
        if password:changed.append('LDAP_PASSWORD')
        if op=='config-validate':return dict(valid=True,changed=changed)
        password_file=old.get('BIND_PW_FILE','')
        temporary=None
        if password:
            temporary=self.history/('ldap-'+uuid.uuid4().hex+'.pass')
            write_atomic(temporary,password.encode());password_file=str(temporary)
        keep=False
        try:
            checked={}
            if op=='config-test' or values['DRY_RUN']=='0':checked=self.ldap_test(values,password_file,c)
            if op=='config-test':return dict(tested=True,**checked)
            if password_file:values['BIND_PW_FILE']=password_file
            outcome=self.transact(c,values,{k:data[k] for k in ('allow_live','allow_delete')},changed,raw,overlay)
            keep=True
            return outcome
        finally:
            if temporary and not keep:temporary.unlink(missing_ok=True)


def request_config(operation, **fields):
    from password_service import request_rpc
    return request_rpc(dict(operation=operation,**fields),timeout=65)
