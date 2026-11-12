#!/usr/bin/env python3
"""Offline R proof: private systemd/UID fixture, then the complete package proof."""
import argparse
import copy
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import select
import shutil
import signal
import socket
import stat
import subprocess
import sys
import time
import uuid

REPO = Path(__file__).resolve().parents[2]
SERVICE, AGENT = 65534, 65532
BWRAP = 'bd2ed101e19111f5c06ea4bb16ace19ca10693a56b343265b156937d4434f023'


def sha(path):
    with Path(path).open('rb') as f: return hashlib.file_digest(f, 'sha256').hexdigest()


def run(args, *, uid=None, expected=0, timeout=30, payload=None, env=None):
    identity = dict(user=uid, group=uid, extra_groups=[AGENT]) if uid is not None else {}
    result = subprocess.run(list(map(str, args)), input=payload, capture_output=True,
                            timeout=timeout, env=env, **identity)
    assert result.returncode == expected, 'proof_command_failed: ' + str(args[0]) + ' ' + result.stderr.decode(errors='replace')[:500]
    return result


def fixture(a):
    assert os.geteuid() == 0 and os.path.exists('/run/systemd/system'), 'isolated systemd fixture required'
    assert a.work_dir==Path('/work') and a.package==Path('/opt/fleet-codex/proof')
    assert Path('/run/systemd/container').read_text().strip()=='systemd-nspawn'
    assert Path('/etc/renewal-proof-id').read_text()==Path('/work/fixture-id').read_text(), 'owned fixture marker required'
    root, pkg = a.work_dir, a.package
    binary, helper = pkg / 'bin/codex-factory-bridge', pkg / 'setup-forge-box.py'
    cfg, state, runtime = Path('/etc/fleet-manager-bridge'), Path('/var/lib/fleet-manager-bridge'), Path('/run/fleet-manager-bridge')
    checks, children = [], []
    def directory(path, uid=SERVICE, mode=0o700):
        path.mkdir(parents=True, exist_ok=True); os.chown(path, uid, AGENT); path.chmod(mode)
    def command(*args, uid=SERVICE, expected=0, payload=None):
        p = run(args, uid=uid, expected=expected, payload=payload)
        return json.loads(p.stdout if expected == 0 else p.stderr)
    def setup(*args, uid=SERVICE, expected=0, payload=None):
        return command('/usr/bin/python3', '-I', helper, *args, uid=uid, expected=expected, payload=payload)
    def admin(*args): return command(binary, *args)
    def load(path): return json.loads(Path(path).read_text())
    def save(path, data):
        Path(path).write_text(json.dumps(data)); Path(path).chmod(0o600)
    def snapshot(): return admin('inspect', '--state', state / 'service')
    def credential(role): return cfg / 'credentials' / (role + '.json')
    def binding(role): return cfg / (role + '.binding.json')
    def enroll(role):
        staged = setup('bootstrap', '--config-root', cfg, '--role', role)
        dst = cfg / 'credentials' / (role + '.bootstrap')
        shutil.copyfile(staged['bootstrapFile'], dst); os.chown(dst, AGENT, AGENT); dst.chmod(0o600)
        # The systemd container tests mailbox authority, not nested userns.
        # Packaged sandbox preflight and the full endpoint CLI are proved in
        # the separate mount-only setup fixture below, on the actual kernel.
        result = command('/usr/bin/python3', REPO/'scripts/factory/isolation_client.py',
                '--socket', runtime/'bridge.sock', '--bootstrap', dst,
                '--credential-out', credential(role), uid=AGENT)
        assert result['ok']
    def begin_hub():
        log = (root / 'hub.stderr').open('a')
        p = subprocess.Popen([str(binary), 'serve', '--state', str(state / 'service'), '--socket', str(runtime / 'bridge.sock')],
              user=SERVICE, group=SERVICE, extra_groups=[AGENT], stdout=subprocess.PIPE, stderr=log, start_new_session=True)
        log.close(); children.append(p)
        assert select.select([p.stdout], [], [], 10)[0] and json.loads(p.stdout.readline())['ready']
        return p
    def stop(p):
        if p.poll() is None: os.killpg(p.pid, signal.SIGTERM)
        p.wait(timeout=10)
        if p.stdout: p.stdout.close()
    def mailbox():
        sender=load(binding('spec-manager'))['grant']; receiver=load(binding('foreman-bot'))['grant']
        outbox=state/'native/spec-manager'/('proof-'+uuid.uuid4().hex); directory(outbox,uid=AGENT)
        now=int(time.time()); mid=str(uuid.uuid4())
        envelope=dict(protocol=1,messageId=mid,conversationId=str(uuid.uuid4()),assignmentId=None,replyTo=None,
              sender=sender['seat'],incarnation=sender['incarnation'],recipient=receiver['seat'],project=sender['projects'][0],
              kind='note',mode='queue_only',createdAt=now,expiresAt=now+3600,text='Inert renewal proof.',artifactRefs=[])
        for name,data in [('grant.json',sender),('envelope.json',envelope)]:
            save(outbox/name,data); os.chown(outbox/name,AGENT,AGENT)
        command(binary,'enqueue','--state',outbox/'outbox','--grant',outbox/'grant.json','--envelope',outbox/'envelope.json',uid=AGENT)
        sent=command(binary,'relay','--state',outbox/'outbox','--grant',outbox/'grant.json','--socket',runtime/'bridge.sock',
                     '--service-uid',SERVICE,'--credential',credential('spec-manager'),'--message-id',mid,uid=AGENT)
        sequence=next(s['lastSequence'] for s in snapshot()['seats'] if s['seat']==receiver['seat'])+1
        request=dict(protocol=1,sender=receiver['seat'],host=receiver['host'],incarnation=receiver['incarnation'],sequence=sequence,
                     project=receiver['projects'][0],recipient=receiver['seat'],operation='observe')
        wire=state/'native/foreman-bot'/('request-'+uuid.uuid4().hex+'.json')
        save(wire,dict(method='receive',protocol=1,request=request,message_id=mid)); os.chown(wire,AGENT,AGENT)
        received=command('/usr/bin/python3',REPO/'scripts/factory/isolation_client.py','--socket',runtime/'bridge.sock',
                         '--credential',credential('foreman-bot'),'--wire',wire,uid=AGENT)
        assert sent['state'] == 'service_accepted' and len(received['receipts']) == 2
        return mid
    allowlist = Path('/etc/fleet-manager-bridge-renew.json')
    def broker(expected=0):
        p = run(['/usr/bin/python3', '-I', helper, 'renew', '--config', allowlist], expected=expected)
        if expected:
            line = p.stderr.decode().strip()
            assert re.fullmatch(r'renewal_failed host=(?:none|[0-9a-f-]{36}) seat=(?:none|[0-9a-f-]{36}) rotation=(?:none|[0-9a-f-]{36}) code=[a-z][a-z0-9_]*', line), line
            assert not p.stdout
            for role in ('spec-manager', 'foreman-bot'):
                assert load(credential(role))['credential'] not in line
            return line
        return json.loads(p.stdout)
    def force_due(role):
        out = state / 'pending' / ('force-' + uuid.uuid4().hex)
        admin('rotate', '--state', state / 'service', '--seat', meta['seats'][role], '--output', out,
              '--credential-ttl', 2, '--overlap-seconds', 60)
        candidate = load(out)
        out.unlink()  # This fixture-owned legacy candidate is deliberately not published.
        return candidate
    def publish(policy, data, expected=0):
        return run(['/usr/bin/python3', '-I', helper, 'publish-credential', '--policy-json', json.dumps(policy)],
                   uid=AGENT, payload=json.dumps(data).encode(), expected=expected)
    try:
        directory(cfg, mode=0o2750); directory(state, mode=0o2710); directory(runtime, mode=0o2710)
        directory(cfg / 'credentials', uid=AGENT)
        directory(state / 'native', mode=0o710)
        for role in ('spec-manager', 'foreman-bot'): directory(state / 'native' / role, uid=AGENT)
        args = ['init', '--defer-remote', '--bin-dir', pkg / 'bin', '--state-root', state,
                '--config-root', cfg, '--run-root', runtime, '--remote-state-root', '/var/lib/deferred-remote',
                '--remote-config-root', '/etc/deferred-remote', '--remote-run-root', '/run/deferred-remote',
                '--agent-uid', AGENT, '--remote-agent-uid', AGENT, '--remote-service-uid', SERVICE]
        assert setup(*args)['remoteAdmitted'] is False
        meta = load(cfg / 'setup.json'); identity = copy.deepcopy(meta)
        assert snapshot()['remoteHosts'] == {} and 'pilot-seat' not in meta['pending']
        assert setup('bootstrap', '--config-root', cfg, '--role', 'pilot-seat', expected=1)['error'] == 'remote_admission_deferred'
        hub = begin_hub(); enroll('spec-manager'); enroll('foreman-bot'); original_message = mailbox()
        checks.append('deferred_init_two_local_seats_send_receive_before_any_remote_admission')
        stage = Path('/var/lib/fleet-manager-bridge-renew/staged')
        setup('render-renewal', '--config-root', cfg, '--output', stage, uid=0)
        shutil.copyfile(stage / 'allowlist.json', allowlist); allowlist.chmod(0o600)
        config = load(allowlist)
        config.update(seatTtl=12, seatRenewBefore=9, overlapSeconds=2, hostTtl=20, hostRenewBefore=10)
        save(allowlist, config)
        originals = {r: load(credential(r)) for r in ('spec-manager', 'foreman-bot')}
        old_candidates = {role: force_due(role) for role in originals}
        assert len(broker()['seats']) == 2 and snapshot()['remoteHosts'] == {}
        for role in originals:
            path = credential(role)
            assert path.stat().st_uid == AGENT and stat.S_IMODE(path.stat().st_mode) == 0o600
            assert load(path)['credential'] != originals[role]['credential']
        mailbox()
        assert any(m['messageId'] == original_message and len(m['receipts']) == 2 for m in snapshot()['mail'])
        checks.append('local_rotation_atomic_ai_0600_no_restart_sequence_and_receipts_preserved')
        policy = config['seats'][0]['policy']; saved = credential('spec-manager').read_bytes()
        assert int(time.time()) < old_candidates['spec-manager']['expiresAt']
        denied = publish(policy, old_candidates['spec-manager'], expected=1)
        assert 'publication_candidate_not_current' in denied.stderr.decode()
        assert credential('spec-manager').read_bytes() == saved
        checks.append('stale_publisher_cannot_overwrite_new_current_credential_even_during_overlap')
        # A real pinned loopback SSH publisher runs as the endpoint UID. A fixture
        # barrier drops its acknowledgment after the atomic publication completed.
        sshdir = Path('/etc/ssh'); sshdir.mkdir(exist_ok=True)
        Path('/run/sshd').mkdir(exist_ok=True)
        Path('/etc/shadow').write_text('root::1:0:99999:7:::\n'); Path('/etc/shadow').chmod(0o600)
        pubroot = Path('/var/lib/fleet-manager-bridge-renew/publisher'); pubroot.mkdir(mode=0o700)
        for name in ('client', 'host'):
            run(['/usr/bin/ssh-keygen', '-q', '-t', 'ed25519', '-N', '', '-f', pubroot / name])
        remote_policy = Path('/etc/fleet-manager-bridge-publisher.json')
        remote_policy.write_text(json.dumps(config['seats'][1]['policy'])); remote_policy.chmod(0o644)
        arm_ack, hit_ack = pubroot / 'hold-ack', pubroot / 'hit-ack'
        wrapper = sshdir / 'publisher-proof.py'
        wrapper.write_text('import pathlib,subprocess,sys,time\n' +
            'p=subprocess.run(' + repr(['/usr/bin/setpriv','--reuid',str(AGENT),'--regid',str(AGENT),'--groups',str(AGENT),
            '/usr/bin/python3','-I',str(helper),'publish-credential','--policy-file',str(remote_policy)]) +
            ',input=sys.stdin.buffer.read(65537),capture_output=True)\n' +
            'arm=pathlib.Path(' + repr(str(arm_ack)) + ')\n' +
            'if p.returncode==0 and arm.exists():\n pathlib.Path(' + repr(str(hit_ack)) + ').touch()\n while arm.exists():time.sleep(.01)\n' +
            'sys.stdout.buffer.write(p.stdout);sys.stderr.buffer.write(p.stderr);sys.exit(p.returncode)\n')
        wrapper.chmod(0o755)
        key = (pubroot / 'client.pub').read_text().strip()
        auth = sshdir / 'publisher-proof.keys'
        auth.write_text('restrict,command="/usr/bin/python3 ' + str(wrapper) + '" ' + key + '\n'); auth.chmod(0o600)
        with socket.socket() as sock:
            sock.bind(('127.0.0.1',0)); port=sock.getsockname()[1]
        sshconf=sshdir/'publisher-proof.conf'
        sshconf.write_text('\n'.join([f'Port {port}','ListenAddress 127.0.0.1',f'HostKey {pubroot}/host',
            f'PidFile {pubroot}/sshd.pid',f'AuthorizedKeysFile {auth}','AllowUsers root',
            'PermitRootLogin forced-commands-only','UsePAM no','PasswordAuthentication no',
            'KbdInteractiveAuthentication no','StrictModes yes','DisableForwarding yes','PermitTTY no',
            'PermitUserRC no','PermitUserEnvironment no','UseDNS no','LogLevel ERROR','']))
        run(['/usr/bin/sshd','-t','-f',sshconf])
        def start_sshd():
            log=(root/'publisher-sshd.log').open('a')
            process=subprocess.Popen(['/usr/bin/sshd','-D','-e','-f',str(sshconf)],stdout=log,stderr=log,start_new_session=True)
            log.close(); children.append(process); deadline=time.monotonic()+5
            while not (pubroot/'sshd.pid').exists():
                assert process.poll() is None and time.monotonic()<deadline; time.sleep(.02)
            return process
        sshd=start_sshd()
        pin=pubroot/'known_hosts'
        pin.write_text('renewal-publisher-proof '+(pubroot/'host.pub').read_text()); pin.chmod(0o600)
        remote = copy.deepcopy(config); remote['seats']=[remote['seats'][1]]
        remote.update(seatTtl=30,seatRenewBefore=20)
        remote['seats'][0]['publish']=dict(kind='ssh',address='127.0.0.1',port=port,user='root',
             identityFile=str(pubroot/'client'),knownHosts=str(pin),hostKeyAlias='renewal-publisher-proof')
        save(allowlist,remote); force_due('foreman-bot'); assert len(broker()['seats'])==1
        force_due('foreman-bot'); arm_ack.touch(mode=0o600)
        p=subprocess.Popen(['/usr/bin/python3','-I',str(helper),'renew','--config',str(allowlist)],
                           stdout=subprocess.PIPE,stderr=subprocess.PIPE,start_new_session=True); children.append(p)
        deadline=time.monotonic()+10
        while not hit_ack.exists():
            assert p.poll() is None and time.monotonic()<deadline, 'SSH publisher barrier not reached'; time.sleep(.01)
        journal_path=state/'service'/('rotation-'+meta['seats']['foreman-bot']+'.json')
        pending=load(journal_path); activated=credential('foreman-bot').read_bytes()
        os.killpg(p.pid,signal.SIGKILL); p.communicate(timeout=10); arm_ack.unlink(); hit_ack.unlink()
        assert broker()['seats'][0]['rotationId']==pending['id']
        assert credential('foreman-bot').read_bytes()==activated
        assert sum(x['event'].get('rotationId')==pending['id'] for x in snapshot()['audit'])==1
        checks.append('real_pinned_ssh_endpoint_uid_publication_lost_ack_retries_identical_candidate_once')
        stop(sshd); (pubroot/'sshd.pid').unlink(missing_ok=True)
        old_file=credential('foreman-bot').read_bytes(); force_due('foreman-bot')
        broker(expected=1)
        assert credential('foreman-bot').read_bytes()==old_file
        sshd=start_sshd(); assert len(broker()['seats'])==1
        stop(sshd); save(allowlist,config)
        checks.append('publisher_outage_preserves_old_file_and_resumes_pending_after_reconnect')
        fd = os.open('/var/lib/fleet-manager-bridge-renew/renew.lock', os.O_RDWR)
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        assert 'renewal_already_running' in broker(expected=1)
        os.close(fd)
        for mutation, code in [(lambda c: c['files'].update({'setup-forge-box.py': '0'*64}), 'renewal_code_changed'),
                               (lambda c: c['seats'][0]['actor'].update(uid=0), 'renewal_actor_identity'),
                               (lambda c: c.update(seatTtl=3601), 'renewal_seat_ttl')]:
            altered = copy.deepcopy(config); mutation(altered); save(allowlist, altered)
            assert code in broker(expected=1)
        save(allowlist, config); allowlist.chmod(0o640)
        assert 'root_config_mode' in broker(expected=1)
        allowlist.chmod(0o600)
        checks.append('concurrent_broker_wrong_code_uid_ttl_and_nonprivate_allowlist_fail_one_redacted_line')
        # Matching feature-gated fault binary; only this owned fixture copy is changed.
        production_bridge = root / 'production-bridge'; shutil.copy2(binary, production_bridge)
        replacement=binary.with_name('.fixture-bridge')
        shutil.copyfile(a.fault_bridge,replacement); replacement.chmod(0o755); os.replace(replacement,binary)
        config['files']['bin/codex-factory-bridge'] = sha(binary)
        one = copy.deepcopy(config); one['seats'] = [one['seats'][0]]; one['seatTtl'] = 30; one['seatRenewBefore'] = 20
        save(allowlist, one)
        points=['rotation_after_prepare', 'rotation_after_commit', 'rotation_before_output_rename', 'rotation_after_output', 'rotation_before_close']
        for point in points:
            force_due('spec-manager')
            arm, hit = [state / 'service' / (prefix + point) for prefix in ('fault.', 'hit.')]
            arm.touch(mode=0o600); os.chown(arm, SERVICE, AGENT)
            p = subprocess.Popen(['/usr/bin/python3', '-I', str(helper), 'renew', '--config', str(allowlist)],
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
            children.append(p); deadline = time.monotonic() + 10
            while not hit.exists():
                assert p.poll() is None and time.monotonic() < deadline, 'rotation barrier not reached: ' + point
                time.sleep(.01)
            journal = load(state / 'service' / ('rotation-' + meta['seats']['spec-manager'] + '.json'))
            rotation = journal['id']; nonce = journal['nonce']
            os.killpg(p.pid, signal.SIGKILL); p.communicate(timeout=10)
            arm.unlink(); hit.unlink()
            assert broker()['seats'][0]['rotationId'] == rotation
            after = load(state / 'service' / ('rotation-' + meta['seats']['spec-manager'] + '.json'))
            assert after['nonce'] == nonce and after['closed']
            assert sum(x['event'].get('rotationId') == rotation for x in snapshot()['audit']) == 1
            checks.append('process_death_' + point + '_resumes_same_nonce_once')
        # Expiry during an outage abandons only the expired operation, then creates a fresh one.
        force_due('spec-manager'); one.update(seatTtl=2, seatRenewBefore=1); save(allowlist, one)
        time.sleep(1.1)  # Make the two-second legacy lease due under the one-second threshold.
        arm = state / 'service/fault.rotation_after_commit'; hit = state / 'service/hit.rotation_after_commit'
        arm.touch(mode=0o600); os.chown(arm, SERVICE, AGENT)
        p = subprocess.Popen(['/usr/bin/python3', '-I', str(helper), 'renew', '--config', str(allowlist)],
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True); children.append(p)
        deadline = time.monotonic()+10
        while not hit.exists():
            assert p.poll() is None and time.monotonic()<deadline; time.sleep(.01)
        expired_id = load(state / 'service' / ('rotation-' + meta['seats']['spec-manager'] + '.json'))['id']
        os.killpg(p.pid, signal.SIGKILL); p.communicate(timeout=10); arm.unlink(); hit.unlink(); time.sleep(3)
        one.update(seatTtl=30, seatRenewBefore=20); save(allowlist, one)
        assert broker()['seats'][0]['rotationId'] != expired_id
        checks.append('outage_past_overlap_and_ttl_replaces_expired_pending_without_publishing_it')
        shutil.copyfile(production_bridge,replacement); replacement.chmod(0o755); os.replace(replacement,binary)
        config['files']['bin/codex-factory-bridge'] = sha(binary); save(allowlist, config)
        # Run the actual generated service and an accelerated copy of its timer.
        stop(hub); (runtime / 'bridge.sock').unlink(missing_ok=True)
        units = Path('/etc/systemd/system')
        (units / 'fleet-manager-bridge.service').write_text('[Unit]\nDescription=Real isolated fixture hub\n[Service]\nUser=65534\nGroup=65534\nSupplementaryGroups=65532\nExecStart=' + str(binary) + ' serve --state ' + str(state / 'service') + ' --socket ' + str(runtime / 'bridge.sock') + '\n')
        for name in ('fleet-manager-bridge-renew.service', 'fleet-manager-bridge-renew.timer'):
            text = (stage / name).read_text()
            if name.endswith('.timer'): text = text.replace('OnBootSec=30s', 'OnBootSec=1s').replace('OnUnitActiveSec=5min', 'OnUnitActiveSec=2s').replace('AccuracySec=5s', 'AccuracySec=100ms')
            (units / name).write_text(text)
        run(['systemd-analyze', 'verify', units / 'fleet-manager-bridge.service', units / 'fleet-manager-bridge-renew.service', units / 'fleet-manager-bridge-renew.timer'])
        for role in originals: force_due(role)
        before = snapshot()['auditSequence']
        run(['systemctl', 'daemon-reload']); run(['systemctl', 'start', 'fleet-manager-bridge-renew.timer'])
        deadline=time.monotonic()+15
        while True:
            changed = [x['event'] for x in snapshot()['audit'] if x['sequence']>before and x['event']['kind']=='rotated']
            counts = {role:sum(x['seat']==meta['seats'][role] for x in changed) for role in originals}
            if min(counts.values()) >= 2: break
            if time.monotonic()>=deadline:
                status=subprocess.run(['systemctl','status','fleet-manager-bridge-renew.service','fleet-manager-bridge-renew.timer',
                                       'fleet-manager-bridge.service','--no-pager'],capture_output=True)
                journal=subprocess.run(['journalctl','-u','fleet-manager-bridge-renew.service','--no-pager','-o','cat'],capture_output=True)
                (root/'timer-failure.log').write_bytes(status.stdout+status.stderr+journal.stdout+journal.stderr)
                diagnostic = ('import runpy,traceback,json,os; os.umask(0o077); m=runpy.run_path('+repr(str(helper))+'); '
                              'print("".join(x for x in open("/proc/self/status") if x.startswith(("Cap","NoNewPrivs"))));\n'
                              'try: m["renew"](m["validate_allowlist"]('+repr(str(allowlist))+'))\n'
                              'except Exception as e: print(json.dumps(dict(type=type(e).__name__,errno=getattr(e,"errno",None),'
                              'frames=[dict(line=f.lineno,function=f.name) for f in traceback.extract_tb(e.__traceback__)])))\n')
                probe=subprocess.run(['systemd-run','--wait','--pipe','--collect','--unit=renewal-diagnostic',
                        '-p','NoNewPrivileges=yes','-p','ProtectHome=yes','-p','ProtectSystem=strict',
                        '-p','PrivateTmp=yes','-p','UMask=0077','-p','User=root','-p','Group=root',
                        '-p','ProtectKernelTunables=yes','-p','ProtectKernelModules=yes',
                        '-p','ReadWritePaths=/var/lib/fleet-manager-bridge /var/lib/fleet-manager-bridge-renew /etc/fleet-manager-bridge/credentials',
                        '/usr/bin/python3','-I','-c',diagnostic],capture_output=True,timeout=30)
                (root/'timer-diagnostic.log').write_bytes(probe.stdout+probe.stderr)
                raise AssertionError('accelerated generated timer did not renew both local seats twice')
            time.sleep(.25)
        assert snapshot()['remoteHosts'] == {}; mailbox()
        status = run(['systemctl', 'show', 'fleet-manager-bridge-renew.service', '-p', 'Result', '-p', 'ExecMainStatus']).stdout.decode()
        assert 'Result=success' in status and 'ExecMainStatus=0' in status
        run(['systemctl', 'stop', 'fleet-manager-bridge-renew.timer', 'fleet-manager-bridge-renew.service'])
        checks.append(dict(actualGeneratedSystemdService=True, acceleratedTimer=True, localRotations=counts, remoteHosts=0))
        child_code=('import os,json; s=dict(x.split(":",1) for x in open("/proc/self/status") if ":" in x); '
                    'print(json.dumps(dict(uid=os.geteuid(),caps={k:s[k].strip() for k in '
                    '["CapEff","CapPrm","CapAmb","NoNewPrivs"]})))')
        identity_code=('import runpy,json; m=runpy.run_path('+repr(str(helper))+'); '
                      'c=m["validate_allowlist"]('+repr(str(allowlist))+'); '
                      'print(json.dumps([json.loads(m["actor_run"](a,["/usr/bin/python3","-I","-c",'+repr(child_code)+'])) '
                      'for a in [c["service"],c["seats"][0]["actor"]]]))')
        identity=run(['systemd-run','--wait','--pipe','--collect','--unit=renewal-identities',
                   '-p','User=root','-p','Group=root','-p','NoNewPrivileges=yes','-p','PrivateTmp=yes',
                   '-p','CapabilityBoundingSet=CAP_SETUID CAP_SETGID','-p','AmbientCapabilities=CAP_SETUID CAP_SETGID',
                   '/usr/bin/python3','-I','-c',identity_code])
        identities=json.loads(identity.stdout)
        assert [r['uid'] for r in identities]==[SERVICE,AGENT]
        assert all(r['caps']['NoNewPrivs']=='1' and all(int(r['caps'][key],16)==0
                   for key in ('CapEff','CapPrm','CapAmb')) for r in identities)
        checks.append('root_unit_drops_to_real_service_endpoint_uids_without_effective_permitted_or_ambient_capabilities')
        # Later explicit admission uses reserved IDs and preserves every local seat/message.
        before = snapshot()
        admin('admit-host', '--state', state / 'service', '--host', meta['remoteHost'], '--key-id', meta['transportKey'], '--bridge-uid', SERVICE, '--ttl', 1)
        time.sleep(2); assert meta['remoteHost'] in broker()['hosts']
        before = snapshot()
        setup('admit-remote', '--config-root', cfg)
        after = snapshot(); updated = load(cfg / 'setup.json')
        assert updated['seats']==identity['seats'] and updated['remoteHost']==identity['remoteHost'] and updated['transportKey']==identity['transportKey']
        assert before['seats']==after['seats'] and before['mail']==after['mail']
        assert updated['remoteHost'] in after['remoteHosts']
        for _ in range(40): admin('renew-host', '--state', state / 'service', '--host', meta['remoteHost'], '--key-id', meta['transportKey'], '--ttl', 86400)
        host = snapshot()['remoteHosts'][meta['remoteHost']]
        assert len(host['usedKeys']) == 1 and host['currentKey']==meta['transportKey']
        checks.append('explicit_later_admission_preserves_reserved_ids_and_mail_40_same_key_renewals_one_key_slot')
        admin('revoke-host', '--state', state / 'service', '--host', meta['remoteHost'])
        assert 'renewal_host_identity' in broker(expected=1)
        run(['systemctl', 'start', 'fleet-manager-bridge-renew.service'], expected=1)
        failed = run(['systemctl', 'show', 'fleet-manager-bridge-renew.service', '-p', 'Result', '-p', 'ExecMainStatus']).stdout.decode()
        assert 'Result=exit-code' in failed and 'ExecMainStatus=1' in failed
        invocation = run(['systemctl', 'show', 'fleet-manager-bridge-renew.service', '-p', 'InvocationID', '--value']).stdout.decode().strip()
        journal = run(['journalctl', '_SYSTEMD_INVOCATION_ID=' + invocation, '-o', 'cat', '--no-pager']).stdout.decode()
        assert journal.count('renewal_failed ') == 1 and 'code=renewal_host_identity' in journal
        for role in originals: assert load(credential(role))['credential'] not in journal
        (root / 'unit-failure.txt').write_text(failed + journal)
        checks.append('revoked_host_is_not_renewed_or_readmitted_by_root_broker')
        admin('revoke','--state',state/'service','--seat',meta['seats']['foreman-bot'])
        last=copy.deepcopy(config); last['hosts']=[]; last['seats']=[last['seats'][1]]; save(allowlist,last)
        assert 'renewal_grant_changed' in broker(expected=1)
        checks.append('revoked_seat_cannot_be_rotated_or_published')
        last['seats']=[config['seats'][0]]; save(allowlist,last); force_due('spec-manager')
        epoch_path=state/'service/lifecycle.json'; epoch=load(epoch_path); epoch['boot']=str(uuid.uuid4()); save(epoch_path,epoch)
        preserved=credential('spec-manager').read_bytes()
        assert 'reboot_requires_service_recovery' in broker(expected=1)
        assert credential('spec-manager').read_bytes()==preserved
        checks.append('boot_identity_change_fences_renewal_without_publication_or_automatic_recovery')
        result = dict(passed=True, checks=checks, modelsStarted=0, externalHostContacts=0,
                      packageBridgeSha256=sha(production_bridge), faultBridgeSha256=sha(a.fault_bridge))
        (root / 'renewal-result.json').write_text(json.dumps(result, indent=2)+'\n')
        return result
    finally:
        for p in reversed(children):
            if p.poll() is None:
                os.killpg(p.pid, signal.SIGKILL); p.wait(timeout=10)


def container_fixture(a):
    assert os.geteuid()==0 and os.readlink('/proc/self/ns/mnt') != os.readlink('/proc/1/ns/mnt')
    root = a.work_dir / 'container'; root.mkdir(mode=0o755)
    for name in ('usr','etc/systemd/system','run','var','tmp','proc','sys','dev','opt/fleet-codex','work','repo'):
        (root/name).mkdir(parents=True, exist_ok=True)
    for name in ('lib','lib64','bin','sbin'): (root/name).symlink_to('usr/'+name)
    for name in ('os-release','passwd','group','nsswitch.conf'):
        if (Path('/etc')/name).exists(): shutil.copyfile(Path('/etc')/name, root/'etc'/name)
    with (root/'etc/passwd').open('a') as f: f.write('renewal-agent:x:65532:65532:Offline fixture:/nonexistent:/usr/bin/nologin\n')
    with (root/'etc/group').open('a') as f: f.write('renewal-agent:x:65532:\n')
    (root/'etc/machine-id').write_text(uuid.uuid4().hex+'\n')
    marker=uuid.uuid4().hex+'\n'
    for path in (root/'etc/renewal-proof-id',a.work_dir/'fixture-id'):
        path.write_text(marker); path.chmod(0o600)
    pkg=root/'opt/fleet-codex/proof'
    run(['cp','-a','--reflink=auto',a.package,pkg],timeout=60)
    for path in [pkg,*pkg.rglob('*')]: os.chown(path,0,0,follow_symlinks=False)
    fault=root/'opt/fleet-codex/fault-bridge'; shutil.copy2(a.fault_bridge,fault); os.chown(fault,0,0)
    (root/'etc/systemd/system/renewal-proof.service').write_text('[Unit]\nDescription=Offline renewal qualification\nAfter=basic.target\n[Service]\nType=oneshot\nTimeoutStartSec=240\nExecStart=/usr/bin/python3 /repo/scripts/factory/qualify_renewals.py --fixture --package /opt/fleet-codex/proof --fault-bridge /opt/fleet-codex/fault-bridge --work-dir /work\nStandardOutput=append:/work/renewal-worker.log\nStandardError=append:/work/renewal-worker.log\n[Install]\nWantedBy=multi-user.target\n')
    wants=root/'etc/systemd/system/multi-user.target.wants'; wants.mkdir()
    (wants/'renewal-proof.service').symlink_to('../renewal-proof.service')
    (a.work_dir/'container-tmp').mkdir(mode=0o1777); (a.work_dir/'container-tmp').chmod(0o1777)
    log=(a.work_dir/'systemd.log').open('w')
    p=subprocess.Popen(['systemd-nspawn','--quiet','--register=no','--keep-unit','--settings=no','--resolv-conf=off','--private-network',
            '--capability=CAP_SYS_ADMIN',
            '--directory='+str(root),'--bind-ro=/usr','--bind-ro='+str(REPO)+':/repo',
            '--bind='+str(a.work_dir)+':/work','--bind='+str(a.work_dir/'container-tmp')+':/tmp',
            '--boot','--','--log-target=console','--show-status=no'],stdout=log,stderr=log,start_new_session=True)
    try:
        deadline=time.monotonic()+250
        while not (a.work_dir/'renewal-result.json').exists():
            assert p.poll() is None and time.monotonic()<deadline, 'isolated renewal worker failed; inspect renewal-worker.log'
            worker=a.work_dir/'renewal-worker.log'
            if worker.exists() and 'Traceback (most recent call last)' in worker.read_text():
                raise RuntimeError('isolated renewal worker failed; inspect renewal-worker.log')
            time.sleep(.2)
        print((a.work_dir/'renewal-result.json').read_text(), flush=True)
    finally:
        if p.poll() is None: p.terminate()
        try: p.wait(timeout=20)
        except subprocess.TimeoutExpired: os.killpg(p.pid,signal.SIGKILL); p.wait()
        log.close()


def pipeline(a):
    assert os.geteuid()==0 and os.readlink('/proc/self/ns/mnt')!=os.readlink('/proc/1/ns/mnt')
    root=a.work_dir
    run(['mount','--make-rprivate','/'])
    for name,target in [('mount-tmp','/tmp'),('mount-run','/run')]:
        path=root/name; path.mkdir(mode=0o755)
        if target=='/tmp': path.chmod(0o1777)
        else:
            # nspawn requires its propagation directory on a volatile /run.
            # This mount exists only in this private namespace, never on host /run.
            run(['mount','-t','tmpfs','-o','mode=0755,size=256M','tmpfs',path])
        run(['mount','--bind',path,target])
    caller=a.caller_uid
    home=root/'home'; home.mkdir(mode=0o700); os.chown(home,caller,caller)
    user=Path('/run/user')/str(caller); user.mkdir(mode=0o700,parents=True); os.chown(user,caller,caller)
    Path('/run/sshd').mkdir(mode=0o755)
    sudoers=root/'fixture-sudoers'
    sudoers.write_text('Defaults env_reset\nroot ALL=(ALL:ALL) NOPASSWD: ALL\n#'+str(caller)+' ALL=(ALL:ALL) NOPASSWD: ALL\n'); sudoers.chmod(0o440)
    run(['/usr/bin/visudo','-c','-f',sudoers]); run(['mount','--bind',sudoers,'/etc/sudoers'])
    environment=dict(PATH='/usr/bin:/bin',HOME=str(home),CODEX_HOME=str(home/'unused-codex'),TMPDIR='/tmp',
                     XDG_CONFIG_HOME=str(home/'config'),XDG_CACHE_HOME=str(home/'cache'),
                     XDG_DATA_HOME=str(home/'data'),XDG_STATE_HOME=str(home/'state'),
                     GIT_CONFIG_NOSYSTEM='1',GIT_CONFIG_GLOBAL='/dev/null',NO_PROXY='127.0.0.1,localhost',
                     INSTA_UPDATE='no')
    def proof(name,args,uid=caller,timeout=240):
        start=time.monotonic()
        with (root/(name+'.log')).open('wb') as log:
            p=subprocess.run(list(map(str,args)),stdout=log,stderr=subprocess.STDOUT,env=environment,
                  user=uid,group=uid,extra_groups=[],timeout=timeout)
        assert p.returncode==0, name+' failed; inspect '+str(root/(name+'.log'))
        return dict(passed=True,exit=p.returncode,seconds=round(time.monotonic()-start,2))
    renewal=root/'renewals'; renewal.mkdir(mode=0o755)
    result={'renewals':proof('renewals', ['/usr/bin/python3',__file__,'--container','--package',a.package,
                        '--fault-bridge',a.fault_bridge,'--work-dir',renewal],uid=0,timeout=270)}
    result['renewals'].update(json.loads((renewal/'renewal-result.json').read_text()))
    result['setupCompatibility']=proof('setup-compatibility',['/usr/bin/python3',REPO/'scripts/factory/test-setup-forge-box.py',
            '--root','/tmp/setup-proof-'+uuid.uuid4().hex,'--dist',a.package],uid=0)
    result['mailbox']={}
    for name in ('contract','credentials','recovery','native','lifecycle','durability','reconciliation'):
        fault=name in ('durability','reconciliation')
        args=['/usr/bin/python3',REPO/('scripts/factory/qualify_'+name+'.py'),'--binary',
              a.fault_bridge if fault else a.package/'bin/codex-factory-bridge','--evidence',root/name]
        if name in ('native','lifecycle','reconciliation'):args.extend(['--cli',a.fault_cli if fault else a.package/'bin/codex'])
        result['mailbox'][name]=proof(name,args)
        record=json.loads((root/name/'result.json').read_text())
        assert record['passed']; result['mailbox'][name]['checks']=record.get('checks')
    result['packagedTools']=proof('packaged-tools',['/usr/bin/python3',REPO/'scripts/factory/qualify_packaged_tools.py',
                       '--package',a.package,'--work-dir',root/'tools','--offline'])
    result['packagedTools']['results']=json.loads((root/'tools/results.json').read_text())
    paths=['qualify_renewals.py','test-setup-forge-box.py','qualify_packaged_tools.py','fleet_fixture.py',
           'native_fixture.py','session_fixture.py','isolation_client.py']
    paths += ['qualify_'+name+'.py' for name in result['mailbox']]
    result['proofScriptsSha256']={name:sha(REPO/'scripts/factory'/name) for name in paths}
    result.update(status='passed',externalModelRequests=0,externalFleetHostContacts=0,bwrapSha256=BWRAP)
    (root/'qualification.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(dict(status='passed',mailboxLanes=7,renewals=True,setup=True,packagedTools=True,
                         externalModelRequests=0,externalFleetHostContacts=0,bwrapSha256=BWRAP)),flush=True)


