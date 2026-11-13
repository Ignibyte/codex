#!/usr/bin/env python3
"""Offline T proof using real sshd, kernel peer UIDs and an isolated systemd."""
import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import re
import select
import shutil
import signal
import subprocess
import sys
import time
import types
import uuid

REPO = Path(__file__).resolve().parents[2]
SERVICE, AGENT = 65534, 65532
BWRAP = '0ce4fa5b9da214841641f12032afa6cda3d93b45f321705fa8bde5fb2c270b74'


def sha(path):
    with Path(path).open('rb') as f: return hashlib.file_digest(f, 'sha256').hexdigest()


def run(args, *, uid=None, expected=0, timeout=30, payload=None, env=None):
    identity = dict(user=uid, group=uid, extra_groups=[AGENT]) if uid is not None else {}
    p = subprocess.run(list(map(str, args)), input=payload, capture_output=True,
                       timeout=timeout, env=env, **identity)
    assert p.returncode == expected, str(args[0])+' exit='+str(p.returncode)+' '+p.stderr.decode(errors='replace')[:500]
    return p


def load(path): return json.loads(Path(path).read_text())


def save(path, data, uid=0, mode=0o600):
    Path(path).write_text(json.dumps(data, indent=2)+'\n')
    os.chown(path, uid, AGENT if uid else 0); Path(path).chmod(mode)


