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
        return command('/usr/bin/python3','-I',helper,*args,uid=uid,expected=expected)
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
        for c,s,r,roles in [(cfg,state,runtime,('spec-manager','foreman-bot')),
                            (remote_cfg,remote_state,remote_run,('pilot-seat',))]:
            directory(c,mode=0o2750);directory(s,mode=0o2710);directory(r,mode=0o2710)
            directory(c/'credentials',uid=AGENT);directory(s/'native',mode=0o710)
            for role in roles:directory(s/'native'/role,uid=AGENT)
        assert setup('init','--pilot','--defer-remote','--bin-dir',pkg/'bin',
                     '--config-root',cfg,'--state-root',state,'--run-root',runtime,
                     '--remote-config-root',remote_cfg,'--remote-state-root',remote_state,'--remote-run-root',remote_run,
                     '--agent-uid',AGENT,'--remote-agent-uid',AGENT,'--remote-service-uid',SERVICE)['remoteAdmitted'] is False
        meta=load(cfg/'setup.json'); before=snapshot()
        setup('source-keygen','--config-root',cfg)
        keyroot=Path('/etc/ssh'); keyroot.mkdir(exist_ok=True)
        Path('/run/sshd').mkdir(exist_ok=True)
        run(['/usr/bin/ssh-keygen','-q','-t','ed25519','-N','','-f',keyroot/'proof-host'])
        inp=Path('/etc/pilot-transport-input.json')
        save(inp,dict(forgeAddress='10.200.68.55',sourceAddress='10.200.68.77',port=22,
                      clientPublicKey=(cfg/'transport/forge-ed25519.pub').read_text(),
                      sourceHostKey=(keyroot/'proof-host.pub').read_text()))
        stage=Path('/var/lib/fleet-codex-pilot-stage')
        staged=setup('configure-transport','--direction','forge-initiated','--config-root',cfg,
                     '--input',inp,'--output',stage,uid=0)
        assert staged['servicesStarted']==0 and snapshot()==before
        generation=load(stage/'generation.json')
        assert all(sha(stage/name)==data['sha256'] for name,data in generation['files'].items())
        targets={'entry.json':'fleet-bridge-pilot-entry.json','launch.json':'fleet-bridge-pilot-launch.json'}
        for name in ('fleet-bridge-pilot-login','fleet-bridge-pilot-launcher','fleet-bridge-pilot.keys','fleet-bridge-pilot-match.conf','entry.json','launch.json'):
            dst=keyroot/targets.get(name,name);shutil.copyfile(stage/name,dst)
            dst.chmod(int(generation['files'][name]['mode'],8))
        for name,dst,mode in [('source.json',cfg/'transport/source.json',0o600),
                              ('source_known_hosts',cfg/'transport/source_known_hosts',0o600),('setup.json',cfg/'setup.json',0o640)]:
            shutil.copyfile(stage/name,dst);os.chown(dst,SERVICE,AGENT);dst.chmod(mode)
        shutil.copyfile(cfg/'pilot-seat.binding.json',binding('pilot-seat'))
        os.chown(binding('pilot-seat'),SERVICE,AGENT);binding('pilot-seat').chmod(0o640)
        checks.append('generated_generation_preserves_ids_grants_registry_and_starts_nothing')
        sshconf=keyroot/'transport-proof.conf'
        sshconf.write_text('\n'.join(['Port 22','ListenAddress 10.200.68.77',f'HostKey {keyroot}/proof-host',
          f'PidFile {keyroot}/proof.pid','AllowUsers fleet-bridge','UsePAM yes','PasswordAuthentication no',
          'KbdInteractiveAuthentication no','PermitUserEnvironment no','StrictModes yes','UseDNS no','PrintMotd no','LogLevel VERBOSE',
          f'Include {keyroot}/fleet-bridge-pilot-match.conf','']))
        sshconf.chmod(0o600)
        run(['/usr/bin/sshd','-t','-f',sshconf])
        actual=run(['/usr/bin/sshd','-T','-f',sshconf,'-C','user=fleet-bridge,host=proof,addr=10.200.68.55']).stdout.decode()
        (work/'effective-sshd.txt').write_text(actual)
        for line in ('permittty no','allowtcpforwarding no','x11forwarding no','allowagentforwarding no',
                     'permittunnel no','authenticationmethods publickey','authorizedkeysfile /etc/ssh/fleet-bridge-pilot.keys'):
            assert line in actual.lower(),line
        assert next(x for x in Path('/etc/shadow').read_text().splitlines() if x.startswith('fleet-bridge:')).split(':')[1]=='!'
        denied=subprocess.run(['/usr/bin/sudo','-n','true'],user=SERVICE,group=SERVICE,extra_groups=[AGENT],capture_output=True)
        assert denied.returncode!=0
        sshd=start(['/usr/bin/sshd','-D','-e','-f',sshconf],uid=0)
        wait(lambda:(keyroot/'proof.pid').exists(),'sshd readiness')
        proof=run(['/usr/bin/python3','-I',helper,'verify-launcher','--config',cfg/'transport/source.json'])
        assert proof.stdout.decode().splitlines()==['AUTHENTICATED_PROTOCOL PASS','SHELL REFUSED','PTY REFUSED','FORWARD REFUSED']
        checks.append('actual_pam_locked_account_root_key_no_sudo_shell_pty_forward_refused')
        wait(lambda:bool(run(['/usr/bin/journalctl','-t','fleet-bridge-entry','-o','cat','--no-pager']).stdout.strip()),'refusal syslog')
        logged=run(['/usr/bin/journalctl','-t','fleet-bridge-entry','-o','cat','--no-pager']).stdout.decode().splitlines()
        assert len(logged)==1 and re.fullmatch(r'entry_refused host=[0-9a-f-]{36} key=[0-9a-f-]{36} generation=[0-9a-f-]{36} code=command',logged[0])
        checks.append('one_ids_only_syslog_line_per_wrong_command_without_raw_command_or_bearer')
        # Use the real administrative admission command with a short lease;
        # mark the fixture's explicit admission before minting its pilot token.
        admin('admit-host','--state',state/'service','--host',meta['remoteHost'],'--key-id',meta['transportKey'],'--bridge-uid',SERVICE,'--ttl',8)
        current=load(cfg/'setup.json');current['remoteAdmitted']=True;save(cfg/'setup.json',current,SERVICE,0o640)
        hub=start([binary,'serve','--state',state/'service','--socket',runtime/'bridge.sock'],ready=True)
        source=connector()
        wait(lambda:(remote_run/'remote.sock').exists(),'source socket')
        for role in ('spec-manager','foreman-bot','pilot-seat'):
            staged=setup('bootstrap','--config-root',cfg,'--role',role)
            target=(remote_cfg if role=='pilot-seat' else cfg)/'credentials'/(role+'.bootstrap')
            shutil.copyfile(staged['bootstrapFile'],target);os.chown(target,AGENT,AGENT);target.chmod(0o600)
            b=load(binding(role))
            enrolled=command('/usr/bin/python3',REPO/'scripts/factory/isolation_client.py','--socket',b['socket'],
                              '--bootstrap',target,'--credential-out',b['credential'],uid=AGENT)
            assert enrolled['ok']
        checks.append('three_real_kernel_uid_enrollments_over_local_socket_and_standing_openssh')
        lease=snapshot()['remoteHosts'][meta['remoteHost']]['expiresAt']
        wait(lambda:int(time.time())>=lease,'short admitted lease expiry',10)
        expired=probe('pilot-seat',request('pilot-seat'))
        assert not expired.get('ok') and expired['error']=='host_credential_inactive',expired
        # Local seats remain independent when the remote lease lapses.
        assert probe('spec-manager',request('spec-manager'))['ok']
        restored=admin('renew-host','--state',state/'service','--host',meta['remoteHost'],'--key-id',meta['transportKey'],'--ttl',86400)
        assert restored['ok'] and probe('pilot-seat',request('pilot-seat'))['ok']
        checks.append('per_frame_real_lease_expiry_denies_remote_only_same_key_renewal_restores_without_reconnect')
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
        run(['/usr/bin/python3','-I',helper,'verify-launcher','--config',cfg/'transport/source.json'])
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
        pin.write_text('factory-source-'+meta['remoteHost']+' '+(cfg/'transport/forge-ed25519.pub').read_text());pin.chmod(0o600)
        refused=run([binary,'connect-source','--state',state/'connector','--config',cfg/'transport/source.json'],uid=SERVICE,expected=1)
        assert b'network_' in refused.stderr and not (remote_run/'remote.sock').exists()
        pin.write_bytes(correct);source=connector()
        checks.append('unproven_socket_file_and_wrong_inventory_host_pin_fail_closed_without_fallback')
        # A local publisher call in this one-kernel fixture uses the same R
        # validation/atomic rename. Full R proof separately tests real SSH
        # publisher delivery, loss, outage and crash boundaries.
        old=load(remote_cfg/'credentials/pilot-seat.json')
        candidate=state/'pending/pilot-rotated.json'
        admin('rotate','--state',state/'service','--seat',meta['seats']['pilot-seat'],'--output',candidate,'--credential-ttl',3600,'--overlap-seconds',0)
        assert not probe('pilot-seat',request('pilot-seat')).get('ok')
        policy=load(binding('pilot-seat'))
        payload=candidate.read_bytes()
        installed=run(['/usr/bin/python3','-I',helper,'publish-credential','--policy-json',json.dumps(policy)],uid=AGENT,payload=payload)
        assert json.loads(installed.stdout)['published'] and load(remote_cfg/'credentials/pilot-seat.json')['credential']!=old['credential']
        assert probe('pilot-seat',request('pilot-seat'))['ok']
        checks.append('standing_connection_rechecks_rotated_bearer_and_reads_atomic_R_publication_without_restart')
        admin('renew-host','--state',state/'service','--host',meta['remoteHost'],'--key-id',meta['transportKey'],'--ttl',86400)
        assert probe('pilot-seat',request('pilot-seat'))['ok']
        # Expiry is tested with an actual admitted short-lived additional
        # generation in a separate registry below; active leases never shorten.
        checks.append('same_key_host_renewal_keeps_standing_link_valid_and_grants_unchanged')
        # Exact installed units, including real systemd restart/backoff.
        stop(source);source_dead();stop(hub);(runtime/'bridge.sock').unlink()
        for name in ('fleet-manager-bridge-pilot.service','fleet-manager-bridge-pilot-source.service'):
            shutil.copyfile(stage/name,Path('/etc/systemd/system')/name)
        renewal_stage=Path('/var/lib/fleet-manager-bridge-pilot-renew/staged')
        setup('render-renewal','--config-root',cfg,'--runtime-root','/var/lib/fleet-manager-bridge-pilot-renew',
              '--unit-prefix','fleet-manager-bridge-pilot','--output',renewal_stage,uid=0)
        allowlist=Path('/etc/fleet-manager-bridge-pilot-renew.json')
        shutil.copyfile(renewal_stage/'allowlist.json',allowlist);allowlist.chmod(0o600)
        for name in ('fleet-manager-bridge-pilot-renew.service','fleet-manager-bridge-pilot-renew.timer'):
            shutil.copyfile(renewal_stage/name,Path('/etc/systemd/system')/name)
        renewal_text=(renewal_stage/'fleet-manager-bridge-pilot-renew.service').read_text()
        assert '--config /etc/fleet-manager-bridge-pilot-renew.json' in renewal_text
        assert 'ReadWritePaths='+str(state)+' /var/lib/fleet-manager-bridge-pilot-renew '+str(cfg/'credentials') in renewal_text
        run(['/usr/bin/systemctl','daemon-reload'])
        run(['/usr/bin/systemctl','start','fleet-manager-bridge-pilot-source.service'])
        wait(lambda:(remote_run/'remote.sock').exists(),'generated unit entry readiness')
        assert probe('pilot-seat',request('pilot-seat'))['ok']
        local_tokens={role:load(cfg/'credentials'/(role+'.json'))['credential'] for role in ('spec-manager','foreman-bot')}
        for role in local_tokens:
            admin('rotate','--state',state/'service','--seat',meta['seats'][role],
                  '--output',state/'pending'/('force-'+role),'--credential-ttl',2,'--overlap-seconds',60)
        run(['/usr/bin/systemctl','start','--wait','fleet-manager-bridge-pilot-renew.service'])
        for role,token in local_tokens.items():
            assert load(cfg/'credentials'/(role+'.json'))['credential']!=token
            assert probe(role,request(role))['ok']
        before_migration=snapshot();input_data=load(inp);input_data['renewalConfig']=str(allowlist);save(inp,input_data)
        setup('configure-transport','--direction','forge-initiated','--config-root',cfg,'--input',inp,
              '--output','/var/lib/fleet-codex-pilot-stage-validation',uid=0)
        assert snapshot()==before_migration
        invalid=load(allowlist);invalid['seats'][0]['policy']['grant']['role']='worker'
        bad_allowlist=Path('/etc/wrong-pilot-renew.json');save(bad_allowlist,invalid)
        input_data['renewalConfig']=str(bad_allowlist);save(inp,input_data)
        refused=setup('configure-transport','--direction','forge-initiated','--config-root',cfg,'--input',inp,
                      '--output','/var/lib/rejected-pilot-generation',uid=0,expected=1)
        assert refused['error']=='transport_renewal_binding_conflict'
        assert not Path('/var/lib/rejected-pilot-generation').exists() and snapshot()==before_migration
        checks.append('pilot_R_unit_uses_own_allowlist_and_writable_roots_rotates_locals_existing_generation_checked_without_mutation')
        unitpid=int(run(['/usr/bin/systemctl','show','-p','MainPID','--value','fleet-manager-bridge-pilot-source.service']).stdout)
        assert Path('/proc')/str(unitpid) != Path('/proc/1')
        os.kill(unitpid,signal.SIGKILL)
        wait(lambda:int(run(['/usr/bin/systemctl','show','-p','MainPID','--value','fleet-manager-bridge-pilot-source.service']).stdout) not in (0,unitpid),
             'systemd backoff/restart',15)
        wait(lambda:(remote_run/'remote.sock').exists(),'restarted entry readiness')
        assert probe('pilot-seat',request('pilot-seat'))['ok']
        run(['/usr/bin/systemctl','stop','fleet-manager-bridge-pilot-source.service','fleet-manager-bridge-pilot.service'])
        checks.append('exact_generated_systemd_units_run_as_service_and_restart_after_real_sigkill_with_backoff')
        wait(lambda:not (remote_run/'remote.sock').exists(),'unit stop closes owned entry')
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
        ignored=[str(attacker) if x==str(cfg/'transport/forge-ed25519') else x for x in sshargs]
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
        f.write('fleet-bridge:x:65534:65534:Offline service:/var/lib/fleet-manager-bridge-remote-pilot:/etc/ssh/fleet-bridge-pilot-login\n')
        f.write('transport-agent:x:65532:65532:Offline endpoint:/nonexistent:/usr/bin/nologin\n')
    with (root/'etc/group').open('a') as f:
        f.write('fleet-bridge:x:65534:\nfleet-bridge-clients:x:65532:fleet-bridge,transport-agent\n')
    (root/'etc/shadow').write_text('root:!:20000:0:99999:7:::\nfleet-bridge:!:20000:0:99999:7:::\ntransport-agent:!:20000:0:99999:7:::\n')
    (root/'etc/shadow').chmod(0o600)
    (root/'etc/sudoers').write_text('Defaults env_reset\nroot ALL=(ALL:ALL) NOPASSWD: ALL\n');(root/'etc/sudoers').chmod(0o440)
    (root/'etc/shells').write_text('/bin/bash\n/usr/bin/bash\n/etc/ssh/fleet-bridge-pilot-login\n')
    (root/'etc/machine-id').write_text(uuid.uuid4().hex+'\n')
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
    p=subprocess.Popen(['systemd-nspawn','--quiet','--register=no','--keep-unit','--settings=no','--resolv-conf=off','--private-network',
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
    result['forgeTransport']=load(transport/'transport-result.json')
    result['forgeTransport']['seconds']=round(time.monotonic()-started,2)
    result['proofScriptsSha256']['qualify_forge_transport.py']=sha(__file__)
    result.update(status='passed',bwrapSha256=BWRAP,packageManifestSha256=sha(a.package/'MANIFEST.json'))
    (root/'qualification.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(dict(status='passed',mailboxLanes=7,renewals=True,setup=True,packagedTools=True,
        forgeInitiatedTransport=True,transportChecks=len(result['forgeTransport']['checks']),
        externalModelRequests=0,externalFleetHostContacts=0,bwrapSha256=BWRAP)),flush=True)


