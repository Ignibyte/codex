"""Private UID-separated real-service fixture shared by CF-006 onward."""
import hashlib
import json
import os
from pathlib import Path
import select
import subprocess
import sys
import tempfile
import time
import uuid

ROOT=Path(__file__).resolve().parents[2]
SERVICE=65534
GROUP=65534

class Fleet:
    def __init__(self,binary,evidence):
        self.e=Path(evidence);self.e.mkdir(mode=0o700,parents=True,exist_ok=False)
        self.base=Path(tempfile.mkdtemp(prefix='cf-fleet-',dir='/tmp'))
        self.binary=self.base/'bin/bridge';self.state=self.base/'state';self.socket=self.base/'bridge.sock'
        self.source=Path(binary).resolve();self.helper=ROOT/'scripts/factory/isolation_client.py'
        self.commands=[];self.server=None;self.record=None;self.counter=0;self.host=str(uuid.uuid4());self.children=[]
        self.subjects={name:{'uid':uid,'seat':str(uuid.uuid4()),'sequence':0} for name,uid in [('sender',65533),('receiver',65532)]}
        self.sudo('chown',f'{SERVICE}:{GROUP}',self.base);self.sudo('chmod','0711',self.base)
        for name in ['bin','public']:
            self.sudo('install','-d','-o',SERVICE,'-g',GROUP,'-m','0711',self.base/name)
        for name,s in self.subjects.items():
            self.sudo('install','-d','-o',s['uid'],'-g',GROUP,'-m','0700',self.base/name)
        self.sudo('install','-o',SERVICE,'-g',GROUP,'-m','0555',self.source,self.binary)

    def run(self,cmd,expected=0,timeout=20):
        r=subprocess.run(list(map(str,cmd)),capture_output=True,text=True,timeout=timeout)
        self.commands.append({'command':list(map(str,cmd)),'exit_code':r.returncode,'stdout':r.stdout,'stderr':r.stderr})
        if expected is not None and r.returncode!=expected:raise RuntimeError(str(self.commands[-1]))
        return r

    def sudo(self,*cmd):return self.run(['sudo','-n',*cmd])
    def prefix(self,uid):return ['sudo','-n','setpriv','--reuid',str(uid),'--regid',str(GROUP),'--clear-groups','--no-new-privs']
    def admin(self,*cmd,expected=0):return self.run([*self.prefix(SERVICE),self.binary,*cmd],expected)
    def publish(self,value):
        self.counter+=1;src=self.e/f'input-{self.counter}.json';src.write_text(json.dumps(value))
        dst=self.base/'public'/src.name
        self.sudo('install','-o',SERVICE,'-g',GROUP,'-m','0644',src,dst);return dst

    def enroll(self,name,operations=None,role="worker",projects=None,recipients=None):
        s=self.subjects[name]
        grant={'seat':s['seat'],'host':self.host,'incarnation':str(uuid.uuid4()),'agentUid':s['uid'],
               'role':role,'projects':projects or ['fixture-project'],
               'recipients':recipients or [x['seat'] for x in self.subjects.values()],'operations':operations or ['observe','queue']}
        s['grant']=grant
        token=self.base/f'bootstrap-{name}'
        self.admin('bootstrap','--state',self.state,'--grant',self.publish(grant),'--output',token,'--credential-ttl','3600')
        dest=self.base/name/'bootstrap';self.sudo('mv','--',token,dest);self.sudo('chown',f"{s['uid']}:{GROUP}",dest)
        s['credential']=self.base/name/'credential'
        if self.server is None:self.start()
        r=self.run([*self.prefix(s['uid']),sys.executable,self.helper,'--socket',self.socket,'--bootstrap',dest,'--credential-out',s['credential']])
        assert json.loads(r.stdout)['ok']

    def start(self):
        cmd=[*self.prefix(SERVICE),self.binary,'serve','--state',self.state,'--socket',self.socket]
        err=(self.e/f'service-{self.counter}.log').open('w')
        self.server=subprocess.Popen(list(map(str,cmd)),stdout=subprocess.PIPE,stderr=err,text=True);err.close()
        assert select.select([self.server.stdout],[],[],15)[0]
        ready=json.loads(self.server.stdout.readline());pid=ready['pid']
        fields=Path(f'/proc/{pid}/stat').read_text().rsplit(')',1)[1].split()
        self.record={'pid':pid,'startTicks':fields[19],'bootId':Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
                     'exe':str(self.binary),'uid':SERVICE}
        (self.e/'stop-record.json').write_text(json.dumps(self.record))
        self.commands.append({'command':list(map(str,cmd)),'ready':ready,'incarnation':self.record})

    def stop(self):
        if self.server is None:return
        if self.server.poll() is None:self.sudo(sys.executable,self.helper,'--stop-record',self.e/'stop-record.json')
        code=self.server.wait(timeout=15);self.server.stdout.close();self.server=None
        self.commands.append({'supervisor_exit':code,'incarnation':self.record})
        if self.socket.exists():
            info=self.socket.lstat();assert info.st_uid==SERVICE
            self.sudo('rm','--',self.socket)
            self.commands.append({'removed_socket_inode':info.st_ino})

    def request(self,name,op='observe',recipient=None,sequence=None):
        s=self.subjects[name];g=s['grant']
        return {'protocol':1,'sender':g['seat'],'host':g['host'],'incarnation':g['incarnation'],
                'sequence':sequence if sequence is not None else s['sequence']+1,'project':'fixture-project',
                'recipient':recipient or g['seat'],'operation':op}

    def wire(self,name,value,credential=None,expected=None,held=False):
        s=self.subjects[name];path=self.publish(value)
        cmd=[*self.prefix(s['uid']),sys.executable,self.helper,'--socket',self.socket,
             '--credential',credential or s['credential'],'--wire',path]
        if held:
            ready=self.base/name/'ready';release=self.base/name/'release'
            cmd.extend(['--hold-ready',ready,'--hold-release',release])
            proc=subprocess.Popen(list(map(str,cmd)),stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
            self.children.append(proc)
            assert select.select([proc.stdout],[],[],2)[0]
            assert json.loads(proc.stdout.readline())['fixtureConnected']
            return proc,release,cmd
        out=self.run(cmd);response=json.loads(out.stdout)
        if expected is not None:assert response.get('error')==expected,response
        elif not response.get('ok'):raise RuntimeError(str(response))
        if response.get('ok'):s['sequence']=max(s['sequence'],value['request']['sequence'])
        return response

    def envelope(self,name='sender',recipient='receiver',**overrides):
        g=self.subjects[name]['grant'];t=int(time.time())
        return {'protocol':1,'messageId':str(uuid.uuid4()),'conversationId':str(uuid.uuid4()),
                'assignmentId':None,'replyTo':None,'sender':g['seat'],'incarnation':g['incarnation'],
                'recipient':self.subjects[recipient]['seat'],'project':'fixture-project','kind':'note',
                'mode':'queue_only','createdAt':t,'expiresAt':t+3600,'text':'Read the fixture record.','artifactRefs':[],**overrides}

    def send(self,envelope,name='sender',**kwargs):
        return self.wire(name,{'method':'send','protocol':1,'request':self.request(name,'queue',envelope['recipient']),
                               'envelope':envelope},**kwargs)
    def receive(self,id,name='receiver',**kwargs):
        return self.wire(name,{'method':'receive','protocol':1,'request':self.request(name), 'message_id':id},**kwargs)
    def rotate(self,name,overlap=0,ttl=3600):
        s=self.subjects[name];self.counter+=1;out=self.base/f'rotated-{self.counter}'
        self.admin('rotate','--state',self.state,'--seat',s['seat'],'--output',out,
                   '--overlap-seconds',overlap,'--credential-ttl',ttl)
        dest=self.base/name/f'credential-{self.counter}'
        self.sudo('mv','--',out,dest);self.sudo('chown',f"{s['uid']}:{GROUP}",dest)
        old=s['credential'];s['credential']=dest;return old
    def inspect(self):return json.loads(self.admin('inspect','--state',self.state).stdout)
    def close(self):
        self.stop()
        for proc in self.children:
            if proc.poll() is None:proc.communicate(timeout=5)
        if self.base.parent!=Path('/tmp') or not self.base.name.startswith('cf-fleet-'):raise RuntimeError('unexpected fixture root')
        self.sudo('rm','-rf','--',self.base)
        (self.e/'commands.json').write_text(json.dumps(self.commands,indent=2)+'\n')

def result(evidence,checks,error=None):
    path=Path(evidence)
    record={'passed':error is None,'checks':checks,'error':error,'exit_code':0 if error is None else 1,
            'testSources':{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in (ROOT/'scripts/factory').glob('*.py')}}
    (path/'result.json').write_text(json.dumps(record,indent=2)+'\n');print(json.dumps(record,indent=2))
    return record['exit_code']