def fixture(a):
    assert os.geteuid()==0 and a.work_dir==Path('/work') and a.package==Path('/opt/fleet-codex/proof')
    assert Path('/run/systemd/container').read_text().strip()=='systemd-nspawn'
    assert Path('/etc/transport-proof-id').read_text()==Path('/work/fixture-id').read_text()
    pkg, work = a.package, a.work_dir
    binary, helper = pkg/'bin/codex-factory-bridge', pkg/'setup-forge-box.py'
    production_sha=sha(binary)
    cfg, state = Path('/etc/fleet-manager-bridge-pilot'), Path('/var/lib/fleet-manager-bridge-pilot')
    remote_cfg, remote_state = Path('/etc/fleet-manager-bridge-remote-pilot'), Path('/var/lib/fleet-manager-bridge-remote-pilot')
    runtime, remote_run = state/'run', remote_state/'run'
    checks, processes = [], []
    def directory(path, uid=SERVICE, mode=0o700):
        path.mkdir(parents=True, exist_ok=True); os.chown(path,uid,AGENT); path.chmod(mode)
    def command(*args, uid=SERVICE, expected=0, payload=None):
        p=run(args,uid=uid,expected=expected,payload=payload)
        return json.loads(p.stdout if not expected else p.stderr)
    def setup(*args,uid=SERVICE,expected=0):
        p=run(['/usr/bin/python3','-I',helper,*args],uid=uid,expected=expected)
        return json.loads(p.stdout.splitlines()[-1] if not expected else p.stderr)
    def admin(*args): return command(binary,*args)
    def snapshot(): return admin('inspect','--state',state/'service')
    def binding(role): return (remote_cfg if role=='pilot-seat' else cfg)/(role+'.binding.json')
    def endpoint(role,operation,**fields):
        # Test the production protocol helper directly inside the container.
        # Full endpoint sandbox preflight/CLI is exercised outside nspawn in
        # the inherited complete R proof; nested proc mounts are not available.
        args=dict(command=operation,binding=str(binding(role)),bin_dir=str(pkg/'bin'),**fields)
        code='import runpy,json,sys,argparse;print(json.dumps(runpy.run_path(sys.argv[1])["endpoint"](argparse.Namespace(**json.loads(sys.argv[2])))))'
        return command('/usr/bin/python3','-I','-c',code,helper,json.dumps(args),uid=AGENT)
    def request(role,method='sync',recipient=None,operation='observe',**fields):
        g=load(binding(role))['grant']
        sequence=next(x['lastSequence'] for x in snapshot()['seats'] if x['seat']==g['seat'])+1
        wire=dict(method=method,protocol=1,request=dict(protocol=1,sender=g['seat'],host=g['host'],incarnation=g['incarnation'],
           sequence=sequence,project=g['projects'][0],recipient=recipient or g['seat'],operation=operation),**fields)
        path=Path(load(binding(role))['state'])/('wire-'+uuid.uuid4().hex+'.json');save(path,wire,AGENT)
        return path
    def client_args(role,wire):
        b=load(binding(role))
        return ['/usr/bin/python3',REPO/'scripts/factory/isolation_client.py','--socket',b['socket'],'--credential',b['credential'],'--wire',wire]
    def probe(role,wire):return command(*client_args(role,wire),uid=AGENT)
    def connector():
        return start([binary,'connect-source','--state',state/'connector','--config',cfg/'transport/source.json'],ready=True)
    def source_record():return load(remote_state/'source/listener.json')
    def source_dead():
        try:record=source_record()
        except FileNotFoundError:
            wait(lambda:not (remote_run/'remote.sock').exists(),'graceful entry socket cleanup')
            return
        def dead():
            path=Path('/proc')/str(record['pid'])/'stat'
            return not path.exists()
        wait(dead,'owned SSH entry did not exit')
    def start(args,uid=SERVICE,ready=False):
        logfile=work/('child-'+uuid.uuid4().hex+'.log')
        log=logfile.open('wb')
        p=subprocess.Popen(list(map(str,args)),user=uid,group=uid,extra_groups=[AGENT],
                           stdout=subprocess.PIPE,stderr=log,start_new_session=True)
        log.close(); processes.append(p)
        if ready:
            assert select.select([p.stdout],[],[],10)[0], str(logfile)+' readiness timeout'
            line=p.stdout.readline()
            assert line and json.loads(line).get('ready'),str(logfile)+' readiness '+repr(line)
        return p
    def stop(p,sig=signal.SIGTERM):
        if p.poll() is None:os.killpg(p.pid,sig)
        p.wait(timeout=15)
        if p.stdout:p.stdout.close()
    def wait(test,label,seconds=10):
        end=time.monotonic()+seconds
        while not test():
            assert time.monotonic()<end,label
            time.sleep(.02)
    try:
        run(['/usr/bin/ip','link','set','lo','up'])
        for ip in ('10.200.68.55','10.200.68.77'):
            run(['/usr/bin/ip','address','add',ip+'/32','dev','lo'])
        keyroot=Path('/etc/ssh');keyroot.mkdir(exist_ok=True);Path('/run/sshd').mkdir(exist_ok=True)
        for name in ('proof-client','proof-host','proof-operator'):
            run(['/usr/bin/ssh-keygen','-q','-t','ed25519','-N','','-f',keyroot/name])
        inp=Path('/etc/pilot-transport-input.json')
        save(inp,dict(forgeAddress='10.200.68.55',sourceAddress='10.200.68.77',port=22,sshUnit='sshd.service',
             clientPublicKey=(keyroot/'proof-client.pub').read_text(),sourceHostKey=(keyroot/'proof-host.pub').read_text(),
             serviceUid=SERVICE,serviceGid=SERVICE,clientsGid=AGENT,agentUid=AGENT,agentGid=AGENT,remoteAgentUid=AGENT,remoteAgentGid=AGENT))
        stage=Path('/var/lib/fleet-codex-pilot-stage')
        staged=setup('prepare-pilot','--input',inp,'--output',stage,uid=0)
        generation=load(stage/'generation.json');meta=load(stage/'pilot-plan.json')['setup']
        assert not cfg.exists() and not state.exists() and not remote_cfg.exists() and not remote_state.exists()
        assert staged['servicesStarted']==0 and all(sha(stage/n)==x['sha256'] for n,x in generation['files'].items())
        assert all('PRIVATE KEY' not in (stage/n).read_text() for n in generation['files'])
        refused=setup('apply-pilot','--stage',stage,'--host','forge','--approved-generation',str(uuid.uuid4()),'--private-key',keyroot/'proof-client',uid=0,expected=1)
        assert refused['error']=='ops_review_required' and not cfg.exists() and not state.exists()
        sudo_query=subprocess.run(['/usr/bin/sudo','-n','-l','-U','fleet-bridge'],capture_output=True,text=True,env={'PATH':'/usr/bin:/bin','LC_ALL':'C'})
        save(work/'sudo-query.json',dict(exit=sudo_query.returncode,stdout=sudo_query.stdout,stderr=sudo_query.stderr))
        setup('apply-pilot','--stage',stage,'--host','forge','--approved-generation',generation['generation'],'--private-key',keyroot/'proof-client',uid=0)
        assert snapshot()['remoteHosts']=={} and not (cfg/'operator/proxy.json').exists()
        registry_before=sha(state/'service/registry.json');names_before=set((cfg/'operator').iterdir())
        refused=setup('ssh-config','--config-root',cfg,'--client-public-key','/does-not-exist',expected=1)
        assert refused['error']=='pilot_legacy_ssh_refused'
        assert sha(state/'service/registry.json')==registry_before and set((cfg/'operator').iterdir())==names_before
        refused=setup('render-renewal','--config-root',cfg,'--output','/var/lib/must-not-exist',uid=0,expected=1)
        assert refused['error']=='pilot_renewal_off' and not Path('/var/lib/must-not-exist').exists()
        checks.append('offline_whole_public_generation_no_private_bytes_no_host_mutation_or_services_before_approval')
        checks.append('pilot_legacy_sudo_refuses_before_key_read_or_write_no_reverse_proxy_and_renewal_render_refused')
        # Two mount views model distinct host login shells with the same service
        # UID. The SSH daemon and dev-3 local verifier share the dev-3 view;
        # the connector/systemd and Forge verifier retain nologin. One kernel.
        devpasswd=keyroot/'proof-dev-passwd'
        original=Path('/etc/passwd').read_text()
        devtext='\n'.join(':'.join(line.split(':')[:6]+['/etc/ssh/fleet-bridge-pilot-login']) if line.startswith('fleet-bridge:') else line
                          for line in original.splitlines())+'\n'
        devpasswd.write_text(devtext);devpasswd.chmod(0o644)
        ns=start(['/usr/bin/unshare','--mount','--fork','/bin/sh','-c',
            'mount --make-rprivate / && mount --bind /etc/ssh/proof-dev-passwd /etc/passwd && touch /run/dev-mount-ready && exec /usr/bin/sleep 240'],uid=0)
        wait(lambda:Path('/run/dev-mount-ready').exists(),'dev-3 mount view')
        def devrun(args,expected=0,env=None):
            return run(['/usr/bin/nsenter','-t',ns.pid,'-m','--',*args],expected=expected,env=env)
        def devsetup(*args,expected=0):
            p=devrun(['/usr/bin/python3','-I',helper,*args],expected)
            return json.loads(p.stdout.splitlines()[-1] if not expected else p.stderr)
        devsetup('apply-pilot','--stage',stage,'--host','dev-3','--approved-generation',generation['generation'])
        sshconf=keyroot/'sshd_config'
        operator_key=(keyroot/'proof-operator.pub').read_text().strip()
        (keyroot/'proof-operator.keys').write_text('restrict,command="/usr/bin/sleep 180" '+operator_key+'\n')
        sshconf.write_text('\n'.join(['Port 22','ListenAddress 10.200.68.77',f'HostKey {keyroot}/proof-host',
          f'PidFile {keyroot}/proof.pid','AllowUsers fleet-bridge ops-proof',f'AuthorizedKeysFile {keyroot}/proof-operator.keys',
          'UsePAM yes','PasswordAuthentication no','KbdInteractiveAuthentication no','PermitUserEnvironment no',
          'StrictModes yes','UseDNS no','PrintMotd no','LogLevel VERBOSE',
          (stage/'sshd-include.fragment').read_text(),'']))
        sshconf.chmod(0o600)
        unit=Path('/etc/systemd/system/sshd.service')
        unit.write_text('[Service]\nType=simple\nExecStart=/usr/bin/nsenter -t '+str(ns.pid)+' -m -- /usr/bin/sshd -D -e\nExecReload=/bin/kill -HUP $MAINPID\nKillMode=control-group\n')
        run(['/usr/bin/systemctl','daemon-reload']);run(['/usr/bin/systemctl','start','sshd.service'])
        wait(lambda:(keyroot/'proof.pid').exists(),'sshd readiness')
        sshd_pid=int((keyroot/'proof.pid').read_text())
        # Preserve a real already-authenticated operator channel across reload.
        operator_pin=keyroot/'operator_known_hosts';operator_pin.write_text('10.200.68.77 '+(keyroot/'proof-host.pub').read_text())
        fallback=start(['/usr/bin/ssh','-F','/dev/null','-T','-oBatchMode=yes','-oIdentityAgent=none','-oIdentitiesOnly=yes',
            '-oStrictHostKeyChecking=yes','-oUserKnownHostsFile='+str(operator_pin),'-oBindAddress=10.200.68.55',
            '-i',keyroot/'proof-operator','ops-proof@10.200.68.77','keep-fallback'],uid=0)
        time.sleep(.15);assert fallback.poll() is None
        # Real privileged account-query negative cases, not a failed sudo true.
        def account_failure(code):
            denied=devsetup('verify-host','--stage',stage,'--host','dev-3','--report','/etc/rejected-report.json',expected=1)
            assert denied['error']==code,denied
            assert not Path('/etc/rejected-report.json').exists()
        sudoers=Path('/etc/sudoers');good_sudo=sudoers.read_text()
        sudoers.write_text(good_sudo+'fleet-bridge ALL=(root) NOPASSWD: /usr/bin/true\n');account_failure('account_sudo_not_denied');sudoers.write_text(good_sudo)
        devpasswd.write_text(devtext.replace('/etc/ssh/fleet-bridge-pilot-login','/bin/bash'));account_failure('account_shell_denied');devpasswd.write_text(devtext)
        shadow=Path('/etc/shadow');good_shadow=shadow.read_text();shadow.write_text(good_shadow.replace('fleet-bridge:!:','fleet-bridge::'))
        account_failure('account_password_not_locked');shadow.write_text(good_shadow)
        sudoers.write_text('THIS IS INVALID SUDO POLICY\n');account_failure('account_sudo_not_denied');sudoers.write_text(good_sudo)
        checks.append('condition4_real_allowed_command_wrong_shell_unlocked_password_and_query_failure_all_fail_closed')
        # Syntax failure blocks the generated reload procedure. It never invokes restart.
        valid_conf=sshconf.read_text();sshconf.write_text(valid_conf+'UnknownDirective yes\n')
        bad=devrun(['/bin/sh',stage/'dev3-validate-reload-verify.sh'],expected=1,env={'PATH':'/usr/bin:/bin','FALLBACK_SESSION_OPEN':'yes'})
        assert b'sshd_configuration_invalid' in bad.stderr and int((keyroot/'proof.pid').read_text())==sshd_pid
        sshconf.write_text(valid_conf)
        denied=devrun(['/bin/sh',stage/'dev3-validate-reload-verify.sh'],expected=1,env={'PATH':'/usr/bin:/bin'})
        assert b'fallback_session_required' in denied.stderr
        devrun(['/bin/sh',stage/'dev3-validate-reload-verify.sh'],env={'PATH':'/usr/bin:/bin','FALLBACK_SESSION_OPEN':'yes'})
        assert int((keyroot/'proof.pid').read_text())==sshd_pid and fallback.poll() is None
        checks.append('generated_include_matchall_effective_options_syntax_gate_reload_only_retains_real_fallback_session')
        forge_report='/etc/fleet-bridge-pilot-forge-verified.json';source_report='/etc/fleet-bridge-pilot-dev3-verified.json';proof_file='/etc/fleet-bridge-pilot-verified.json'
        # Missing reports/verification cannot enable either unit or admit a host.
        missing=setup('activate-pilot','--stage',stage,'--verification','/etc/missing.json',uid=0,expected=1)
        assert snapshot()['remoteHosts']=={}
        for name in ('fleet-manager-bridge-pilot.service','fleet-manager-bridge-pilot-source.service'):
            assert run(['/usr/bin/systemctl','is-enabled',name],expected=1).stdout.strip()==b'disabled'
            assert run(['/usr/bin/systemctl','is-active',name],expected=3).stdout.strip()==b'inactive'
        keydir=Path('/etc/fleet-bridge-pilot-ssh');key=keydir/'forge-ed25519'
        for change,restore in [(lambda:keydir.chmod(0o755),lambda:keydir.chmod(0o750)),(lambda:key.chmod(0o640),lambda:key.chmod(0o600))]:
            change()
            denied=setup('verify-host','--stage',stage,'--host','forge','--report',forge_report,uid=0,expected=1)
            assert denied['error'] in ('transport_key_directory','transport_key_permissions')
            assert not Path(forge_report).exists();restore()
        checks.append('private_key_group_read_or_nonprivate_root_directory_refuses_verification')
        setup('verify-host','--stage',stage,'--host','forge','--report',forge_report,uid=0)
        valid_report=load(source_report);old_boot=copy.deepcopy(valid_report);old_boot['bootId']=str(uuid.uuid4());save(source_report,old_boot)
        rejected=setup('verify-pilot','--stage',stage,'--forge-report',forge_report,'--source-report',source_report,'--output',proof_file,uid=0,expected=1)
        assert rejected['error']=='launcher_source_boot_changed' and not Path(proof_file).exists()
        stale=copy.deepcopy(valid_report);stale['verifiedAt']-=301;save(source_report,stale)
        rejected=setup('verify-pilot','--stage',stage,'--forge-report',forge_report,'--source-report',source_report,'--output',proof_file,uid=0,expected=1)
        assert rejected['error']=='host_report_not_fresh' and not Path(proof_file).exists()
        save(source_report,valid_report)
        checks.append('source_boot_binding_and_stale_root_reports_refuse_before_activation')
        setup('verify-pilot','--stage',stage,'--forge-report',forge_report,'--source-report',source_report,'--output',proof_file,uid=0)
        proof=load(proof_file);assert len(proof['lines'])==4
        # Failure must invalidate old verification files instead of leaving a
        # still-fresh prior success available to the activation command.
        sudoers.write_text(good_sudo+'fleet-bridge ALL=(root) NOPASSWD: /usr/bin/true\n')
        rejected=devsetup('verify-host','--stage',stage,'--host','dev-3','--report',source_report,expected=1)
        assert rejected['error']=='account_sudo_not_denied' and not Path(source_report).exists()
        setup('activate-pilot','--stage',stage,'--verification',proof_file,uid=0,expected=1)
        sudoers.write_text(good_sudo)
        devsetup('verify-host','--stage',stage,'--host','dev-3','--report',source_report)
        setup('verify-pilot','--stage',stage,'--forge-report',forge_report,'--source-report',source_report,'--output',proof_file,uid=0)
        checks.append('failed_reverification_removes_prior_success_report_and_blocks_still_fresh_activation')
        invalid_proof=copy.deepcopy(proof);invalid_proof['lines']=proof['lines'][:3];save('/etc/bad-proof.json',invalid_proof)
        denied=setup('activate-pilot','--stage',stage,'--verification','/etc/bad-proof.json',uid=0,expected=1)
        assert denied['error']=='activation_launcher_not_verified' and snapshot()['remoteHosts']=={}
        setup('activate-pilot','--stage',stage,'--verification',proof_file,uid=0)
        wait(lambda:(remote_run/'remote.sock').exists(),'gated source readiness')
        assert len(snapshot()['remoteHosts'])==1
        keydir=Path('/etc/fleet-bridge-pilot-ssh');key=keydir/'forge-ed25519'
        assert keydir.stat().st_uid==0 and keydir.stat().st_gid==SERVICE and keydir.stat().st_mode&0o777==0o750
        assert key.stat().st_uid==SERVICE and key.stat().st_mode&0o777==0o600
        for path in ('/etc/fleet-manager-bridge-pilot-renew.json','/etc/fleet-manager-bridge-pilot-publisher.json',
                     '/etc/systemd/system/fleet-manager-bridge-pilot-renew.service','/etc/systemd/system/fleet-manager-bridge-pilot-renew.timer'):
            assert not Path(path).exists()
        checks.append('both_fresh_root_host_results_and_four_lines_gate_both_units_private_key_root_directory_verified_renewal_absent')
        # Switch to explicit owned child sessions for crash fault boundaries.
        run(['/usr/bin/systemctl','disable','--now','fleet-manager-bridge-pilot-source.service','fleet-manager-bridge-pilot.service'])
        wait(lambda:not (remote_run/'remote.sock').exists(),'gated units drained')
        (runtime/'bridge.sock').unlink()
        hub=start([binary,'serve','--state',state/'service','--socket',runtime/'bridge.sock'],ready=True)
        source=connector();wait(lambda:(remote_run/'remote.sock').exists(),'source socket')
        for role in ('spec-manager','foreman-bot','pilot-seat'):
            staged=setup('bootstrap','--config-root',cfg,'--role',role)
            target=(remote_cfg if role=='pilot-seat' else cfg)/'credentials'/(role+'.bootstrap')
            shutil.copyfile(staged['bootstrapFile'],target);os.chown(target,AGENT,AGENT);target.chmod(0o600)
            b=load(binding(role))
            enrolled=command('/usr/bin/python3',REPO/'scripts/factory/isolation_client.py','--socket',b['socket'],
                              '--bootstrap',target,'--credential-out',b['credential'],uid=AGENT)
            assert enrolled['ok']
        checks.append('three_real_kernel_uid_enrollments_over_local_socket_and_standing_openssh')
        checks.append('three_one_hour_credentials_no_renewal_publisher_or_timer_in_the_notices_only_pilot')
        first=source_record();inode=(remote_run/'remote.sock').stat().st_ino
        for sender,receiver in [('spec-manager','foreman-bot'),('foreman-bot','spec-manager'),('foreman-bot','pilot-seat'),('pilot-seat','foreman-bot')]:
            out=endpoint(sender,'send',recipient=meta['seats'][receiver]);assert out['state']=='service_accepted'
            received=endpoint(receiver,'receive',message=out['messageId']);assert len(received['receipts'])==2
            status=endpoint(sender,'status',message=out['messageId'],recipient=meta['seats'][receiver]);assert status['state']=='delivered'
        for sender,receiver in [('spec-manager','pilot-seat'),('pilot-seat','spec-manager')]:
            assert endpoint(sender,'forbidden',recipient=meta['seats'][receiver])['forbiddenRefused']
        assert len(snapshot()['mail'])==4 and all(len(m['receipts'])==2 for m in snapshot()['mail'])
        checks.append('all_four_directed_edges_ordered_receipts_and_two_ungranted_directions_refused')
        duplicate=run([binary,'connect-source','--state',state/'connector','--config',cfg/'transport/source.json'],uid=SERVICE,expected=1)
        assert b'network_source_already_running' in duplicate.stderr
        # The production one-command gate also runs while an owner is live:
        # the authenticated busy greeting never takes over its Unix listener.
        busy=run([binary,'connect-source','--state',state/'duplicate-state','--config',cfg/'transport/source.json'],uid=SERVICE,expected=1)
        assert b'network_source_identity_denied' in busy.stderr
        assert source_record()==first and (remote_run/'remote.sock').stat().st_ino==inode
        before=snapshot();time.sleep(1.1)
        assert source.poll() is None and source_record()==first and snapshot()==before
        ssh_children=Path('/proc')/str(source.pid)/'task'/str(source.pid)/'children'
        assert len(ssh_children.read_text().split())==1
        checks.append('one_idle_ssh_child_no_registry_polling_duplicate_connectors_and_entries_cannot_replace_owner')
        raw=remote_run/'raw.json';raw.write_text('{"protocol":1,"method":"invalid"}\n');raw.chmod(0o644)
        denied=command('/usr/bin/python3',REPO/'scripts/factory/isolation_client.py','--socket',remote_run/'remote.sock','--raw',raw,uid=65533)
        assert denied['error']=='network_peer_uid_denied',denied
        # Direct launcher/entry calls with forged SSH context do not confer the service UID.
        fake=dict(PATH='/usr/bin:/bin',SSH_ORIGINAL_COMMAND='codex-factory-v1',SSH_CONNECTION='10.200.68.55 12345 10.200.68.77 22')
        assert run([keyroot/'fleet-bridge-pilot-launcher'],uid=AGENT,expected=1,env=fake).stdout==b''
        direct=run([binary,'source-entry','--config',keyroot/'fleet-bridge-pilot-entry.json'],uid=AGENT,expected=1,env=fake)
        assert b'network_service_uid_denied' in direct.stderr
        run([keyroot/'fleet-bridge-pilot-launcher','unexpected'],uid=SERVICE,expected=1,env=fake)
        for path in (keyroot/'fleet-bridge-pilot.keys',keyroot/'fleet-bridge-pilot-login',keyroot/'fleet-bridge-pilot-launcher'):
            assert path.stat().st_uid==0 and not path.stat().st_mode&0o022
        assert source_record()==first
        checks.append('actual_peer_uid_and_direct_fake_ssh_context_or_launcher_argument_injection_refused')
        # Break each side independently. Keep exactly the same credential,
        # IDs, outboxes and committed receipts throughout recovery.
        prior_mail=copy.deepcopy(snapshot()['mail']);prior_grants=[x['grant'] for x in snapshot()['seats']]
        stop(source,signal.SIGKILL);source_dead();source=connector()
        assert probe('pilot-seat',request('pilot-seat'))['ok']
        entry=source_record();os.kill(entry['pid'],signal.SIGKILL)
        wait(lambda:source.poll() is not None,'connector must notice entry death')
        source=connector();assert probe('pilot-seat',request('pilot-seat'))['ok']
        assert snapshot()['mail']==prior_mail and [x['grant'] for x in snapshot()['seats']]==prior_grants
        checks.append('connector_and_source_sigkill_restart_prove_owned_stale_inode_and_keep_ids_receipts_grants')
        stop(source);source_dead()
        wait(lambda:not (remote_run/'remote.sock').exists(),'graceful cleanup')
        # Unknown files/sockets must never be deleted to make startup succeed.
        foreign=remote_run/'remote.sock';foreign.write_text('must remain');os.chown(foreign,SERVICE,AGENT)
        refused=run([binary,'connect-source','--state',state/'connector','--config',cfg/'transport/source.json'],uid=SERVICE,expected=1)
        assert foreign.read_text()=='must remain';foreign.unlink()
        pin=cfg/'transport/source_known_hosts';correct=pin.read_bytes()
        pin.write_text('factory-source-'+meta['remoteHost']+' '+(keyroot/'proof-client.pub').read_text());pin.chmod(0o600)
        refused=run([binary,'connect-source','--state',state/'connector','--config',cfg/'transport/source.json'],uid=SERVICE,expected=1)
        assert b'network_' in refused.stderr and not (remote_run/'remote.sock').exists()
        pin.write_bytes(correct);source=connector()
        checks.append('unproven_socket_file_and_wrong_inventory_host_pin_fail_closed_without_fallback')
        # Reverify then use the exact gated units for the restart proof; R stays absent.
        stop(source);source_dead();stop(hub);(runtime/'bridge.sock').unlink()
        setup('verify-host','--stage',stage,'--host','forge','--report',forge_report,uid=0)
        setup('verify-pilot','--stage',stage,'--forge-report',forge_report,'--source-report',source_report,'--output',proof_file,uid=0)
        setup('activate-pilot','--stage',stage,'--verification',proof_file,uid=0)
        wait(lambda:(remote_run/'remote.sock').exists(),'generated unit readiness')
        assert probe('pilot-seat',request('pilot-seat'))['ok']
        unitpid=int(run(['/usr/bin/systemctl','show','-p','MainPID','--value','fleet-manager-bridge-pilot-source.service']).stdout)
        os.kill(unitpid,signal.SIGKILL)
        wait(lambda:int(run(['/usr/bin/systemctl','show','-p','MainPID','--value','fleet-manager-bridge-pilot-source.service']).stdout) not in (0,unitpid),'systemd backoff/restart',15)
        wait(lambda:(remote_run/'remote.sock').exists(),'restarted entry readiness')
        assert probe('pilot-seat',request('pilot-seat'))['ok']
        run(['/usr/bin/systemctl','disable','--now','fleet-manager-bridge-pilot-source.service','fleet-manager-bridge-pilot.service'])
        checks.append('exact_gated_generated_systemd_units_restart_after_sigkill_without_renewal')
        wait(lambda:not (remote_run/'remote.sock').exists(),'unit stop closes entry')
        # Root generation/code/config integrity and OpenSSH key provenance.
        config=load(cfg/'transport/source.json')
        module=__import__('runpy').run_path(str(helper))
        sshargs=module['transport_probe_command'](config,'protocol')+['--','10.200.68.77','codex-factory-v1']
        def ssh_once(args=sshargs):
            return subprocess.run(list(map(str,args)),input=b'',capture_output=True,timeout=10,
                                  user=SERVICE,group=SERVICE,extra_groups=[AGENT],env={'PATH':'/usr/bin:/bin','LC_ALL':'C'})
        invalid_from=[('-oBindAddress=127.0.0.1' if x=='-oBindAddress=10.200.68.55' else x) for x in sshargs]
        assert ssh_once(invalid_from).returncode==255
        attacker=cfg/'transport/attacker'
        run(['/usr/bin/ssh-keygen','-q','-t','ed25519','-N','','-f',attacker],uid=SERVICE)
        user_keys=remote_state/'.ssh';directory(user_keys)
        auth=user_keys/'authorized_keys';auth.write_text((Path(str(attacker)+'.pub')).read_text());os.chown(auth,SERVICE,AGENT);auth.chmod(0o600)
        ignored=[str(attacker) if x==str(keyroot.parent/'fleet-bridge-pilot-ssh/forge-ed25519') else x for x in sshargs]
        assert ssh_once(ignored).returncode==255
        launchfile=keyroot/'fleet-bridge-pilot-launch.json';launch=load(launchfile)
        wrong=copy.deepcopy(launch);wrong['files'][str(binary)]='0'*64;save(launchfile,wrong,mode=0o644)
        assert ssh_once().returncode==1
        save(launchfile,launch,mode=0o644)
        entryfile=keyroot/'fleet-bridge-pilot-entry.json'
        os.chown(entryfile,SERVICE,AGENT);assert ssh_once().returncode==1;os.chown(entryfile,0,0)
        entrydata=entryfile.read_bytes();changed=load(entryfile);changed['generation']=str(uuid.uuid4());save(entryfile,changed,mode=0o644)
        assert ssh_once().returncode==1
        entryfile.write_bytes(entrydata)
        assert ssh_once().returncode==0
        checks.append('from_filter_service_home_key_injection_code_digest_root_owner_and_mixed_generation_rejected')
        # A controlled protocol peer sends wrong context, unknown fields,
        # truncation, an oversized reply or no reply at all. The actual entry
        # attests a real endpoint UID and closes after its bounded deadline.
        for invalid in ('requestId','generation','sourceHost','keyId','protocol','unknown','oversize','truncated','deadline'):
            log=(work/('bad-reply-'+invalid+'.log')).open('wb')
            peer=subprocess.Popen(list(map(str,sshargs)),stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=log,
                                   user=SERVICE,group=SERVICE,extra_groups=[AGENT],start_new_session=True)
            log.close();processes.append(peer)
            assert select.select([peer.stdout],[],[],10)[0]
            hello=json.loads(peer.stdout.readline());assert hello['available']
            endpoint_client=start(['/usr/bin/python3',REPO/'scripts/factory/isolation_client.py','--socket',remote_run/'remote.sock','--raw',raw],uid=AGENT)
            assert select.select([peer.stdout],[],[],5)[0]
            frame=json.loads(peer.stdout.readline());assert frame['peerUid']==AGENT
            reply={k:frame[k] for k in ('protocol','generation','requestId','sourceHost','keyId')};reply['response']={'ok':True}
            started=time.monotonic()
            if invalid in ('requestId','generation','sourceHost','keyId'):reply[invalid]=str(uuid.uuid4())
            elif invalid=='protocol':reply['protocol']=1
            elif invalid=='unknown':reply['unexpected']=True
            data=json.dumps(reply).encode()+b'\n'
            if invalid=='oversize':data=b'x'*66500+b'\n'
            if invalid=='truncated':data=b'{'
            if invalid!='deadline':peer.stdin.write(data);peer.stdin.flush()
            if invalid=='truncated':peer.stdin.close()
            output=endpoint_client.communicate(timeout=6)[0]
            assert endpoint_client.returncode==0 and json.loads(output)['error']=='network_transport_unavailable'
            assert time.monotonic()-started<4.5
            peer.wait(timeout=6)
            wait(lambda:not (remote_run/'remote.sock').exists(),'bad reply must close owned socket')
        checks.append('nine_bad_reply_context_version_unknown_field_bound_truncation_deadline_cases_close_connection')
        # A verified SSH host with a different generation/identity is still
        # not an admitted source. No mailbox frame is accepted before hello.
        launch_snapshot=copy.deepcopy(launch);entry_snapshot=load(entryfile)
        for field in ('generation','sourceHost','transportKey','hub'):
            different=copy.deepcopy(entry_snapshot);different[field]=str(uuid.uuid4());save(entryfile,different,mode=0o644)
            launch['files'][str(entryfile)]=sha(entryfile);save(launchfile,launch,mode=0o644)
            refused=run([binary,'connect-source','--state',state/'connector','--config',cfg/'transport/source.json'],uid=SERVICE,expected=1)
            assert b'network_source_identity_denied' in refused.stderr
            wait(lambda:not (remote_run/'remote.sock').exists(),'wrong hello cleanup')
        save(entryfile,entry_snapshot,mode=0o644);launch=launch_snapshot;save(launchfile,launch,mode=0o644)
        # Restore the exact bytes as well as their parsed meaning.
        assert sha(entryfile)==launch['files'][str(entryfile)]
        source=connector();same=load(cfg/'transport/source.json');changed_config=copy.deepcopy(same)
        changed_config['generation']=str(uuid.uuid4());save(cfg/'transport/source.json',changed_config,SERVICE)
        assert probe('pilot-seat',request('pilot-seat'))['error']=='network_transport_unavailable'
        wait(lambda:source.poll() is not None,'Forge changed generation closes channel');source_dead()
        save(cfg/'transport/source.json',same,SERVICE)
        checks.append('four_authenticated_wrong_hello_identities_and_live_Forge_generation_change_fail_closed')
        if a.fault_bridge:
            # Only this task-owned container package is replaced. Production
            # dist bytes and production launcher pins remain untouched.
            replacement=binary.with_name('fault-replacement');shutil.copyfile(a.fault_bridge,replacement);replacement.chmod(0o755);os.replace(replacement,binary)
            launch['files'][str(binary)]=sha(binary);save(launchfile,launch,mode=0o644)
            (runtime/'bridge.sock').unlink(missing_ok=True)
            hub=start([binary,'serve','--state',state/'service','--socket',runtime/'bridge.sock'],ready=True)
            source=connector()
            for point in ('before_rename','transport_before_reply','caller_lost_after_commit'):
                before_count=len(snapshot()['mail'])
                g=load(binding('pilot-seat'))['grant'];now=int(time.time());mid=str(uuid.uuid4())
                envelope=dict(protocol=1,messageId=mid,conversationId=str(uuid.uuid4()),assignmentId=None,replyTo=None,
                    sender=g['seat'],incarnation=g['incarnation'],recipient=meta['seats']['foreman-bot'],project=g['projects'][0],
                    kind='note',mode='queue_only',createdAt=now,expiresAt=now+3600,text='Inert transport crash proof.',artifactRefs=[])
                wire=request('pilot-seat','send',recipient=envelope['recipient'],operation='queue',envelope=envelope)
                barrier='transport_before_reply' if point=='caller_lost_after_commit' else point
                arm=state/'service'/('fault.'+barrier);hit=state/'service'/('hit.'+barrier)
                arm.touch(mode=0o600);os.chown(arm,SERVICE,AGENT)
                client=start(client_args('pilot-seat',wire),uid=AGENT)
                wait(lambda:hit.exists() or client.poll() is not None,'transport barrier '+point,3)
                assert hit.exists(),'barrier '+point+' client='+client.stdout.read().decode()
                if point=='caller_lost_after_commit':
                    stop(client,signal.SIGKILL);arm.unlink();hit.unlink()
                    wait(lambda:source.poll() is None,'source remains after caller loss',1)
                    time.sleep(.05)
                else:
                    stop(source,signal.SIGKILL);arm.unlink();hit.unlink();source_dead()
                    client.communicate(timeout=6);source=connector()
                replay=probe('pilot-seat',wire);assert replay['ok']
                assert len(snapshot()['mail'])==before_count+1
                receipt=endpoint('foreman-bot','receive',message=mid);assert len(receipt['receipts'])==2
                assert len(next(m for m in snapshot()['mail'] if m['messageId']==mid)['receipts'])==2
            checks.append('before_commit_after_commit_and_caller_loss_retry_original_id_payload_sequence_once')
            stop(source);source_dead();stop(hub)
        # A changed transport key and revocation are checked on each frame,
        # without replacing a live SSH channel. Neither timer nor reconnect
        # may silently undo revocation or reboot fencing.
        source=connector()
        changed=str(uuid.uuid4())
        admin('rotate-host','--state',state/'service','--host',meta['remoteHost'],'--key-id',changed,'--ttl',86400,'--overlap-seconds',0)
        assert probe('pilot-seat',request('pilot-seat'))['error']=='host_credential_inactive'
        stop(source);source_dead()
        # The service configuration is intentionally stale and still cannot
        # turn the current key back into the old, revoked generation.
        source=connector()
        admin('revoke-host','--state',state/'service','--host',meta['remoteHost'])
        assert probe('pilot-seat',request('pilot-seat'))['error']=='host_credential_inactive'
        denied=run([binary,'renew-host','--state',state/'service','--host',meta['remoteHost'],'--key-id',changed,'--ttl',86400],uid=SERVICE,expected=1)
        assert b'host_renewal_denied' in denied.stderr
        stop(source);source_dead()
        checks.append('per_frame_key_rotation_and_host_revocation_override_live_channel_and_renewal')
        epoch=state/'service/lifecycle.json';prior_epoch=load(epoch);prior_epoch['boot']=str(uuid.uuid4());save(epoch,prior_epoch,SERVICE)
        source=connector()
        assert probe('pilot-seat',request('pilot-seat'))['error']=='reboot_requires_service_recovery'
        denied=run([binary,'renew-host','--state',state/'service','--host',meta['remoteHost'],'--key-id',changed,'--ttl',86400],uid=SERVICE,expected=1)
        assert b'reboot_requires_service_recovery' in denied.stderr
        stop(source);source_dead()
        checks.append('prior_boot_epoch_fences_standing_frames_and_renewal_without_resetting_ids_or_receipts')
        result=dict(passed=True,checks=checks,externalModelRequests=0,externalFleetHostContacts=0,
                    serviceUid=SERVICE,endpointUid=AGENT,transport='Forge-initiated actual OpenSSH on private port 22',
                    packageBridgeSha256=production_sha,faultBridgeSha256=sha(a.fault_bridge) if a.fault_bridge else None)
        save(work/'transport-result.json',result)
        return result
    finally:
        for p in reversed(processes):
            try:stop(p)
            except (OSError,subprocess.TimeoutExpired):
                if p.poll() is None:os.killpg(p.pid,signal.SIGKILL);p.wait()


