# Copyright (c) 2026, rslpro
# SPDX-License-Identifier: AGPL-3.0-only
"""Typed configuration, parsed as data. Never evaluate a shell configuration."""
import re
import shlex
from pathlib import PurePosixPath
from urllib.parse import urlsplit

# key, section, label, type, default, help; numeric bounds/options follow separately.
FIELDS = [
 ('LDAP_URI','AD','Адрес LDAP','text','ldap://example.org:389','ldap:// с обязательным STARTTLS или ldaps://.'),
 ('BIND_DN','AD','Сервисная учётная запись','text','svc-lifecycle@example.org','UPN или DN учётной записи с правами чтения всех нужных OU.'),
 ('BASE_DN','AD','Основная база поиска','text','DC=example,DC=org','Например OU=Users,DC=example,DC=org.'),
 ('LEGACY_SEARCH_BASE','AD','Широкая база поиска','text','DC=example,DC=org','Корень домена, включающий основной OU и перемещённых пользователей.'),
 ('ADDRESS_ATTRIBUTE','AD','Основной адрес','select','userPrincipalName','Должен совпадать с Postfix/Dovecot/SOGo. Для MailFieldNames=(userPrincipalName) выберите UPN.'),
 ('ADDRESS_INCLUDE_MAIL','AD','Учитывать mail для защиты','bool','0','Дополнительное совпадение защищает адрес, но не разрешает штатную архивацию.'),
 ('ADDRESS_INCLUDE_SMTP_ALIASES','AD','Учитывать SMTP-алиасы для защиты','bool','0','Алиас не становится основным адресом ящика.'),
 ('EMPLOYEE_STATUS_ATTRIBUTE','AD','Кадровый атрибут','text','extensionAttribute6','Значение 0 означает увольнение. Статусы 1–8 остаются защищёнными.'),
 ('LDAP_FILTER','AD','Фильтр пользователей','text','(&(objectClass=person)(objectCategory=user))','Не исключайте disabled-пользователей: их состояние требуется для проверки.'),
 ('LDAP_PAGE_SIZE','AD','Размер страницы LDAP','number','1000','Используется при постраничном чтении AD.'),
 ('EXPECTED_MIN_USERS','AD','Минимум объектов AD','number','1','Если AD вернёт меньше, обработка остановится. Укажите ожидаемый минимум своей организации.'),
 ('MAIL_DOMAINS','Хранилище','Почтовые домены','domains','example.org','По одному домену на строку. Только перечисленные домены обрабатываются.'),
 ('VMAIL_ROOT','Хранилище','Корень рабочих ящиков','path','/var/vmail/vmail1','Структура: корень/домен/логин с Maildir. Изменение настройки не переносит данные.'),
 ('ARCHIVE_ROOT','Хранилище','Штатные архивы','path','/mnt/mail-archive/iredmail','Отдельный от рабочих ящиков каталог. Данные автоматически не перемещаются.'),
 ('LEGACY_ARCHIVE_ROOT','Хранилище','Legacy-архивы','path','/mnt/mail-archive/iredmail-legacy','Отдельный каталог для явно одобренных отсутствующих в AD ящиков.'),
 ('REQUIRE_SEPARATE_ARCHIVE_FS','Хранилище','Требовать отдельную файловую систему','bool','1','Защита от размещения архива на том же томе, что рабочие ящики.'),
 ('DRY_RUN','Штатная обработка','Только проверка, без изменений','bool','1','Отключение разрешает реальные операции и влияет на уже включённый ежедневный таймер.'),
 ('TERMINATED_ARCHIVE_DAYS','Штатная обработка','Ожидание после обнаружения увольнения, дней','number','0','Автоархивация возможна только для disabled + кадровый статус 0.'),
 ('ARCHIVE_RETENTION_DAYS','Штатная обработка','Хранение штатных архивов, дней','number','90','0 отключает автоматическое удаление штатных архивов.'),
 ('MAX_TERMINATED_PERCENT','Штатная обработка','Максимум уволенных, % ящиков','number','10','Остановка при неожиданно большой доле уволенных.'),
 ('VERIFY_MIN_PERCENT','Штатная обработка','Минимум размера проверенной копии, %','number','99','Допустимый диапазон 90–100. Не заменяет резервное копирование.'),
 ('REPORT_MAILBOX_SIZES','Штатная обработка','Считать размеры в отчёте','bool','0','Увеличивает время отчёта и нагрузку на диск.'),
 ('LEGACY_MIN_INACTIVE_DAYS','Legacy-очистка','Минимум файловой неактивности, дней','number','365','Возраст последнего mtime; это не дата входа пользователя.'),
 ('LEGACY_RETENTION_DAYS','Legacy-очистка','Хранение legacy-архивов, дней','number','0','0 отключает удаление. Legacy retention запускается отдельно от штатного таймера.'),
 ('LEGACY_MAX_COUNT','Legacy-очистка','Максимум ящиков в одном пакете','number','20','Явное одобрение списка требуется независимо от этого лимита.'),
 ('LEGACY_MAX_PERCENT','Legacy-очистка','Максимальная доля пакета, % ящиков','number','5','Доля по количеству, не по объёму почты.'),
 ('MANUAL_DELETE_MAX_COUNT','Ручное удаление','Максимум удаляемых за раз','number','100','1–500. Полное удаление файлов требует отдельного подтверждения.'),
 ('MANUAL_DELETE_MAX_PERCENT','Ручное удаление','Максимальная доля удаления, % ящиков','number','25','Доля от всех рабочих ящиков в выбранных доменах.'),
]
SCHEMA = {f[0]: dict(key=f[0], group=f[1], label=f[2], kind=f[3], default=f[4], help=f[5]) for f in FIELDS}
BOUNDS = {k:(0,36500) for k in ('TERMINATED_ARCHIVE_DAYS','ARCHIVE_RETENTION_DAYS','LEGACY_MIN_INACTIVE_DAYS','LEGACY_RETENTION_DAYS')}
BOUNDS.update(LDAP_PAGE_SIZE=(1,1000),EXPECTED_MIN_USERS=(1,10000000),MAX_TERMINATED_PERCENT=(0,100),VERIFY_MIN_PERCENT=(90,100),LEGACY_MAX_COUNT=(1,100000),LEGACY_MAX_PERCENT=(1,100),MANUAL_DELETE_MAX_COUNT=(1,500),MANUAL_DELETE_MAX_PERCENT=(1,100))
PROTECTED = {'BIND_PW_FILE','TERMINATED_STATUS_VALUE','STATE_DIR','EXCLUSION_FILE','LOG_FILE','REPORT_DIR','LOCK_FILE','LEGACY_MODE','LEGACY_APPROVAL_FILE','LEGACY_INVENTORY_FILE','LDAP_EXTRA_ARGS'}


