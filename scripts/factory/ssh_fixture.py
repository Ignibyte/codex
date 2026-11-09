"""Owned loopback OpenSSH fixture. It is not evidence of a second physical host."""
import json
import os
from pathlib import Path
import select
import socket
import subprocess
import sys
import time
import uuid

from fleet_fixture import Fleet, SERVICE, GROUP


class RoutedFleet(Fleet):
    def __init__(self, *args):
        super().__init__(*args)
        self.remote_sockets = {}

    def wire(self, name, value, **kwargs):
        original = self.socket
        host = self.subjects[name]['grant']['host']
        self.socket = self.remote_sockets.get(host, original)
        try:
            return super().wire(name, value, **kwargs)
        finally:
            self.socket = original


class SshHost:
    def __init__(self, fleet):
        self.f = fleet
        self.host, self.key_id = str(uuid.uuid4()), str(uuid.uuid4())
        self.root = fleet.base / 'network'
        self.proxy_socket = fleet.base / 'remote.sock'
        self.proxy_state = self.root / 'proxy'
        self.configuration = self.root / 'proxy.json'
        self.sshd, self.proxy = None, None
        self.keys = []
        self.records = []
        self.root_ssh = Path('/run') / ('cf15-ssh-' + uuid.uuid4().hex)
        self.closed = False
        try:
            self.setup()
        except Exception:
            self.close()
            raise

    def setup(self):
        fleet = self.f
        fleet.sudo('install', '-d', '-o', SERVICE, '-g', GROUP, '-m', '0700', self.root)
        fleet.sudo('install', '-d', '-o', 0, '-g', 0, '-m', '0700', self.root_ssh)
        # All keys are synthetic and remain in this private scratch directory.
        self.server_key = self.key('server-key')
        self.client_key = self.key('client-key')
        self.keys.append((self.client_key, self.host, self.key_id))
        with socket.socket() as listener:
            listener.bind(('127.0.0.1', 0))
            self.port = listener.getsockname()[1]
        self.sshd_config = self.root / 'sshd.conf'
        self.authorized = self.root_ssh / 'authorized_keys'
        self.pidfile = self.root / 'sshd.pid'
        self.write_authorized()
        configuration = '\n'.join([
            f'Port {self.port}', 'ListenAddress 127.0.0.1', 'AddressFamily inet',
            f'HostKey {self.server_key}', f'PidFile {self.pidfile}',
            f'AuthorizedKeysFile {self.authorized}',
            'AuthenticationMethods publickey', 'PasswordAuthentication no',
            'KbdInteractiveAuthentication no', 'PubkeyAuthentication yes',
            'PermitRootLogin forced-commands-only', 'AllowUsers root', 'UsePAM yes',
            'StrictModes yes', 'DisableForwarding yes', 'PermitTTY no',
            'PermitTunnel no', 'X11Forwarding no', 'AllowAgentForwarding no',
            'PermitUserEnvironment no', 'PermitUserRC no', 'UseDNS no',
            'LoginGraceTime 5', 'MaxAuthTries 1', 'MaxStartups 4', 'MaxSessions 1',
            'ClientAliveInterval 1', 'ClientAliveCountMax 1', 'LogLevel VERBOSE',
            'PrintMotd no', ''])
        self.sshd_text = configuration
        self.install_text(configuration, self.sshd_config, owner=0)
        fleet.sudo('/usr/bin/sshd', '-t', '-f', self.sshd_config)
        fleet.admin('admit-host', '--state', fleet.state, '--host', self.host,
                    '--key-id', self.key_id, '--bridge-uid', SERVICE)
        self.install_pin()
        self.proxy_config = {'sourceHost': self.host, 'transportKey': self.key_id, 'hub': fleet.host, 'address': '127.0.0.1',
            'port': self.port, 'user': 'root', 'identityFile': str(self.client_key),
            'knownHosts': str(self.root / 'known_hosts'),
            'allowedAgentUids': [fleet.subjects['receiver']['uid']]}
        self.write_proxy_config()
        self.start_sshd()
        self.start_proxy()
        fleet.remote_sockets[self.host] = self.proxy_socket

    def install_text(self, value, destination, owner=SERVICE):
        self.f.counter += 1
        source = self.f.e / f'ssh-public-input-{self.f.counter}.txt'
        source.write_text(value)
        self.f.sudo('install', '-o', owner, '-g', GROUP, '-m', '0600', source, destination)

    def key(self, name):
        path = self.root / name
        self.f.run([*self.f.prefix(SERVICE), '/usr/bin/ssh-keygen', '-q', '-t', 'ed25519',
                    '-N', '', '-C', 'cf-015-owned-fixture', '-f', path])
        return path

    def public_key(self, key):
        return self.f.run([*self.f.prefix(SERVICE), 'cat', str(key) + '.pub']).stdout.strip()

    def write_authorized(self):
        rows = []
        for key, host, key_id in self.keys:
            forced = ' '.join(map(str, ['/usr/bin/setpriv', '--reuid', SERVICE, '--regid', GROUP,
                '--clear-groups', '--no-new-privs', self.f.binary, 'remote-entry', '--state', self.f.state,
                '--host', host, '--key-id', key_id]))
            assert all(character not in forced for character in ['"', '\\', '\n', '$', '`'])
            rows.append(f'restrict,command="{forced}" {self.public_key(key)}')
        self.install_text('\n'.join(rows) + '\n', self.authorized, owner=0)

    def install_pin(self, server_key=None):
        public = self.public_key(server_key or self.server_key)
        self.install_text(f'factory-hub-{self.f.host} {public}\n', self.root / 'known_hosts')

    def write_proxy_config(self):
        self.install_text(json.dumps(self.proxy_config), self.configuration)

    def record(self, pid, label, expected_uid, executable):
        fields = Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()
        lease = {'pid': pid, 'startTicks': fields[19],
            'bootId': Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
            'uid': expected_uid, 'exe': str(executable)}
        record = self.f.e / f'{label}-incarnation-{self.f.counter}.json'
        record.write_text(json.dumps(lease))
        self.records.append(record)
        return record

    def start_sshd(self):
        self.f.counter += 1
        record_path = self.root / f'sshd-process-{self.f.counter}.json'
        specification = self.f.publish({'argv': ['/usr/bin/sshd', '-D', '-e', '-f', str(self.sshd_config)],
            'record': str(record_path), 'cwd': str(self.root),
            'env': {'PATH': '/usr/bin:/bin', 'HOME': str(self.root)}})
        self.sshd_log = self.f.e / f'sshd-{self.f.counter}.log'
        log = self.sshd_log.open('w')
        self.sshd = subprocess.Popen(['sudo', '-n', sys.executable,
            str(Path(__file__).with_name('live_command.py')), '--spec', str(specification)], stdout=log, stderr=log)
        log.close()
        deadline = time.monotonic() + 10
        while self.f.run(['sudo', '-n', 'test', '-f', record_path], expected=None).returncode:
            assert self.sshd.poll() is None, 'The owned sshd stopped.'
            assert time.monotonic() < deadline
            time.sleep(.05)
        lease = json.loads(self.f.sudo('cat', record_path).stdout)
        self.sshd_record = self.f.e / f'sshd-incarnation-{self.f.counter}.json'
        self.sshd_record.write_text(json.dumps(lease))
        while self.f.run(['sudo', '-n', 'test', '-f', self.pidfile], expected=None).returncode:
            assert self.sshd.poll() is None, 'The owned sshd stopped.'
            assert time.monotonic() < deadline
            time.sleep(.05)
        assert int(self.f.sudo('cat', self.pidfile).stdout) == lease['pid']
        self.f.commands.append({'sshdSupervisorPid': self.sshd.pid, 'listener': f'127.0.0.1:{self.port}',
                                'config': str(self.sshd_config), 'incarnation': lease})

    def stop_sshd(self):
        if self.sshd is not None:
            if self.sshd.poll() is None and hasattr(self, 'sshd_record'):
                self.f.sudo(sys.executable, self.f.helper, '--stop-record', self.sshd_record)
            self.f.commands.append({'sshdExit': self.sshd.wait(timeout=10)})
            self.sshd = None
            self.f.sudo('rm', '-f', '--', self.pidfile)

    def start_proxy(self):
        self.f.counter += 1
        record_path = self.root / f'proxy-process-{self.f.counter}.json'
        argv = list(map(str, [self.f.binary, 'connect-host', '--state', self.proxy_state,
            '--socket', self.proxy_socket, '--config', self.configuration]))
        spec = self.f.publish({'argv': argv, 'record': str(record_path), 'cwd': str(self.root),
            'env': {'PATH': '/usr/bin:/bin', 'HOME': str(self.root)}})
        log = (self.f.e / f'proxy-{self.f.counter}.log').open('w')
        self.proxy = subprocess.Popen(list(map(str, [*self.f.prefix(SERVICE), sys.executable,
            Path(__file__).with_name('live_command.py'), '--spec', spec])),
            stdout=subprocess.PIPE, stderr=log, text=True)
        log.close()
        deadline = time.monotonic() + 10
        while self.f.run(['sudo', '-n', 'test', '-f', record_path], expected=None).returncode:
            assert self.proxy.poll() is None, 'The owned proxy stopped.'
            assert time.monotonic() < deadline
            time.sleep(.05)
        lease = json.loads(self.f.sudo('cat', record_path).stdout)
        self.proxy_record = self.f.e / f'proxy-incarnation-{self.f.counter}.json'
        self.proxy_record.write_text(json.dumps(lease))
        assert select.select([self.proxy.stdout], [], [], 10)[0], 'The proxy did not start.'
        ready = json.loads(self.proxy.stdout.readline())
        assert ready['pid'] == lease['pid']
        self.f.commands.append({'command': argv, 'ready': ready, 'incarnation': lease})

    def enroll(self, name='receiver'):
        actor = self.f.subjects[name]
        actor['grant'] = {'seat': actor['seat'], 'host': self.host, 'incarnation': str(uuid.uuid4()),
            'agentUid': actor['uid'], 'role': 'worker', 'projects': ['fixture-project'],
            'recipients': [item['seat'] for item in self.f.subjects.values()],
            'operations': ['observe', 'queue', 'acknowledge']}
        token = self.f.base / f'bootstrap-{name}'
        self.f.admin('bootstrap-remote', '--state', self.f.state, '--grant', self.f.publish(actor['grant']), '--output', token)
        destination = self.f.base / name / 'bootstrap'
        self.f.sudo('mv', token, destination)
        self.f.sudo('chown', f"{actor['uid']}:{GROUP}", destination)
        actor['credential'] = self.f.base / name / 'credential'
        enrolled = self.f.run([*self.f.prefix(actor['uid']), sys.executable, self.f.helper,
            '--socket', self.proxy_socket, '--bootstrap', destination, '--credential-out', actor['credential']])
        assert json.loads(enrolled.stdout)['ok'], enrolled.stdout

    def probe(self, wire, peer_uid, credential=None, mode=None, held=False):
        self.f.counter += 1
        label = f'probe-{self.f.counter}'
        spec = {'config': self.proxy_config.copy(), 'wire': wire, 'peerUid': peer_uid, 'mode': mode,
                'record': str(self.root / (label + '-record.json'))}
        if credential:
            copied = self.root / (label + '-credential')
            self.f.sudo('install', '-o', SERVICE, '-g', GROUP, '-m', '0600', credential, copied)
            spec['credential'] = str(copied)
        if held:
            spec['holdReady'] = str(self.root / (label + '-ready'))
            spec['holdRelease'] = str(self.root / (label + '-release'))
        private = self.root / (label + '-spec.json')
        self.install_text(json.dumps(spec), private)
        command = [*self.f.prefix(SERVICE), sys.executable, Path(__file__).with_name('network_probe.py'), '--spec', private]
        if not held:
            return json.loads(self.f.run(command, timeout=15).stdout)
        process = subprocess.Popen(list(map(str, command)), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.f.children.append(process)
        return process, spec

    def rotate_server_key(self):
        self.stop_sshd()
        new_key = self.key('server-key-2')
        self.sshd_text = self.sshd_text.replace('HostKey ' + str(self.server_key), 'HostKey ' + str(new_key))
        self.server_key = new_key
        self.install_text(self.sshd_text, self.sshd_config, owner=0)
        self.start_sshd()

    def close(self):
        if self.closed:
            return
        self.closed = True
        if self.proxy is not None:
            if self.proxy.poll() is None and hasattr(self, 'proxy_record'):
                self.f.sudo(sys.executable, self.f.helper, '--stop-record', self.proxy_record)
            self.f.commands.append({'proxyExit': self.proxy.wait(timeout=10)})
            self.proxy.stdout.close()
            self.proxy = None
            self.f.sudo('rm', '-f', '--', self.proxy_socket)
        self.stop_sshd()
        assert self.root_ssh.parent == Path('/run') and self.root_ssh.name.startswith('cf15-ssh-')
        self.f.sudo('rm', '-rf', '--', self.root_ssh)
