#!/usr/bin/python3 -I
"""Root-installed protocol login dispatcher and zero-argument launcher support."""
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import stat
import sys
import syslog
import uuid


def root_file(path, executable=False):
    path=Path(path)
    if not path.is_absolute() or '..' in path.parts: raise ValueError('path')
    for component in [*reversed(path.parents),path]:
        info=component.lstat()
        if stat.S_ISLNK(info.st_mode) or info.st_uid!=0 or info.st_mode&0o022: raise ValueError('owner')
    info=path.stat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink!=1: raise ValueError('type')
    if executable and stat.S_IMODE(info.st_mode)!=0o755: raise ValueError('mode')
    return path


def refuse(config, reason):
    def identity(name):
        try:return str(uuid.UUID(str(config.get(name))))
        except (ValueError,TypeError,AttributeError):return 'none'
    # Only fixed reason labels and UUIDs reach syslog. Never command/environment/data.
    syslog.openlog('fleet-bridge-entry',syslog.LOG_PID,syslog.LOG_AUTHPRIV)
    syslog.syslog(syslog.LOG_NOTICE,'entry_refused host='+identity('sourceHost')+' key='+identity('transportKey')+
                  ' generation='+identity('generation')+' code='+reason)
    raise SystemExit(1)


def configuration(path):
    c={}
    try:
        source=root_file(path)
        if source.stat().st_size>65536: raise ValueError('bound')
        c=json.loads(source.read_text())
        for name in ('generation','sourceHost','transportKey','hub'):
            if not uuid.UUID(c[name]).int:raise ValueError('id')
        if not 0< c['serviceUid'] <2**32-1 or os.geteuid()!=c['serviceUid']:refuse(c,'service_uid')
        root_file(c['launcher'],True);root_file(c['login'],True)
        for filename,digest in c['files'].items():
            with root_file(filename).open('rb') as f:
                if hashlib.file_digest(f,'sha256').hexdigest()!=digest:raise ValueError('digest')
        if Path(c['package'])!=Path(__file__).resolve().parent:raise ValueError('package')
        return c
    except SystemExit:raise
    except Exception:refuse(c,'configuration')


def dispatch(path):
    c=configuration(path)
    if sys.argv!=[c['login'],'-c',c['launcher']]:refuse(c,'login_arguments')
    os.execv(c['launcher'],[c['launcher']])


def launch(path):
    c=configuration(path)
    if sys.argv!=[c['launcher']]:refuse(c,'launcher_arguments')
    if os.environ.get('SSH_ORIGINAL_COMMAND')!='codex-factory-v1':refuse(c,'command')
    if os.environ.get('SSH_TTY'):refuse(c,'pty')
    try:
        source,source_port,target,target_port=os.environ['SSH_CONNECTION'].split()
        if ipaddress.ip_address(source)!=ipaddress.ip_address(c['forgeAddress']):raise ValueError('source')
        if not 0<int(source_port)<65536 or int(target_port)!=22:raise ValueError('port')
        ipaddress.ip_address(target)
    except Exception:refuse(c,'ssh_context')
    binary=str(Path(c['package'])/'bin/codex-factory-bridge')
    # Retain only the validated SSH context and fixed runtime environment.
    env={'PATH':'/usr/bin:/bin','LC_ALL':'C','HOME':'/','SSH_ORIGINAL_COMMAND':'codex-factory-v1',
         'SSH_CONNECTION':os.environ['SSH_CONNECTION']}
    os.execve(binary,[binary,'source-entry','--config',c['entryConfig']],env)
