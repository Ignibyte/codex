import argparse
import json
import time
import traceback
from pathlib import Path
from fleet_fixture import Fleet,result

def main():
    p=argparse.ArgumentParser();p.add_argument('--binary',required=True);p.add_argument('--evidence',required=True)
    a=p.parse_args();f=Fleet(a.binary,a.evidence);checks=[];error=None
    try:
        f.enroll('sender');f.enroll('receiver')
        first=f.envelope();f.send(first)
        overlap=5
        old=f.rotate('sender',overlap=overlap)
        rotation=next(row['event'] for row in reversed(f.inspect()['audit']) if row['event']['kind']=='rotated')
        deadline=rotation['at']+overlap
        r=f.receive(first['messageId']);assert r['envelope']==first
        checks.append('rotation_preserves_pending_delivery_and_attribution')
        before=time.time()
        assert before<deadline,'The fixture missed the recorded overlap window.'
        second=f.envelope();f.send(second,credential=old)
        checks.append({'bounded_old_key_overlap_delivers':True,'sentBefore':before,'validUntil':deadline})
        time.sleep(max(0,deadline+.05-time.time()))
        third=f.envelope();f.send(third,credential=old,expected='credential_denied')
        f.send(third);checks.append('expired_overlap_rejected_new_key_delivers')
        current=f.subjects['sender']['credential']
        held,release,cmd=f.wire('sender',{'method':'receive','protocol':1,
             'request':f.request('sender'),'message_id':first['messageId']},held=True)
        revoke=json.loads(f.admin('revoke','--state',f.state,'--seat',f.subjects['sender']['seat']).stdout)
        assert revoke['pendingQuarantined']==2 and revoke['deliveredRequiresReconciliation']==1,revoke
        start=time.monotonic();f.sudo('touch',release)
        stdout,stderr=held.communicate(timeout=5);duration=time.monotonic()-start
        f.commands.append({'command':list(map(str,cmd)),'exit_code':held.returncode,'stdout':stdout,'stderr':stderr})
        assert json.loads(stdout)['error']=='credential_revoked' and duration<3.5
        checks.append({'active_connection_invalidated':True,'seconds_after_revoke':duration})
        f.receive(second['messageId'],expected='message_quarantined')
        checks.append('pending_quarantined_already_delivered_requires_reconciliation')
        snapshot=f.inspect();assert [x['event']['kind'] for x in snapshot['audit']]==['rotated','revoked']
        assert snapshot['mail'][0:] and all('credential' not in json.dumps(x) for x in snapshot['seats'])
        checks.append('audit_redacts_both_operational_keys')
        f.stop();f.start()
        f.send(f.envelope(),credential=current,expected='credential_revoked')
        snapshot=f.inspect();assert any(s['revoked'] for s in snapshot['seats'])
        checks.append('revocation_and_disposition_survive_restart')
    except Exception:error=traceback.format_exc()
    finally:f.close()
    return result(a.evidence,checks,error)

if __name__=='__main__':raise SystemExit(main())
