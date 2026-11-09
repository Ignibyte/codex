#!/usr/bin/env python3
"""Actual UID-isolated bridge enrollment and authorization, with redacted evidence."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import select
import shutil
import subprocess
import sys
import tempfile
import time
import traceback
import uuid

ROOT=Path(__file__).resolve().parents[2]
SERVICE=65534
WORKER=65533
OBSERVER=65532
STRANGER=65531
GROUP=65534


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--binary',required=True,type=Path)
    parser.add_argument('--evidence',required=True,type=Path)
    args=parser.parse_args();args.binary=args.binary.resolve()
    args.evidence.mkdir(mode=0o700,parents=True,exist_ok=False)
    e=args.evidence
    checks=[];commands=[];server=None;record=None
    base=Path(tempfile.mkdtemp(prefix='cf5-',dir='/tmp'))
    socket=base/'bridge.sock';state=base/'state';binary=base/'bin/bridge'
    helper=ROOT/'scripts/factory/isolation_client.py'
    def run(cmd,expected=0,timeout=15):
        out=subprocess.run(cmd,capture_output=True,text=True,timeout=timeout)
        commands.append({'command':list(map(str,cmd)),'exit_code':out.returncode,'stdout':out.stdout,'stderr':out.stderr})
        if expected is not None:assert out.returncode==expected,commands[-1]
        return out
    def sudo(*cmd):return run(['sudo','-n',*map(str,cmd)])
    def as_uid(uid,*cmd,expected=0):
        return run(['sudo','-n','setpriv','--reuid',str(uid),'--regid',str(GROUP),
                    '--clear-groups','--no-new-privs',*map(str,cmd)],expected)
    def publish(name,value):
        src=e/('input-'+name+'.json');src.write_text(json.dumps(value))
        dst=base/'public'/(name+'.json')
        sudo('install','-o',SERVICE,'-g',GROUP,'-m','0644',src,dst)
        return dst
    def check(name,value):
        assert value, name
        checks.append({'check':name,'passed':True})
    def identity(pid):
        fields=Path(f'/proc/{pid}/stat').read_text().rsplit(')',1)[1].split()
        return {'pid':pid,'startTicks':fields[19],
                'bootId':Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
                'exe':str(binary),'uid':SERVICE}
    def start():
        nonlocal server,record
        cmd=['sudo','-n','setpriv','--reuid',str(SERVICE),'--regid',str(GROUP),
             '--clear-groups','--no-new-privs',str(binary),'serve','--state',str(state),'--socket',str(socket)]
        log=(e/('service-'+str(len([p for p in e.glob('service-*.log')]))+'.log')).open('w')
        server=subprocess.Popen(cmd,stdout=subprocess.PIPE,stderr=log,text=True)
        assert select.select([server.stdout],[],[],15)[0], 'service readiness timed out'
        ready=json.loads(server.stdout.readline());assert ready['ready'] and ready['uid']==SERVICE
        record=identity(ready['pid']);(e/'stop-record.json').write_text(json.dumps(record))
        commands.append({'command':cmd,'ready':ready,'incarnation':record})
        check('socket_acl_0660',socket.stat().st_mode & 0o777 == 0o660)
    def stop():
        nonlocal server
        if server is None:return
        if server.poll() is None:
            run(['sudo','-n',sys.executable,str(helper),'--stop-record',str(e/'stop-record.json')])
        code=server.wait(timeout=15)
        commands.append({'service_supervisor_exit':code,'owned_incarnation':record})
        server.stdout.close();server=None
        info=socket.lstat()
        assert info.st_uid==SERVICE
        inode=info.st_ino
        sudo('rm','--',socket)
        commands.append({'removed_owned_socket_inode':inode})
    def client(uid,*options):
        out=as_uid(uid,sys.executable,helper,'--socket',socket,*options)
        return json.loads(out.stdout)
    grants={}
    def mint(name,uid,role,ops,bootstrap_ttl=60,credential_ttl=60,host=None):
        grant={'seat':str(uuid.uuid4()),'host':host or host_id,'incarnation':str(uuid.uuid4()),
            'agentUid':uid,'role':role,'projects':['fixture-project'],'recipients':[recipient], 'operations':ops}
        grants[name]=grant
        grant_path=publish('grant-'+name,grant)
        token=base/('bootstrap-'+name)
        out=as_uid(SERVICE,binary,'bootstrap','--state',state,'--grant',grant_path,'--output',token,
                   '--bootstrap-ttl',str(bootstrap_ttl),'--credential-ttl',str(credential_ttl),expected=None)
        return grant,token,out
    def handoff(token,uid,role):
        dest=base/role/'bootstrap'
        sudo('mv','--',token,dest);sudo('chown',str(uid)+':'+str(GROUP),dest)
        return dest
    def enroll(name,uid,role,ops,**kwargs):
        grant,token,out=mint(name,uid,role,ops,**kwargs);assert out.returncode==0,commands[-1]
        token=handoff(token,uid,name)
        credential=base/name/'credential'
        response=client(uid,'--bootstrap',token,'--credential-out',credential)
        check(name+'_enrolled',response.get('ok') is True)
        return grant,token,credential,response
    def request_for(grant,sequence=1,**changes):
        return {'protocol':1,'sender':grant['seat'],'host':grant['host'],
            'incarnation':grant['incarnation'],'sequence':sequence,'project':'fixture-project',
            'recipient':recipient,'operation':'observe',**changes}
    try:
        sudo('chown',str(SERVICE)+':'+str(GROUP),base);sudo('chmod','0711',base)
        for name in ['bin','public']:
            sudo('install','-d','-o',SERVICE,'-g',GROUP,'-m','0711',base/name)
        for name,uid in [('worker',WORKER),('observer',OBSERVER),('stranger',STRANGER),('expired',WORKER),('short',WORKER)]:
            sudo('install','-d','-o',uid,'-g',GROUP,'-m','0700',base/name)
        sudo('install','-o',SERVICE,'-g',GROUP,'-m','0555',args.binary,binary)
        host_id=str(uuid.uuid4());recipient=str(uuid.uuid4())
        worker,worker_token,out=mint('worker',WORKER,'worker',['observe','queue'])
        check('worker_bootstrap_created',out.returncode==0)
        worker_token=handoff(worker_token,WORKER,'worker')
        start()
        worker_credential=base/'worker/credential'
        response=client(WORKER,'--bootstrap',worker_token,'--credential-out',worker_credential)
        check('worker_enrolled',response.get('ok') is True)
        permissions=as_uid(SERVICE,'stat','-c','%a %u',state,state/'service.key',state/'registry.json')
        check('service_state_and_secret_modes',permissions.stdout.splitlines()==[
            '700 '+str(SERVICE),'600 '+str(SERVICE),'600 '+str(SERVICE)])
        response=client(WORKER,'--bootstrap',worker_token)
        check('one_use_bootstrap',response.get('error')=='bootstrap_reused')
        response=client(WORKER,'--bootstrap',worker_token,'--forge-bootstrap')
        check('unknown_bootstrap_denied',response.get('error')=='bootstrap_unknown')
        observer,observer_token,observer_credential,_=enroll('observer',OBSERVER,'observer',['observe'])
        for name,value,expected in [
            ('unknown_method',{'method':'execute','protocol':1,'command':'synthetic-no-execution'},'invalid_request'),
            ('state_read_method',{'method':'read_state','protocol':1},'invalid_request'),
            ('oversized_frame',{'method':'enroll','protocol':1,'bootstrap':'0'*70000},'frame_bound'),
        ]:
            src=e/('raw-'+name+'.json');src.write_text(json.dumps(value)+'\n')
            dst=base/'public'/('raw-'+name+'.json')
            sudo('install','-o',SERVICE,'-g',GROUP,'-m','0644',src,dst)
            response=client(OBSERVER,'--raw',dst)
            check(name+'_refused',response.get('error')==expected)
        path=publish('valid-worker',request_for(worker))
        response=client(WORKER,'--credential',worker_credential,'--request',path)
        check('scoped_request_authorized_without_dispatch',response.get('authorized') is True and response.get('dispatch')=='not_performed')
        for name,changes,expected in [
            ('forged_sender',{'sender':str(uuid.uuid4())},'sender_mismatch'),
            ('wrong_host',{'host':str(uuid.uuid4())},'host_mismatch'),
            ('stale_incarnation',{'incarnation':str(uuid.uuid4())},'incarnation_stale'),
            ('wrong_project',{'project':'other-project'},'project_denied'),
            ('wrong_recipient',{'recipient':str(uuid.uuid4())},'recipient_denied'),
            ('unknown_version',{'protocol':2},'protocol_unsupported'),
            ('worker_cannot_execute',{'operation':'execute'},'operation_denied'),
            ('worker_cannot_approve',{'operation':'approve'},'operation_denied'),
        ]:
            path=publish(name,request_for(worker,2,**changes))
            response=client(WORKER,'--credential',worker_credential,'--request',path)
            check(name,response.get('error')==expected)
        path=publish('forge-credential',request_for(worker,2))
        response=client(WORKER,'--credential',worker_credential,'--request',path,'--forge-credential')
        check('forged_credential_denied',response.get('error')=='credential_denied')
        response=client(WORKER,'--credential',worker_credential,'--request',path,'--claim-seat',str(uuid.uuid4()))
        check('unknown_sender_denied',response.get('error')=='credential_denied')
        response=client(WORKER,'--credential',worker_credential,'--request',path,'--wire-version','2')
        check('wire_version_denied',response.get('error')=='protocol_unsupported')
        sudo('cp','--',worker_credential,base/'stranger/credential');sudo('chown',str(STRANGER)+':'+str(GROUP),base/'stranger/credential')
        response=client(STRANGER,'--credential',base/'stranger/credential','--request',path)
        check('stolen_credential_wrong_uid_denied',response.get('error')=='peer_uid_denied')
        for operation in ['queue','interrupt','approve','execute']:
            path=publish('observer-'+operation,request_for(observer,operation=operation))
            response=client(OBSERVER,'--credential',observer_credential,'--request',path)
            check('observer_cannot_'+operation,response.get('error')=='operation_denied')
        path=publish('observer-observe',request_for(observer))
        response=client(OBSERVER,'--credential',observer_credential,'--request',path)
        check('observer_can_observe',response.get('authorized') is True)
        inspected=as_uid(OBSERVER,sys.executable,helper,'--inspect',state/'service.key',state/'registry.json',worker_credential)
        privacy=json.loads(inspected.stdout)
        check('real_service_and_peer_secret_read_denied',not any(privacy['readable'].values()))
        check('agent_no_new_privs_and_no_sudo',privacy['uid']==OBSERVER and privacy['noNewPrivs'] and privacy['sudo_exit']!=0)
        _,_,out=mint('same-uid',SERVICE,'observer',['observe'])
        check('same_uid_enrollment_refused',out.returncode!=0 and 'agent_service_uid_boundary' in out.stderr)
        _,_,out=mint('wrong-registry-host',WORKER,'observer',['observe'],host=str(uuid.uuid4()))
        check('host_admission_cannot_be_self_asserted',out.returncode!=0 and 'host_registry_mismatch' in out.stderr)
        expired,token,out=mint('expired',WORKER,'worker',['observe'],bootstrap_ttl=1)
        assert out.returncode==0
        token=handoff(token,WORKER,'expired');time.sleep(1.1)
        response=client(WORKER,'--bootstrap',token)
        check('expired_bootstrap_refused',response.get('error')=='bootstrap_expired')
        short,_,credential,reply=enroll('short',WORKER,'worker',['observe'],credential_ttl=1)
        time.sleep(1.1)
        path=publish('expired-credential',request_for(short))
        response=client(WORKER,'--credential',credential,'--request',path)
        check('expired_operational_credential_refused',response.get('error')=='credential_expired')
        # Durable fencing and bootstrap tombstones survive a real service restart.
        stop();start()
        path=publish('replay-after-restart',request_for(worker))
        response=client(WORKER,'--credential',worker_credential,'--request',path)
        check('replay_denied_after_restart',response.get('error')=='sequence_replay_or_gap')
        response=client(WORKER,'--bootstrap',worker_token)
        check('bootstrap_reuse_denied_after_restart',response.get('error')=='bootstrap_reused')
        path=publish('new-sequence-after-restart',request_for(worker,2,operation='queue'))
        response=client(WORKER,'--credential',worker_credential,'--request',path)
        check('valid_next_sequence_after_denials_and_restart',response.get('authorized') is True and response.get('dispatch')=='not_performed')
        result={'passed':True,'checks':checks,'command':[sys.executable,*sys.argv]}
    except Exception:
        result={'passed':False,'checks':checks,'error':traceback.format_exc()}
    finally:
        stop()
        assert base.parent==Path('/tmp') and base.name.startswith('cf5-')
        sudo('rm','-rf','--',base)
        (e/'commands.json').write_text(json.dumps(commands,indent=2)+'\n')
    result['cleanup']={'fixture_root_removed':not base.exists(),'service_supervisor_ended':server is None}
    result['binarySha256']=hashlib.sha256(args.binary.read_bytes()).hexdigest()
    result['testSources']={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in [Path(__file__),helper]}
    result['exit_code']=0 if result['passed'] else 1
    (e/'result.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result,indent=2));return result['exit_code']

if __name__=='__main__':raise SystemExit(main())
