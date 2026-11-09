"""Normal build ignores fault markers and old native code refuses new ledger state."""
import argparse,json,time,traceback
from pathlib import Path
from fleet_fixture import Fleet,result,SERVICE,GROUP
from native_fixture import Native
from session_fixture import Model

def main():
 p=argparse.ArgumentParser();p.add_argument('--binary',required=True);p.add_argument('--cli',required=True);p.add_argument('--old-cli',required=True);p.add_argument('--evidence',required=True);a=p.parse_args()
 f=Fleet(a.binary,a.evidence);model=Model();native=None;checks=[];error=None
 try:
  f.enroll('sender');f.enroll('receiver',['observe','queue','acknowledge']);native=Native(f,a.cli,model)
  project='fixture-project';thread=native.request('thread/start',{'cwd':str(native.home/'project'),'model':'mock-model'})['thread']['id']
  native.request('factory/peers',{'project':project});state=f.base/'receiver/native-ledger'
  for point in ['before_rename','after_intent','after_submit']:
   f.run([*f.prefix(native.actor['uid']),'touch',state/f'fault.{point}']);f.run([*f.prefix(native.actor['uid']),'chmod','0600',state/f'fault.{point}'])
  e=f.envelope(mode='wake_when_idle',text='Assignment: CF-010 — Default build\nDo: Read the fixture record.\nDone when: The record is complete.\nLimits: Use no tools.');f.send(e)
  params={'threadId':thread,'project':project,'messageId':e['messageId']}
  started=native.request('thread/factoryMessage',params)['data'];assert started['state']=='started'
  model.wait_request(0);model.release(0);native.event('turn/completed',started['turnId'])
  done=native.request('thread/factoryMessage',params)['data'];assert done['state']=='completed',done
  for point in ['before_rename','after_intent','after_submit']:
   assert f.run(['sudo','-n','test','-e',state/f'hit.{point}'],expected=None).returncode==1
   f.sudo('rm','--',state/f'fault.{point}')
  checks.append('default_cli_ignores_private_fault_markers_and_proves_native_completion')
  count=len(model.requests);native.stop();old=f.base/'bin/codex-old'
  f.sudo('install','-o',SERVICE,'-g',GROUP,'-m','0555',Path(a.old_cli).resolve(),old)
  spec=json.loads(native.spec.read_text());spec['binary']=str(old);native.spec=f.publish(spec);native.binary=old
  native.start();native.request('thread/resume',{'threadId':thread})
  native.request('thread/factoryMessage',params,expected_error='factory_storage_unsupported')
  time.sleep(.2);assert len(model.requests)==count
  checks.append('cf009_binary_refuses_cf010_native_ledger_without_reinjecting')
 except Exception:error=traceback.format_exc()
 finally:
  model.close()
  if native:native.close()
  f.close()
 return result(a.evidence,checks,error)
if __name__=='__main__':raise SystemExit(main())
