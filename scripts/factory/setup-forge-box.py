#!/usr/bin/env python3
"""Explicit operator steps for the existing bridge. Never starts a service."""
import argparse
import hashlib
import fcntl
import ipaddress
import pwd
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


class SandboxPreconditionError(RuntimeError):
    pass


def sandbox_preflight(bin_dir):
    package = absolute(bin_dir).resolve().parent
    bwrap = package / 'codex-resources/bwrap'
    manifest = package / 'MANIFEST.json'
    expected = 'unavailable'
    try:
        require(manifest.is_file() and not manifest.is_symlink()
                and manifest.stat().st_size <= 2 * 1024 * 1024, 'package_manifest_missing_or_invalid')
        expected = json.loads(manifest.read_text()).get('bwrapEmbeddedSha256', '')
        require(isinstance(expected, str) and re.fullmatch('[0-9a-f]{64}', expected), 'manifest_bwrap_digest_missing')
        require(bwrap.is_file() and not bwrap.is_symlink() and os.access(bwrap, os.X_OK), 'packaged_bwrap_missing')
        with bwrap.open('rb') as f:
            actual = hashlib.file_digest(f, 'sha256').hexdigest()
        require(actual == expected, 'packaged_bwrap_digest_mismatch')
        command = [str(bwrap), '--unshare-user', '--unshare-pid', '--unshare-net', '--die-with-parent',
                   '--ro-bind', '/', '/', '--proc', '/proc', '--dev', '/dev', '--', '/usr/bin/true']
        result = subprocess.run(command, capture_output=True, text=True, timeout=15)
        if result.returncode != 0:
            reason = result.stderr.strip().splitlines()
            raise RuntimeError(reason[0][:400] if reason else 'namespace probe exited ' + str(result.returncode))
    except Exception as exc:
        raise SandboxPreconditionError(
            f'Sandbox precondition failed: {bwrap}\nSHA-256 from MANIFEST.json: {expected}\n'
            f'Probe refused: {str(exc)[:400]}\n'
            'Ops must install/approve this exact verified bwrap path and digest on this host '
            '(root-owned file, required root-only xattr and AppArmor profile through ucs-ops on restricted Ubuntu). '
            'Refusing endpoint setup. No AppArmor, sysctl or ownership change was attempted; '
            'there is no danger-full-access fallback.'
        ) from None
    return dict(passed=True, bwrapPath=str(bwrap), bwrapSha256=expected, uid=os.geteuid(),
                probe='read-only user/pid/network namespaces; /usr/bin/true', modelsStarted=0)


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
    fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


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
    require(role != 'pilot-seat' or m.get('remoteAdmitted', True), 'remote_admission_deferred')
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
             sshUser=a.ssh_user, serviceUser=a.service_user, remoteAdmitted=not a.defer_remote)
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
    if not a.defer_remote:
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
    return dict(initialized=True, seats=3, servicesStarted=0, routesEnabled=False,
                endpointSandboxCheckRequired=True, remoteAdmitted=not a.defer_remote)


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
    if a.command == 'admit-remote':
        binary = Path(m['binDir']) / 'codex-factory-bridge'
        service = Path(m['stateRoot']) / 'service'
        admitted = bridge(binary, 'inspect', '--state', service)['remoteHosts'].get(m['remoteHost'])
        if admitted:
            require(not admitted['revoked'] and admitted['currentKey'] == m['transportKey']
                    and admitted['bridgeUid'] == m['remoteServiceUid'], 'remote_admission_conflict')
            bridge(binary, 'renew-host', '--state', service, '--host', m['remoteHost'],
                   '--key-id', m['transportKey'], '--ttl', 86400)
        else:
            bridge(binary, 'admit-host', '--state', service, '--host', m['remoteHost'],
                   '--key-id', m['transportKey'], '--bridge-uid', m['remoteServiceUid'], '--ttl', 86400)
        m['remoteAdmitted'] = True
        write(cfg / 'setup.json', m, 0o640, replace=True)
        return dict(admitted=True, host=m['remoteHost'], keyId=m['transportKey'])
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



