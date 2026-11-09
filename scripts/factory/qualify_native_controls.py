"""Existing local-child and recipient approval paths under native peer delivery."""
import argparse,json,time,traceback,uuid
from fleet_fixture import Fleet,result
from native_fixture import Native
from session_fixture import Model

def main():
 p=argparse.ArgumentParser();p.add_argument('--binary',required=True);p.add_argument('--cli',required=True);p.add_argument('--evidence',required=True);a=p.parse_args()
 f=Fleet(a.binary,a.evidence);model=Model();native=None;checks=[];error=None
 try:
  f.enroll('sender');f.enroll('receiver',['observe','queue','acknowledge']);native=Native(f,a.cli,model)
  project='fixture-project';thread=native.request('thread/start',{'cwd':str(native.home/'project'),'model':'mock-model'})['thread']['id']
  def deliver(text):
   e=f.envelope(mode='wake_when_idle',text=text);f.send(e)
   return native.request('thread/factoryMessage',{'threadId':thread,'project':project,'messageId':e['messageId']})['data']
  child_text='Assignment: CF-009 — Local child fixture\nDo: Report that the fixture is ready.\nDone when: The report is complete.\nLimits: Use no tools.'
  model.outputs[0]={'type':'function_call','call_id':'local_child_fixture','namespace':'multi_agent_v1','name':'spawn_agent','arguments':json.dumps({'message':child_text,'fork_context':False})}
  started=deliver('Assignment: CF-009 — Local child path\nDo: Test the local child path.\nDone when: The local child reports.\nLimits: Use the local fixture only.')
  assert started['state']=='started';model.wait_request(0);model.release(0)
  deadline=time.monotonic()+35;last_change=time.monotonic();last_len=0
  while True:
   for index in range(1,len(model.gates)):model.release(index)
   native.poll(.05)
   if len(model.requests)!=last_len:last_len=len(model.requests);last_change=time.monotonic()
   done=any(x.get('method')=='turn/completed' and x.get('params',{}).get('turn',{}).get('id')==started['turnId'] for x in native.events)
   if done and len(model.requests)>=3 and time.monotonic()-last_change>.5:break
   if time.monotonic()>deadline:raise RuntimeError('local child fixture did not finish')
  loaded=native.request('thread/loaded/list',{})['data'];assert len(loaded)>=2,loaded
  child=next(id for id in loaded if id!=thread)
  history=native.request('thread/read',{'threadId':child,'includeTurns':True});encoded=json.dumps(history)
  assert thread in encoded and ('subAgent' in encoded or 'subagent' in encoded),encoded[:2000]
  assert len(f.inspect()['seats'])==2
  checks.append('native_local_child_retains_parent_lineage_without_registering_coworker_as_child')
  marker=f.base/'receiver/approval-marker';index=len(model.requests)
  model.outputs[index]={'type':'function_call','call_id':'approval_boundary_fixture','name':'exec_command',
      'arguments':json.dumps({'cmd':'touch '+str(marker),'sandbox_permissions':'require_escalated','justification':'Test the recipient approval boundary.','yield_time_ms':1000})}
  started=deliver('Assignment: CF-009 — Approval boundary\nDo: Test the recipient approval boundary.\nDone when: The recipient refuses the command.\nLimits: Do not create the marker.')
  assert started['state']=='started',started
  model.wait_request(index);model.release(index)
  approval=native.event('item/commandExecution/requestApproval')
  assert approval['params']['itemId']=='approval_boundary_fixture',approval
  assert f.run(['sudo','-n','test','-e',marker],expected=None).returncode==1
  native.write({'jsonrpc':'2.0','id':approval['id'],'result':{'decision':'decline'}})
  model.wait_request(index+1);model.release(index+1);native.event('turn/completed',started['turnId'])
  assert f.run(['sudo','-n','test','-e',marker],expected=None).returncode==1
  checks.append('native_peer_triggered_tool_still_requests_recipient_approval_decline_prevents_effect')
  (f.e/'local-child-history.json').write_text(json.dumps(history,indent=2)+'\n')
  (f.e/'provider-requests.json').write_text(json.dumps(model.requests,indent=2)+'\n')
 except Exception:error=traceback.format_exc()
 finally:
  model.close()
  if native:native.close()
  f.close()
 return result(a.evidence,checks,error)
if __name__=='__main__':raise SystemExit(main())