def full_package_proof(a):
    from check_package import check,TARGET_SPECS
    from codex_package.v8 import resolve_codex_v8_cargo_env
    assert check(a.package)['passed'] and sha(a.package/'codex-resources/bwrap')==BWRAP
    subprocess.run(['sha256sum','-c','SHA256SUMS'],cwd=a.package,stdout=subprocess.DEVNULL,check=True)
    for relative in ('setup-forge-box.py','fleet-bridge-ssh-entry.py','systemd/fleet-manager-bridge-source.service.in',
                     'systemd/fleet-manager-bridge-renew.service.in','systemd/fleet-manager-bridge-renew.timer.in'):
        assert sha(a.package/relative)==sha(REPO/'scripts/factory'/relative),'packaged operator asset differs: '+relative
    manifest=load(a.package/'MANIFEST.json');source=manifest['runtimeSourceCommit']
    assert manifest['workspaceVersion']=='0.167.2+ignibyte.1112f'
    env=os.environ.copy();env.update(resolve_codex_v8_cargo_env(TARGET_SPECS[manifest['target']]))
    env.update(RUSTUP_TOOLCHAIN='1.95.0',CARGO_BUILD_JOBS='8',CARGO_INCREMENTAL='0',CODEX_FACTORY_REVISION=source,
               STABLE_GIT_COMMIT=source,CODEX_BWRAP_SHA256=BWRAP)
    target=Path(env.get('CARGO_TARGET_DIR','/mnt/fast/target')).resolve()
    args=['cargo','build','--locked','--profile','dev-small','--target',manifest['target'],'--target-dir',target,
          '-p','codex-cli','-p','codex-factory','--features',
          'codex-install-context/factory,codex-app-server/factory-fixture-faults,codex-factory/fixture-faults',
          '--bin','codex','--bin','codex-factory-bridge']
    with (a.work_dir/'fault-build.log').open('wb') as log:
        subprocess.run(list(map(str,args)),cwd=REPO/'codex-rs',env=env,stdout=log,stderr=subprocess.STDOUT,check=True)
    pair=a.work_dir/'fault-pair';pair.mkdir(mode=0o755)
    for name in ('codex','codex-factory-bridge'):shutil.copy2(target/manifest['target']/'dev-small'/name,pair/name)
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