class RenewalFailure(RuntimeError):
    def __init__(self, code, seat='none', rotation='none', host='none'):
        if isinstance(code, OSError): code = 'os_error_' + str(code.errno or 0)
        elif isinstance(code, subprocess.TimeoutExpired): code = 'renewal_child_timeout'
        elif isinstance(code, subprocess.SubprocessError): code = 'renewal_child_identity_failed'
        else: code = str(code)
        self.code = code if re.fullmatch('[a-z][a-z0-9_]*', code) else 'renewal_operation_failed'
        def identifier(value):
            try:
                return str(uuid.UUID(str(value)))
            except (ValueError, TypeError, AttributeError):
                return 'none'
        self.seat, self.rotation = identifier(seat), identifier(rotation)
        self.host = identifier(host)


def rooted(path, *, private=False, directory_only=False):
    path = absolute(path)
    for part in [*reversed(path.parents), path]:
        info = part.lstat()
        require(not stat.S_ISLNK(info.st_mode) and info.st_uid == 0 and not info.st_mode & 0o022,
                'root_owned_path_required')
    info = path.stat()
    require(stat.S_ISDIR(info.st_mode) if directory_only else stat.S_ISREG(info.st_mode), 'root_path_type')
    if private:
        require(stat.S_IMODE(info.st_mode) == 0o600 and info.st_nlink == 1, 'root_config_mode')
    return path


def actor_ids(uid, clients):
    try:
        gid = pwd.getpwuid(uid).pw_gid
    except KeyError:
        gid = uid
    return dict(uid=uid, gid=gid, groups=sorted({gid, clients}))


def actor_run(actor, args, *, payload=None, timeout=20):
    env = {'PATH': '/usr/bin:/bin', 'LANG': 'C', 'LC_ALL': 'C', 'HOME': '/', 'TMPDIR': '/tmp'}
    # No shell, writable module path, inherited login or user config is consulted.
    kwargs = dict(user=actor['uid'], group=actor['gid'], extra_groups=actor['groups']) if os.geteuid() == 0 else {}
    result = subprocess.run(list(map(str, args)), input=payload, capture_output=True,
                            timeout=timeout, env=env, **kwargs)
    if result.returncode:
        raw = result.stderr.decode(errors='replace')
        codes = re.findall(r'(?:Error: |"error":\s*")([a-z][a-z0-9_]+)', raw)
        raise RuntimeError(codes[-1] if codes else 'renewal_child_failed')
    return result.stdout


def administrative(c, *args):
    return json.loads(actor_run(c['service'], [Path(c['package']) / 'bin/codex-factory-bridge',
                                             *args], timeout=25))


def validate_allowlist(path):
    require(os.geteuid() == 0, 'renewal_requires_root')
    path = rooted(path, private=True)
    require(path.stat().st_size <= 65536, 'root_config_bound')
    c = read(path)
    require(c['schemaVersion'] == 1, 'renewal_schema')
    package = rooted(c['package'], directory_only=True)
    require(package == Path(__file__).resolve().parent, 'renewal_wrong_package')
    require(set(c['files']) == {'bin/codex-factory-bridge', 'setup-forge-box.py'}, 'renewal_code_inventory')
    for name, expected in c['files'].items():
        source = rooted(package / name)
        with source.open('rb') as f:
            require(hashlib.file_digest(f, 'sha256').hexdigest() == expected, 'renewal_code_changed')
    service = c['service']
    require(isinstance(service['uid'], int) and 0 < service['uid'] < 2**32-1, 'renewal_service_uid')
    for actor in [service, *[entry['actor'] for entry in c['seats']]]:
        require(all(isinstance(actor[field], int) and 0 < actor[field] < 2**32-1
                    for field in ('uid', 'gid')) and len(actor['groups']) <= 32
                and all(isinstance(gid, int) and 0 < gid < 2**32-1 for gid in actor['groups']),
                'renewal_actor_identity')
    for field in ('state', 'pending'):
        absolute(service[field])
    require(0 < c['hostTtl'] <= 86400 and 0 <= c['hostRenewBefore'] < c['hostTtl'], 'renewal_host_ttl')
    require(0 < c['seatTtl'] <= 3600 and 0 <= c['seatRenewBefore'] < c['seatTtl']
            and 0 <= c['overlapSeconds'] <= 60, 'renewal_seat_ttl')
    require(len(c['seats']) <= 128 and len(c['hosts']) <= 32, 'renewal_scope_bound')
    for host in c['hosts']:
        require(uuid.UUID(host['host']).int and uuid.UUID(host['keyId']).int
                and host['bridgeUid'] == service['uid'], 'renewal_host_binding')
    require(len({entry['policy']['grant']['seat'] for entry in c['seats']}) == len(c['seats']),
            'renewal_duplicate_seat')
    for entry in c['seats']:
        require(0 < entry['actor']['uid'] < 2**32-1 and entry['actor']['uid'] != service['uid'],
                'renewal_endpoint_uid')
        policy = entry['policy']
        require(policy['grant']['agentUid'] == entry['actor']['uid'] and policy['serviceUid'] == service['uid'],
                'renewal_binding_conflict')
        for field in ('socket', 'credential'):
            absolute(policy[field])
        uuid.UUID(policy['grant']['seat']); uuid.UUID(policy['grant']['incarnation'])
        require(entry['publish']['kind'] in ('local', 'ssh', 'deferred'), 'renewal_publisher_kind')
        if entry['publish']['kind'] == 'ssh':
            publisher = entry['publish']
            require(ipaddress.ip_address(publisher['address']).is_private
                    or ipaddress.ip_address(publisher['address']).is_loopback, 'publisher_private_address')
            rooted(publisher['identityFile'], private=True); rooted(publisher['knownHosts'], private=True)
            require(0 < publisher['port'] < 65536 and re.fullmatch('[a-zA-Z0-9_-]+', publisher['user']),
                    'publisher_identity_invalid')
            require(re.fullmatch('[a-zA-Z0-9_-]+', publisher['hostKeyAlias']), 'publisher_pin_alias')
    runtime = rooted(c['runtimeRoot'], directory_only=True)
    require(stat.S_IMODE(runtime.stat().st_mode) == 0o700, 'renewal_runtime_mode')
    return c


