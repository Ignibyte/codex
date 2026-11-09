"""Real sender and recipient native endpoints, cancellation/complete race outcomes."""
import argparse,json,time,traceback,uuid
from fleet_fixture import Fleet,result
from native_fixture import Native
from session_fixture import Model

def main():
 p=argparse.ArgumentParser();p.add_argument('--binary',required=True);p.add_argument('--cli',required=True);p.add_argument('--evidence',required=True);a=p.parse_args()
 f=Fleet(a.binary,a.evidence);model=Model();receiver=None;sender=None;checks=[];error=None
 try:
  f.enroll('sender');f.enroll('receiver',['observe','queue','acknowledge'])
  receiver=Native(f,a.cli,model);sender=Native(f,a.cli,model,name='sender')
  project='fixture-project';thread=receiver.request('thread/start',{'cwd':str(receiver.home/'project'),'model':'mock-model'})['thread']['id']
  def send(mode='wake_when_idle'):
   value={'messageId':str(uuid.uuid4()),'conversationId':str(uuid.uuid4()),'recipient':f.subjects['receiver']['seat'],
      'project':project,'kind':'assignment','mode':mode,'text':'Assignment: CF-010 — Cancellation fixture\nDo: Read the fixture record.\nDone when: The record is complete.\nLimits: Use no tools.', 'replyTo':None,'expiresInSeconds':3600}
   sender.request('factory/send',value);return value
  def deliver(v):return receiver.request('thread/factoryMessage',{'threadId':thread,'project':project,'messageId':v['messageId']})['data']
  def cancel(v):return sender.request('factory/cancel',{'project':project,'messageId':v['messageId']})['data']
  def status(v):return sender.request('factory/status',{'project':project,'messageId':v['messageId']})['data']
  first=send();started=deliver(first);model.wait_request(0)
  assert cancel(first)['cancellation']['state']=='requested'
  # The original sender can request cancellation, never attest its application.
  f.subjects['sender']['sequence']=next(s['lastSequence'] for s in f.inspect()['seats'] if s['seat']==f.subjects['sender']['seat'])
  f.wire('sender',{'method':'cancel_result','protocol':1,'request':f.request('sender','queue',first['recipient']),
       'message_id':first['messageId'],'state':'applied','turn_id':started['turnId'],'reason':'forged_result'},expected='cancel_result_scope_denied')
  f.subjects['receiver']['sequence']=next(s['lastSequence'] for s in f.inspect()['seats'] if s['seat']==f.subjects['receiver']['seat'])
  f.wire('receiver',{'method':'cancel','protocol':1,'request':f.request('receiver','queue'), 'message_id':first['messageId']},expected='cancel_scope_denied')
  observed=deliver(first);assert observed.get('cancellation',{}).get('state') in ['requested','applied'],observed
  receiver.event('turn/completed',started['turnId']);observed=deliver(first)
  assert observed['state']=='interrupted' and observed['cancellation']['state']=='applied',observed
  assert status(first)['cancellation']['state']=='applied'
  model.release(0);checks.append('request_is_not_application_named_turn_abort_proves_applied_and_forged_results_fail')
  second=send();started=deliver(second);model.wait_request(1);model.release(1);receiver.event('turn/completed',started['turnId'])
  complete=deliver(second);assert complete['state']=='completed',complete
  assert [r['state'] for r in status(second)['receipts']]==['service_accepted','delivered','consumed','completed']
  unrelated=receiver.request('turn/start',{'threadId':thread,'input':[{'type':'text','text':'Read the other fixture record.'}]})['turn']['id'];model.wait_request(2)
  assert cancel(second)['cancellation']['state']=='requested'
  late=deliver(second);assert late['cancellation']['state']=='too_late',late
  view=receiver.request('thread/read',{'threadId':thread,'includeTurns':True})['thread']
  assert view['status']['type']=='active' and view['turns'][-1]['id']==unrelated,view
  model.release(2);receiver.event('turn/completed',unrelated)
  checks.append('durable_completion_wins_late_cancel_without_interrupting_later_unrelated_work')
  queued=send('queue_only');count=len(model.requests);assert cancel(queued)['cancellation']['state']=='requested'
  no_start=deliver(queued);assert no_start['state']=='cancelled_before_start' and no_start['cancellation']['state']=='applied'
  assert deliver(queued)['cancellation']['state']=='applied' and len(model.requests)==count
  checks.append('cancel_before_admission_is_applied_without_starting_any_turn')
  racing=send();index=len(model.requests);started=deliver(racing);model.wait_request(index)
  request=sender.begin('factory/cancel',{'project':project,'messageId':racing['messageId']})
  assert sender.finish(request)['data']['cancellation']['state']=='requested'
  acting=receiver.begin('thread/factoryMessage',{'threadId':thread,'project':project,'messageId':racing['messageId']});model.release(index)
  receiver.finish(acting);receiver.event('turn/completed',started['turnId'])
  observed=deliver(racing)
  assert observed['cancellation']['state'] in ['applied','too_late'],observed
  checks.append({'actual_cancel_completion_race':observed['cancellation']['state'],'nativeTerminal':observed['state']})
  (f.e/'provider-requests.json').write_text(json.dumps(model.requests,indent=2)+'\n')
  (f.e/'cancel-state.json').write_text(json.dumps({'active':status(first),'late':status(second),'beforeStart':status(queued),'race':status(racing)},indent=2)+'\n')
 except Exception:error=traceback.format_exc()
 finally:
  model.close()
  if receiver:receiver.close()
  if sender:sender.close()
  f.close()
 return result(a.evidence,checks,error)
if __name__=='__main__':raise SystemExit(main())
