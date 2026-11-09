#!/usr/bin/env python3
"""Actual second-kernel native journey over private pinned SSH. No paid provider."""
import argparse
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import select
import signal
import re
import shlex
import subprocess
import sys
import tarfile
import time
import traceback
import uuid

from fleet_fixture import GROUP, SERVICE
from native_fixture import Native
from package_control import digest, source_identity
from session_fixture import Model
from ssh_fixture import RoutedFleet, SshHost


class PrivateHub(SshHost):
    def __init__(self, fleet, address):
        self.address = address
        super().__init__(fleet)

    def start_proxy(self):
        # The source proxy runs on the other machine.
        pass

    def start_sshd(self):
        self.sshd_text = self.sshd_text.replace('ListenAddress 127.0.0.1', 'ListenAddress ' + self.address)
        self.install_text(self.sshd_text, self.sshd_config, owner=0)
        super().start_sshd()
        self.f.commands[-1]['listener'] = self.address + ':' + str(self.port)


class SshRelay:
    """Operator-owned TCP route; application SSH still verifies its own host pin."""
    def __init__(self, alias, destination, port, gateway, evidence):
        self.command = ['ssh', '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=yes',
            '-o', 'ExitOnForwardFailure=yes', '-N', '-T', '-R',
            '127.0.0.1:0:' + destination + ':' + str(port), alias]
        self.process = subprocess.Popen(self.command, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
        self.e = evidence
        try:
            assert select.select([self.process.stderr], [], [], 10)[0], 'The private SSH route did not start.'
            line = self.process.stderr.readline()
            match = re.search(r'Allocated port (\d+)', line)
            assert match, line
            self.port = int(match.group(1))
            fields = Path('/proc/' + str(self.process.pid) + '/stat').read_text().rsplit(')', 1)[1].split()
            self.identity = {'pid': self.process.pid, 'startTicks': fields[19],
                'bootId': Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
                'uid': os.getuid(), 'exe': os.readlink('/proc/' + str(self.process.pid) + '/exe')}
            self.record = {'incarnation': self.identity, 'command': self.command,
                'remoteBind': '127.0.0.1:' + str(self.port), 'guestGateway': gateway,
                'destination': destination + ':' + str(port), 'allocation': line}
            (evidence / 'private-ssh-route.json').write_text(json.dumps(self.record, indent=2) + '\n')
        except Exception:
            self.process.stdin = None
            if self.process.poll() is None:
                self.process.terminate()
            self.process.wait(timeout=10)
            self.process.stderr.close()
            raise

    def close(self):
        if self.process.poll() is None:
            fd = os.pidfd_open(self.process.pid)
            try:
                fields = Path('/proc/' + str(self.process.pid) + '/stat').read_text().rsplit(')', 1)[1].split()
                assert fields[19] == self.identity['startTicks']
                assert Path('/proc/sys/kernel/random/boot_id').read_text().strip() == self.identity['bootId']
                assert os.readlink('/proc/' + str(self.process.pid) + '/exe') == self.identity['exe']
                assert Path('/proc/' + str(self.process.pid)).stat().st_uid == self.identity['uid']
                signal.pidfd_send_signal(fd, signal.SIGTERM)
            finally:
                os.close(fd)
        self.record['exitCode'] = self.process.wait(timeout=15)
        self.record['stderrAfterAllocation'] = self.process.stderr.read()
        self.process.stderr.close()
        (self.e / 'private-ssh-route.json').write_text(json.dumps(self.record, indent=2) + '\n')


class Remote:
    def __init__(self, args, evidence):
        self.args, self.e = args, evidence
        self.root = '/tmp/cf15-remote-' + uuid.uuid4().hex
        self.log = (evidence / 'remote-supervisor.log').open('w')
        self.transcript, self.buffer = [], b''
        self.closed_result = None
        argv = self.docker('exec', '-i', args.container, 'python3',
            '/opt/cf015/scripts/remote_worker_fixture.py', '--root', self.root)
        self.process = subprocess.Popen(self.ssh(argv), stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=self.log)
        (evidence / 'remote-run-owner.json').write_text(json.dumps({'root': self.root,
            'container': args.container, 'context': args.context, 'sshAlias': args.ssh_alias,
            'command': argv}, indent=2) + '\n')

    def docker(self, *args):
        return ['docker', '--context', self.args.context, *map(str, args)]

    def ssh(self, argv):
        return ['ssh', '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=yes',
            '-o', 'ConnectTimeout=8', '-T', self.args.ssh_alias,
            'export PATH=/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin\nexec ' + shlex.join(argv)]

    def call(self, op, **value):
        self.process.stdin.write(json.dumps({'op': op, **value}).encode() + b'\n')
        self.process.stdin.flush()
        deadline = time.monotonic() + 70
        while b'\n' not in self.buffer:
            assert time.monotonic() < deadline, 'Remote fixture response timed out: ' + op
            if select.select([self.process.stdout], [], [], .1)[0]:
                part = os.read(self.process.stdout.fileno(), 65536)
                assert part, 'Remote fixture stopped: ' + op
                self.buffer += part
        line, self.buffer = self.buffer.split(b'\n', 1)
        response = json.loads(line)
        # Setup and key commands carry synthetic secrets in stdin. Do not retain them.
        self.transcript.append({'operation': op, 'response': response})
        assert response['ok'], response
        return response['data']

    def close(self):
        try:
            if self.process.poll() is None:
                result = self.call('close')
                self.closed_result = result
                assert result['closed'] and result.get('nativeStopped', True) and result.get('proxyStopped', True)
        finally:
            if not self.process.stdin.closed:
                self.process.stdin.close()
            code = self.process.wait(timeout=20)
            self.process.stdout.close()
            self.log.close()
            (self.e / 'remote-rpc.json').write_text(json.dumps(self.transcript, indent=2) + '\n')
            assert code == 0, 'Remote fixture exit: ' + str(code)
            with (self.e / 'remote-evidence.tar.gz').open('wb') as output:
                subprocess.run(self.ssh(self.docker('exec', self.args.container, 'tar', '-cz',
                    '-C', self.root + '/evidence', '.')), stdout=output, stderr=subprocess.PIPE, check=True, timeout=25)


def main():
    parser = argparse.ArgumentParser()
    for name in ['binary', 'cli', 'ssh-alias', 'context', 'container', 'hub-address', 'remote-address', 'evidence']:
        parser.add_argument('--' + name, required=True)
    parser.add_argument('--ssh-relay-gateway')
    args = parser.parse_args()
    args.evidence = Path(args.evidence).resolve()
    for address in [args.hub_address, args.remote_address]:
        assert ipaddress.ip_address(address).is_private and not ipaddress.ip_address(address).is_loopback
    if args.ssh_relay_gateway:
        assert ipaddress.ip_address(args.ssh_relay_gateway).is_private
    assert len(args.container) == 64 and all(c in '0123456789abcdef' for c in args.container)
    os.umask(0o077)
    before = source_identity()
    fleet, model = RoutedFleet(args.binary, args.evidence), Model()
    host = remote = foreman = relay = None
    firewall = remote_verified = False
    checks, error, cleanup_errors = [], None, []
    label = 'cf15-' + uuid.uuid4().hex[:12]
    rule = None
    try:
        # Only the owned container is changed. No remote user home or Docker default changes.
        shell = ['ssh', '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=yes', '-T', args.ssh_alias]
        docker = ['docker', '--context', args.context]
        def remote_run(argv, **kwargs):
            return subprocess.run([*shell, 'export PATH=/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin\nexec ' + shlex.join(list(map(str, argv)))], check=True, **kwargs)
        info = json.loads(remote_run([*docker, 'inspect', args.container], capture_output=True, text=True).stdout)[0]
        assert info['Id'] == args.container and info['State']['Running']
        assert info['Config']['Labels'].get('codex.factory.cf015') and not info['Mounts']
        assert not info['HostConfig']['PortBindings'] and not info['HostConfig']['Privileged']
        remote_run([*docker, 'exec', args.container, 'mkdir', '-p', '/opt/cf015/bin', '/opt/cf015/scripts'])
        bundle = args.evidence / 'remote-input.tar.gz'
        scripts = ['remote_worker_fixture.py', 'native_fixture.py', 'native_owner.py',
                   'fleet_fixture.py', 'session_fixture.py', 'isolation_client.py', 'live_command.py', 'network_probe.py']
        probe = "import hashlib,json;from pathlib import Path;print(json.dumps({p:hashlib.sha256(Path(p).read_bytes()).hexdigest() for p in ['/opt/cf015/bin/codex','/opt/cf015/bin/codex-factory-bridge'] if Path(p).is_file()}))"
        installed = json.loads(remote_run([*docker, 'exec', args.container, 'python3', '-c', probe], capture_output=True, text=True).stdout)
        with tarfile.open(bundle, 'w:gz') as archive:
            for name, path in [('codex', args.cli), ('codex-factory-bridge', args.binary)]:
                if installed.get('/opt/cf015/bin/' + name) != digest(path):
                    archive.add(Path(path).resolve(), arcname='bin/' + name)
            for name in scripts:
                archive.add(Path(__file__).with_name(name), arcname='scripts/' + name)
        with bundle.open('rb') as source:
            remote_run([*docker, 'exec', '-i', args.container, 'tar', '-xz', '-C', '/opt/cf015'], stdin=source)
        remote_run([*docker, 'exec', args.container, 'chmod', '-R', 'a+rX', '/opt/cf015'])
        fleet.enroll('sender', ['observe', 'queue', 'acknowledge'])
        host = PrivateHub(fleet, '127.0.0.1' if args.ssh_relay_gateway else args.hub_address)
        rule = ['allow', 'from', args.remote_address, 'to', args.hub_address,
                'port', str(host.port), 'proto', 'tcp', 'comment', label]
        if args.ssh_relay_gateway:
            relay = SshRelay(args.ssh_alias, host.address, host.port, args.ssh_relay_gateway, args.evidence)
        else:
            fleet.sudo('ufw', *rule)
            firewall = True
            (args.evidence / 'temporary-firewall-owner.json').write_text(json.dumps({'rule': rule,
                'label': label, 'port': host.port, 'listenerIncarnation': json.loads(host.sshd_record.read_text())}, indent=2) + '\n')
        receiver = fleet.subjects['receiver']
        receiver['grant'] = {'seat': receiver['seat'], 'host': host.host, 'incarnation': str(uuid.uuid4()),
            'agentUid': receiver['uid'], 'role': 'worker', 'projects': ['fixture-project'],
            'recipients': [s['seat'] for s in fleet.subjects.values()],
            'operations': ['observe', 'queue', 'acknowledge']}
        bootstrap = fleet.base / 'remote-bootstrap'
        fleet.admin('bootstrap-remote', '--state', fleet.state, '--grant', fleet.publish(receiver['grant']), '--output', bootstrap)
        def private(path):
            return subprocess.check_output(['sudo', '-n', 'cat', str(path)], text=True)
        remote = Remote(args, args.evidence)
        transport_address = args.ssh_relay_gateway or args.hub_address
        config = {**host.proxy_config, 'address': transport_address, 'port': relay.port if relay else host.port}
        ready = remote.call('setup', grant=receiver['grant'], config=config,
            transportKey=private(host.client_key), knownHosts=private(host.root / 'known_hosts'), bootstrap=private(bootstrap))
        assert ready['kernelBootId'] != Path('/proc/sys/kernel/random/boot_id').read_text().strip()
        assert ready['binaryHashes'] == {'bridge': digest(args.binary), 'codex': digest(args.cli)}
        assert ready['operatorSourceHashes'] == {name: digest(Path(__file__).with_name(name)) for name in scripts}
        remote_verified = True
        (args.evidence / 'remote-ready.json').write_text(json.dumps(ready, indent=2) + '\n')
        checks.append('actual_remote_linux_kernel_and_exact_factory_binaries_are_distinct_from_the_hub')
        foreman = Native(fleet, args.cli, model, name='sender')
        thread = foreman.request('thread/start', {'cwd': str(foreman.home / 'project')})['thread']['id']
        conversation, messages, turns = str(uuid.uuid4()), [], {}
        def send(actor, recipient, kind, reply=None, mode='wake_when_idle'):
            outgoing = {'messageId': str(uuid.uuid4()), 'conversationId': conversation,
                'recipient': fleet.subjects[recipient]['seat'], 'project': 'fixture-project',
                'kind': kind, 'mode': mode, 'replyTo': reply,
                'text': 'Assignment: CF-015 — Remote transport proof\nDo: Reply READY. Use no tools. Do not create another agent.',
                'expiresInSeconds': 3600}
            result = (remote.call('native', method='factory/send', params=outgoing) if actor == 'worker'
                      else foreman.request('factory/send', outgoing))
            assert result['data']['state'] == 'service_accepted'
            messages.append(outgoing)
            return outgoing
        def consume(actor, message):
            params = {'threadId': ready['thread'] if actor == 'worker' else thread,
                'project': 'fixture-project', 'messageId': message['messageId']}
            if actor == 'worker':
                result = remote.call('complete', params=params)
                turns[message['messageId']] = (host.host, result['turnId'])
            else:
                index = len(model.requests)
                admitted = foreman.request('thread/factoryMessage', params)['data']
                model.wait_request(index)
                model.release(index)
                foreman.event('turn/completed', admitted['turnId'])
                assert foreman.request('thread/factoryMessage', params)['data']['state'] == 'completed'
                turns[message['messageId']] = (fleet.host, admitted['turnId'])
        assignment = send('foreman', 'receiver', 'assignment')
        consume('worker', assignment)
        question = send('worker', 'sender', 'clarification', assignment['messageId'])
        consume('foreman', question)
        answer = send('foreman', 'receiver', 'reply', question['messageId'])
        consume('worker', answer)
        result = send('worker', 'sender', 'result', assignment['messageId'])
        consume('foreman', result)
        checks.append('two_machine_assignment_clarification_answer_result_pass_through_actual_native_clients')
        remote.call('collect')
        subject = fleet.subjects['sender']
        fleet.run([*fleet.prefix(subject['uid']), fleet.binary, 'collect-recording', '--native-state',
            fleet.base / 'sender/native-ledger', '--grant', fleet.publish(subject['grant']),
            '--socket', fleet.socket, '--service-uid', SERVICE, '--credential', subject['credential']])
        entries = json.loads(fleet.admin('recording-outbox', '--state', fleet.state / 'recording').stdout)['entries']
        assert len(entries) == 4
        for entry in entries.values():
            event = entry['event']
            assert (event['host'], event['turnId']) == tuple(turns[event['messageId']])
        (args.evidence / 'journey.json').write_text(json.dumps({'messages': messages, 'turns': turns, 'recordings': entries}, indent=2) + '\n')
        checks.append('four_durable_native_turns_and_hub_records_retain_the_correct_source_host')
        def wire(method='sync', **extra):
            receiver['sequence'] = next(s['lastSequence'] for s in fleet.inspect()['seats'] if s['seat'] == receiver['seat'])
            return {'method': method, 'protocol': 1, 'request': fleet.request('receiver'), **extra}
        assert remote.call('untrusted_key', wire=wire())['error'] == 'network_transport_unavailable'
        assert remote.call('wrong_uid', wire=wire())['error'] == 'network_peer_uid_denied'
        assert remote.call('unknown_bootstrap')['error'] == 'bootstrap_unknown'
        checks.append('real_remote_untrusted_key_copied_seat_uid_and_invented_bootstrap_are_refused')
        for mode in ['command', 'forward']:
            denied = remote.call('probe', wire=wire(), mode=mode)
            assert denied['sshExit'] != 0 and denied['response'] is None, denied
        checks.append('the_real_remote_transport_key_cannot_execute_arbitrary_commands_or_forward_ports')
        remote.call('probe', wire=wire(), held=True)
        stalled = remote.call('finish_hold', release=False)
        assert stalled['elapsedSeconds'] < 5 and stalled['response']['response']['error'] == 'network_timeout', stalled
        checks.append('an_actual_remote_authenticated_channel_expires_without_a_frame')
        pending = send('foreman', 'receiver', 'note', mode='queue_only')
        host.stop_sshd()
        assert remote.call('wire', wire=wire('receive', message_id=pending['messageId']))['error'] == 'network_transport_unavailable'
        host.start_sshd()
        recovered = remote.call('wire', wire=wire('receive', message_id=pending['messageId']))
        assert recovered['ok'] and recovered['envelope']['messageId'] == pending['messageId'], recovered
        checks.append('real_network_partition_recovers_the_same_pending_message_without_a_new_assignment')
        host.rotate_server_key()
        assert remote.call('wire', wire=wire())['error'] == 'network_transport_unavailable'
        remote.call('pin', publicPin='factory-hub-' + fleet.host + ' ' + host.public_key(host.server_key) + '\n')
        assert remote.call('wire', wire=wire())['ok']
        new_key, new_id = host.key('remote-client-key-2'), str(uuid.uuid4())
        host.keys.append((new_key, host.host, new_id))
        host.write_authorized()
        fleet.admin('rotate-host', '--state', fleet.state, '--host', host.host, '--key-id', new_id, '--overlap-seconds', '0')
        assert remote.call('wire', wire=wire())['error'] == 'host_credential_inactive'
        remote.call('transport_key', privateKey=private(new_key), keyId=new_id)
        assert remote.call('wire', wire=wire())['ok']
        checks.append('actual_remote_host_pin_and_transport_key_rotations_refuse_old_keys_and_recover')
        audit = remote.call('inspect', hubAddress=transport_address)
        audit['hubListeners'] = fleet.sudo('ss', '-lntp').stdout
        audit['hubRoute'] = fleet.run(['ip', 'route', 'get', args.remote_address]).stdout
        audit['sshdPolicy'] = fleet.sudo('/usr/bin/sshd', '-T', '-f', host.sshd_config).stdout
        owned = [line for line in audit['hubListeners'].splitlines() if 'pid=' + str(json.loads(host.sshd_record.read_text())['pid']) + ',' in line]
        assert len(owned) == 1 and host.address + ':' + str(host.port) in owned[0], owned
        if relay:
            audit['privateSshRoute'] = relay.record
            audit['macRelayListeners'] = remote_run(['/usr/sbin/lsof', '-nP', '-iTCP:' + str(relay.port), '-sTCP:LISTEN'],
                capture_output=True, text=True).stdout
            assert '127.0.0.1:' + str(relay.port) in audit['macRelayListeners']
        assert 'sshd' not in audit['listeners'] and 'codex' not in audit['listeners'], audit['listeners']
        assert 'disableforwarding yes' in audit['sshdPolicy'].lower() and 'permittty no' in audit['sshdPolicy'].lower()
        (args.evidence / 'listener-route-audit.json').write_text(json.dumps(audit, indent=2) + '\n')
        checks.append('actual_two_machine_routes_and_listeners_expose_only_the_pinned_private_forced_ssh_entry')
        offset = len(host.sshd_log.read_text())
        remote.call('probe', wire=wire(), held=True)
        deadline = time.monotonic() + 2
        while 'Starting session:' not in host.sshd_log.read_text()[offset:]:
            assert time.monotonic() < deadline, 'The actual remote channel did not authenticate.'
            time.sleep(.01)
        fleet.admin('revoke-host', '--state', fleet.state, '--host', host.host)
        revoked = remote.call('finish_hold', release=True)
        assert revoked['response']['response']['error'] == 'host_credential_inactive', revoked
        assert remote.call('wire', wire=wire())['error'] == 'host_credential_inactive'
        checks.append('actual_remote_host_revocation_blocks_an_already_authenticated_channel_and_existing_proxy')
        assert before == source_identity(), 'The source changed during the remote test.'
    except Exception:
        error = traceback.format_exc()
    finally:
        for obj in [foreman, remote, model, host, relay]:
            if obj is not None:
                try:
                    obj.close()
                except Exception:
                    cleanup_errors.append(traceback.format_exc())
        if firewall:
            try:
                fleet.sudo('ufw', '--force', 'delete', *rule)
            except Exception:
                cleanup_errors.append(traceback.format_exc())
        try:
            fleet.close()
        except Exception:
            cleanup_errors.append(traceback.format_exc())
        model_requests = {'foreman': len(model.requests),
            'worker': remote.closed_result.get('syntheticProviderRequests') if remote and remote.closed_result else None}
        if error is None and model_requests != {'foreman': 2, 'worker': 2}:
            error = 'Unexpected synthetic provider request count: ' + str(model_requests)
        record = {'passed': error is None and not cleanup_errors, 'error': error,
            'cleanupErrors': cleanup_errors, 'checks': checks, 'sourceUnchanged': before == source_identity(),
            'secondMachine': remote_verified, 'secondKernel': remote_verified, 'remotePlatform': 'isolated Linux guest on selected SSH host',
            'nativeDarwinSupport': False, 'realAccountModelTurns': 0, 'source': before,
            'syntheticModelRequests': model_requests,
            'privateAdministrativeSshRoute': bool(args.ssh_relay_gateway),
            'runnerSha256': digest(__file__), 'externalVmAndContainerCleanupRequired': True}
        (args.evidence / 'result.json').write_text(json.dumps(record, indent=2) + '\n')
    print(json.dumps({k: v for k, v in record.items() if k != 'source'}), flush=True)
    return 0 if record['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
