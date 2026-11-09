"""Partition, retry, expiry, FIFO, queue pressure and real ENOSPC journeys."""
import argparse,json,select,subprocess,sys,time,traceback,uuid
from pathlib import Path
from fleet_fixture import Fleet,result,SERVICE

def main():
 p=argparse.ArgumentParser();p.add_argument('--binary',required=True);p.add_argument('--evidence',required=True);a=p.parse_args()
 f=Fleet(a.binary,a.evidence);checks=[];error=None;namespace=None;oldprefix=f.prefix
 try:
  f.enroll('sender');f.enroll('receiver');sender=f.subjects['sender'];outbox=f.base/'sender/outbox';grant=f.publish(sender['grant'])
  def local(*args,expected=0):return f.run([*f.prefix(sender['uid']),f.binary,*args],expected)
  def enqueue(e):return json.loads(local('enqueue','--state',outbox,'--grant',grant,'--envelope',f.publish(e)).stdout)
  def relay(e,command='relay',expected=0):return local(command,'--state',outbox,'--grant',grant,'--socket',f.socket,'--service-uid',SERVICE,'--credential',sender['credential'],'--message-id',e['messageId'],expected=expected)
  first=f.envelope();enqueue(first);f.stop()
  r=relay(first,'retry',1);assert 'outbox_retry_exhausted' in r.stderr
  stored=json.loads(local('outbox','--state',outbox).stdout)['entries'][first['messageId']]
  assert stored['deadLetter']=='retry_exhausted' and stored['attempts']==3 and stored['lastError']=='transport_unavailable'
  checks.append('partition_exhausts_three_persisted_attempts_no_silent_loss')
  f.start();local('replay','--state',outbox,'--message-id',first['messageId']);relay(first)
  f.stop();f.start();relay(first);assert len(f.inspect()['mail'])==1
  checks.append('explicit_same_key_replay_and_restart_reconcile_once')
  # Different streams can proceed; one stream cannot overtake local acceptance order.
  before=f.envelope();after=f.envelope(conversationId=before['conversationId']);enqueue(before);enqueue(after)
  r=relay(after,expected=1);assert 'outbox_order_blocked' in r.stderr
  relay(before);relay(after);checks.append('per_sender_recipient_conversation_fifo')
  expired=f.envelope(expiresAt=int(time.time())+3);enqueue(expired);time.sleep(3.1)
  r=relay(expired,expected=1);assert 'outbox_expired' in r.stderr
  r=local('replay','--state',outbox,'--message-id',expired['messageId'],expected=1);assert 'replay_expired' in r.stderr
  f.stop();f.start();r=relay(expired,expected=1);assert 'outbox_dead_letter' in r.stderr
  checks.append('expired_local_work_retained_terminal_across_restart_and_replay')
  sender['sequence']=next(s['lastSequence'] for s in f.inspect()['seats'] if s['seat']==sender['seat'])
  future=f.envelope(createdAt=int(time.time())+120,expiresAt=int(time.time())+240);f.send(future,expected='message_expired_or_invalid_time')
  value={'method':'send','protocol':1,'request':f.request('sender','queue',first['recipient'],sequence=sender['sequence']+2),'envelope':f.envelope()}
  f.wire('sender',value,expected='sequence_replay_or_gap')
  value['request']=f.request('sender','queue',first['recipient']);value['protocol']=2
  f.wire('sender',value,expected='protocol_unsupported')
  checks.append('future_clock_skew_sequence_gap_and_version_rejected_without_consuming_sequence')
  service_expired=f.envelope(expiresAt=int(time.time())+3);f.send(service_expired);time.sleep(3.1)
  f.receive(service_expired['messageId'],expected='message_expired')
  response=f.send(service_expired);assert response['state']=='expired'
  f.stop();f.start();f.receive(service_expired['messageId'],expected='message_quarantined')
  checks.append('expired_service_tombstone_not_resurrected_by_identical_retry')
  current=len(f.inspect()['mail'])
  for _ in range(128-current):f.send(f.envelope())
  f.send(f.envelope(),expected='queue_full');assert len(f.inspect()['mail'])==128
  checks.append('queue_count_limit_rejects_without_false_ack_or_sequence_commit')
  revoked=json.loads(f.admin('revoke','--state',f.state,'--seat',f.subjects['receiver']['seat']).stdout)
  assert revoked['pendingQuarantined']==127
  checks.append('control_revocation_available_at_queue_capacity')
  # A separate tiny filesystem in a private mount namespace supplies real ENOSPC.
  f.stop();f.sudo('mkdir',f.base/'tiny')
  cmd=['sudo','-n','unshare','--mount','--propagation','private',sys.executable,Path(__file__).with_name('private_tmpfs.py'),'--mountpoint',f.base/'tiny']
  namespace=subprocess.Popen(list(map(str,cmd)),stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
  assert select.select([namespace.stdout],[],[],10)[0];ready=json.loads(namespace.stdout.readline());pid=ready['pid']
  fields=Path(f'/proc/{pid}/stat').read_text().rsplit(')',1)[1].split();ready['startTicks']=fields[19];ready['bootId']=Path('/proc/sys/kernel/random/boot_id').read_text().strip()
  f.commands.append({'command':list(map(str,cmd)),'namespace':ready})
  f.prefix=lambda uid:['sudo','-n','nsenter',f'--mount=/proc/{pid}/ns/mnt','setpriv','--reuid',str(uid),'--regid','65534','--clear-groups','--no-new-privs']
  f.state=f.base/'tiny/state'
  # Fresh grants/credentials, same private socket and UIDs, independently recorded state.
  for name in ['sender','receiver']:
   f.sudo('rm','--',f.base/name/'bootstrap',f.base/name/'credential');f.subjects[name]['sequence']=0
  f.enroll('sender');f.enroll('receiver')
  filler=f.base/'tiny/fill'
  fillcode="import os,sys\nf=open(sys.argv[1],'wb',buffering=0)\ntry:\n while True:f.write(b'x'*4096)\nexcept OSError as e:\n print(e.errno)\nfinally:f.close()"
  r=f.run([*f.prefix(SERVICE),sys.executable,'-c',fillcode,filler]);assert r.stdout.strip()=='28',r.stdout
  before=f.inspect();e=f.envelope();f.send(e,expected='No space left on device (os error 28)')
  assert f.inspect()['mail']==before['mail']
  f.run([*f.prefix(SERVICE),'rm','--',filler]);f.send(e);f.receive(e['messageId'])
  checks.append('actual_enospc_before_ack_preserves_old_commit_retry_delivers_once')
 except Exception:error=traceback.format_exc()
 finally:
  f.stop()
  if namespace:
   namespace.stdin.write('\n');namespace.stdin.flush();out,err=namespace.communicate(timeout=10)
   f.commands.append({'namespace_supervisor_exit':namespace.returncode,'stdout':out,'stderr':err})
   if namespace.returncode and error is None:error='namespace cleanup failed: '+err
  f.prefix=oldprefix;f.close()
 return result(a.evidence,checks,error)
if __name__=='__main__':raise SystemExit(main())