def full_package_proof(a):
    from check_package import TARGET_SPECS
    from codex_package.v8 import resolve_codex_v8_cargo_env
    manifest=json.loads((a.package/'MANIFEST.json').read_text())
    source=manifest['runtimeSourceCommit']
    env=os.environ.copy()
    env.update(resolve_codex_v8_cargo_env(TARGET_SPECS[manifest['target']]))
    env.update(RUSTUP_TOOLCHAIN='1.95.0',CARGO_BUILD_JOBS='8',CARGO_INCREMENTAL='0',
               CODEX_FACTORY_REVISION=source,STABLE_GIT_COMMIT=source,CODEX_BWRAP_SHA256=BWRAP)
    target=Path(env.get('CARGO_TARGET_DIR','/mnt/fast/target')).resolve()
    args=['cargo','build','--locked','--profile','dev-small','--target',manifest['target'],'--target-dir',target,
          '-p','codex-cli','-p','codex-factory','--features',
          'codex-install-context/factory,codex-app-server/factory-fixture-faults,codex-factory/fixture-faults',
          '--bin','codex','--bin','codex-factory-bridge']
    with (a.work_dir/'fault-build.log').open('wb') as log:
        subprocess.run(list(map(str,args)),cwd=REPO/'codex-rs',env=env,stdout=log,stderr=subprocess.STDOUT,check=True)
    pair=a.work_dir/'fault-pair'; pair.mkdir(mode=0o755)
    for name in ('codex','codex-factory-bridge'):
        shutil.copy2(target/manifest['target']/'dev-small'/name,pair/name)
    subprocess.run(['sudo','-n','unshare','--mount','--fork',sys.executable,__file__,'--pipeline',
          '--package',str(a.package),'--work-dir',str(a.work_dir),'--fault-bridge',str(pair/'codex-factory-bridge'),
          '--fault-cli',str(pair/'codex'),'--caller-uid',str(os.getuid())],check=True)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--package',type=Path,required=True); p.add_argument('--work-dir',type=Path,required=True)
    p.add_argument('--fault-bridge',type=Path); p.add_argument('--full-package-proof',action='store_true')
    p.add_argument('--fault-cli',type=Path); p.add_argument('--caller-uid',type=int)
    p.add_argument('--fixture',action='store_true',help=argparse.SUPPRESS)
    p.add_argument('--container',action='store_true',help=argparse.SUPPRESS)
    p.add_argument('--pipeline',action='store_true',help=argparse.SUPPRESS)
    a=p.parse_args(); a.package=a.package.resolve(); a.work_dir=a.work_dir.resolve()
    if a.fixture: print(json.dumps(fixture(a))); return
    if a.container: container_fixture(a); return
    if a.pipeline: pipeline(a); return
    assert not a.work_dir.exists(), 'fresh task work directory required'
    a.work_dir.mkdir(mode=0o700)
    from check_package import check
    assert check(a.package)['passed'] and sha(a.package/'codex-resources/bwrap')==BWRAP
    subprocess.run(['sha256sum','-c','SHA256SUMS'],cwd=a.package,stdout=subprocess.DEVNULL,check=True)
    for relative in ('setup-forge-box.py','systemd/fleet-manager-bridge-renew.service.in',
                     'systemd/fleet-manager-bridge-renew.timer.in'):
        assert sha(a.package/relative)==sha(REPO/'scripts/factory'/relative), 'package operator assets differ from this branch'
    if a.full_package_proof:
        full_package_proof(a)
    else:
        assert a.fault_bridge
        subprocess.run(['sudo','-n','unshare','--mount','--fork',sys.executable,__file__,
                        '--container','--package',str(a.package),'--fault-bridge',str(a.fault_bridge.resolve()),
                        '--work-dir',str(a.work_dir)],check=True)


if __name__=='__main__': main()
