"""Attributed native coworker messages through actual Codex Core and App Server."""
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
  def deliver(e):return native.request('thread/factoryMessage',{'threadId':thread,'project':project,'messageId':e['messageId']})['data']
  peers=native.request('factory/peers',{'project':project})['data']['peers'];assert {p['seat'] for p in peers}=={s['seat'] for s in f.subjects.values()}
  queued=f.envelope(text='Status: CF-009\nState: idle\nDone: The fixture record is ready.');f.send(queued)
  assert deliver(queued)['state']=='queue_only';time.sleep(.3);assert not model.requests
  checks.append('authenticated_discovery_queue_only_never_wakes_with_native_wake_feature_enabled')
  first=f.envelope(mode='wake_when_idle',text='Assignment: CF-009 — Native delivery\nDo: Read the fixture record.\nDone when: The conversation contains this message.\nLimits: Use no tools.');f.send(first)
  started=deliver(first);assert started['state']=='started';model.wait_request(0)
  body=model.requests[0]['body'];serialized=json.dumps(body)
  assert first['text'] in serialized or 'Native delivery' in serialized
  assert '/coworker/'+f.subjects['sender']['seat'].replace('-','') in serialized
  authored=[item for item in body['input'] if item.get('type')=='agent_message' and item.get('id')=='amsg_'+first['messageId']]
  assert len(authored)==1 and authored[0]['author']=='/coworker/'+f.subjects['sender']['seat'].replace('-','')+'/'+f.subjects['sender']['grant']['incarnation'].replace('-',''),authored
  assert not any(item.get('role') in ['user','developer','system'] and first['text'] in json.dumps(item) for item in body['input'])
  checks.append('native_automatic_turn_retains_coworker_author_and_immutable_message_id')
  second=f.envelope(mode='wake_when_idle',text='Status: CF-009\nState: idle\nDone: The second record is ready.');f.send(second)
  deferred=deliver(second);assert deferred['state']=='deferred',deferred
  time.sleep(.2);assert len(model.requests)==1
  model.release(0);native.event('turn/completed',started['turnId'])
  second_start=deliver(second);assert second_start['state']=='started';model.wait_request(1)
  model.release(1);native.event('turn/completed',second_start['turnId'])
  checks.append('busy_recipient_defers_without_steering_then_idle_retry_starts_once')
  reply={'messageId':str(uuid.uuid4()),'conversationId':queued['conversationId'],'recipient':f.subjects['sender']['seat'],
    'project':project,'kind':'reply','mode':'queue_only','text':'Received: '+queued['messageId']+'\nState: received\nNext: Read the fixture record.',
    'replyTo':queued['messageId'],'expiresInSeconds':3600}
  assert native.request('factory/send',reply)['data']['state']=='service_accepted'
  received=f.receive(reply['messageId'],name='sender')['envelope'];assert received['sender']==f.subjects['receiver']['seat'] and received['replyTo']==queued['messageId']
  assert native.request('factory/send',reply)['data']['state']=='delivered'
  checks.append('native_reply_is_authenticated_correlated_and_idempotent')
  unknown={**reply,'messageId':str(uuid.uuid4()),'recipient':str(uuid.uuid4()),'replyTo':None}
  native.request('factory/send',unknown,expected_error='recipient_denied')
  native.request('factory/send',{**reply,'sender':f.subjects['sender']['seat']},expected_error='unknown field')
  native.request('thread/factoryMessage',{'threadId':thread,'project':project,'messageId':str(uuid.uuid4())},expected_error='message_unknown')
  native.request('thread/factoryMessage',{'threadId':thread,'project':project,'messageId':first['messageId'],'approvalPolicy':'never'},expected_error='unknown field')
  checks.append('unknown_seat_sender_impersonation_and_approval_override_refused')
  history=native.request('thread/read',{'threadId':thread,'includeTurns':True})
  (f.e/'thread-history.json').write_text(json.dumps(history,indent=2)+'\n')
  (f.e/'provider-requests.json').write_text(json.dumps(model.requests,indent=2)+'\n')
 except Exception:error=traceback.format_exc()
 finally:
  model.close()
  if native:native.close()
  f.close()
 return result(a.evidence,checks,error)
if __name__=='__main__':raise SystemExit(main())
