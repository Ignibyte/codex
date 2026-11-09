"""Native process death at admission/receipt boundaries, never a mock Core."""
import argparse,json,sys,time,traceback
from pathlib import Path
from fleet_fixture import Fleet,result
from native_fixture import Native
from session_fixture import Model

def capture(f,native,thread,label):
 history=native.request('thread/read',{'threadId':thread,'includeTurns':True})
 path=history['thread'].get('path')
 if path:
  assert Path(path).is_relative_to(native.home)
  raw=f.run([*f.prefix(native.actor['uid']),'cat','--',path]).stdout
  (f.e/f'{label}-raw-history.jsonl').write_text(raw)
 (f.e/f'{label}-thread.json').write_text(json.dumps(history,indent=2)+'\n')

def lane(binary,cli,evidence,point):
 f=Fleet(binary,evidence);model=Model();native=None;checks=[];error=None
 try:
  f.enroll('sender');f.enroll('receiver',['observe','queue','acknowledge']);native=Native(f,cli,model)
  project='fixture-project';thread=native.request('thread/start',{'cwd':str(native.home/'project'),'model':'mock-model'})['thread']['id']
  warm=native.request('turn/start',{'threadId':thread,'input':[{'type':'text','text':'Read the isolated fixture record.'}]})['turn']['id']
  model.wait_request(0);model.release(0);native.event('turn/completed',warm)
  native.request('factory/peers',{'project':project})
  e=f.envelope(mode='wake_when_idle',text='Assignment: CF-010 — Recovery fixture\nDo: Read the fixture record.\nDone when: The conversation contains the record.\nLimits: Use no tools.')
  f.send(e);params={'threadId':thread,'project':project,'messageId':e['messageId']}
  state=f.base/'receiver/native-ledger';arm=state/f'fault.{point}';hit=state/f'hit.{point}'
  if point in ['before_consumed_ack','after_consumed_ack','after_interrupt_admission']:
   started=native.request('thread/factoryMessage',params)['data'];assert started['state']=='started'
   model.wait_request(1)
  if point=='after_interrupt_admission':
   f.wire('sender',{'method':'cancel','protocol':1,'request':f.request('sender','queue',e['recipient']),'message_id':e['messageId']})
  f.run([*f.prefix(native.actor['uid']),'touch',arm]);f.run([*f.prefix(native.actor['uid']),'chmod','0600',arm])
  pending=native.begin('thread/factoryMessage',params)
  deadline=time.monotonic()+8
  while f.run(['sudo','-n','test','-f',hit],expected=None).returncode:
   native.poll(.01)
   if pending in native.replies:raise RuntimeError('fault barrier bypassed: '+str(native.replies[pending]))
   if time.monotonic()>deadline:raise RuntimeError('native barrier not reached: '+point)
  if point=='after_submit':model.wait_request(1)
  if point=='after_interrupt_admission':
   native.event('turn/completed',started['turnId'])
   deadline=time.monotonic()+5
   while True:
    durable=native.request('thread/turns/list',{'threadId':thread,'limit':10})
    target=next(turn for turn in durable['data'] if turn['id']==started['turnId'])
    if target['status']=='interrupted':break
    if time.monotonic()>deadline:raise RuntimeError('abort not durable before crash')
    time.sleep(.02)
   (f.e/'durable-abort-before-crash.json').write_text(json.dumps(target,indent=2)+'\n')
  expected='consumed' if point in ['after_consumed_ack','after_interrupt_admission'] else 'delivered'
  assert next(m for m in f.inspect()['mail'] if m['messageId']==e['messageId'])['state']==expected
  native.stop(crash=True);f.sudo('rm','--',arm,hit)
  if len(model.gates)>1:model.release(1)
  count=len(model.requests);native.start();native.request('thread/resume',{'threadId':thread})
  observed=native.request('thread/factoryMessage',params)['data']
  capture(f,native,thread,point)
  if point=='after_interrupt_admission':
   assert observed['state']=='uncertain' and observed['cancellation']['state']=='uncertain' and observed['reconcileReason']=='interrupt_application_ambiguous',observed
   checks.append('lost_interrupt_admission_ack_is_uncertain_despite_durable_abort')
  elif point=='after_intent':
   assert observed['state']=='uncertain' and count==1,observed
   request=f.request('sender','queue',e['recipient'])
   cancellation=f.wire('sender',{'method':'cancel','protocol':1,'request':request,'message_id':e['messageId']})
   assert cancellation['cancellation']['state']=='requested'
   observed=native.request('thread/factoryMessage',params)['data']
   assert observed['state']=='uncertain' and observed['cancellation']['state']=='uncertain',observed
   checks.append('missing_history_is_uncertain_including_cancellation_never_success')
  else:
   assert observed['state']=='consumed' and observed['turnId'],observed
   again=native.request('thread/factoryMessage',params)['data'];assert again['turnId']==observed['turnId'] and again['state']=='consumed'
   mail=next(m for m in f.inspect()['mail'] if m['messageId']==e['messageId'])
   assert [r['state'] for r in mail['receipts']]==['service_accepted','delivered','consumed'],mail
   checks.append('durable_history_reconstructs_actual_turn_and_one_consumption_receipt')
  time.sleep(.2);assert len(model.requests)==count
  checks.append({'crashPoint':point,'providerRequestsBeforeRestart':count,'providerRequestsAfterRestart':len(model.requests),'noReinjection':True,'nativeState':observed['state']})
  (f.e/'observed-state.json').write_text(json.dumps(observed,indent=2)+'\n')
  (f.e/'provider-requests.json').write_text(json.dumps(model.requests,indent=2)+'\n')
 except Exception:error=traceback.format_exc()
 finally:
  model.close()
  if native:native.close()
  f.close()
 return result(evidence,checks,error)

def main():
 p=argparse.ArgumentParser();p.add_argument('--binary',required=True);p.add_argument('--cli',required=True);p.add_argument('--evidence',required=True);a=p.parse_args()
 root=Path(a.evidence);root.mkdir(mode=0o700,parents=True,exist_ok=False)
 results=[]
 for point in ['after_intent','after_submit','before_consumed_ack','after_consumed_ack','after_interrupt_admission']:
  code=lane(a.binary,a.cli,root/point,point);results.append({'point':point,'exit_code':code})
  if code:break
 (root/'result.json').write_text(json.dumps({'passed':all(x['exit_code']==0 for x in results) and len(results)==5,'lanes':results},indent=2)+'\n')
 return 0 if len(results)==5 and all(x['exit_code']==0 for x in results) else 1
if __name__=='__main__':raise SystemExit(main())