def container_fixture(a):
    assert os.geteuid()==0 and os.readlink('/proc/self/ns/mnt')!=os.readlink('/proc/1/ns/mnt')
    run(['mount','--make-rprivate','/'])
    run(['mount','-t','tmpfs','-o','mode=0755,size=256M','tmpfs','/run'])
    root=a.work_dir/'container';root.mkdir(mode=0o755)
    for name in ('usr','etc/systemd/system','run','var','tmp','proc','sys','dev','opt/fleet-codex','work','repo'):
        (root/name).mkdir(parents=True,exist_ok=True)
    for name in ('lib','lib64','bin','sbin'):(root/name).symlink_to('usr/'+name)
    for name in ('os-release','passwd','group','nsswitch.conf','login.defs'):
        if (Path('/etc')/name).exists():shutil.copyfile(Path('/etc')/name,root/'etc'/name)
    shutil.copytree('/etc/pam.d',root/'etc/pam.d')
    shutil.copytree('/etc/security',root/'etc/security',symlinks=True)
    with (root/'etc/passwd').open('a') as f:
        f.write('fleet-bridge:x:65534:65534:Offline service:/var/lib/fleet-manager-bridge-remote-pilot:/usr/sbin/nologin\n')
        f.write('ops-proof:x:65531:65531:Offline operator:/nonexistent:/bin/bash\n')
        f.write('transport-agent:x:65532:65532:Offline endpoint:/nonexistent:/usr/bin/nologin\n')
    with (root/'etc/group').open('a') as f:
        f.write('fleet-bridge:x:65534:\nfleet-bridge-clients:x:65532:fleet-bridge,transport-agent\n')
    (root/'etc/shadow').write_text('root:!:20000:0:99999:7:::\nfleet-bridge:!:20000:0:99999:7:::\ntransport-agent:!:20000:0:99999:7:::\nops-proof:!:20000:0:99999:7:::\n')
    (root/'etc/shadow').chmod(0o600)
    (root/'etc/sudoers').write_text('Defaults env_reset\nroot ALL=(ALL:ALL) NOPASSWD: ALL\n');(root/'etc/sudoers').chmod(0o440)
    (root/'etc/shells').write_text('/bin/bash\n/usr/bin/bash\n/etc/ssh/fleet-bridge-pilot-login\n')
    (root/'etc/machine-id').write_text(uuid.uuid4().hex+'\n')
    (root/'etc/hostname').write_text('pilot-proof\n')
    (root/'etc/hosts').write_text('127.0.0.1 localhost pilot-proof\n::1 localhost\n')
    marker=uuid.uuid4().hex+'\n'
    for path in (root/'etc/transport-proof-id',a.work_dir/'fixture-id'):path.write_text(marker);path.chmod(0o600)
    pkg=root/'opt/fleet-codex/proof'
    run(['cp','-a','--reflink=auto',a.package,pkg],timeout=90)
    for path in [pkg,*pkg.rglob('*')]:os.chown(path,0,0,follow_symlinks=False)
    fault_arg=''
    if a.fault_bridge:
        fault=root/'opt/fleet-codex/fault-bridge';shutil.copyfile(a.fault_bridge,fault);fault.chmod(0o755)
        fault_arg=' --fault-bridge /opt/fleet-codex/fault-bridge'
    unit=root/'etc/systemd/system/transport-proof.service'
    unit.write_text('[Unit]\nAfter=basic.target\n[Service]\nType=oneshot\nTimeoutStartSec=300\nExecStart=/usr/bin/python3 /repo/scripts/factory/qualify_forge_transport.py --fixture --package /opt/fleet-codex/proof --work-dir /work'+fault_arg+'\nStandardOutput=append:/work/transport-worker.log\nStandardError=append:/work/transport-worker.log\n[Install]\nWantedBy=multi-user.target\n')
    wants=root/'etc/systemd/system/multi-user.target.wants';wants.mkdir();(wants/unit.name).symlink_to('../'+unit.name)
    (a.work_dir/'container-tmp').mkdir(mode=0o1777);(a.work_dir/'container-tmp').chmod(0o1777)
    log=(a.work_dir/'systemd.log').open('w')
    p=subprocess.Popen(['systemd-nspawn','--quiet','--register=no','--keep-unit','--settings=no','--resolv-conf=off','--hostname=pilot-proof','--private-network',
         '--capability=CAP_SYS_ADMIN,CAP_NET_ADMIN','--directory='+str(root),'--bind-ro=/usr',
         '--bind-ro='+str(REPO)+':/repo','--bind='+str(a.work_dir)+':/work','--bind='+str(a.work_dir/'container-tmp')+':/tmp',
         '--boot','--','--log-target=console','--show-status=no'],stdout=log,stderr=log,start_new_session=True)
    try:
        end=time.monotonic()+310
        while not (a.work_dir/'transport-result.json').exists():
            assert p.poll() is None and time.monotonic()<end,'transport fixture failed; inspect transport-worker.log'
            worker=a.work_dir/'transport-worker.log'
            if worker.exists() and 'Traceback (most recent call last)' in worker.read_text():raise RuntimeError('transport worker failed; inspect transport-worker.log')
            time.sleep(.2)
        print((a.work_dir/'transport-result.json').read_text(),flush=True)
    finally:
        if p.poll() is None:p.terminate()
        try:p.wait(timeout=20)
        except subprocess.TimeoutExpired:os.killpg(p.pid,signal.SIGKILL);p.wait()
        log.close()


