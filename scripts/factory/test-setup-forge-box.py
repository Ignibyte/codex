#!/usr/bin/env python3
"""Owned local SSH fixture only. Run as root in a private mount namespace."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import select
import shutil
import socket
import stat
import subprocess
import sys
import time
import uuid

SERVICE, AGENT = 65534, 65532


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--root', required=True, type=Path)
    p.add_argument('--dist', required=True, type=Path)
    a = p.parse_args()
    assert os.geteuid() == 0
    assert os.readlink('/proc/self/ns/mnt') != os.readlink('/proc/1/ns/mnt'), 'private mount namespace required'
    root = a.root
    assert root.is_absolute() and root.parent == Path('/tmp') and root.name.startswith('setup-proof-')
    root.mkdir(mode=0o755)
    os.umask(0o022)
    checks, processes, hub_server, proxy_server = [], [], None, None
    bindir = root / 'bin'; bindir.mkdir()
    binary = bindir / 'codex-factory-bridge'
    shutil.copyfile(a.dist / 'bin' / binary.name, binary); binary.chmod(0o555)
    script = bindir / 'setup-forge-box.py'
    shutil.copyfile(a.dist / script.name, script); script.chmod(0o555)
    def mkdir(path, uid=SERVICE, mode=0o700, gid=AGENT):
        path.mkdir(parents=True, exist_ok=True); os.chown(path, uid, gid); path.chmod(mode)
    for host in ('hub', 'dev3'):
        base = root / host; base.mkdir(mode=0o755)
        mkdir(base / 'state', mode=0o2710)
        mkdir(base / 'config', mode=0o2750)
        mkdir(base / 'run', mode=0o2710)
        mkdir(base / 'config/credentials', uid=AGENT)
        mkdir(base / 'state/native', mode=0o710)
        for role in (('spec-manager', 'foreman-bot') if host == 'hub' else ('pilot-seat',)):
            mkdir(base / 'state/native' / role, uid=AGENT)
    hub, dev = root / 'hub', root / 'dev3'
    def command(args, uid=SERVICE, expected=0, json_output=True):
        prefix = ['/usr/bin/setpriv', '--reuid', str(uid), '--regid', str(uid), '--groups', str(AGENT)]
        r = subprocess.run([*prefix, *map(str, args)], capture_output=True, text=True, timeout=20,
                           env={'PATH': '/usr/bin:/bin', 'HOME': str(root), 'TMPDIR': '/tmp'})
        assert r.returncode == expected, f'fixture_command_exit_{r.returncode}: {r.stderr[:180]}'
        return json.loads(r.stdout if expected == 0 else r.stderr) if json_output else r
    def setup(*args, uid=SERVICE, expected=0):
        return command([sys.executable, '-B', script, *args], uid, expected)
    def service(args, uid=SERVICE):
        log = (root / ('process-' + uuid.uuid4().hex + '.log')).open('w')
        prefix = ['/usr/bin/setpriv', '--reuid', str(uid), '--regid', str(uid), '--groups', str(AGENT)]
        proc = subprocess.Popen([*prefix, *map(str, args)], stdout=subprocess.PIPE, stderr=log, text=True)
        log.close(); processes.append(proc)
        assert select.select([proc.stdout], [], [], 10)[0], 'service readiness timeout'
        ready = json.loads(proc.stdout.readline()); assert ready['ready']
        return proc
    def stop(proc, socket_path=None):
        if proc.poll() is None: proc.terminate()
        proc.wait(timeout=10)
        if proc.stdout: proc.stdout.close()
        if socket_path and socket_path.exists(): socket_path.unlink()
    try:
        # Init does not launch a listener; SSH setup is performed by the fixture owner.
        with socket.socket() as s:
            s.bind(('127.0.0.1', 0)); port = s.getsockname()[1]
        init_args = ['init', '--bin-dir', bindir, '--state-root', hub / 'state', '--config-root', hub / 'config',
                     '--run-root', hub / 'run', '--remote-state-root', dev / 'state',
                     '--remote-config-root', dev / 'config', '--remote-run-root', dev / 'run',
                     '--agent-uid', AGENT, '--remote-agent-uid', AGENT, '--remote-service-uid', SERVICE,
                     '--ssh-address', '127.0.0.1', '--ssh-port', port, '--ssh-user', 'root', '--service-user', 'nobody']
        result = setup(*init_args); assert result['seats'] == 3 and result['servicesStarted'] == 0
        assert not (hub / 'run/bridge.sock').exists()
        meta = json.loads((hub / 'config/setup.json').read_text())
        before = hashlib.sha256((hub / 'state/service/registry.json').read_bytes()).digest()
        assert setup(*init_args, expected=1)['error'] == 'setup_already_initialized'
        assert before == hashlib.sha256((hub / 'state/service/registry.json').read_bytes()).digest()
        checks.append('init_stages_three_seats_without_services_and_repeat_refuses_without_mutation')
        setup('keygen', '--config-root', dev / 'config')
        client_pub = hub / 'config/operator/dev3.pub'
        shutil.copyfile(dev / 'config/transport/dev3-ed25519.pub', client_pub)
        os.chown(client_pub, SERVICE, AGENT); client_pub.chmod(0o600)
        setup('ssh-config', '--config-root', hub / 'config', '--client-public-key', client_pub)
        # sshd StrictModes disallows /tmp ancestors, even sticky root-owned /tmp.
        # The fixture entry point provides a private /run mount as well.
        ssh = Path('/run') / root.name; ssh.mkdir(mode=0o700)
        subprocess.run(['/usr/bin/ssh-keygen', '-q', '-t', 'ed25519', '-N', '', '-f', str(ssh / 'server-key')], check=True)
        pub = (ssh / 'server-key.pub').read_text().split()
        pin = dev / 'config/transport/known_hosts'
        pin.write_text(f"factory-hub-{meta['hub']} {pub[0]} {pub[1]}\n")
        os.chown(pin, SERVICE, AGENT); pin.chmod(0o600)
        shutil.copyfile(hub / 'config/operator/proxy.json', dev / 'config/transport/proxy.json')
        os.chown(dev / 'config/transport/proxy.json', SERVICE, AGENT)
        (dev / 'config/transport/proxy.json').chmod(0o600)
        shutil.copyfile(hub / 'config/pilot-seat.binding.json', dev / 'config/pilot-seat.binding.json')
        os.chown(dev / 'config/pilot-seat.binding.json', SERVICE, AGENT)
        (dev / 'config/pilot-seat.binding.json').chmod(0o640)
        auth = ssh / 'authorized_keys'
        shutil.copyfile(hub / 'config/operator/authorized_keys.fragment', auth); auth.chmod(0o600)
        # Test the generated sudo forced command with the nologin service account.
        # No host sudoers file is edited: this bind exists only in this namespace.
        sudoers = ssh / 'sudoers'
        sudoers.write_text('Defaults env_reset\nroot ALL=(ALL:ALL) NOPASSWD: ALL\n' +
                           (hub / 'config/operator/fleet-bridge-remote.sudoers').read_text())
        sudoers.chmod(0o440)
        subprocess.run(['/usr/bin/visudo', '-c', '-f', str(sudoers)], check=True, stdout=subprocess.DEVNULL)
        subprocess.run(['mount', '--bind', str(sudoers), '/etc/sudoers'], check=True)
        sshconf = ssh / 'sshd.conf'
        sshconf.write_text('\n'.join([f'Port {port}', 'ListenAddress 127.0.0.1', f'HostKey {ssh}/server-key',
            f'PidFile {ssh}/sshd.pid', f'AuthorizedKeysFile {auth}', 'AllowUsers root',
            'PermitRootLogin forced-commands-only', 'UsePAM yes', 'PasswordAuthentication no',
            'KbdInteractiveAuthentication no', 'PubkeyAuthentication yes', 'StrictModes yes',
            'DisableForwarding yes', 'PermitTTY no', 'PermitUserRC no', 'PermitUserEnvironment no',
            'PrintMotd no', 'UseDNS no', 'LogLevel VERBOSE', '']))
        subprocess.run(['/usr/bin/sshd', '-t', '-f', str(sshconf)], check=True)
        log = (ssh / 'sshd.log').open('w')
        sshd = subprocess.Popen(['/usr/bin/sshd', '-D', '-e', '-f', str(sshconf)], stdout=log, stderr=log)
        log.close(); processes.append(sshd)
        deadline = time.monotonic() + 5
        while not (ssh / 'sshd.pid').exists():
            assert sshd.poll() is None and time.monotonic() < deadline
            time.sleep(.02)
        hub_server = service([binary, 'serve', '--state', hub / 'state/service', '--socket', hub / 'run/bridge.sock'])
        proxy_server = service([binary, 'connect-host', '--state', dev / 'state/proxy', '--socket', dev / 'run/remote.sock',
                                '--config', dev / 'config/transport/proxy.json'])
        probe = ['/usr/bin/ssh', '-F', '/dev/null', '-T', '-oBatchMode=yes', '-oIdentitiesOnly=yes',
                 '-oIdentityAgent=none', '-oStrictHostKeyChecking=yes',
                 '-oUserKnownHostsFile=' + str(pin), '-oHostKeyAlias=factory-hub-' + meta['hub'],
                 '-i', str(dev / 'config/transport/dev3-ed25519'), '-p', str(port), 'root@127.0.0.1', 'codex-factory-v1']
        r = subprocess.run(['/usr/bin/setpriv', '--reuid', str(SERVICE), '--regid', str(SERVICE), '--groups', str(AGENT),
                            *probe], input='{}\n', capture_output=True, text=True, timeout=10)
        assert r.returncode == 0, 'ssh_fixture_probe: ' + r.stderr[:600]
        checks.append('real_pinned_OpenSSH_generated_restricted_sudo_entry_and_nologin_proxy_start')
        def bind(role): return (dev if role == 'pilot-seat' else hub) / ('config/' + role + '.binding.json')
        for role in ('spec-manager', 'foreman-bot', 'pilot-seat'):
            staged = setup('bootstrap', '--config-root', hub / 'config', '--role', role)
            dst = (dev if role == 'pilot-seat' else hub) / ('config/credentials/' + role + '.bootstrap')
            shutil.copyfile(staged['bootstrapFile'], dst); os.chown(dst, AGENT, AGENT); dst.chmod(0o600)
            assert setup('enroll', '--binding', bind(role), '--bootstrap', dst, uid=AGENT)['enrolled']
        checks.append('three_real_enrollments_local_pair_and_remote_pilot_with_separate_service_endpoint_uids')
        registry = command([binary, 'inspect', '--state', hub / 'state/service'])
        assert len(registry['seats']) == 3
        edges = [('spec-manager', 'foreman-bot'), ('foreman-bot', 'spec-manager'),
                 ('foreman-bot', 'pilot-seat'), ('pilot-seat', 'foreman-bot')]
        sent = []
        for sender, receiver in edges:
            out = setup('send', '--bin-dir', bindir, '--binding', bind(sender), '--recipient', meta['seats'][receiver], uid=AGENT)
            mid = out['messageId']; assert out['state'] == 'service_accepted'
            received = setup('receive', '--binding', bind(receiver), '--message', mid, uid=AGENT)
            status = setup('status', '--binding', bind(sender), '--message', mid, '--recipient', meta['seats'][receiver], uid=AGENT)
            assert status['state'] == 'delivered' and len(status['receipts']) == 2
            assert [(r['state'], r['writer']) for r in received['receipts']] == [('service_accepted', 'service'), ('delivered', 'inbox')]
            sent.append((sender, receiver, mid))
        checks.append('all_four_directed_edges_send_receive_and_read_authentic_ordered_receipts_no_model')
        for sender, receiver in [('spec-manager', 'pilot-seat'), ('pilot-seat', 'spec-manager')]:
            assert setup('forbidden', '--binding', bind(sender), '--recipient', meta['seats'][receiver], uid=AGENT)['forbiddenRefused']
        checks.append('both_ungranted_cross_seat_directions_refused')
        stop(hub_server, hub / 'run/bridge.sock')
        hub_server = service([binary, 'serve', '--state', hub / 'state/service', '--socket', hub / 'run/bridge.sock'])
        for sender, receiver, mid in sent:
            replay = setup('resend', '--bin-dir', bindir, '--binding', bind(sender), '--message', mid, uid=AGENT)
            assert replay['messageId'] == mid and replay['state'] == 'delivered'
            received = setup('receive', '--binding', bind(receiver), '--message', mid, uid=AGENT)
            assert len(received['receipts']) == 2
        registry = command([binary, 'inspect', '--state', hub / 'state/service'])
        assert len(registry['mail']) == 4
        assert all(x['state'] == 'delivered' and len(x['receipts']) == 2 for x in registry['mail'])
        checks.append('same_state_serve_restart_replay_keeps_four_unique_messages_and_single_delivery_receipts')
        threads = hub / 'config/operator/threads.json'
        threads.write_text(json.dumps({r: str(uuid.uuid4()) for r in meta['seats']}))
        os.chown(threads, SERVICE, AGENT); threads.chmod(0o600)
        assert setup('routes', '--config-root', hub / 'config', '--threads', threads)['suppliedThreadIds'] == 3
        routes = json.loads((hub / 'config/routes.json').read_text())
        assert routes['enabled'] is False and routes['pilot_seat'] == 'dev-3'
        for r in meta['seats']:
            assert uuid.UUID(routes['endpoints'][r]['thread_id']).int
            file = bind(r); assert file.stat().st_uid == SERVICE and stat.S_IMODE(file.stat().st_mode) == 0o640
            credential = Path(json.loads(file.read_text())['credential'])
            assert credential.stat().st_uid == AGENT and stat.S_IMODE(credential.stat().st_mode) == 0o600
        checks.append('disabled_routes_all_uuid_fields_bound_and_binding_credential_owners_modes_correct')
        print(json.dumps({'passed': True, 'checks': checks, 'seats': 3, 'directedEdges': 4,
                          'durableMessagesAfterRestart': 4, 'receiptsPerMessage': 2,
                          'serviceUid': SERVICE, 'endpointUid': AGENT,
                          'transport': 'actual loopback OpenSSH; generated sudo forced command; one kernel',
                          'modelsStarted': 0, 'realFleetHostsTested': False}, indent=2), flush=True)
    finally:
        for proc in reversed(processes):
            if proc.poll() is None: proc.terminate()
            proc.wait(timeout=10)
            if proc.stdout and not proc.stdout.closed: proc.stdout.close()


if __name__ == '__main__':
    main()