def parse(text):
    values = {}
    for number, line in enumerate(text.splitlines(),1):
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        match = re.fullmatch(r'([A-Z][A-Z_0-9]*)=(.*)', line)
        if not match or re.search(r'`|\$\(|\$\{|\$[A-Za-z_]',match[2]):
            raise ValueError('Конфиг содержит вычисления/команды, строка %d. Веб-редактор поддерживает только постоянные значения.' % number)
        key, raw = match.groups()
        if key in values or key not in SCHEMA and key not in PROTECTED:
            raise ValueError('Повторный или неподдерживаемый параметр в конфиге: '+key)
        array = key in ('MAIL_DOMAINS','LDAP_EXTRA_ARGS')
        if array:
            if not raw.startswith('(') or ')' not in raw:
                raise ValueError('Некорректный массив: '+key)
            end=raw.rfind(')')
            if raw[end+1:].strip() and not raw[end+1:].lstrip().startswith('#'):
                raise ValueError('Лишние команды после массива: '+key)
            raw=raw[1:end]
        lex=shlex.shlex(raw,posix=True,punctuation_chars=';&|<>')
        lex.whitespace_split=True
        tokens=list(lex)
        if not array and len(tokens)!=1:
            raise ValueError('Ожидалось одно постоянное значение: '+key)
        values[key]=tokens if array else tokens[0]
    return values


def display(values):
    return {k:'\n'.join(values[k]) if isinstance(values.get(k),list) else str(values.get(k,s['default'])) for k,s in SCHEMA.items()}