def credential_bytes(path):
    path = absolute(path)
    info = path.lstat()
    require(stat.S_ISREG(info.st_mode) and info.st_uid == os.geteuid()
            and stat.S_IMODE(info.st_mode) == 0o600 and info.st_nlink == 1
            and info.st_size <= 65536, 'credential_private_file_required')
    return path.read_bytes()


def publish_credential(policy, data):
    require(os.geteuid() == policy['grant']['agentUid'] != 0, 'publication_endpoint_uid')
    destination = absolute(policy['credential'])
    for parent in destination.parents:
        info = parent.lstat()
        sticky = info.st_uid == 0 and info.st_mode & 0o1000
        require(stat.S_ISDIR(info.st_mode) and info.st_uid in (0, os.geteuid(), policy['serviceUid'])
                and (not info.st_mode & 0o022 or sticky), 'publication_parent_permissions')
    info = destination.parent.stat()
    require(info.st_uid == os.geteuid() and stat.S_IMODE(info.st_mode) == 0o700, 'publication_directory')
    lockfile = destination.parent / '.publication.lock'
    fd = os.open(lockfile, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        info = os.fstat(fd)
        require(info.st_uid == os.geteuid() and stat.S_ISREG(info.st_mode) and not info.st_mode & 0o077,
                'publication_lock_permissions')
        require(info.st_nlink == 1, 'publication_lock_links')
        deadline = time.monotonic() + 8
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB); break
            except BlockingIOError:
                require(time.monotonic() < deadline, 'publication_lock_timeout'); time.sleep(.02)
        g = policy['grant']
        require(data['seat'] == g['seat'] and data['incarnation'] == g['incarnation']
                and data['expiresAt'] > int(time.time()), 'publication_credential_mismatch')
        require(re.fullmatch('[0-9a-f]{64}', data['credential']), 'publication_credential_invalid')
        request = dict(protocol=1, sender=g['seat'], host=g['host'], incarnation=g['incarnation'],
                       sequence=1, project=g['projects'][0], recipient=g['seat'], operation='observe')
        response = exchange(policy, dict(method='sync', auth=dict(seat=g['seat'],
                            credential=data['credential'], request=request)))
        require(response.get('ok') and response.get('grant') == g
                and response.get('credentialCurrent') is True, 'publication_candidate_not_current')
        if destination.exists():
            credential_bytes(destination)
        public = {key: data[key] for key in ('ok', 'seat', 'incarnation', 'expiresAt', 'credential')}
        write(destination, public, replace=True)
        return dict(published=True, seat=g['seat'], incarnation=g['incarnation'])
    finally:
        os.close(fd)


