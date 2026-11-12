#!/usr/bin/env python3
"""Explicit operator steps for the existing bridge. Never starts a service."""
import argparse
import json
import os
from pathlib import Path
import re
import socket
import stat
import struct
import subprocess
import sys
import time
import uuid

ROLES = ('spec-manager', 'foreman-bot', 'pilot-seat')
EDGES = {'spec-manager': ['foreman-bot'],
         'foreman-bot': ['spec-manager', 'pilot-seat'], 'pilot-seat': ['foreman-bot']}
PROJECT = '12c0b4e6-82d1-4840-a643-d1dfdb4ccf68'


def require(condition, code):
    if not condition:
        raise RuntimeError(code)


def absolute(value):
    p = Path(value)
    require(p.is_absolute() and re.fullmatch(r'[A-Za-z0-9/._-]+', str(p))
            and '..' not in p.parts, 'absolute_simple_path_required')
    return p


def read(path):
    require(not path.is_symlink() and path.is_file(), 'regular_file_required')
    require(path.stat().st_size <= 65536, 'input_bound')
    return json.loads(path.read_text())


def write(path, value, mode=0o600, replace=False):
    data = json.dumps(value, indent=2) + '\n' if not isinstance(value, str) else value
    target = path.with_name('.write-' + uuid.uuid4().hex) if replace else path
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode)
    with os.fdopen(fd, 'w') as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    target.chmod(mode)
    if replace:
        os.replace(target, path)


def directory(path, mode=0o700):
    if not path.exists():
        path.mkdir(mode=mode)
        path.chmod(mode)
    require(not path.is_symlink() and path.is_dir() and path.stat().st_uid == os.geteuid(),
            'directory_owner_mismatch')
    require(stat.S_IMODE(path.stat().st_mode) == mode, 'directory_mode_mismatch')


def bridge(binary, *args):
    p = subprocess.run([str(binary), *map(str, args)], capture_output=True, text=True, timeout=15)
    if p.returncode:
        # Never forward subprocess output that could contain private wire data.
        codes = re.findall(r'^Error: ([a-z][a-z0-9_]+)\s*$', p.stderr, re.M)
        raise RuntimeError(codes[-1] if codes else 'bridge_command_failed')
    return json.loads(p.stdout)


def load_setup(config):
    m = read(config / 'setup.json')
    require(m['serviceUid'] == os.geteuid(), 'run_as_service_user')
    return m


def mint(m, role):
    binary = Path(m['binDir']) / 'codex-factory-bridge'
    service = Path(m['stateRoot']) / 'service'
    enrolled = bridge(binary, 'inspect', '--state', service)['seats']
    require(not any(x['seat'] == m['seats'][role] for x in enrolled), 'seat_already_enrolled')
    previous = m['pending'].get(role)
    if previous and previous['expiresAt'] > int(time.time()) and Path(previous['path']).is_file():
        return previous
    path = Path(m['stateRoot']) / 'pending' / (role + '-' + uuid.uuid4().hex + '.bootstrap')
    reply = bridge(binary, 'bootstrap-remote' if role == 'pilot-seat' else 'bootstrap',
                   '--state', service, '--grant', Path(m['configRoot']) / 'grants' / (role + '.json'),
                   '--output', path, '--bootstrap-ttl', 300, '--credential-ttl', 3600)
    m['pending'][role] = {'path': str(path), 'expiresAt': reply['expiresAt']}
    return m['pending'][role]


