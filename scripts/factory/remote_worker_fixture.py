#!/usr/bin/env python3
"""Owned Linux worker inside an isolated remote test container. No login files."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import select
import subprocess
import sys
import time
import traceback

from fleet_fixture import Fleet, GROUP, SERVICE
from native_fixture import Native
from session_fixture import Model


class WorkerFleet(Fleet):
    def __init__(self, base, grant):
        self.base = base
        self.e = base / 'evidence'
        self.binary = base / 'bin/bridge'
        self.socket = base / 'remote.sock'
        self.helper = Path(__file__).with_name('isolation_client.py')
        self.counter, self.commands = 0, []
        self.subjects = {'receiver': {'uid': grant['agentUid'], 'seat': grant['seat'],
            'grant': grant, 'credential': base / 'receiver/credential', 'sequence': 0}}

    def sudo(self, *cmd):
        return self.run(cmd)

    def run(self, cmd, expected=0, timeout=20):
        # The operator already runs as root inside this isolated container.
        if list(cmd[:2]) == ['sudo', '-n']:
            cmd = cmd[2:]
        return super().run(cmd, expected, timeout)

    def prefix(self, uid):
        return ['/usr/bin/setpriv', '--reuid', str(uid), '--regid', str(GROUP),
                '--clear-groups', '--no-new-privs']


class Worker:
    def __init__(self, base):
        self.base, self.native, self.model, self.proxy = base, None, None, None
        self.f = None
        self.closed = False
        self.proxy_lease = None
        self.held = None

    def install(self, path, data, uid, mode=0o600):
        path.write_text(data)
        os.chown(path, uid, GROUP)
        path.chmod(mode)

    def start_proxy(self):
        f = self.f
        self.proxy_record = self.base / 'service/proxy-record.json'
        self.proxy_record.unlink(missing_ok=True)
        spec = f.publish({'argv': [str(f.binary), 'connect-host', '--state',
            str(self.base / 'service/state'), '--socket', str(f.socket), '--config',
            str(self.base / 'service/proxy.json')], 'record': str(self.proxy_record),
            'cwd': str(self.base / 'service'), 'env': {'PATH': '/usr/bin:/bin', 'HOME': str(self.base / 'service')}})
        self.proxy_log = (f.e / ('proxy-' + str(f.counter) + '.log')).open('w')
        self.proxy = subprocess.Popen([*f.prefix(SERVICE), sys.executable,
            str(Path(__file__).with_name('live_command.py')), '--spec', str(spec)],
            stdout=subprocess.PIPE, stderr=self.proxy_log, text=True)
        assert select.select([self.proxy.stdout], [], [], 15)[0], 'Remote proxy did not start.'
        line = self.proxy.stdout.readline()
        assert line, 'Remote proxy stopped. Check its retained log.'
        ready = json.loads(line)
        self.proxy_lease = json.loads(self.proxy_record.read_text())
        assert ready['pid'] == self.proxy_lease['pid']
        self.proxy_lease['exe'] = os.readlink('/proc/' + str(ready['pid']) + '/exe')
        self.proxy_record.write_text(json.dumps(self.proxy_lease))
        f.commands.append({'proxyReady': ready, 'incarnation': self.proxy_lease})

    def stop_proxy(self):
        if self.proxy is None:
            return
        if self.proxy.poll() is None:
            assert self.proxy_lease is not None, 'A running remote proxy has no proven incarnation.'
            self.f.sudo(sys.executable, self.f.helper, '--stop-record', self.proxy_record)
        self.f.commands.append({'proxyExit': self.proxy.wait(timeout=15), 'incarnation': self.proxy_lease})
        self.proxy.stdout.close()
        self.proxy_log.close()
        self.proxy = None
        self.f.socket.unlink(missing_ok=True)

    def setup(self, value):
        assert self.f is None and os.geteuid() == 0
        assert self.base.parent == Path('/tmp') and self.base.name.startswith('cf15-remote-')
        assert not self.base.exists()
        self.base.mkdir(mode=0o711)
        self.base.chmod(0o711)
        os.chown(self.base, SERVICE, GROUP)
        for name, uid, mode in [('bin', SERVICE, 0o711), ('public', SERVICE, 0o711),
                ('service', SERVICE, 0o700), ('receiver', value['grant']['agentUid'], 0o700),
                ('evidence', 0, 0o700)]:
            p = self.base / name
            p.mkdir(mode=mode)
            p.chmod(mode)
            os.chown(p, uid, GROUP)
        self.f = WorkerFleet(self.base, value['grant'])
        f = self.f
        for name, source in [('bridge', '/opt/cf015/bin/codex-factory-bridge'), ('codex', '/opt/cf015/bin/codex')]:
            f.sudo('install', '-o', str(SERVICE), '-g', str(GROUP), '-m', '0555', source, f.base / 'bin' / name)
        config = value['config']
        config['identityFile'] = str(self.base / 'service/client-key')
        config['knownHosts'] = str(self.base / 'service/known_hosts')
        self.install(Path(config['identityFile']), value['transportKey'], SERVICE)
        self.install(Path(config['knownHosts']), value['knownHosts'], SERVICE)
        self.install(self.base / 'service/proxy.json', json.dumps(config), SERVICE)
        self.start_proxy()
        bootstrap = self.base / 'receiver/bootstrap'
        self.install(bootstrap, value['bootstrap'], value['grant']['agentUid'])
        enrolled = f.run([*f.prefix(value['grant']['agentUid']), sys.executable, f.helper,
            '--socket', f.socket, '--bootstrap', bootstrap, '--credential-out', f.subjects['receiver']['credential']])
        if not json.loads(enrolled.stdout)['ok']:
            spec = {'config': config, 'peerUid': value['grant']['agentUid'],
                'wire': {'method': 'enroll', 'protocol': 1, 'bootstrap': '0' * 64}}
            path = self.base / 'service/connection-diagnostic.json'
            self.install(path, json.dumps(spec), SERVICE)
            probe = f.run([*f.prefix(SERVICE), sys.executable,
                Path(__file__).with_name('network_probe.py'), '--spec', path])
            raise RuntimeError(json.dumps({'enrollment': json.loads(enrolled.stdout),
                'nativeSshProbe': json.loads(probe.stdout),
                'sourceRoute': f.run(['ip', 'route', 'get', config['address']]).stdout}))
        self.model = Model()
        self.native = Native(f, '/opt/cf015/bin/codex', self.model)
        self.native.identity['exe'] = os.readlink('/proc/' + str(self.native.identity['pid']) + '/exe')
        self.native.stoprecord.write_text(json.dumps(self.native.identity))
        thread = self.native.request('thread/start', {'cwd': str(self.native.home / 'project')})['thread']['id']
        return {'thread': thread, 'nativeIdentity': self.native.identity,
            'kernelBootId': Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
            'operatorSourceHashes': {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                for p in Path(__file__).parent.glob('*.py')},
            'binaryHashes': {n: hashlib.sha256((f.base / 'bin' / n).read_bytes()).hexdigest() for n in ('bridge', 'codex')}}

    def wire(self, value):
        f = self.f
        credential = value.pop('credential', None) or f.subjects['receiver']['credential']
        uid = value.pop('uid', f.subjects['receiver']['uid'])
        source = f.publish(value['wire'])
        response = f.run([*f.prefix(uid), sys.executable, f.helper, '--socket', f.socket,
            '--wire', source, '--credential', credential])
        return json.loads(response.stdout)

    def command(self, value):
        op = value.pop('op')
        if op == 'setup':
            return self.setup(value)
        if op == 'native':
            return self.native.request(value['method'], value.get('params', {}))
        if op == 'complete':
            index = len(self.model.requests)
            admitted = self.native.request('thread/factoryMessage', value['params'])['data']
            self.model.wait_request(index)
            self.model.release(index)
            self.native.event('turn/completed', admitted['turnId'])
            receipt = self.native.request('thread/factoryMessage', value['params'])['data']
            assert receipt['state'] == 'completed'
            return {'turnId': admitted['turnId'], 'receipt': receipt}
        if op == 'wire':
            return self.wire(value)
        if op == 'config':
            p = self.base / 'service/proxy.json'
            config = json.loads(p.read_text())
            config.update(value['changes'])
            self.install(p, json.dumps(config), SERVICE)
            return {'updatedFields': sorted(value['changes'])}
        if op == 'pin':
            self.install(self.base / 'service/known_hosts', value['publicPin'], SERVICE)
            return {'pinInstalled': True}
        if op == 'transport_key':
            self.install(self.base / 'service/client-key', value['privateKey'], SERVICE)
            p = self.base / 'service/proxy.json'
            config = json.loads(p.read_text())
            config['transportKey'] = value['keyId']
            self.install(p, json.dumps(config), SERVICE)
            return {'transportKeyUpdated': True}
        if op == 'unknown_bootstrap':
            f = self.f
            out = f.run([*f.prefix(f.subjects['receiver']['uid']), sys.executable, f.helper,
                '--socket', f.socket, '--bootstrap', self.base / 'receiver/bootstrap', '--forge-bootstrap'])
            return json.loads(out.stdout)
        if op == 'wrong_uid':
            f = self.f
            p = self.base / 'intruder'
            p.mkdir(mode=0o700)
            os.chown(p, 65533, GROUP)
            credential = p / 'credential'
            f.sudo('install', '-o', '65533', '-g', str(GROUP), '-m', '0600',
                f.subjects['receiver']['credential'], credential)
            return self.wire({'wire': value['wire'], 'uid': 65533, 'credential': credential})
        if op == 'untrusted_key':
            f = self.f
            key = self.base / 'service/untrusted-key'
            f.run([*f.prefix(SERVICE), '/usr/bin/ssh-keygen', '-q', '-t', 'ed25519',
                '-N', '', '-f', key])
            p = self.base / 'service/proxy.json'
            config = json.loads(p.read_text())
            original = config['identityFile']
            config['identityFile'] = str(key)
            self.install(p, json.dumps(config), SERVICE)
            try:
                return self.wire({'wire': value['wire']})
            finally:
                config['identityFile'] = original
                self.install(p, json.dumps(config), SERVICE)
        if op == 'probe':
            f = self.f
            f.counter += 1
            stem = self.base / 'service' / ('probe-' + str(f.counter))
            credential = Path(str(stem) + '-credential')
            f.sudo('install', '-o', str(SERVICE), '-g', str(GROUP), '-m', '0600',
                f.subjects['receiver']['credential'], credential)
            spec = {'config': json.loads((self.base / 'service/proxy.json').read_text()),
                'wire': value['wire'], 'peerUid': f.subjects['receiver']['uid'],
                'credential': str(credential), 'record': str(stem) + '-ssh.json', 'mode': value.get('mode')}
            if value.get('held'):
                spec.update(holdReady=str(stem) + '-ready', holdRelease=str(stem) + '-release')
            path = Path(str(stem) + '-spec.json')
            self.install(path, json.dumps(spec), SERVICE)
            argv = [*f.prefix(SERVICE), sys.executable,
                str(Path(__file__).with_name('network_probe.py')), '--spec', str(path)]
            if not value.get('held'):
                return json.loads(f.run(argv, timeout=15).stdout)
            assert self.held is None
            self.held = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            self.held_spec = spec
            deadline = time.monotonic() + 5
            while not Path(spec['holdReady']).exists():
                assert self.held.poll() is None and time.monotonic() < deadline
                time.sleep(.01)
            return {'held': True}
        if op == 'finish_hold':
            assert self.held is not None
            if value.get('release'):
                Path(self.held_spec['holdRelease']).touch()
            stdout, stderr = self.held.communicate(timeout=12)
            assert self.held.returncode == 0 and not stderr, (stdout, stderr)
            self.held = None
            return json.loads(stdout)
        if op == 'collect':
            f = self.f
            s = f.subjects['receiver']
            out = f.run([*f.prefix(s['uid']), f.binary, 'collect-recording', '--native-state',
                f.base / 'receiver/native-ledger', '--grant', f.publish(s['grant']),
                '--socket', f.socket, '--service-uid', str(SERVICE), '--credential', s['credential']])
            return json.loads(out.stdout)
        if op == 'inspect':
            return {'listeners': self.f.run(['ss', '-lntup']).stdout,
                'route': self.f.run(['ip', 'route', 'get', value['hubAddress']]).stdout,
                'nativeIdentity': self.native.identity, 'proxyIdentity': self.proxy_lease,
                'bootId': Path('/proc/sys/kernel/random/boot_id').read_text().strip()}
        if op == 'close':
            return self.close()
        raise RuntimeError('Unknown remote fixture operation.')

    def close(self):
        if self.closed:
            return {'closed': True}
        errors = []
        if self.held is not None:
            try:
                self.command({'op': 'finish_hold', 'release': True})
            except Exception:
                errors.append(traceback.format_exc())
        for close in [self.native.close if self.native is not None else None,
                self.model.close if self.model is not None else None,
                self.stop_proxy if self.f is not None else None]:
            if close is not None:
                try:
                    close()
                except Exception:
                    errors.append(traceback.format_exc())
        if self.f is not None:
            (self.f.e / 'commands.json').write_text(json.dumps(self.f.commands, indent=2) + '\n')
        self.closed = True
        assert not errors, '\n'.join(errors)
        return {'closed': True, 'nativeStopped': self.native is None or self.native.process.poll() is not None,
                'proxyStopped': self.proxy is None,
                'syntheticProviderRequests': len(self.model.requests) if self.model is not None else 0}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', type=Path, required=True)
    args = parser.parse_args()
    os.umask(0o077)
    worker = Worker(args.root)
    try:
        for raw in sys.stdin:
            try:
                result = {'ok': True, 'data': worker.command(json.loads(raw))}
            except Exception:
                result = {'ok': False, 'error': traceback.format_exc()}
            print(json.dumps(result), flush=True)
            if worker.closed:
                break
    finally:
        worker.close()


if __name__ == '__main__':
    main()