def ssh_publisher(p, data):
    command = ['/usr/bin/ssh', '-F', '/dev/null', '-T', '-oBatchMode=yes', '-oIdentitiesOnly=yes',
               '-oIdentityAgent=none', '-oStrictHostKeyChecking=yes', '-oCheckHostIP=no',
               '-oGlobalKnownHostsFile=/dev/null', '-oUpdateHostKeys=no', '-oControlMaster=no',
               '-oControlPath=none', '-oClearAllForwardings=yes', '-oForwardAgent=no',
               '-oForwardX11=no', '-oProxyCommand=none', '-oProxyJump=none',
               '-oPermitLocalCommand=no', '-oPasswordAuthentication=no', '-oKbdInteractiveAuthentication=no',
               '-oConnectTimeout=3', '-oConnectionAttempts=1', '-oServerAliveInterval=2',
               '-oServerAliveCountMax=2', '-oLogLevel=ERROR',
               '-oUserKnownHostsFile=' + p['knownHosts'], '-oHostKeyAlias=' + p['hostKeyAlias'],
               '-i', p['identityFile'], '-l', p['user'], '-p', str(p['port']), '--', p['address'],
               'codex-factory-publish-v1']
    try:
        return json.loads(actor_run(dict(uid=0, gid=0, groups=[]), command,
                                   payload=json.dumps(data).encode(), timeout=20))
    except Exception:
        # A remote peer can echo its input in stderr. Never project any remote
        # diagnostic (even one shaped like an error code) into the root journal.
        raise RuntimeError('publisher_failed') from None