def initialize(a):
    cfg, state, run = map(absolute, (a.config_root, a.state_root, a.run_root))
    binary_dir = absolute(a.bin_dir)
    require(os.geteuid() != 0 and a.agent_uid not in (0, os.geteuid()), 'separate_uids_required')
    require(a.remote_agent_uid not in (0, a.remote_service_uid), 'separate_remote_uids_required')
    require(a.remote_service_uid == os.geteuid(), 'routes_require_matching_service_uid_on_both_hosts')
    require(not (cfg / '.setup-started').exists(), 'setup_already_initialized')
    directory(cfg, 0o2750)
    directory(state, 0o2710)
    directory(run, 0o2710)
    write(cfg / '.setup-started', 'Explicit setup intent; rerun refuses.\n')
    for p in (cfg / 'grants', cfg / 'operator', cfg / 'transport', state / 'service', state / 'pending'):
        directory(p)
    seats = {r: str(uuid.uuid4()) for r in ROLES}
    hub, remote, key = [str(uuid.uuid4()) for _ in range(3)]
    m = dict(binDir=str(binary_dir), configRoot=str(cfg), stateRoot=str(state), runRoot=str(run),
             remoteConfigRoot=str(absolute(a.remote_config_root)), remoteStateRoot=str(absolute(a.remote_state_root)),
             remoteRunRoot=str(absolute(a.remote_run_root)), serviceUid=os.geteuid(),
             remoteServiceUid=a.remote_service_uid, agentUid=a.agent_uid, remoteAgentUid=a.remote_agent_uid,
             hub=hub, remoteHost=remote, transportKey=key, seats=seats, pending={}, project=PROJECT,
             sshUser=a.ssh_user, serviceUser=a.service_user)
    for role in ROLES:
        remote_seat = role == 'pilot-seat'
        grant = dict(seat=seats[role], host=remote if remote_seat else hub,
                     incarnation=str(uuid.uuid4()), agentUid=a.remote_agent_uid if remote_seat else a.agent_uid,
                     role={'spec-manager': 'manager', 'foreman-bot': 'foreman', 'pilot-seat': 'worker'}[role],
                     projects=[PROJECT], recipients=sorted([seats[role], *[seats[r] for r in EDGES[role]]]),
                     operations=['observe', 'queue', 'acknowledge'])
        write(cfg / 'grants' / (role + '.json'), grant)
        endpoint_cfg = Path(m['remoteConfigRoot']) if remote_seat else cfg
        endpoint_state = Path(m['remoteStateRoot']) if remote_seat else state
        endpoint_run = Path(m['remoteRunRoot']) if remote_seat else run
        binding = dict(serviceUid=m['remoteServiceUid'] if remote_seat else m['serviceUid'],
                       socket=str(endpoint_run / ('remote.sock' if remote_seat else 'bridge.sock')),
                       credential=str(endpoint_cfg / 'credentials' / (role + '.json')),
                       state=str(endpoint_state / 'native' / role), grant=grant)
        write(cfg / (role + '.binding.json'), binding, 0o640)
    # The first local bootstrap initializes the existing registry and service MAC key.
    token = state / 'pending' / ('spec-manager-' + uuid.uuid4().hex + '.bootstrap')
    reply = bridge(binary_dir / 'codex-factory-bridge', 'bootstrap', '--state', state / 'service',
                   '--grant', cfg / 'grants/spec-manager.json', '--output', token,
                   '--bootstrap-ttl', 300, '--credential-ttl', 3600)
    m['pending']['spec-manager'] = dict(path=str(token), expiresAt=reply['expiresAt'])
    bridge(binary_dir / 'codex-factory-bridge', 'admit-host', '--state', state / 'service',
           '--host', remote, '--key-id', key, '--bridge-uid', a.remote_service_uid, '--ttl', 86400)
    proxy = dict(sourceHost=remote, transportKey=key, hub=hub, address=a.ssh_address,
                 port=a.ssh_port, user=a.ssh_user,
                 identityFile=str(Path(m['remoteConfigRoot']) / 'transport/dev3-ed25519'),
                 knownHosts=str(Path(m['remoteConfigRoot']) / 'transport/known_hosts'),
                 allowedAgentUids=[a.remote_agent_uid])
    write(cfg / 'operator/proxy.json', proxy)
    routes = dict(version=1, enabled=False, project=PROJECT, pilot_seat='dev-3', epoch=str(uuid.uuid4()),
                  service_uid=m['serviceUid'], smoke_reference=None,
                  application_journals={'spec-manager': '/home/ai/fleet-spec-project/spec-bridge',
                                        'foreman-bot': '/home/ai/fleet-spec-project/pilot-driver',
                                        'pilot-seat': '/home/ai/fleet-spec-project/pilot-bridge'},
                  endpoints={r: dict(seat_id=seats[r], host_id=read(cfg / (r + '.binding.json'))['grant']['host'],
                                     incarnation_id=read(cfg / (r + '.binding.json'))['grant']['incarnation'],
                                     thread_id=None, binding_file=str((Path(m['remoteConfigRoot']) if r == 'pilot-seat' else cfg) / (r + '.binding.json')))
                             for r in ROLES})
    write(cfg / 'routes.pending.json', routes, 0o640)
    write(cfg / 'setup.json', m, 0o640)
    return dict(initialized=True, seats=3, servicesStarted=0, routesEnabled=False)


