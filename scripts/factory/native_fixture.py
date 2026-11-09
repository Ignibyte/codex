"""Native stdio App Server owned by one isolated factory seat."""
import json,os,select,subprocess,sys,time
from pathlib import Path
from fleet_fixture import GROUP,SERVICE

class Native:
 def __init__(self,f,cli,model,name='receiver',managed=False,supervised=False,sandbox='read-only',workflow=False,manager_job=None,canonical_workflow=False,multi_agent=True):
  assert sandbox in ['read-only','workspace-write']
  self.supervised=supervised;self.unit='cf-worker-'+f.subjects[name]['seat']+'.service'
  self.f=f;self.name=name;self.actor=f.subjects[name];self.events=[];self.transcript=[];self.serial=0;self.buffer=b'';self.replies={}
  self.home=f.base/name/'native-home';self.record=f.base/name/'native-record';self.binary=f.base/'bin/codex'
  if not self.binary.exists():f.sudo('install','-o',SERVICE,'-g',GROUP,'-m','0555',Path(cli).resolve(),self.binary)
  f.sudo('install','-d','-o',self.actor['uid'],'-g',GROUP,'-m','0700',self.home,self.home/'project')
  config=f'''model = "mock-model"
model_provider = "fixture"
approval_policy = "on-request"
sandbox_mode = "{sandbox}"
cli_auth_credentials_store = "file"
[sandbox_workspace_write]
exclude_slash_tmp = true
exclude_tmpdir_env_var = true
[features]
plugins = false
api_key_model_discovery = false
wake_on_agent_messages = true
multi_agent = {str(multi_agent).lower()}
[analytics]
enabled = false
[model_providers.fixture]
name = "Bounded native factory fixture"
base_url = "http://127.0.0.1:{model.server.server_port}/v1"
wire_api = "responses"
request_max_retries = 0
stream_max_retries = 0
supports_websockets = false
[projects."{self.home/'project'}"]
trust_level = "trusted"
'''
  if workflow or canonical_workflow:
   args=['workflow-mcp','--grant',str(f.publish(self.actor['grant'])),'--socket',str(f.socket),
         '--service-uid',str(SERVICE),'--credential',str(self.actor['credential']),'--project','fixture-project']
   if canonical_workflow:args.append('--canonical-specs')
   config+='\n[mcp_servers.factory_workflow]\ncommand = '+json.dumps(str(f.binary))+'\nargs = '+json.dumps(args)+'\nstartup_timeout_sec = 10\n'
  if manager_job:
   args=['manager-mcp','--grant',str(f.publish(self.actor['grant'])),'--socket',str(f.socket),
         '--service-uid',str(SERVICE),'--credential',str(self.actor['credential']),'--project','fixture-project',
         '--job-id',str(manager_job)]
   config+='\n[mcp_servers.factory_manager]\ncommand = '+json.dumps(str(f.binary))+'\nargs = '+json.dumps(args)+'\nstartup_timeout_sec = 15\n'
  configsrc=f.e/f'native-config-{name}.toml';configsrc.write_text(config)
  f.sudo('install','-o',self.actor['uid'],'-g',GROUP,'-m','0600',configsrc,self.home/'config.toml')
  binding={'serviceUid':SERVICE,'socket':str(f.socket),'credential':str(self.actor['credential']),
           'state':str(f.base/name/'native-ledger'),'grant':self.actor['grant']}
  if managed:binding['managedAssignments']=True
  self.binding=f.base/'public'/f'binding-{name}.json'
  f.sudo('install','-o',SERVICE,'-g',GROUP,'-m','0640',f.publish(binding),self.binding)
  env={'PATH':os.environ['PATH'],'HOME':str(self.home),'CODEX_HOME':str(self.home),'SHELL':'/bin/bash',
       'NO_PROXY':'127.0.0.1,localhost','TERM':'xterm-256color',
       'CODEX_FACTORY_BINDING':str(self.binding),'CODEX_FACTORY_SERVICE_UID':str(SERVICE)}
  self.spec=f.publish({'binary':str(self.binary),'cwd':str(self.home/'project'),'record':str(self.record),'env':env})
  try:self.start()
  except Exception:
   if hasattr(self,'identity'):self.stop(crash=True)
   elif self.supervised:
    f.run(['sudo','-n','systemctl','stop',self.unit],expected=None)
    if hasattr(self,'process'):
     self.process.wait(timeout=10)
     self.process.stdin.close();self.process.stdout.close();self.stderr.close()
   raise
 def start(self):
  self.f.counter+=1
  self.stopped=False;self.buffer=b'';self.replies={}
  cmd=[*self.f.prefix(self.actor['uid']),sys.executable,Path(__file__).with_name('native_owner.py'),'--spec',self.spec]
  if self.supervised:
   cmd=['sudo','-n','systemd-run','--quiet','--wait','--pipe','--collect','--service-type=exec',
        '--unit',self.unit,'--property=KillMode=control-group','--property=TimeoutStopSec=5',
        '--property=NoNewPrivileges=yes','--property=MemoryMax=768M','--property=TasksMax=128',
        '--property=CPUQuota=100%',*cmd[2:]]
  self.stderr=(self.f.e/f'native-{self.name}-{self.f.counter}.log').open('w')
  self.f.commands.append({'command':list(map(str,cmd)),'unit':self.unit if self.supervised else None})
  self.process=subprocess.Popen(list(map(str,cmd)),stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=self.stderr)
  deadline=time.monotonic()+10
  while self.f.run(['sudo','-n','test','-f',self.record],expected=None).returncode:
   if time.monotonic()>deadline:raise RuntimeError('native process not ready')
   time.sleep(.02)
  self.identity=json.loads(self.f.sudo('cat','--',self.record).stdout)
  self.stoprecord=self.f.e/f'native-stop-{self.name}-{self.f.counter}.json';self.stoprecord.write_text(json.dumps(self.identity))
  self.f.commands.append({'command':list(map(str,cmd)),'nativeIdentity':self.identity})
  self.request('initialize',{'clientInfo':{'name':'factory-qualification','version':'0'},'capabilities':{'experimentalApi':True}})
  self.notify('initialized',{})
 def notify(self,method,params):self.write({'jsonrpc':'2.0','method':method,'params':params})
 def write(self,value):
  self.transcript.append({'direction':'send','value':value});self.process.stdin.write(json.dumps(value).encode()+b'\n');self.process.stdin.flush()
 def poll(self,timeout=.1):
  if select.select([self.process.stdout],[],[],timeout)[0]:
   part=os.read(self.process.stdout.fileno(),65536)
   if not part:raise RuntimeError('native app-server exited')
   self.buffer+=part
   while b'\n' in self.buffer:
    raw,self.buffer=self.buffer.split(b'\n',1)
    if not raw.strip():continue
    value=json.loads(raw);self.transcript.append({'direction':'receive','value':value})
    if 'id' in value and 'method' not in value:self.replies[value['id']]=value
    else:self.events.append(value)
 def begin(self,method,params):
  self.serial+=1;id=self.serial;self.write({'jsonrpc':'2.0','id':id,'method':method,'params':params})
  return id
 def finish(self,id,expected_error=None):
  deadline=time.monotonic()+30
  while id not in self.replies:
   self.poll()
   if time.monotonic()>deadline:raise RuntimeError('native RPC timeout: '+str(id))
  value=self.replies.pop(id)
  if expected_error is not None:assert expected_error in json.dumps(value.get('error')),value;return value
  if 'error' in value:raise RuntimeError(str(value))
  return value['result']
 def request(self,method,params,expected_error=None):return self.finish(self.begin(method,params),expected_error)
 def event(self,method,turn=None):
  deadline=time.monotonic()+30
  while True:
   for value in self.events:
    if value.get('method')==method and (turn is None or value.get('params',{}).get('turn',{}).get('id')==turn):return value
   self.poll()
   if time.monotonic()>deadline:raise RuntimeError('native event timeout '+method)
 def stop(self,crash=False):
  if self.stopped:return
  if self.process.poll() is None:
   if crash:self.f.sudo(sys.executable,self.f.helper,'--stop-record',self.stoprecord,'--stop-signal','KILL')
   else:self.process.stdin.close()
   try:code=self.process.wait(timeout=15)
   except subprocess.TimeoutExpired:
    self.f.sudo(sys.executable,self.f.helper,'--stop-record',self.stoprecord,'--stop-signal','KILL');code=self.process.wait(timeout=10)
   self.f.commands.append({'nativeExit':code,'identity':self.identity,'crash':crash})
  self.process.stdout.close();self.stderr.close()
  if not self.process.stdin.closed:self.process.stdin.close()
  self.f.sudo('rm','--',self.record)
  self.stopped=True
 def close(self):
  self.stop();(self.f.e/f'native-rpc-{self.name}.json').write_text(json.dumps(self.transcript,indent=2)+'\n')