def renew(c):
    fd = os.open(Path(c['runtimeRoot']) / 'renew.lock', os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        info = os.fstat(fd)
        require(stat.S_ISREG(info.st_mode) and info.st_uid == 0 and not info.st_mode & 0o077,
                'renewal_lock_permissions')
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RenewalFailure('renewal_already_running') from None
        snapshot = administrative(c, 'inspect', '--state', c['service']['state'])
        hosts, seats = [], []
        now = int(time.time())
        for expected in c['hosts']:
            host = snapshot['remoteHosts'].get(expected['host'])
            if host is None: continue  # Deferred means no implicit admission.
            try:
                require(not host['revoked'] and host['currentKey'] == expected['keyId']
                        and host['bridgeUid'] == expected['bridgeUid'], 'renewal_host_identity')
                if host['expiresAt'] <= now + c['hostRenewBefore']:
                    administrative(c, 'renew-host', '--state', c['service']['state'], '--host', expected['host'],
                                   '--key-id', expected['keyId'], '--ttl', c['hostTtl'])
                    hosts.append(expected['host'])
            except Exception as exc:
                raise RenewalFailure(exc, host=expected['host']) from None
        for entry in c['seats']:
            seat = entry['policy']['grant']['seat']; rotation = 'none'
            try:
                current = next((x for x in snapshot['seats'] if x['seat'] == seat), None)
                if current is None: continue
                require(not current['revoked'] and current['grant'] == entry['policy']['grant'], 'renewal_grant_changed')
                status = administrative(c, 'rotation-status', '--state', c['service']['state'], '--seat', seat)
                if status['pending']:
                    rotation = status['rotationId']
                    if status['expiresAt'] <= int(time.time()):
                        administrative(c, 'finish-rotation', '--state', c['service']['state'], '--seat', seat,
                                       '--rotation-id', rotation, '--abandon-expired')
                        status = dict(pending=False)
                if not status['pending'] and current['expiresAt'] > now + c['seatRenewBefore']: continue
                require(entry['publish']['kind'] != 'deferred', 'renewal_publisher_not_configured')
                if not status['pending']:
                    rotation = str(uuid.uuid4())
                    output = Path(c['service']['pending']) / ('rotation-' + seat + '-' + rotation + '.json')
                else:
                    output = absolute(status['credentialFile'])
                    require(output.parent == Path(c['service']['pending']), 'renewal_output_path')
                administrative(c, 'rotate', '--state', c['service']['state'], '--seat', seat,
                               '--output', output, '--rotation-id', rotation, '--credential-ttl', c['seatTtl'],
                               '--overlap-seconds', c['overlapSeconds'])
                candidate = json.loads(actor_run(c['service'], [sys.executable, '-I',
                                      Path(c['package']) / 'setup-forge-box.py', 'credential-bytes', '--file', output]))
                if entry['publish']['kind'] == 'local':
                    published = json.loads(actor_run(entry['actor'], [sys.executable, '-I',
                        Path(c['package']) / 'setup-forge-box.py', 'publish-credential',
                        '--policy-json', json.dumps(entry['policy'], separators=(',', ':'))],
                        payload=json.dumps(candidate).encode()))
                else:
                    published = ssh_publisher(entry['publish'], candidate)
                require(published.get('published') and published.get('seat') == seat, 'renewal_publish_not_confirmed')
                administrative(c, 'finish-rotation', '--state', c['service']['state'], '--seat', seat,
                               '--rotation-id', rotation)
                seats.append(dict(seat=seat, rotationId=rotation))
            except RenewalFailure: raise
            except Exception as exc:
                raise RenewalFailure(exc, seat, rotation) from None
        return dict(renewal='ok', hosts=hosts, seats=seats)
    finally:
        os.close(fd)


def render_renewal(a):
    require(os.geteuid() == 0, 'render_requires_root')
    package = rooted(Path(__file__).resolve().parent, directory_only=True)
    cfg = absolute(a.config_root); owner = cfg.stat().st_uid
    require(owner != 0 and not cfg.is_symlink() and not cfg.stat().st_mode & 0o022, 'render_service_config')
    m = read(cfg / 'setup.json'); require(m['serviceUid'] == owner, 'render_service_uid')
    clients = cfg.stat().st_gid
    c = dict(schemaVersion=1, package=str(package), files={}, service=actor_ids(owner, clients),
             runtimeRoot='/var/lib/fleet-manager-bridge-renew', hostTtl=86400, hostRenewBefore=43200,
             seatTtl=3600, seatRenewBefore=1800, overlapSeconds=60,
             hosts=[dict(host=m['remoteHost'], keyId=m['transportKey'], bridgeUid=m['remoteServiceUid'])], seats=[])
    c['service'].update(state=str(Path(m['stateRoot']) / 'service'), pending=str(Path(m['stateRoot']) / 'pending'))
    for name in ('bin/codex-factory-bridge', 'setup-forge-box.py'):
        with rooted(package / name).open('rb') as f: c['files'][name] = hashlib.file_digest(f, 'sha256').hexdigest()
    for role in ROLES:
        policy = read(cfg / (role + '.binding.json'))
        require(policy['grant']['seat'] == m['seats'][role], 'render_seat_identity')
        c['seats'].append(dict(role=role, actor=actor_ids(policy['grant']['agentUid'], clients), policy=policy,
                               publish=dict(kind='deferred' if role == 'pilot-seat' else 'local')))
    if a.publisher_config:
        publisher = read(rooted(a.publisher_config, private=True))
        require(publisher['kind'] == 'ssh', 'publisher_config_kind')
        require(a.publisher_public_key, 'publisher_public_key_required')
        c['seats'][-1]['publish'] = publisher
    else:
        require(not a.publisher_public_key, 'publisher_config_required')
    runtime = Path(c['runtimeRoot'])
    directory(runtime); rooted(runtime, directory_only=True)
    output = absolute(a.output); output.mkdir(mode=0o700)
    rooted(output, directory_only=True)
    write(output / 'allowlist.json', c)
    for name in ('fleet-manager-bridge-renew.service', 'fleet-manager-bridge-renew.timer'):
        template = rooted(package / 'systemd' / (name + '.in')).read_text()
        template = template.replace('@PACKAGE@', str(package))
        write(output / name, template, 0o644)
    publisher_policy = c['seats'][-1]['policy']
    write(output / 'pilot-publisher.json', publisher_policy, 0o644)
    if a.publisher_config:
        public = rooted(a.publisher_public_key).read_text().strip().split()
        require(len(public) >= 2 and public[0] == 'ssh-ed25519' and re.fullmatch('[A-Za-z0-9+/=]+', public[1]),
                'publisher_ed25519_public_key_required')
        command = f'/usr/bin/python3 -I {package}/setup-forge-box.py publish-credential --policy-file /etc/fleet-manager-bridge-publisher.json'
        write(output / 'publisher-authorized-keys.fragment', 'restrict,command="' + command + '" ' +
              public[0] + ' ' + public[1] + ' fleet-credential-publisher\n', 0o644)
    return dict(rendered=True, allowlist=str(output / 'allowlist.json'), units=2, servicesStarted=0)

def main():
    os.umask(0o077)
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest='command', required=True)
    init = sub.add_parser('init')
    for name in ('bin-dir', 'state-root', 'config-root', 'run-root', 'remote-state-root', 'remote-config-root', 'remote-run-root'):
        init.add_argument('--' + name, required=True)
    for name in ('agent-uid', 'remote-agent-uid', 'remote-service-uid'):
        init.add_argument('--' + name, required=True, type=int)
    init.add_argument('--defer-remote', action='store_true')
    init.add_argument('--ssh-address', default='10.200.68.55')
    init.add_argument('--ssh-port', type=int, default=22)
    init.add_argument('--ssh-user', default='ai')
    init.add_argument('--service-user', default='fleet-bridge')
    probe = sub.add_parser('preflight', help='Check the packaged sandbox as the endpoint user; no model or policy changes')
    probe.add_argument('--bin-dir', required=True)
    for cmd in ('keygen', 'bootstrap', 'routes', 'ssh-config', 'admit-remote'):
        q = sub.add_parser(cmd); q.add_argument('--config-root', required=True)
        if cmd == 'bootstrap': q.add_argument('--role', choices=ROLES, required=True)
        if cmd == 'routes': q.add_argument('--threads', required=True)
        if cmd == 'ssh-config': q.add_argument('--client-public-key', required=True)
    for cmd in ('enroll', 'send', 'resend', 'receive', 'status', 'forbidden'):
        q = sub.add_parser(cmd); q.add_argument('--binding', required=True)
        if cmd == 'enroll': q.add_argument('--bootstrap', required=True)
        if cmd in ('send', 'resend'): q.add_argument('--bin-dir', required=True)
        else: q.add_argument('--bin-dir', default=str(Path(__file__).resolve().parent / 'bin'))
        if cmd in ('send', 'status', 'forbidden'): q.add_argument('--recipient', required=True)
        if cmd in ('resend', 'receive', 'status'): q.add_argument('--message', required=True)
    for cmd in ('renew', 'render-renewal', 'credential-bytes', 'publish-credential'):
        q = sub.add_parser(cmd)
        if cmd == 'renew': q.add_argument('--config', required=True)
        elif cmd == 'render-renewal':
            q.add_argument('--config-root', required=True); q.add_argument('--output', required=True)
            q.add_argument('--publisher-config'); q.add_argument('--publisher-public-key')
        elif cmd == 'credential-bytes': q.add_argument('--file', required=True)
        else:
            group = q.add_mutually_exclusive_group(required=True)
            group.add_argument('--policy-json'); group.add_argument('--policy-file')
    a = p.parse_args()
    if a.command == 'credential-bytes':
        require(not sys.stdout.isatty(), 'credential_pipe_required')
        sys.stdout.buffer.write(credential_bytes(Path(a.file))); return
    if a.command == 'publish-credential':
        if a.policy_file:
            require(os.environ.get('SSH_ORIGINAL_COMMAND') == 'codex-factory-publish-v1'
                    and os.environ.get('SSH_CONNECTION'), 'publisher_forced_command_required')
            policy = read(rooted(a.policy_file))
        else: policy = json.loads(a.policy_json)
        raw = sys.stdin.buffer.read(65537); require(len(raw) <= 65536, 'publication_input_bound')
        print(json.dumps(publish_credential(policy, json.loads(raw)))); return
    if a.command == 'render-renewal':
        print(json.dumps(render_renewal(a))); return
    if a.command == 'renew':
        try: result = renew(validate_allowlist(a.config))
        except RenewalFailure: raise
        except Exception as exc: raise RenewalFailure(exc) from None
        print(json.dumps(result)); return
    if a.command == 'preflight': result = dict(sandbox=sandbox_preflight(a.bin_dir), servicesStarted=0)
    elif a.command == 'init': result = initialize(a)
    elif a.command in ('keygen', 'bootstrap', 'routes', 'ssh-config', 'admit-remote'): result = operator(a)
    else:
        sandbox = sandbox_preflight(a.bin_dir)
        result = endpoint(a)
        result['sandbox'] = sandbox
    print(json.dumps(result, sort_keys=True))


if __name__ == '__main__':
    try:
        main()
    except RenewalFailure as exc:
        print(f'renewal_failed host={exc.host} seat={exc.seat} rotation={exc.rotation} code={exc.code}', file=sys.stderr)
        sys.exit(1)
    except SandboxPreconditionError as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)
    except Exception as exc:
        # Deliberately bounded diagnostics: no traceback, payload or secret echo.
        code = str(exc)
        print(json.dumps({'ok': False, 'error': code if re.fullmatch('[a-z][a-z0-9_]*', code) else 'setup_step_failed'}), file=sys.stderr)
        sys.exit(1)