def binding(path):
    b = read(path)
    s = path.stat()
    require(s.st_uid == b['serviceUid'] and s.st_mode & 0o022 == 0, 'binding_permissions')
    require(b['grant']['agentUid'] == os.geteuid(), 'run_as_endpoint_user')
    return b


def exchange(b, frame):
    parent = Path(b['socket']).parent.stat()
    require(parent.st_uid == b['serviceUid'] and parent.st_mode & 0o022 == 0, 'socket_parent_permissions')
    with socket.socket(socket.AF_UNIX) as s:
        s.settimeout(8)
        s.connect(b['socket'])
        require(struct.unpack('3i', s.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))[1] == b['serviceUid'],
                'service_uid_mismatch')
        s.sendall(json.dumps(dict(protocol=1, **frame)).encode() + b'\n')
        data = b''
        while not data.endswith(b'\n'):
            chunk = s.recv(65536)
            require(chunk and len(data) + len(chunk) <= 65536, 'wire_bound')
            data += chunk
    return json.loads(data)


def authenticated(b, method, recipient=None, operation='observe', **fields):
    p = Path(b['credential'])
    require(p.stat().st_uid == os.geteuid() and stat.S_IMODE(p.stat().st_mode) == 0o600, 'credential_permissions')
    secret = read(p)
    g = b['grant']
    request = dict(protocol=1, sender=g['seat'], host=g['host'], incarnation=g['incarnation'],
                   sequence=1, project=g['projects'][0], recipient=g['seat'], operation='observe')
    auth = dict(seat=g['seat'], credential=secret['credential'], request=request)
    sync = exchange(b, dict(method='sync', auth=auth))
    require(sync.get('ok') and sync.get('grant') == g, 'sync_failed_or_grant_mismatch')
    request.update(sequence=sync['lastSequence'] + 1, operation=operation, recipient=recipient or g['seat'])
    return exchange(b, dict(method=method, auth=auth, **fields))


