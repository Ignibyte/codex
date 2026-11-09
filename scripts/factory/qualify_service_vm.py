#!/usr/bin/env python3
"""Actual systemd crashes, reboot, disk pressure and same-epoch package rollback in KVM."""
import argparse
import json
from pathlib import Path
import shlex
import subprocess
import tarfile
import time
import traceback
import uuid

from package_control import ROOT, digest, select_package, source_identity, verify
from service_control import render
from session_fixture import Model


def main():
    p = argparse.ArgumentParser()
    for name in ('package', 'canary', 'vm', 'evidence'):
        p.add_argument('--' + name, type=Path, required=True)
    a = p.parse_args()
    a.package, a.canary, a.vm, a.evidence = [v.resolve() for v in vars(a).values()]
    a.evidence.mkdir(mode=0o700, parents=True, exist_ok=False)
    config = json.loads((a.vm / 'vm.json').read_text())
    commands, checks, rpc_records, error = [], [], [], None
    model = Model()
    before = source_identity()
    name = 'cf-' + uuid.uuid4().hex[:12]
    root = '/var/lib/' + name
    package = '/opt/' + name + '/package'
    canary = '/opt/' + name + '/canary'
    state, profile = root + '/state', root + '/profile'
    units = [name + '-bridge.service', name + '-native.service']
    bridge = package + '/bin/codex-factory-bridge'
    native_socket = '/run/' + name + '-native/native.sock'

    def run(argv, data=None, expected=0, timeout=45):
        r = subprocess.run([*config['ssh'], shlex.join(list(map(str, argv)))],
                           input=data, capture_output=True, timeout=timeout)
        record = {'argv': list(map(str, argv)), 'exit': r.returncode,
                  'stdout': r.stdout.decode(errors='replace'), 'stderr': r.stderr.decode(errors='replace')}
        commands.append(record)
        (a.evidence / 'commands.json').write_text(json.dumps(commands, indent=2))
        if expected is not None:
            assert r.returncode == expected, record
        return r

    def script(code, expected=0, timeout=45):
        return run(['sudo', 'python3', '-c', code], expected=expected, timeout=timeout)

    def unit(role, *argv, expected=0):
        return run(['sudo', 'systemctl', *argv, name + '-' + role + '.service'], expected=expected)

    def show(role):
        return dict(line.split('=', 1) for line in unit(role, 'show', '--property=MainPID,ActiveState,SubState,Result,NRestarts,MemoryMax,TasksMax,CPUQuotaPerSecUSec,ControlGroup').stdout.decode().splitlines())

    def wait_service(role, previous=None):
        deadline = time.monotonic() + 25
        while time.monotonic() < deadline:
            value = show(role)
            if value['ActiveState'] == 'active' and int(value['MainPID']) > 0 and value['MainPID'] != previous:
                return value
            time.sleep(.25)
        raise RuntimeError('service_restart_timeout: ' + str(value))

    def admin(*argv, expected=0):
        r = run(['sudo', 'setpriv', '--reuid', '65534', '--regid', '65534', '--clear-groups',
                 bridge, *argv], expected=expected)
        return json.loads(r.stdout) if r.returncode == 0 else r

    def rpc(method, params):
        deadline = time.monotonic() + 20
        while run(['sudo', 'test', '-S', native_socket], expected=None).returncode:
            assert time.monotonic() < deadline, 'native_socket_start_timeout'
            time.sleep(.15)
        data = json.dumps({'method': method, 'params': params}).encode()
        r = run(['sudo', 'setpriv', '--reuid', '65532', '--regid', '65534', '--clear-groups',
                 'python3', '/opt/' + name + '/vm_service_probe.py', native_socket], data=data)
        record = json.loads(r.stdout)
        rpc_records.append(record)
        (a.evidence / 'rpc.json').write_text(json.dumps(rpc_records, indent=2))
        return record['result']

    try:
        pins = {path: digest(path / 'factory-package.json') for path in (a.package, a.canary)}
        for path, pin in pins.items():
            verify(path, pin)
        assert digest(a.package / 'bin/codex') != digest(a.canary / 'bin/codex')
        # Render against the local pin, then change only the explicitly rooted installation paths.
        metadata = render(a.package, pins[a.package], name, 65534, 65532, state, profile, a.evidence / 'units')
        for path in (a.evidence / 'units').glob('*.service'):
            path.write_text(path.read_text().replace(str(a.package), package))
        bundle = a.evidence / 'install.tar.gz'
        with tarfile.open(bundle, 'w:gz') as tar:
            tar.add(a.package, arcname='package')
            tar.add(a.canary, arcname='canary')
            for script_name in ('vm_service_probe.py', 'session_fixture.py', 'isolation_client.py'):
                tar.add(ROOT / 'scripts/factory' / script_name, arcname=script_name)
        run(['sudo', 'install', '-d', '-m', '0755', '/opt/' + name])
        run(['sudo', 'tar', '-xzf', '-', '-C', '/opt/' + name], data=bundle.read_bytes(), timeout=90)
        run(['sudo', 'install', '-d', '-m', '0711', root])
        run(['sudo', 'useradd', '--system', '--uid', '65532', '--gid', '65534',
             '--no-create-home', '--home-dir', profile, '--shell', '/usr/sbin/nologin', name + '-agent'])
        for directory, uid in ((state, 65534), (profile, 65532)):
            run(['sudo', 'install', '-d', '-o', str(uid), '-g', '65534', '-m', '0700', directory])
        fixture_config = f'''model = "mock-model"
model_provider = "fixture"
approval_policy = "on-request"
sandbox_mode = "read-only"
cli_auth_credentials_store = "file"
[features]
plugins = false
api_key_model_discovery = false
[analytics]
enabled = false
[model_providers.fixture]
name = "Bounded service fixture"
base_url = "http://10.0.2.2:{model.server.server_port}/v1"
wire_api = "responses"
request_max_retries = 0
stream_max_retries = 0
supports_websockets = false
'''
        script(f"from pathlib import Path;import os;p=Path({profile!r})/'config.toml';p.write_text({fixture_config!r});p.chmod(0o600);os.chown(p,65532,65534)")
        host, seat = str(uuid.uuid4()), str(uuid.uuid4())
        grant = {'seat': seat, 'host': host, 'incarnation': str(uuid.uuid4()), 'agentUid': 65532,
                 'role': 'worker', 'projects': ['fixture-project'], 'recipients': [seat], 'operations': ['observe', 'queue', 'acknowledge']}
        script(f"from pathlib import Path;p=Path({root!r})/'grant.json';p.write_text({json.dumps(grant)!r});p.chmod(0o644)")
        admin('bootstrap', '--state', state, '--grant', root + '/grant.json', '--output', state + '/bootstrap')
        for path in (a.evidence / 'units').glob('*.service'):
            run(['sudo', 'tee', '/etc/systemd/system/' + path.name], data=path.read_bytes())
        run(['sudo', 'systemd-analyze', 'verify', *['/etc/systemd/system/' + unit for unit in units]])
        run(['sudo', 'systemctl', 'daemon-reload'])
        run(['sudo', 'systemctl', 'enable', '--now', *units])
        initial = {role: wait_service(role) for role in ('bridge', 'native')}
        run(['sudo', 'mv', state + '/bootstrap', profile + '/bootstrap'])
        run(['sudo', 'chown', '65532:65534', profile + '/bootstrap'])
        seat_prefix = ['sudo', 'setpriv', '--reuid', '65532', '--regid', '65534', '--clear-groups']
        helper = '/opt/' + name + '/isolation_client.py'
        bridge_socket = '/run/' + name + '-bridge/bridge.sock'
        enrolled = run([*seat_prefix, 'python3', helper, '--socket', bridge_socket, '--bootstrap',
                        profile + '/bootstrap', '--credential-out', profile + '/credential'])
        assert json.loads(enrolled.stdout)['ok']
        sequence = 0

        def wire(method, operation='observe', expected_error=None, **extra):
            nonlocal sequence
            sequence += 1
            request = {'protocol': 1, 'sender': seat, 'host': host, 'incarnation': grant['incarnation'],
                       'sequence': sequence, 'project': 'fixture-project', 'recipient': seat, 'operation': operation}
            value = {'method': method, 'protocol': 1, 'request': request, **extra}
            source = root + '/wire.json'
            script(f"from pathlib import Path;p=Path({source!r});p.write_text({json.dumps(value)!r});p.chmod(0o644)")
            response = json.loads(run([*seat_prefix, 'python3', helper, '--socket', bridge_socket,
                                      '--credential', profile + '/credential', '--wire', source]).stdout)
            if expected_error:
                assert response['error'] == expected_error, response
            else:
                assert response['ok'], response
            return response

        for value in initial.values():
            assert value['MemoryMax'] == str(768 * 1024**2) and value['TasksMax'] == '128'
            assert value['CPUQuotaPerSecUSec'] == '1s'
        checks.append({'check': 'actual_systemd_owners_cgroups_and_bounded_resource_policy', 'units': metadata, 'observed': initial})
        thread = rpc('thread/start', {'cwd': profile, 'historyMode': 'legacy'})['thread']['id']
        physical = run(['sudo', 'readlink', native_socket]).stdout.decode().strip()
        for socket_path in (native_socket, physical):
            code = f"import socket\ns=socket.socket(socket.AF_UNIX)\ntry:s.connect({socket_path!r})\nexcept PermissionError as e:assert e.errno==13;print('refused')\nelse:raise RuntimeError('foreign_uid_native_control_admitted')"
            denied = run(['sudo', 'setpriv', '--reuid', '65533', '--regid', '65534', '--clear-groups', 'python3', '-c', code])
            assert denied.stdout.strip() == b'refused'
        turn = rpc('turn/start', {'threadId': thread, 'input': [{'type': 'text', 'text': 'Reply READY. Use no tools.'}]})['turn']['id']
        model.wait_request(0)
        model.release(0)
        deadline = time.monotonic() + 25
        while True:
            history = rpc('thread/read', {'threadId': thread, 'includeTurns': True})['thread']
            if any(t['id'] == turn and t['status'] == 'completed' for t in history['turns']):
                break
            assert time.monotonic() < deadline
            time.sleep(.1)
        messages = []
        for disposition in ('pending', 'consumed', 'completed'):
            envelope = {'protocol': 1, 'messageId': str(uuid.uuid4()), 'conversationId': str(uuid.uuid4()),
                        'assignmentId': None, 'replyTo': None, 'sender': seat, 'incarnation': grant['incarnation'],
                        'recipient': seat, 'project': 'fixture-project', 'kind': 'note', 'mode': 'queue_only',
                        'createdAt': int(time.time()), 'expiresAt': int(time.time()) + 3600,
                        'text': 'Read the fixture record.', 'artifactRefs': []}
            wire('send', 'queue', envelope=envelope)
            messages.append(envelope['messageId'])
            if disposition != 'pending':
                wire('receive', message_id=envelope['messageId'])
                for receipt_state, writer in [('consumed', 'runtime'), *([('completed', 'worker')] if disposition == 'completed' else [])]:
                    wire('ack', 'acknowledge', receipt={'protocol': 1, 'messageId': envelope['messageId'],
                         'state': receipt_state, 'writer': writer, 'turnId': turn})
        completed_receipt = next(m for m in admin('inspect', '--state', state)['mail'] if m['messageId'] == messages[2])
        for role in ('bridge', 'native'):
            previous = show(role)['MainPID']
            unit(role, 'kill', '--kill-whom=main', '--signal=SIGKILL')
            changed = wait_service(role, previous)
            checks.append({'check': 'actual_crash_restart_' + role, 'oldPid': previous, 'new': changed})
        resumed = rpc('thread/resume', {'threadId': thread})['thread']
        assert turn in json.dumps(resumed) and 'fixture-result-0' in json.dumps(resumed)
        assert len(model.requests) == 1
        # A genuinely interrupted in-flight native request remains history, with no new provider request.
        active = rpc('turn/start', {'threadId': thread, 'input': [{'type': 'text', 'text': 'Reply READY. Use no tools.'}]})['turn']['id']
        model.wait_request(1)
        old_boot = run(['cat', '/proc/sys/kernel/random/boot_id']).stdout.decode().strip()
        key_before = run(['sudo', 'sha256sum', state + '/service.key']).stdout.decode().split()[0]
        previous = show('native')['MainPID']
        unit('native', 'kill', '--kill-whom=main', '--signal=SIGKILL')
        wait_service('native', previous)
        model.release(1)
        rpc('thread/resume', {'threadId': thread})
        assert len(model.requests) == 2
        # Reboot only this explicitly identified owned guest.
        run(['sudo', 'systemctl', 'reboot'], expected=None)
        deadline = time.monotonic() + 100
        while time.monotonic() < deadline:
            r = run(['cat', '/proc/sys/kernel/random/boot_id'], expected=None, timeout=8)
            if r.returncode == 0 and r.stdout.decode().strip() != old_boot:
                new_boot = r.stdout.decode().strip()
                break
            time.sleep(1)
        else:
            raise RuntimeError('guest_reboot_timeout')
        for role in ('bridge', 'native'):
            wait_service(role)
        key_after = run(['sudo', 'sha256sum', state + '/service.key']).stdout.decode().split()[0]
        assert key_before != key_after
        inspected = admin('inspect', '--state', state)
        assert all(s['revoked'] for s in inspected['seats'])
        raw_registry = json.loads(run(['sudo', 'cat', state + '/registry.json']).stdout)
        assert not raw_registry['bootstraps']
        mail = {m['messageId']: m for m in inspected['mail']}
        assert mail[messages[0]]['state'] == 'quarantined'
        assert mail[messages[1]]['reconcileRequired']
        assert mail[messages[2]] == completed_receipt
        wire('peers', expected_error='credential_denied')
        assert any(x['event']['kind'] == 'boot_recovery' for x in inspected['audit'])
        resumed = rpc('thread/resume', {'threadId': thread})['thread']
        assert turn in json.dumps(resumed) and active in json.dumps(resumed)
        assert len(model.requests) == 2
        checks.append({'check': 'actual_kernel_reboot_fences_bootstraps_rotates_key_and_never_replays_native_turns',
                       'previousBoot': old_boot, 'boot': new_boot, 'thread': thread, 'completedTurn': turn, 'interruptedTurn': active})
        # A second actual package serves the same native history, then rolls back under one service owner.
        selector = a.evidence / 'selection'
        select_package(selector, a.package, pins[a.package])
        for phase, local, remote in (('canary', a.canary, canary), ('rollback', a.package, package)):
            unit('native', 'stop')
            assert show('native')['MainPID'] == '0'
            select_package(selector, local, pins[local])
            native_unit = (a.evidence / 'units' / units[1]).read_text().replace(package + '/bin/codex', remote + '/bin/codex')
            run(['sudo', 'tee', '/etc/systemd/system/' + units[1]], data=native_unit.encode())
            run(['sudo', 'systemctl', 'daemon-reload'])
            unit('native', 'start')
            observed = wait_service('native')
            history = rpc('thread/resume', {'threadId': thread})['thread']
            assert turn in json.dumps(history) and 'fixture-result-0' in json.dumps(history)
            assert len(model.requests) == 2
            checks.append({'check': 'same_epoch_' + phase + '_one_service_owner_same_history', 'service': observed,
                           'cliSha256': digest(local / 'bin/codex')})
        # Fill only a private 32 KiB tmpfs, never the guest or host root filesystem.
        tiny = root + '/tiny'
        run(['sudo', 'install', '-d', '-m', '0700', tiny])
        run(['sudo', 'mount', '-t', 'tmpfs', '-o', 'size=32k,mode=0700,uid=65534,gid=65534', 'tmpfs', tiny])
        script(f"from pathlib import Path\nf=open({tiny + '/fill'!r},'wb',buffering=0)\ntry:\n while True:f.write(b'x'*4096)\nexcept OSError as e:assert e.errno==28\nfinally:f.close()")
        refused = admin('backup', '--state', state, '--output', tiny + '/backup.json', expected=1)
        assert b'No space left on device' in refused.stderr
        assert admin('inspect', '--state', state) == inspected
        run(['sudo', 'umount', tiny])
        checks.append('actual_enospc_on_private_filesystem_fails_without_altering_live_workflow')
        # Bounded restart budget: kill exactly the unit main process until systemd refuses the loop.
        for _ in range(3):
            previous = show('bridge')['MainPID']
            if previous == '0':
                break
            unit('bridge', 'kill', '--kill-whom=main', '--signal=SIGKILL')
            time.sleep(2.3)
        limited = show('bridge')
        journal = run(['sudo', 'journalctl', '--no-pager', '-o', 'cat', '-u', units[0]]).stdout.decode()
        assert limited['ActiveState'] == 'failed' and limited['MainPID'] == '0', limited
        assert limited['NRestarts'] == '3' and 'Start request repeated too quickly.' in journal
        unit('bridge', 'reset-failed')
        unit('bridge', 'start')
        wait_service('bridge')
        checks.append({'check': 'actual_crash_loop_stops_at_start_limit_and_explicit_operator_recovery', 'observed': limited})
    except Exception:
        error = traceback.format_exc()
    finally:
        try:
            run(['sudo', 'systemctl', 'status', '--no-pager', *units], expected=None)
            script(f"""from pathlib import Path
for p in Path({profile!r}).rglob('*.log'):
 if p.stat().st_size < 2*1024*1024:
  print(str(p),p.read_text(errors='replace'))
print('sockets',list(Path('/run').glob({name + '*/*'!r})))
""", expected=None)
            run(['sudo', 'journalctl', '--no-pager', '-u', units[0], '-u', units[1]], expected=None)
            run(['sudo', 'systemctl', 'disable', '--now', *units], expected=None)
            run(['sudo', 'umount', root + '/tiny'], expected=None)
            run(['sudo', 'rm', '-f', *['/etc/systemd/system/' + unit for unit in units]], expected=None)
            run(['sudo', 'systemctl', 'daemon-reload'], expected=None)
            run(['sudo', 'userdel', name + '-agent'], expected=None)
            run(['sudo', 'rm', '-rf', '--', root, '/opt/' + name], expected=None)
        except Exception:
            error = (error or '') + '\ncleanup: ' + traceback.format_exc()
        model.close()
    record = {'passed': error is None, 'checks': checks, 'error': error,
              'command': ['python3', *map(str, __import__('sys').argv)],
              'source': before, 'sourceUnchanged': before == source_identity(),
              'providerRequests': len(model.requests), 'realAccountTurns': 0,
              'vm': config, 'packageSha256': digest(a.package / 'factory-package.json')}
    if not record['sourceUnchanged']:
        record['passed'] = False
    (a.evidence / 'result.json').write_text(json.dumps(record, indent=2))
    print(json.dumps({'passed': record['passed'], 'checks': len(checks), 'error': error}))
    return 0 if record['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