def validate(form):
    if not isinstance(form,dict) or set(form)!=set(SCHEMA):
        raise ValueError('Неполный набор настроек или неизвестные поля; обновите страницу')
    result={}
    for key, spec in SCHEMA.items():
        value=form[key]
        if not isinstance(value,str) or len(value)>2048 or any(ord(c)<32 and c not in '\r\n' for c in value):
            raise ValueError('Неподходящее значение: '+spec['label'])
        value=value.strip()
        if re.search(r'`|\$\(|\$\{|\$[A-Za-z_]',value):
            raise ValueError('Вычисления и подстановки не разрешены: '+spec['label'])
        kind=spec['kind']
        if kind!='domains' and ('\n' in value or '\r' in value):
            raise ValueError('Перенос строки не разрешён: '+spec['label'])
        if kind=='bool' and value not in ('0','1'):
            raise ValueError('Выберите да или нет: '+spec['label'])
        if kind=='number':
            lo,hi=BOUNDS[key]
            if not re.fullmatch(r'0|[1-9][0-9]*',value) or not lo<=int(value)<=hi:
                raise ValueError('%s: допустимо %s–%s' % (spec['label'],lo,hi))
        if kind=='select' and value not in ('userPrincipalName','mail'):
            raise ValueError('Основной адрес: userPrincipalName или mail')
        if kind=='domains':
            value=[s.lower() for s in value.split()]
            if not 1<=len(value)<=100 or len(set(value))!=len(value) or any(not re.fullmatch(r'(?=.{1,253}$)[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?',s) or '..' in s or any(not label or label.startswith('-') or label.endswith('-') or len(label)>63 for label in s.split('.')) for s in value):
                raise ValueError('Укажите разные DNS-домены, по одному на строку; для IDN используйте punycode')
        if kind=='path':
            p=PurePosixPath(value)
            if (not value.startswith('/') or str(p)!=value or '..' in p.parts or len(p.parts)<4
                or p.parts[1] not in ('var','mnt','srv','media','data','mail')
                or p.parts[1]=='var' and p.parts[2] not in ('vmail','mail')):
                raise ValueError('Путь хранилища: отдельный подкаталог /var/vmail, /var/mail, /mnt, /srv, /media, /data или /mail')
        if kind=='text' and not value:
            raise ValueError('Заполните поле: '+spec['label'])
        result[key]=value
    uri=urlsplit(result['LDAP_URI'])
    if uri.scheme not in ('ldap','ldaps') or not uri.hostname or uri.username or uri.password or uri.path not in ('','/') or uri.query or uri.fragment:
        raise ValueError('LDAP URI: ldap://сервер:389 или ldaps://сервер:636, без пароля/пути')
    try:
        if uri.port is not None and not 1<=uri.port<=65535: raise ValueError()
    except ValueError: raise ValueError('Некорректный порт LDAP')
    if not re.fullmatch(r'[A-Za-z][A-Za-z0-9-]{0,63}',result['EMPLOYEE_STATUS_ATTRIBUTE']):
        raise ValueError('Некорректное имя кадрового атрибута')
    for key in ('BASE_DN','LEGACY_SEARCH_BASE'):
        if not re.fullmatch(r'(?:[A-Za-z][A-Za-z0-9-]*=[^,=]+)(?:\s*,\s*[A-Za-z][A-Za-z0-9-]*=[^,=]+)*',result[key]):
            raise ValueError('Используйте обычный DN без экранированных запятых: '+key)
    narrow=re.sub(r'\s*,\s*',',',result['BASE_DN']).casefold()
    broad=re.sub(r'\s*,\s*',',',result['LEGACY_SEARCH_BASE']).casefold()
    if narrow!=broad and not narrow.endswith(','+broad):
        raise ValueError('Широкая база должна включать основную базу поиска')
    ldap_filter=result['LDAP_FILTER']
    depth=0
    for ch in ldap_filter:
        depth += (ch=='(')-(ch==')')
        if depth<0: break
    if depth or not ldap_filter.startswith('(') or not ldap_filter.endswith(')'):
        raise ValueError('Неверные скобки LDAP-фильтра')
    roots=[PurePosixPath(result[k]) for k in ('VMAIL_ROOT','ARCHIVE_ROOT','LEGACY_ARCHIVE_ROOT')]
    for i,p in enumerate(roots):
        for q in roots[i+1:]:
            if p==q or p in q.parents or q in p.parents:
                raise ValueError('Рабочие ящики и два архива должны иметь непересекающиеся корни')
    result['LDAP_EXTRA_ARGS']=['-ZZ'] if uri.scheme=='ldap' else []
    return result


def render(values):
    lines=['# Managed by lifecycle web console. Values only; do not add commands.']
    for key,value in sorted(values.items()):
        if key not in SCHEMA and key not in ('LDAP_EXTRA_ARGS','BIND_PW_FILE'):
            raise ValueError('Недопустимое поле управляемого конфига')
        encoded='('+' '.join(shlex.quote(v) for v in value)+')' if isinstance(value,list) else shlex.quote(str(value))
        lines.append(key+'='+encoded)
    return '\n'.join(lines)+'\n'
