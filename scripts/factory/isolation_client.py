#!/usr/bin/env python3
"""Restricted fixture client; never print or retain credential material in logs."""
import argparse
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import time


def exchange(path, value, ready=None, release=None):
    sock = socket.socket(socket.AF_UNIX)
    sock.settimeout(8)
    sock.connect(str(path))
    if ready:
        ready.write_text('connected')
        print(json.dumps({'fixtureConnected':True}),flush=True)
        deadline=time.monotonic()+2.5
        while not release.exists():
            if time.monotonic()>deadline:raise RuntimeError('fixture hold timed out')
            time.sleep(.01)
    payload = value if isinstance(value, bytes) else json.dumps(value).encode()+b'\n'
    sock.sendall(payload)
    data=b''
    while not data.endswith(b'\n'):
        part=sock.recv(65536)
        if not part:break
        data+=part
    sock.close()
    return json.loads(data)


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--socket',type=Path)
    parser.add_argument('--bootstrap',type=Path)
    parser.add_argument('--credential',type=Path)
    parser.add_argument('--credential-out',type=Path)
    parser.add_argument('--request',type=Path)
    parser.add_argument('--claim-seat')
    parser.add_argument('--forge-credential',action='store_true')
    parser.add_argument('--forge-bootstrap',action='store_true')
    parser.add_argument('--wire-version',type=int,default=1)
    parser.add_argument('--raw',type=Path)
    parser.add_argument('--inspect',type=Path,nargs='+')
    parser.add_argument('--stop-record',type=Path)
    parser.add_argument('--stop-signal',choices=['TERM','KILL'],default='TERM')
    parser.add_argument('--wire',type=Path)
    parser.add_argument('--hold-ready',type=Path)
    parser.add_argument('--hold-release',type=Path)
    args=parser.parse_args()
    if args.stop_record:
        if os.geteuid()!=0:raise RuntimeError('fixture supervisor requires root')
        record=json.loads(args.stop_record.read_text())
        pid=record['pid']
        fd=os.pidfd_open(pid)
        try:
            fields=Path(f'/proc/{pid}/stat').read_text().rsplit(')',1)[1].split()
            if fields[19]!=record['startTicks']:raise RuntimeError('fixture incarnation changed')
            if Path('/proc/sys/kernel/random/boot_id').read_text().strip()!=record['bootId']:raise RuntimeError('fixture boot changed')
            if os.readlink(f'/proc/{pid}/exe')!=record['exe']:raise RuntimeError('fixture executable changed')
            if Path(f'/proc/{pid}').stat().st_uid!=record['uid']:raise RuntimeError('fixture uid changed')
            signal.pidfd_send_signal(fd,signal.SIGKILL if args.stop_signal=='KILL' else signal.SIGTERM)
        finally:os.close(fd)
        print(json.dumps({'signalled_owned_incarnation':True}));return
    if args.inspect:
        status=Path('/proc/self/status').read_text()
        results={}
        for path in args.inspect:
            try:
                with path.open('rb') as f:f.read(1)
                results[str(path)]=True
            except PermissionError:results[str(path)]=False
        sudo=subprocess.run(['sudo','-n','true'],capture_output=True,timeout=5)
        print(json.dumps({'uid':os.geteuid(),'noNewPrivs':'NoNewPrivs:\t1' in status,
                          'readable':results,'sudo_exit':sudo.returncode}));return
    if args.wire:
        value=json.loads(args.wire.read_text())
        credential=json.loads(args.credential.read_text())
        value['auth']={'seat':credential['seat'],'credential':credential['credential'],
                       'request':value.pop('request')}
        result=exchange(args.socket,value,args.hold_ready,args.hold_release)
    elif args.raw:
        result=exchange(args.socket,args.raw.read_bytes())
    elif args.bootstrap:
        token='0'*64 if args.forge_bootstrap else args.bootstrap.read_text()
        result=exchange(args.socket,{'method':'enroll','protocol':args.wire_version,'bootstrap':token})
        if result.get('ok') and args.credential_out:
            fd=os.open(args.credential_out,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
            with os.fdopen(fd,'w') as out:json.dump(result,out);out.flush();os.fsync(out.fileno())
    else:
        credential=json.loads(args.credential.read_text())
        token='0'*64 if args.forge_credential else credential['credential']
        result=exchange(args.socket,{'method':'authorize','protocol':args.wire_version,
            'seat':args.claim_seat or credential['seat'],'credential':token,
            'request':json.loads(args.request.read_text())})
    result.pop('credential',None)
    print(json.dumps(result))

if __name__=='__main__':main()