def pipeline(a):
    import qualify_renewals as r
    assert os.geteuid()==0 and os.readlink('/proc/self/ns/mnt')!=os.readlink('/proc/1/ns/mnt')
    r.BWRAP=BWRAP
    root=a.work_dir
    inherited=root/'R';inherited.mkdir(mode=0o755);os.chown(inherited,a.caller_uid,a.caller_uid)
    base=types.SimpleNamespace(**vars(a));base.work_dir=inherited
    r.pipeline(base)
    transport=root/'T';transport.mkdir(mode=0o755)
    fixture_args=types.SimpleNamespace(**vars(a));fixture_args.work_dir=transport
    started=time.monotonic();container_fixture(fixture_args)
    result=load(inherited/'qualification.json')
    result['compatibilityOnly']=['renewals','setupCompatibility']
    result['runtimeCompilation']=False
    result['faultsReused']=load(root/'reused-faults.json')
    result['forgeTransport']=load(transport/'transport-result.json')
    result['forgeTransport']['seconds']=round(time.monotonic()-started,2)
    result['proofScriptsSha256']['qualify_forge_transport.py']=sha(__file__)
    result.update(status='passed',bwrapSha256=BWRAP,packageManifestSha256=sha(a.package/'MANIFEST.json'))
    (root/'qualification.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(dict(status='passed',mailboxLanes=7,renewals=True,setup=True,packagedTools=True,
        forgeInitiatedTransport=True,transportChecks=len(result['forgeTransport']['checks']),
        externalModelRequests=0,externalFleetHostContacts=0,bwrapSha256=BWRAP)),flush=True)


