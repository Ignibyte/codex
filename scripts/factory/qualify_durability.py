"""Actual process-death and durable mailbox qualification, no model calls."""
import argparse,json,subprocess,sys,time,traceback,uuid
from pathlib import Path
from fleet_fixture import Fleet,result,SERVICE

def main():
 p=argparse.ArgumentParser();p.add_argument('--binary',required=True);p.add_argument('--evidence',required=True);a=p.parse_args()
 f=Fleet(a.binary,a.evidence);checks=[];error=None
 try:
  f.enroll('sender');f.enroll('receiver',['observe','queue','acknowledge'])
  sender=f.subjects['sender'];outbox=f.base/'sender/outbox';grant=f.publish(sender['grant'])
  def local(*args,expected=0):return f.run([*f.prefix(sender['uid']),f.binary,*args],expected)
  def enqueue(e):return json.loads(local('enqueue','--state',outbox,'--grant',grant,'--envelope',f.publish(e)).stdout)
  def relay(e):return json.loads(local('relay','--state',outbox,'--grant',grant,'--socket',f.socket,'--service-uid',SERVICE,'--credential',sender['credential'],'--message-id',e['messageId']).stdout)
  first=f.envelope();ack=enqueue(first);assert ack['receipt']['state']=='local_accepted'
  snapshot=json.loads(local('outbox','--state',outbox).stdout)
  assert snapshot['entries'][first['messageId']]['receipts'][0]['state']=='local_accepted'
  checks.append('local_ack_survives_sender_process_restart_before_network')
  r=relay(first);assert r['state']=='service_accepted'
  f.stop();f.start();r=relay(first);assert len(f.inspect()['mail'])==1
  checks.append('service_commit_survives_restart_exact_retry_one_record')
  # A new sequence can reconcile the original payload, but never change it.
  sender['sequence']=next(s['lastSequence'] for s in f.inspect()['seats'] if s['seat']==sender['seat'])
  changed={**first,'text':'Different payload.'};f.send(changed,expected='message_conflict')
  f.send(first);assert len(f.inspect()['mail'])==1
  f.receive(first['messageId']);f.receive(first['messageId'])
  checks.append('conflicting_key_rejected_duplicate_receive_one_delivery_receipt')
  turn=str(uuid.uuid4())
  for state,writer in [('consumed','runtime'),('completed','worker')]:
   frame={'method':'ack','protocol':1,'request':f.request('receiver','acknowledge'),
          'receipt':{'protocol':1,'messageId':first['messageId'],'state':state,'writer':writer,'turnId':turn}}
   receipt=f.wire('receiver',frame)
  assert [r['state'] for r in receipt['receipts']]==['service_accepted','delivered','consumed','completed']
  assert len(relay(first)['receipts'])==5
  checks.append('five_distinct_receipt_authorities_with_correlated_fixture_turn')
  sender['sequence']=next(s['lastSequence'] for s in f.inspect()['seats'] if s['seat']==sender['seat'])
  # Explicit capability check: a sender cannot claim consumption with Observe.
  f.wire('sender',{'method':'ack','protocol':1,'request':f.request('sender'),
        'receipt':{'protocol':1,'messageId':first['messageId'],'state':'consumed','writer':'runtime','turnId':turn}},expected='ack_scope_denied')
  for point,committed in [('before_rename',False),('before_ack',True)]:
   e=f.envelope();enqueue(e)
   sender['sequence']=next(s['lastSequence'] for s in f.inspect()['seats'] if s['seat']==sender['seat'])
   arm=f.state/f'fault.{point}';hit=f.state/f'hit.{point}'
   f.run([*f.prefix(SERVICE),'touch',arm]);f.run([*f.prefix(SERVICE),'chmod','0600',arm])
   value={'method':'send','protocol':1,'request':f.request('sender','queue',e['recipient']),'envelope':e}
   path=f.publish(value);cmd=[*f.prefix(sender['uid']),sys.executable,f.helper,'--socket',f.socket,'--credential',sender['credential'],'--wire',path]
   proc=subprocess.Popen(list(map(str,cmd)),stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True);f.children.append(proc)
   deadline=time.monotonic()+5
   while f.run(['sudo','-n','test','-f',hit],expected=None).returncode:
    if time.monotonic()>deadline:raise RuntimeError('fault point not reached')
    time.sleep(.02)
   f.stop();stdout,stderr=proc.communicate(timeout=5)
   f.commands.append({'command':list(map(str,cmd)),'exit_code':proc.returncode,'stdout':stdout,'stderr':stderr})
   assert proc.returncode!=0 and not stdout.strip(),(stdout,stderr)
   f.sudo('rm','--',arm,hit);f.start()
   ids=[m['messageId'] for m in f.inspect()['mail']];assert (e['messageId'] in ids)==committed
   r=relay(e);assert r['state']=='service_accepted'
   assert sum(m['messageId']==e['messageId'] for m in f.inspect()['mail'])==1
   f.receive(e['messageId'])
   checks.append({'crashPoint':point,'committedBeforeCrash':committed,'noAckBeforeCrash':True,'reconciledOnce':True})
  f.stop();f.start()
  assert len(f.inspect()['mail'])==3
  checks.append('all_committed_records_and_receipts_survive_final_restart')
 except Exception:error=traceback.format_exc()
 finally:f.close()
 return result(a.evidence,checks,error)
if __name__=='__main__':raise SystemExit(main())