def endpoint(a):
    b = binding(absolute(a.binding))
    if a.command == 'enroll':
        p = absolute(a.bootstrap)
        require(p.stat().st_uid == os.geteuid() and stat.S_IMODE(p.stat().st_mode) == 0o600, 'bootstrap_permissions')
        require(not Path(b['credential']).exists(), 'credential_already_exists')
        result = exchange(b, dict(method='enroll', bootstrap=p.read_text()))
        require(result.get('ok'), result.get('error', 'enrollment_failed'))
        require(result['seat'] == b['grant']['seat'], 'seat_mismatch')
        write(Path(b['credential']), result)
        return dict(enrolled=True, seat=result['seat'], expiresAt=result['expiresAt'])
    if a.command == 'receive':
        result = authenticated(b, 'receive', message_id=str(uuid.UUID(a.message)))
    elif a.command == 'status':
        result = authenticated(b, 'status', recipient=str(uuid.UUID(a.recipient)), message_id=str(uuid.UUID(a.message)))
    elif a.command == 'forbidden':
        g = b['grant']; now = int(time.time())
        env = dict(protocol=1, messageId=str(uuid.uuid4()), conversationId=str(uuid.uuid4()),
                   assignmentId=None, replyTo=None, sender=g['seat'], incarnation=g['incarnation'],
                   recipient=str(uuid.UUID(a.recipient)), project=g['projects'][0], kind='note', mode='queue_only',
                   createdAt=now, expiresAt=now + 3600, text='Inert setup route-denial proof.', artifactRefs=[])
        result = authenticated(b, 'send', recipient=env['recipient'], operation='queue', envelope=env)
        require(result.get('error') == 'recipient_denied', 'forbidden_route_was_not_refused')
        return dict(forbiddenRefused=True, error='recipient_denied')
    elif a.command in ('send', 'resend'):
        binary = absolute(a.bin_dir) / 'codex-factory-bridge'
        outbox = Path(b['state']) / 'setup-proof-outbox'
        directory(outbox)
        grant = outbox / 'grant.json'
        if not grant.exists():
            write(grant, b['grant'])
        if a.command == 'send':
            g = b['grant']; mid = str(uuid.uuid4()); now = int(time.time())
            envelope = dict(protocol=1, messageId=mid, conversationId=str(uuid.uuid4()), assignmentId=None,
                            replyTo=None, sender=g['seat'], incarnation=g['incarnation'], recipient=str(uuid.UUID(a.recipient)),
                            project=g['projects'][0], kind='note', mode='queue_only', createdAt=now,
                            expiresAt=now + 3600, text='Inert setup mailbox proof; no work order.', artifactRefs=[])
            file = outbox / (mid + '.json'); write(file, envelope)
            bridge(binary, 'enqueue', '--state', outbox / 'outbox', '--grant', grant, '--envelope', file)
        else:
            mid = str(uuid.UUID(a.message))
        result = bridge(binary, 'relay', '--state', outbox / 'outbox', '--grant', grant, '--socket', b['socket'],
                        '--service-uid', b['serviceUid'], '--credential', b['credential'], '--message-id', mid)
        return dict(messageId=mid, state=result['state'], receipts=result['receipts'])
    require(result.get('ok'), result.get('error', 'mailbox_request_failed'))
    return dict(messageId=a.message, state=result.get('state', 'delivered'), receipts=result['receipts'])