def full_package_proof(a):
    from check_package import check
    assert check(a.package)['passed'] and sha(a.package/'codex-resources/bwrap')==BWRAP
    subprocess.run(['sha256sum','-c','SHA256SUMS'],cwd=a.package,stdout=subprocess.DEVNULL,check=True)
    manifest=load(a.package/'MANIFEST.json');assert manifest['runtimeReuse']['allIdentical']
    assert manifest['workspaceVersion']=='0.167.2+ignibyte.1112f'
    for relative in ('setup-forge-box.py','fleet-bridge-ssh-entry.py','systemd/fleet-manager-bridge-source.service.in'):
        assert sha(a.package/relative)==sha(REPO/'scripts/factory'/relative)
    cached=Path('/mnt/fast/target')/manifest['target']/'dev-small'
    expected=manifest['compatibilityOnlyPriorQualification']['forgeTransport']['faultBridgeSha256']
    assert sha(cached/'codex-factory-bridge')==expected,'same-source cached fault bridge required; NO BUILD permitted'
    identity=json.loads(run([cached/'codex','factory-info']).stdout)
    assert identity['revision']==manifest['runtimeSourceCommit'] and identity['version']==manifest['workspaceVersion']
    pair=a.work_dir/'fault-pair';pair.mkdir(mode=0o755)
    for name in ('codex','codex-factory-bridge'):shutil.copy2(cached/name,pair/name)
    (a.work_dir/'reused-faults.json').write_text(json.dumps({n:sha(pair/n) for n in ('codex','codex-factory-bridge')}))
    subprocess.run(['sudo','-n','unshare','--mount','--fork',sys.executable,__file__,'--pipeline','--package',str(a.package),
        '--work-dir',str(a.work_dir),'--fault-bridge',str(pair/'codex-factory-bridge'),'--fault-cli',str(pair/'codex'),
        '--caller-uid',str(os.getuid())],check=True)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--package',type=Path,required=True);p.add_argument('--work-dir',type=Path,required=True)
    p.add_argument('--fixture',action='store_true',help=argparse.SUPPRESS)
    p.add_argument('--container',action='store_true',help=argparse.SUPPRESS)
    p.add_argument('--fault-bridge',type=Path)
    p.add_argument('--fault-cli',type=Path);p.add_argument('--caller-uid',type=int)
    p.add_argument('--full-package-proof',action='store_true')
    p.add_argument('--pipeline',action='store_true',help=argparse.SUPPRESS)
    a=p.parse_args();a.package=a.package.resolve();a.work_dir=a.work_dir.resolve()
    if a.fixture:print(json.dumps(fixture(a)));return
    if a.container:container_fixture(a);return
    if a.pipeline:pipeline(a);return
    assert not a.work_dir.exists(),'fresh proof directory required'
    a.work_dir.mkdir(mode=0o700)
    if a.full_package_proof:full_package_proof(a);return
    args=['sudo','-n','unshare','--mount','--fork','/usr/bin/python3',__file__,'--container',
          '--package',str(a.package),'--work-dir',str(a.work_dir)]
    if a.fault_bridge:args+=['--fault-bridge',str(a.fault_bridge.resolve())]
    subprocess.run(args,check=True)


if __name__=='__main__':main()
