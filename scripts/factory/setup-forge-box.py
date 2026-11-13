#!/usr/bin/env python3
"""Stage reviewed pilot artifacts; explicit root activation requires verified gates."""
import argparse
import hashlib
import fcntl
import ipaddress
import pwd
import grp
import json
import os
from pathlib import Path
import re
import socket
import shutil
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
    if a.pilot:
        require(all('pilot' in str(path) for path in (cfg,state,run,absolute(a.remote_config_root),
                absolute(a.remote_state_root),absolute(a.remote_run_root))), 'pilot_named_roots_required')
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
    planned = read(rooted(a.pilot_plan)) if getattr(a,'pilot_plan',None) else None
    if planned:
        require(a.pilot and planned['pilot'] is True and planned['renewal']=='off','pilot_plan_required')
        planned=planned['setup']
        for field,value in dict(binDir=str(binary_dir),configRoot=str(cfg),stateRoot=str(state),runRoot=str(run),
             remoteConfigRoot=a.remote_config_root,remoteStateRoot=a.remote_state_root,remoteRunRoot=a.remote_run_root,
             serviceUid=os.geteuid(),agentUid=a.agent_uid,remoteAgentUid=a.remote_agent_uid,remoteServiceUid=a.remote_service_uid).items():
            require(planned[field]==value,'pilot_plan_binding_conflict')
    seats = planned['seats'] if planned else {r: str(uuid.uuid4()) for r in ROLES}
    hub, remote, key = [planned[k] for k in ('hub','remoteHost','transportKey')] if planned else [str(uuid.uuid4()) for _ in range(3)]
    m = dict(binDir=str(binary_dir), configRoot=str(cfg), stateRoot=str(state), runRoot=str(run),
             remoteConfigRoot=str(absolute(a.remote_config_root)), remoteStateRoot=str(absolute(a.remote_state_root)),
             remoteRunRoot=str(absolute(a.remote_run_root)), serviceUid=os.geteuid(),
             remoteServiceUid=a.remote_service_uid, agentUid=a.agent_uid, remoteAgentUid=a.remote_agent_uid,
             hub=hub, remoteHost=remote, transportKey=key, seats=seats, pending={}, project=PROJECT,
             sshUser=a.ssh_user, serviceUser=a.service_user, remoteAdmitted=not a.defer_remote, pilot=a.pilot)
    for role in ROLES:
        remote_seat = role == 'pilot-seat'
        grant = dict(seat=seats[role], host=remote if remote_seat else hub,
                     incarnation=planned['incarnations'][role] if planned else str(uuid.uuid4()), agentUid=a.remote_agent_uid if remote_seat else a.agent_uid,
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
    if not a.pilot: write(cfg / 'operator/proxy.json', proxy)
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
        require(not m.get('pilot',False),'pilot_legacy_ssh_refused')
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
    require(not m.get('pilot',False),'pilot_renewal_off')
    clients = cfg.stat().st_gid
    c = dict(schemaVersion=1, package=str(package), files={}, service=actor_ids(owner, clients),
             runtimeRoot=str(absolute(a.runtime_root)) if a.runtime_root else '/var/lib/fleet-manager-bridge-renew', hostTtl=86400, hostRenewBefore=43200,
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
        require(re.fullmatch('fleet-manager-bridge(?:-pilot)?',a.unit_prefix),'renewal_unit_prefix')
        if name.endswith('.service'):
            template=re.sub(r'^ReadWritePaths=.*$', 'ReadWritePaths='+m['stateRoot']+' '+c['runtimeRoot']+' '+str(cfg/'credentials'),template,flags=re.M)
        template=template.replace('fleet-manager-bridge.service',a.unit_prefix+'.service').replace('Unit=fleet-manager-bridge-renew.service','Unit='+a.unit_prefix+'-renew.service')
        template=template.replace('/etc/fleet-manager-bridge-renew.json','/etc/'+a.unit_prefix+'-renew.json')
        write(output / name.replace('fleet-manager-bridge',a.unit_prefix), template, 0o644)
    publisher_policy = c['seats'][-1]['policy']
    write(output / 'pilot-publisher.json', publisher_policy, 0o644)
    if a.publisher_config:
        public = rooted(a.publisher_public_key).read_text().strip().split()
        require(len(public) >= 2 and public[0] == 'ssh-ed25519' and re.fullmatch('[A-Za-z0-9+/=]+', public[1]),
                'publisher_ed25519_public_key_required')
        command = f'/usr/bin/python3 -I {package}/setup-forge-box.py publish-credential --policy-file /etc/{a.unit_prefix}-publisher.json'
        write(output / 'publisher-authorized-keys.fragment', 'restrict,command="' + command + '" ' +
              public[0] + ' ' + public[1] + ' fleet-credential-publisher\n', 0o644)
    return dict(rendered=True, allowlist=str(output / 'allowlist.json'), units=2, servicesStarted=0)

def public_ed25519(value):
    parts=value.strip().split()
    require(len(parts)>=2 and parts[0]=='ssh-ed25519' and re.fullmatch('[A-Za-z0-9+/=]+',parts[1]),'ed25519_public_key_required')
    return ' '.join(parts[:2])


def source_keygen(a):
    cfg=absolute(a.config_root); m=load_setup(cfg)
    require(m.get('pilot') is True,'pilot_setup_required')
    key=cfg/'transport/forge-ed25519'
    require(not key.exists() and not Path(str(key)+'.pub').exists(),'transport_key_exists')
    result=subprocess.run(['/usr/bin/ssh-keygen','-q','-t','ed25519','-N','','-C','fleet-forge-source','-f',str(key)],capture_output=True)
    require(result.returncode==0,'transport_keygen_failed')
    key.chmod(0o600); Path(str(key)+'.pub').chmod(0o600)
    return dict(keyCreated=True,publicKeyFile=str(key)+'.pub',servicesStarted=0)


def configure_transport(a):
    require(os.geteuid()==0,'configure_transport_requires_root')
    package=rooted(Path(__file__).resolve().parent,directory_only=True)
    cfg=absolute(a.config_root); owner=cfg.stat().st_uid
    m=read(cfg/'setup.json')
    require(owner==m['serviceUid'] and owner!=0 and m.get('pilot') is True,'pilot_setup_required')
    require(m['serviceUid']==m['remoteServiceUid'] and m['serviceUser']=='fleet-bridge','transport_service_identity')
    for field in ('stateRoot','configRoot','runRoot','remoteStateRoot','remoteConfigRoot','remoteRunRoot'):
        require('pilot' in str(absolute(m[field])),'pilot_named_roots_required')
    require(Path(m['runRoot']).is_relative_to(m['stateRoot'])
            and Path(m['remoteRunRoot']).is_relative_to(m['remoteStateRoot']),'pilot_run_within_state_required')
    require(all(isinstance(m[field],int) and 0<m[field]<2**32-1 and m[field]!=owner
                for field in ('agentUid','remoteAgentUid')),'transport_endpoint_identity')
    request=read(rooted(a.input,private=True))
    require(request['forgeAddress']=='10.200.68.55' and request.get('port',22)==22,'transport_route_fixed')
    address=str(ipaddress.ip_address(request['sourceAddress']))
    require(ipaddress.ip_address(address).is_private and address!='10.200.68.55','transport_private_source_required')
    public=public_ed25519(request['clientPublicKey']); hostkey=public_ed25519(request['sourceHostKey'])
    actor=actor_ids(owner,cfg.stat().st_gid)
    identity=cfg/'transport/forge-ed25519'
    info=identity.lstat()
    require(stat.S_ISREG(info.st_mode) and info.st_uid==owner and stat.S_IMODE(info.st_mode)==0o600,'transport_key_permissions')
    actual=actor_run(actor,['/usr/bin/ssh-keygen','-y','-f',identity]).decode()
    require(public_ed25519(actual)==public,'transport_public_key_mismatch')
    snapshot=json.loads(actor_run(actor,[package/'bin/codex-factory-bridge','inspect','--state',Path(m['stateRoot'])/'service']))
    require(snapshot['host']==m['hub'],'transport_hub_identity')
    for role in ROLES:
        b=read(cfg/(role+'.binding.json'))
        require(b['grant']['seat']==m['seats'][role] and b['serviceUid']==owner,'transport_binding_identity')
        existing=next((x for x in snapshot['seats'] if x['seat']==m['seats'][role]),None)
        if existing:require(existing['grant']==b['grant'] and not existing['revoked'],'transport_enrolled_grant_conflict')
    if request.get('renewalConfig'):
        # An existing R generation is inspected by its own pinned helper. It is
        # never edited or executed as a broker during transport staging.
        old=read(rooted(request['renewalConfig'],private=True))
        old_package=rooted(old['package'],directory_only=True)
        old_helper=rooted(old_package/'setup-forge-box.py')
        with old_helper.open('rb') as stream:
            require(hashlib.file_digest(stream,'sha256').hexdigest()==old['files']['setup-forge-box.py'],'transport_renewal_code_changed')
        code='import runpy,sys;runpy.run_path(sys.argv[1])["validate_allowlist"](sys.argv[2])'
        verified=subprocess.run(['/usr/bin/python3','-I','-c',code,str(old_helper),str(request['renewalConfig'])],capture_output=True,timeout=20)
        require(verified.returncode==0,'transport_renewal_config_invalid')
        require(old['service']['uid']==owner and old['service']['state']==str(Path(m['stateRoot'])/'service')
                and old['hosts']==[dict(host=m['remoteHost'],keyId=m['transportKey'],bridgeUid=owner)],'transport_renewal_identity_conflict')
        require({x['role'] for x in old['seats']}==set(ROLES),'transport_renewal_seat_conflict')
        for seat in old['seats']:
            require(seat['policy']==read(cfg/(seat['role']+'.binding.json')),'transport_renewal_binding_conflict')
    return transport_stage(m,request,a.output,package,identity,public,hostkey)


def transport_stage(m,request,output,package,identity,public,hostkey):
    cfg=Path(m['configRoot']);owner=m['serviceUid']
    address=str(ipaddress.ip_address(request['sourceAddress']))
    output=absolute(output); output.mkdir(mode=0o700); rooted(output,directory_only=True)
    generation=str(uuid.uuid4()); prefix='fleet-manager-bridge-pilot'
    entry_path='/etc/ssh/fleet-bridge-pilot-entry.json'; launch_config='/etc/ssh/fleet-bridge-pilot-launch.json'
    login='/etc/ssh/fleet-bridge-pilot-login'; launcher='/etc/ssh/fleet-bridge-pilot-launcher'
    entry=dict(generation=generation,sourceHost=m['remoteHost'],transportKey=m['transportKey'],hub=m['hub'],
               serviceUid=owner,allowedAgentUids=[m['remoteAgentUid']],state=str(Path(m['remoteStateRoot'])/'source'),
               socket=str(Path(m['remoteRunRoot'])/'remote.sock'))
    write(output/'entry.json',entry,0o644)
    wrapper=lambda function: '#!/usr/bin/python3 -I\nimport runpy\nrunpy.run_path('+repr(str(package/'fleet-bridge-ssh-entry.py'))+')["'+function+'"]('+repr(launch_config)+')\n'
    write(output/'fleet-bridge-pilot-login',wrapper('dispatch'),0o755)
    write(output/'fleet-bridge-pilot-launcher',wrapper('launch'),0o755)
    file_digest=lambda path: hashlib.sha256(Path(path).read_bytes()).hexdigest()
    launch=dict(generation=generation,sourceHost=m['remoteHost'],transportKey=m['transportKey'],hub=m['hub'],
         serviceUid=owner,forgeAddress='10.200.68.55',package=str(package),login=login,launcher=launcher,entryConfig=entry_path,
         files={str(package/'bin/codex-factory-bridge'):file_digest(rooted(package/'bin/codex-factory-bridge')),
                str(package/'fleet-bridge-ssh-entry.py'):file_digest(rooted(package/'fleet-bridge-ssh-entry.py')),
                login:file_digest(output/'fleet-bridge-pilot-login'),launcher:file_digest(output/'fleet-bridge-pilot-launcher'),
                entry_path:file_digest(output/'entry.json')})
    write(output/'launch.json',launch,0o644)
    write(output/'fleet-bridge-pilot.keys','restrict,from="10.200.68.55",command="'+launcher+'" '+public+' fleet-forge-source\n',0o644)
    match='\n'.join(['Match User fleet-bridge','    AuthorizedKeysFile /etc/ssh/fleet-bridge-pilot.keys',
         '    AuthorizedKeysCommand none','    PubkeyAcceptedAlgorithms ssh-ed25519','    PubkeyAuthentication yes',
         '    AuthenticationMethods publickey','    PasswordAuthentication no','    KbdInteractiveAuthentication no',
         '    PermitTTY no','    AllowTcpForwarding no','    X11Forwarding no','    AllowAgentForwarding no',
         '    PermitTunnel no','    DisableForwarding yes','    PermitUserRC no',''])
    write(output/'fleet-bridge-pilot-match.conf',match,0o644)
    source=dict(generation=generation,sourceAddress='10.200.68.55',serviceState=str(Path(m['stateRoot'])/'service'),
         transport=dict(sourceHost=m['remoteHost'],transportKey=m['transportKey'],hub=m['hub'],address=address,port=22,
             user='fleet-bridge',identityFile=str(identity),knownHosts=str(cfg/'transport/source_known_hosts'),
             allowedAgentUids=[m['remoteAgentUid']]))
    write(output/'source.json',source)
    write(output/'source_known_hosts','factory-source-'+m['remoteHost']+' '+hostkey+'\n')
    template=rooted(package/'systemd/fleet-manager-bridge-source.service.in').read_text()
    for token,value in dict(PACKAGE=str(package),PREFIX=prefix,STATE=m['stateRoot'],CONFIG=str(cfg)).items():template=template.replace('@'+token+'@',value)
    write(output/(prefix+'-source.service'),template,0o644)
    hub='''[Unit]
Description=Throwaway pilot fleet bridge hub
After=network.target
[Service]
Type=simple
User=fleet-bridge
Group=fleet-bridge
SupplementaryGroups=fleet-bridge-clients
UMask=0077
ExecStart=PACKAGE/bin/codex-factory-bridge serve --state STATE/service --socket RUN/bridge.sock
Restart=no
KillMode=control-group
TimeoutStopSec=10
NoNewPrivileges=yes
ProtectHome=yes
ProtectSystem=strict
ReadWritePaths=STATE
[Install]
WantedBy=multi-user.target
'''.replace('PACKAGE',str(package)).replace('STATE',m['stateRoot']).replace('RUN',m['runRoot'])
    write(output/(prefix+'.service'),hub,0o644)
    updated=dict(m,binDir=str(package/'bin'),transportGeneration=generation,transportDirection='forge-initiated')
    m=updated
    write(output/'pilot-plan.json',dict(schema=2,pilot=True,renewal='off',generation=generation,setup=m),0o644)
    pilot_artifacts(output,m,request,package,generation)
    files={p.name:dict(sha256=file_digest(p),mode=oct(stat.S_IMODE(p.stat().st_mode))) for p in output.iterdir() if p.is_file()}
    manifest=dict(schema=1,pilot=True,generation=generation,package=str(package),stateRoot=m['stateRoot'],configRoot=str(cfg),
            remoteStateRoot=m['remoteStateRoot'],remoteConfigRoot=m['remoteConfigRoot'],sourceHost=m['remoteHost'],hub=m['hub'],files=files)
    write(output/'generation.json',manifest,0o644)
    return dict(staged=True,pilot=True,generation=generation,output=str(output),servicesStarted=0)


def transport_probe_command(c, mode):
    t=c['transport']
    return ['/usr/bin/ssh','-F','/dev/null',('-tt' if mode=='pty' else '-T'),
        '-oBatchMode=yes','-oIdentitiesOnly=yes','-oIdentityAgent=none','-oPreferredAuthentications=publickey',
        '-oPasswordAuthentication=no','-oKbdInteractiveAuthentication=no','-oStrictHostKeyChecking=yes',
        '-oGlobalKnownHostsFile=/dev/null','-oCheckHostIP=no','-oUpdateHostKeys=no','-oControlMaster=no','-oControlPath=none',
        '-oForwardAgent=no','-oForwardX11=no','-oProxyCommand=none','-oProxyJump=none','-oPermitLocalCommand=no',
        '-oConnectTimeout=3','-oConnectionAttempts=1','-oLogLevel=ERROR','-oEscapeChar=none',
        '-oUserKnownHostsFile='+t['knownHosts'],'-oHostKeyAlias=factory-source-'+t['sourceHost'],
        '-oBindAddress='+c['sourceAddress'],'-i',t['identityFile'],'-l',t['user'],'-p',str(t['port'])]


def verify_launcher(a):
    path=absolute(a.config); c=read(path); t=c['transport']
    require(c['sourceAddress']=='10.200.68.55' and t['port']==22 and t['user']=='fleet-bridge','transport_route_fixed')
    uid=path.stat().st_uid; require(uid!=0 and stat.S_IMODE(path.stat().st_mode)==0o600,'transport_config_permissions')
    actor=actor_ids(uid,path.stat().st_gid)
    env={'PATH':'/usr/bin:/bin','LC_ALL':'C','HOME':'/'}
    kwargs=dict(user=actor['uid'],group=actor['gid'],extra_groups=actor['groups']) if os.geteuid()==0 else {}
    require(os.geteuid() in (0,uid),'transport_verifier_uid')
    def probe(mode,tail):
        command=transport_probe_command(c,mode)+tail+['--',t['address']]
        if mode!='forward':command += ['/bin/sh' if mode=='shell' else 'codex-factory-v1']
        return subprocess.run(command,input=b'',capture_output=True,timeout=10,env=env,**kwargs)
    positive=probe('protocol',[])
    lines=positive.stdout.splitlines(); require(positive.returncode==0 and len(lines)==1,'launcher_protocol_not_authenticated')
    hello=json.loads(lines[0])
    require(set(hello)=={'protocol','generation','sourceHost','keyId','hub','serviceUid','bootId','available'}
            and hello['protocol']==2 and hello['generation']==c['generation'] and hello['sourceHost']==t['sourceHost']
            and hello['keyId']==t['transportKey'] and hello['hub']==t['hub'] and hello['serviceUid']==uid
            and uuid.UUID(hello['bootId']).int and isinstance(hello['available'],bool),
            'launcher_protocol_identity_denied')
    if getattr(a,'expected_source_boot',None):
        require(hello['bootId']==a.expected_source_boot,'launcher_source_boot_changed')
    shell=probe('shell',[]); require(shell.returncode==1 and not shell.stdout,'launcher_shell_not_refused')
    # OpenSSH exits 255 when -tt requires a PTY that the authenticated server
    # explicitly refuses; some versions continue the non-PTY command instead.
    pty=probe('pty',[]); require(pty.returncode in (0,255) and b'PTY allocation request failed' in pty.stderr,'launcher_pty_not_refused')
    forwarding=probe('forward',['-N','-oExitOnForwardFailure=yes','-R','127.0.0.1:0:127.0.0.1:1'])
    require(forwarding.returncode!=0 and b'remote port forwarding failed' in forwarding.stderr,'launcher_forward_not_refused')
    return ['AUTHENTICATED_PROTOCOL PASS','SHELL REFUSED','PTY REFUSED','FORWARD REFUSED']

# Operator revision 2: public offline generation, local root verification, gated activation.
PILOT_STAGE='/var/lib/fleet-codex-pilot-stage'
KEY_DIR='/etc/fleet-bridge-pilot-ssh'
PILOT_PREFIX='fleet-manager-bridge-pilot'
EXPECTED_SSHD={
    'authorizedkeysfile':'/etc/ssh/fleet-bridge-pilot.keys','authorizedkeyscommand':'none',
    'pubkeyacceptedalgorithms':'ssh-ed25519','pubkeyauthentication':'yes','authenticationmethods':'publickey',
    'passwordauthentication':'no','kbdinteractiveauthentication':'no','permittty':'no','allowtcpforwarding':'no',
    'x11forwarding':'no','allowagentforwarding':'no','permittunnel':'no','disableforwarding':'yes',
    'permituserrc':'no','permituserenvironment':'no','usepam':'yes'}


def file_sha(path):
    with Path(path).open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()


def prepare_pilot(a):
    require(os.geteuid()==0,'prepare_requires_root')
    package=rooted(Path(__file__).resolve().parent,directory_only=True)
    r=read(rooted(a.input,private=True))
    require(r['forgeAddress']=='10.200.68.55' and r.get('port',22)==22,'transport_route_fixed')
    address=str(ipaddress.ip_address(r['sourceAddress']))
    require(ipaddress.ip_address(address).is_private and address!='10.200.68.55','transport_private_source_required')
    ids={name:r[name] for name in ('serviceUid','serviceGid','clientsGid','agentUid','agentGid','remoteAgentUid','remoteAgentGid')}
    require(all(isinstance(v,int) and 0<v<2**32-1 for v in ids.values()),'pilot_identity_invalid')
    require(ids['serviceUid'] not in (ids['agentUid'],ids['remoteAgentUid']),'separate_uids_required')
    require(r.get('sshUnit','ssh.service') in ('ssh.service','sshd.service'),'ssh_unit_invalid')
    r=dict(r,sshUnit=r.get('sshUnit','ssh.service'))
    m=dict(binDir=str(package/'bin'),configRoot='/etc/fleet-manager-bridge-pilot',stateRoot='/var/lib/fleet-manager-bridge-pilot',
        runRoot='/var/lib/fleet-manager-bridge-pilot/run',remoteConfigRoot='/etc/fleet-manager-bridge-remote-pilot',
        remoteStateRoot='/var/lib/fleet-manager-bridge-remote-pilot',remoteRunRoot='/var/lib/fleet-manager-bridge-remote-pilot/run',
        remoteServiceUid=ids['serviceUid'],hub=str(uuid.uuid4()),remoteHost=str(uuid.uuid4()),transportKey=str(uuid.uuid4()),
        seats={x:str(uuid.uuid4()) for x in ROLES},incarnations={x:str(uuid.uuid4()) for x in ROLES},pending={},project=PROJECT,
        sshUser='fleet-bridge',serviceUser='fleet-bridge',pilot=True,remoteAdmitted=False,operatorAssetsRevision=2,renewal='off',
        forgeLoginShell='/usr/sbin/nologin',sshUnit=r['sshUnit'],**ids)
    return transport_stage(m,r,a.output,package,Path(KEY_DIR)/'forge-ed25519',
                           public_ed25519(r['clientPublicKey']),public_ed25519(r['sourceHostKey']))


def pilot_artifacts(output,m,request,package,generation):
    write(output/'sshd-include.fragment','Match all\nInclude /etc/ssh/fleet-bridge-pilot-match.conf\nMatch all\n',0o644)
    write(output/'sshd-expected.txt',''.join(k+' '+v+'\n' for k,v in EXPECTED_SSHD.items()),0o644)
    write(output/'key-install.json',dict(directory=KEY_DIR,owner='root',group='fleet-bridge',directoryMode='0750',
          privateKey=str(Path(KEY_DIR)/'forge-ed25519'),keyOwner='fleet-bridge',keyMode='0600',passphrase='empty',
          publicKey=public_ed25519(request['clientPublicKey']),privateBytesInPublicStage=False),0o644)
    write(output/'sshd-integration.txt','Install sshd-include.fragment verbatim through the reviewed host configuration at a position after global-only settings.\n'
          'Keep another authenticated operator session OPEN until fresh-session verification succeeds.\n'
          'No restart and no reload-or-restart. The generated procedure validates/readbacks before reload.\n',0o644)
    procedure='''#!/bin/sh
set -eu
# Run locally as Ops/root ONLY after the one ucs-ops PR/executive approval.
# KEEP ANOTHER AUTHENTICATED OPERATOR SESSION OPEN THROUGH FRESH-SESSION VERIFICATION.
test "$(id -u)" -eq 0
test "${FALLBACK_SESSION_OPEN:-}" = yes || { echo fallback_session_required >&2; exit 1; }
/usr/bin/python3 -I PACKAGE/setup-forge-box.py validate-sshd --stage STAGE
/usr/bin/systemctl reload SSHUNIT
/usr/bin/python3 -I PACKAGE/setup-forge-box.py verify-host --stage STAGE --host dev-3 --report /etc/fleet-bridge-pilot-dev3-verified.json
'''.replace('PACKAGE',str(package)).replace('STAGE',PILOT_STAGE).replace('SSHUNIT',m.get('sshUnit','ssh.service'))
    write(output/'dev3-validate-reload-verify.sh',procedure,0o755)
    forge='''#!/bin/sh
set -eu
# Run locally as Ops/root ONLY after PR approval and transfer of the fresh dev-3 root report.
/usr/bin/python3 -I PACKAGE/setup-forge-box.py verify-host --stage STAGE --host forge --report /etc/fleet-bridge-pilot-forge-verified.json
/usr/bin/python3 -I PACKAGE/setup-forge-box.py verify-pilot --stage STAGE --forge-report /etc/fleet-bridge-pilot-forge-verified.json --source-report /etc/fleet-bridge-pilot-dev3-verified.json --output /etc/fleet-bridge-pilot-verified.json
/usr/bin/python3 -I PACKAGE/setup-forge-box.py activate-pilot --stage STAGE --verification /etc/fleet-bridge-pilot-verified.json
'''.replace('PACKAGE',str(package)).replace('STAGE',PILOT_STAGE)
    write(output/'forge-verify-activate.sh',forge,0o755)
    write(output/'pilot-contract.txt','Renewal OFF; no publisher, allowlist, renew unit/timer or publisher key.\n'
          'Only fleet-manager-bridge-pilot.service and fleet-manager-bridge-pilot-source.service are enabled AFTER both host checks and all four launcher lines.\n'
          'Credentials last 3600 seconds from each enrollment; finish before the earliest expiresAt.\n'
          'Display ALL public staging files whole in ONE Ops PR; executive review BEFORE any host step. Private key staging stays separate and is NEVER displayed.\n',0o644)


def load_generation(stage):
    require(os.geteuid()==0,'pilot_requires_root')
    stage=rooted(stage,directory_only=True);g=read(rooted(stage/'generation.json'))
    require(g['pilot'] is True,'pilot_generation_required')
    for name,info in g['files'].items():
        require(Path(name).name==name,'generation_path_invalid')
        path=rooted(stage/name)
        require(file_sha(path)==info['sha256'] and stat.S_IMODE(path.stat().st_mode)==int(info['mode'],8),'generation_file_changed')
    require({x.name for x in stage.iterdir()}==set(g['files'])|{'generation.json'},'generation_inventory_changed')
    p=read(rooted(stage/'pilot-plan.json'))
    require(p['generation']==g['generation'] and p['renewal']=='off','pilot_generation_conflict')
    require(Path(p['setup']['binDir']).parent==Path(__file__).resolve().parent,'pilot_wrong_package')
    return stage,g,p['setup']


def root_query(args):
    p=subprocess.run(list(map(str,args)),capture_output=True,text=True,timeout=15,
        env={'PATH':'/usr/bin:/usr/sbin:/bin:/sbin','LC_ALL':'C','HOME':'/'})
    return p


def account_check(m,host,installed=True):
    actual=pwd.getpwnam('fleet-bridge');expected=m['forgeLoginShell'] if host=='forge' else '/etc/ssh/fleet-bridge-pilot-login'
    require(actual.pw_uid==m['serviceUid']!=0 and actual.pw_gid==m['serviceGid'],'account_uid_denied')
    require(actual.pw_shell==expected,'account_shell_denied')
    password=root_query(['/usr/bin/passwd','-S','fleet-bridge'])
    fields=password.stdout.split()
    require(password.returncode==0 and len(fields)>=2 and fields[:2]==['fleet-bridge','L'],'account_password_not_locked')
    sudo=root_query(['/usr/bin/sudo','-n','-l','-U','fleet-bridge'])
    listing=(sudo.stdout+'\n'+sudo.stderr).strip()
    denial=r'User fleet-bridge is not allowed to run sudo on [^\r\n]+\.'
    # Root's -U query may return 0 even when its target has no privileges.
    # An explicit complete-policy denial, never exit status alone, is proof.
    require(sudo.returncode in (0,1) and re.search('^'+denial+'$',listing,re.M)
            and (not sudo.stderr.strip() or re.fullmatch(denial,sudo.stderr.strip()))
            and not re.search(r'(^|\n)sudo:',listing),'account_sudo_not_denied')
    if host=='dev-3' and installed:rooted(expected);require(stat.S_IMODE(Path(expected).stat().st_mode)==0o755,'account_dispatcher_mode')
    print(password.stdout.strip(),flush=True);print(listing,flush=True)
    print('ACCOUNT '+host+' fleet-bridge LOCKED SHELL_VERIFIED NO_SUDO PASS',flush=True)
    return dict(uid=actual.pw_uid,gid=actual.pw_gid,shell=expected,passwordStatus=password.stdout.strip(),sudoPolicy=listing)


def require_units_off():
    for unit in (PILOT_PREFIX+'.service',PILOT_PREFIX+'-source.service'):
        for operation,allowed in [('is-active',(3,4)),('is-enabled',(1,4))]:
            p=root_query(['/usr/bin/systemctl',operation,unit])
            require(p.returncode in allowed,'pilot_unit_not_drained')
    for name in (PILOT_PREFIX+'-renew.service',PILOT_PREFIX+'-renew.timer'):
        require(not Path('/etc/systemd/system',name).exists(),'pilot_renewal_must_be_off')
        state=root_query(['/usr/bin/systemctl','show','-p','LoadState','--value',name])
        require(state.returncode in (0,1,4) and state.stdout.strip()=='not-found','pilot_renewal_must_be_off')
    require(not Path('/etc/'+PILOT_PREFIX+'-renew.json').exists(),'pilot_renewal_must_be_off')


def validate_sshd(a):
    stage,g,m=load_generation(a.stage)
    binary=Path(shutil.which('sshd') or '/usr/sbin/sshd').resolve();rooted(binary)
    p=root_query([binary,'-t']);require(p.returncode==0,'sshd_configuration_invalid')
    p=root_query([binary,'-T','-C','user=fleet-bridge,host=dev-3,addr=10.200.68.55'])
    require(p.returncode==0,'sshd_readback_failed')
    values={line.split(None,1)[0].lower():line.split(None,1)[1].lower() for line in p.stdout.splitlines() if len(line.split(None,1))==2}
    for key,value in EXPECTED_SSHD.items():require(values.get(key)==value,'sshd_option_mismatch')
    for name,target,mode in [('entry.json','fleet-bridge-pilot-entry.json',0o644),('launch.json','fleet-bridge-pilot-launch.json',0o644),
          ('fleet-bridge-pilot.keys','fleet-bridge-pilot.keys',0o644),('fleet-bridge-pilot-match.conf','fleet-bridge-pilot-match.conf',0o644),
          ('fleet-bridge-pilot-login','fleet-bridge-pilot-login',0o755),('fleet-bridge-pilot-launcher','fleet-bridge-pilot-launcher',0o755)]:
        path=rooted(Path('/etc/ssh')/target)
        require(stat.S_IMODE(path.stat().st_mode)==mode and file_sha(path)==file_sha(stage/name),'installed_entry_changed')
    for key,value in EXPECTED_SSHD.items():print(key+' '+value,flush=True)
    return values


def verify_private_key(m,stage):
    key=Path(KEY_DIR)/'forge-ed25519';parent=rooted(KEY_DIR,directory_only=True)
    require(parent.stat().st_gid==m['serviceGid'] and stat.S_IMODE(parent.stat().st_mode)==0o750,'transport_key_directory')
    info=key.lstat()
    require(stat.S_ISREG(info.st_mode) and info.st_uid==m['serviceUid'] and info.st_nlink==1
            and stat.S_IMODE(info.st_mode)==0o600,'transport_key_permissions')
    p=root_query(['/usr/bin/ssh-keygen','-y','-P','','-f',key]);require(p.returncode==0,'transport_key_not_passphrase_less')
    require(public_ed25519(p.stdout)==read(stage/'key-install.json')['publicKey'],'transport_public_key_mismatch')


def verify_host(a):
    stage,g,m=load_generation(a.stage);invalidate_verification(a.report);require_units_off()
    result=account_check(m,a.host)
    if a.host=='dev-3':result['sshd']=validate_sshd(a)
    else:verify_private_key(m,stage)
    result.update(schema=2,host=a.host,generation=g['generation'],stageSha256=file_sha(stage/'generation.json'),
          verifiedAt=int(time.time()),bootId=Path('/proc/sys/kernel/random/boot_id').read_text().strip())
    write(absolute(a.report),result,replace=True)
    return dict(verified=True,host=a.host,report=str(a.report))


def fresh_report(path,host,g,stage):
    value=read(rooted(path,private=True))
    require(value['host']==host and value['generation']==g['generation'] and value['stageSha256']==file_sha(stage/'generation.json'),
            'host_report_generation_denied')
    require(0<=int(time.time())-value['verifiedAt']<=300,'host_report_not_fresh')
    require(uuid.UUID(value['bootId']).int,'host_report_boot_invalid')
    if host=='forge':require(value['bootId']==Path('/proc/sys/kernel/random/boot_id').read_text().strip(),'host_report_boot_changed')
    return value


def verify_pilot(a):
    stage,g,m=load_generation(a.stage);invalidate_verification(a.output);require_units_off()
    for path,host in [(a.forge_report,'forge'),(a.source_report,'dev-3')]:fresh_report(path,host,g,stage)
    account_check(m,'forge');verify_private_key(m,stage)
    source=fresh_report(a.source_report,'dev-3',g,stage)
    lines=verify_launcher(argparse.Namespace(config=str(Path(m['configRoot'])/'transport/source.json'),expected_source_boot=source['bootId']))
    for line in lines:print(line,flush=True)
    write(absolute(a.output),dict(schema=2,generation=g['generation'],stageSha256=file_sha(stage/'generation.json'),verifiedAt=int(time.time()),
          bootId=Path('/proc/sys/kernel/random/boot_id').read_text().strip(),forgeReport=str(absolute(a.forge_report)),
          sourceReport=str(absolute(a.source_report)),forgeSha256=file_sha(a.forge_report),sourceSha256=file_sha(a.source_report),lines=lines),replace=True)
    return dict(verified=True,output=str(a.output))


def invalidate_verification(path):
    path=absolute(path);rooted(path.parent,directory_only=True)
    if path.exists():rooted(path,private=True).unlink()


def activate_pilot(a):
    stage,g,m=load_generation(a.stage);proof=read(rooted(a.verification,private=True))
    require(proof['generation']==g['generation'] and proof['stageSha256']==file_sha(stage/'generation.json'),'activation_generation_denied')
    require(0<=int(time.time())-proof['verifiedAt']<=300 and proof['bootId']==Path('/proc/sys/kernel/random/boot_id').read_text().strip(),'activation_not_fresh')
    require(proof['lines']==['AUTHENTICATED_PROTOCOL PASS','SHELL REFUSED','PTY REFUSED','FORWARD REFUSED'],'activation_launcher_not_verified')
    for name,host in [('forge','forge'),('source','dev-3')]:
        path=proof[name+'Report'];fresh_report(path,host,g,stage)
        require(file_sha(path)==proof[name+'Sha256'],'activation_report_changed')
    require_units_off();account_check(m,'forge');verify_private_key(m,stage)
    source=fresh_report(proof['sourceReport'],'dev-3',g,stage)
    require(verify_launcher(argparse.Namespace(config=str(Path(m['configRoot'])/'transport/source.json'),expected_source_boot=source['bootId']))==proof['lines'],
            'activation_launcher_not_verified')
    actor=actor_ids(m['serviceUid'],m['clientsGid'])
    actor_run(actor,[sys.executable,'-I',Path(__file__).resolve(),'admit-remote','--config-root',m['configRoot']])
    for name in (PILOT_PREFIX+'.service',PILOT_PREFIX+'-source.service'):
        p=root_query(['/usr/bin/systemctl','enable','--now',name]);require(p.returncode==0,'pilot_activation_failed')
    return dict(activated=True,renewal='off',units=[PILOT_PREFIX+'.service',PILOT_PREFIX+'-source.service'])


def apply_pilot(a):
    stage,g,m=load_generation(a.stage)
    require(a.approved_generation==g['generation'],'ops_review_required')
    require(a.host in ('forge','dev-3'),'pilot_host_invalid')
    account_check(m,a.host,installed=False)
    def directory_fd(path):
        path=absolute(str(path));fd=os.open('/',os.O_RDONLY|os.O_DIRECTORY)
        try:
            for part in path.parts[1:]:
                try:next_fd=os.open(part,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=fd)
                except FileNotFoundError:
                    os.mkdir(part,0o700,dir_fd=fd)
                    next_fd=os.open(part,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=fd)
                os.close(fd);fd=next_fd
            return fd
        except BaseException:os.close(fd);raise
    def make(path,owner,group,mode):
        fd=directory_fd(path)
        try:os.fchown(fd,owner,group);os.fchmod(fd,mode)
        finally:os.close(fd)
    def put(data,destination,owner=0,group=0,mode=0o644):
        destination=absolute(str(destination));parent=directory_fd(destination.parent)
        try:
            fd=os.open(destination.name,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600,dir_fd=parent)
            with os.fdopen(fd,'wb') as stream:
                stream.write(data);stream.flush();os.fchown(stream.fileno(),owner,group);os.fchmod(stream.fileno(),mode);os.fsync(stream.fileno())
            os.fsync(parent)
        finally:os.close(parent)
    def install(source,destination,owner=0,group=0,mode=0o644):
        put(Path(source).read_bytes(),destination,owner,group,mode)
    # The exact login must already be provisioned by the reviewed Ops account task.
    cfg=Path(m['configRoot'] if a.host=='forge' else m['remoteConfigRoot'])
    state=Path(m['stateRoot'] if a.host=='forge' else m['remoteStateRoot'])
    require(not cfg.exists() and not state.exists(),'pilot_fresh_roots_required')
    if a.host=='forge':
        require(a.private_key,'pilot_private_key_required');private=rooted(a.private_key,private=True)
        key_spec=read(stage/'key-install.json')
        pub=root_query(['/usr/bin/ssh-keygen','-y','-P','','-f',private]);require(pub.returncode==0 and public_ed25519(pub.stdout)==key_spec['publicKey'],'transport_public_key_mismatch')
    make(cfg,m['serviceUid'],m['clientsGid'],0o2750);make(state,m['serviceUid'],m['clientsGid'],0o2710)
    make(state/'run',m['serviceUid'],m['clientsGid'],0o2710);make(state/'native',m['serviceUid'],m['clientsGid'],0o710)
    uid=m['agentUid'] if a.host=='forge' else m['remoteAgentUid'];gid=m['agentGid'] if a.host=='forge' else m['remoteAgentGid']
    make(cfg/'credentials',uid,gid,0o700)
    for role in (ROLES[:2] if a.host=='forge' else ROLES[2:]):make(state/'native'/role,uid,gid,0o700)
    if a.host=='forge':
        make(KEY_DIR,0,m['serviceGid'],0o750);install(private,Path(KEY_DIR)/'forge-ed25519',m['serviceUid'],m['serviceGid'],0o600)
        plan=Path('/etc/fleet-bridge-pilot-plan.json');install(stage/'pilot-plan.json',plan)
        args=['init','--pilot','--defer-remote','--pilot-plan',plan,'--bin-dir',m['binDir']]
        for option,key in [('state-root','stateRoot'),('config-root','configRoot'),('run-root','runRoot'),
             ('remote-state-root','remoteStateRoot'),('remote-config-root','remoteConfigRoot'),('remote-run-root','remoteRunRoot'),
             ('agent-uid','agentUid'),('remote-agent-uid','remoteAgentUid'),('remote-service-uid','serviceUid')]:args+=['--'+option,str(m[key])]
        actor_run(actor_ids(m['serviceUid'],m['clientsGid']),[sys.executable,'-I',Path(__file__).resolve(),*args])
        install(stage/'source.json',cfg/'transport/source.json',m['serviceUid'],m['clientsGid'],0o600)
        install(stage/'source_known_hosts',cfg/'transport/source_known_hosts',m['serviceUid'],m['clientsGid'],0o600)
        for name in (PILOT_PREFIX+'.service',PILOT_PREFIX+'-source.service'):install(stage/name,Path('/etc/systemd/system')/name)
        p=root_query(['/usr/bin/systemctl','daemon-reload']);require(p.returncode==0,'unit_reload_failed')
    else:
        targets={'entry.json':'fleet-bridge-pilot-entry.json','launch.json':'fleet-bridge-pilot-launch.json'}
        for name in ('entry.json','launch.json','fleet-bridge-pilot.keys','fleet-bridge-pilot-match.conf','fleet-bridge-pilot-login','fleet-bridge-pilot-launcher'):
            install(stage/name,Path('/etc/ssh')/targets.get(name,name),mode=0o755 if name.endswith(('-login','-launcher')) else 0o644)
        # Construct the same public pilot binding as Forge's planned initialization.
        grant=dict(seat=m['seats']['pilot-seat'],host=m['remoteHost'],incarnation=m['incarnations']['pilot-seat'],agentUid=m['remoteAgentUid'],role='worker',
          projects=[PROJECT],recipients=sorted([m['seats']['pilot-seat'],m['seats']['foreman-bot']]),operations=['observe','queue','acknowledge'])
        binding=dict(serviceUid=m['serviceUid'],socket=m['remoteRunRoot']+'/remote.sock',credential=str(cfg/'credentials/pilot-seat.json'),
              state=str(state/'native/pilot-seat'),grant=grant)
        put((json.dumps(binding,indent=2)+'\n').encode(),cfg/'pilot-seat.binding.json',m['serviceUid'],m['clientsGid'],0o640)
    return dict(applied=True,host=a.host,servicesStarted=0,renewal='off')


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
    init.add_argument('--pilot-plan')
    init.add_argument('--pilot', action='store_true', help='Mark explicitly throwaway pilot roots; never reuse for final setup')
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
            q.add_argument('--runtime-root'); q.add_argument('--unit-prefix',default='fleet-manager-bridge')
        elif cmd == 'credential-bytes': q.add_argument('--file', required=True)
        else:
            group = q.add_mutually_exclusive_group(required=True)
            group.add_argument('--policy-json'); group.add_argument('--policy-file')
    key=sub.add_parser('source-keygen'); key.add_argument('--config-root',required=True)
    transport=sub.add_parser('configure-transport')
    transport.add_argument('--config-root',required=True); transport.add_argument('--input',required=True)
    transport.add_argument('--output',required=True); transport.add_argument('--direction',choices=['forge-initiated'],required=True)
    verify=sub.add_parser('verify-launcher'); verify.add_argument('--config',required=True)
    prepare=sub.add_parser('prepare-pilot');prepare.add_argument('--input',required=True);prepare.add_argument('--output',required=True)
    apply=sub.add_parser('apply-pilot');apply.add_argument('--stage',required=True);apply.add_argument('--host',choices=['forge','dev-3'],required=True)
    apply.add_argument('--approved-generation',required=True);apply.add_argument('--private-key')
    host=sub.add_parser('verify-host');host.add_argument('--stage',required=True);host.add_argument('--host',choices=['forge','dev-3'],required=True);host.add_argument('--report',required=True)
    validate=sub.add_parser('validate-sshd');validate.add_argument('--stage',required=True)
    combined=sub.add_parser('verify-pilot');combined.add_argument('--stage',required=True);combined.add_argument('--forge-report',required=True)
    combined.add_argument('--source-report',required=True);combined.add_argument('--output',required=True)
    activate=sub.add_parser('activate-pilot');activate.add_argument('--stage',required=True);activate.add_argument('--verification',required=True)
    a = p.parse_args()
    handlers={'prepare-pilot':prepare_pilot,'apply-pilot':apply_pilot,'verify-host':verify_host,'validate-sshd':validate_sshd,'verify-pilot':verify_pilot,'activate-pilot':activate_pilot}
    if a.command in handlers:
        print(json.dumps(handlers[a.command](a)));return
    if a.command=='source-keygen': print(json.dumps(source_keygen(a))); return
    if a.command=='configure-transport': print(json.dumps(configure_transport(a))); return
    if a.command=='verify-launcher':
        raise RuntimeError('pilot_use_verify_pilot_with_both_host_reports')
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