def operator(a):
    cfg = absolute(a.config_root)
    if a.command == 'keygen':
        directory(cfg, 0o2750); directory(cfg / 'transport')
        key = cfg / 'transport/dev3-ed25519'
        require(not key.exists() and not key.with_suffix('.pub').exists(), 'transport_key_exists')
        p = subprocess.run(['/usr/bin/ssh-keygen', '-q', '-t', 'ed25519', '-N', '', '-C', 'fleet-dev3-bridge',
                            '-f', str(key)], capture_output=True)
        require(p.returncode == 0, 'transport_keygen_failed')
        key.chmod(0o600); Path(str(key) + '.pub').chmod(0o600)
        return dict(keyCreated=True, publicKeyFile=str(key) + '.pub')
    m = load_setup(cfg)
    if a.command == 'bootstrap':
        result = mint(m, a.role)
        write(cfg / 'setup.json', m, 0o640, replace=True)
        return dict(bootstrapFile=result['path'], expiresAt=result['expiresAt'], role=a.role)
    if a.command == 'routes':
        threads = read(absolute(a.threads))
        require(set(threads) == set(ROLES), 'three_actual_thread_ids_required')
        ids = [str(uuid.UUID(threads[r])) for r in ROLES]
        require(len(set(ids)) == 3 and all(uuid.UUID(x).int for x in ids), 'distinct_thread_ids_required')
        routes = read(cfg / 'routes.pending.json')
        for role, thread in zip(ROLES, ids):
            routes['endpoints'][role]['thread_id'] = thread
        write(cfg / 'routes.json', routes, 0o640)
        return dict(routesWritten=True, enabled=False, suppliedThreadIds=3)
    if a.command == 'ssh-config':
        pub = absolute(a.client_public_key).read_text().strip().split()
        require(len(pub) >= 2 and pub[0] == 'ssh-ed25519' and re.fullmatch('[A-Za-z0-9+/=]+', pub[1]), 'ed25519_public_key_required')
        require(re.fullmatch('[a-z_][a-z0-9_-]*', m['sshUser']) and re.fullmatch('[a-z_][a-z0-9_-]*', m['serviceUser']), 'unix_user_name_required')
        entry = f"{m['binDir']}/codex-factory-bridge remote-entry --state {m['stateRoot']}/service --host {m['remoteHost']} --key-id {m['transportKey']}"
        forced = f"/usr/bin/sudo -n -u {m['serviceUser']} -- {entry}"
        write(cfg / 'operator/authorized_keys.fragment', f'restrict,command="{forced}" {pub[0]} {pub[1]} fleet-dev3-bridge\n')
        sudoers = f'Cmnd_Alias FLEET_BRIDGE_DEV3 = {entry}\nDefaults!FLEET_BRIDGE_DEV3 env_keep += "SSH_ORIGINAL_COMMAND SSH_CONNECTION"\n{m["sshUser"]} ALL=({m["serviceUser"]}) NOPASSWD: FLEET_BRIDGE_DEV3\n'
        write(cfg / 'operator/fleet-bridge-remote.sudoers', sudoers)
        return dict(sshFilesWritten=True, servicesStarted=0)
    raise RuntimeError('unknown_operator_command')


def main():
    os.umask(0o077)
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest='command', required=True)
    init = sub.add_parser('init')
    for name in ('bin-dir', 'state-root', 'config-root', 'run-root', 'remote-state-root', 'remote-config-root', 'remote-run-root'):
        init.add_argument('--' + name, required=True)
    for name in ('agent-uid', 'remote-agent-uid', 'remote-service-uid'):
        init.add_argument('--' + name, required=True, type=int)
    init.add_argument('--ssh-address', default='10.200.68.55')
    init.add_argument('--ssh-port', type=int, default=22)
    init.add_argument('--ssh-user', default='ai')
    init.add_argument('--service-user', default='fleet-bridge')
    for cmd in ('keygen', 'bootstrap', 'routes', 'ssh-config'):
        q = sub.add_parser(cmd); q.add_argument('--config-root', required=True)
        if cmd == 'bootstrap': q.add_argument('--role', choices=ROLES, required=True)
        if cmd == 'routes': q.add_argument('--threads', required=True)
        if cmd == 'ssh-config': q.add_argument('--client-public-key', required=True)
    for cmd in ('enroll', 'send', 'resend', 'receive', 'status', 'forbidden'):
        q = sub.add_parser(cmd); q.add_argument('--binding', required=True)
        if cmd == 'enroll': q.add_argument('--bootstrap', required=True)
        if cmd in ('send', 'resend'): q.add_argument('--bin-dir', required=True)
        if cmd in ('send', 'status', 'forbidden'): q.add_argument('--recipient', required=True)
        if cmd in ('resend', 'receive', 'status'): q.add_argument('--message', required=True)
    a = p.parse_args()
    if a.command == 'init': result = initialize(a)
    elif a.command in ('keygen', 'bootstrap', 'routes', 'ssh-config'): result = operator(a)
    else: result = endpoint(a)
    print(json.dumps(result, sort_keys=True))


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        # Deliberately bounded diagnostics: no traceback, payload or secret echo.
        code = str(exc)
        print(json.dumps({'ok': False, 'error': code if re.fullmatch('[a-z][a-z0-9_]*', code) else 'setup_step_failed'}), file=sys.stderr)
        sys.exit(1)
