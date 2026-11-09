#!/usr/bin/env python3
"""End-to-end contract-spike CLI checks; no authority or dispatch is created."""
import argparse
import copy
import json
from pathlib import Path
import subprocess
import sys
import uuid
import hashlib


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--binary', required=True, type=Path)
    parser.add_argument('--evidence', required=True, type=Path)
    args = parser.parse_args()
    args.binary = args.binary.resolve()
    args.evidence.mkdir(mode=0o700, parents=True, exist_ok=False)
    e = args.evidence
    now = 1800000000
    seat, host, incarnation, recipient, message, conversation = [str(uuid.uuid4()) for _ in range(6)]
    grant = dict(seat=seat, host=host, incarnation=incarnation, agentUid=12345, role='worker',
                 projects=['fixture-project'], recipients=[recipient], operations=['observe','queue'])
    request = dict(protocol=1,sender=seat,host=host,incarnation=incarnation,sequence=1,
                   project='fixture-project',recipient=recipient,operation='queue')
    envelope = dict(protocol=1,messageId=message,conversationId=conversation,assignmentId=None,
                    replyTo=None,sender=seat,incarnation=incarnation,recipient=recipient,
                    project='fixture-project',kind='note',mode='queue_only',createdAt=now,
                    expiresAt=now+60,text='Synthetic contract fixture.',artifactRefs=[])
    receipt = dict(protocol=1,messageId=message,state='delivered',writer='inbox',turnId=None)
    results = []
    def run(name, expected=None, g=None, r=None, env=None, rec=None, peer=12345, last=0):
        case = e/name;case.mkdir()
        values = {'grant':g if g is not None else grant,'request':r if r is not None else request,
                  'envelope':env if env is not None else envelope,'receipt':rec if rec is not None else receipt}
        cmd = [str(args.binary),'check-contract','--peer-uid',str(peer),'--last-sequence',str(last),'--now',str(now)]
        for key,value in values.items():
            if value is False:continue
            path = case/(key+'.json');path.write_text(json.dumps(value))
            cmd.extend(['--'+key,str(path)])
        outcome = subprocess.run(cmd,capture_output=True,text=True,timeout=10)
        passed = outcome.returncode == 0 if expected is None else outcome.returncode != 0 and expected in outcome.stderr
        if expected is None and passed:
            passed = json.loads(outcome.stdout)['dispatch'] == 'not_performed'
        record = dict(check=name,command=cmd,expected_error=expected,exit_code=outcome.returncode,
                      stdout=outcome.stdout,stderr=outcome.stderr,passed=passed)
        results.append(record)
        (case/'result.json').write_text(json.dumps(record,indent=2)+'\n')
    run('valid_scoped_message')
    for name,field,value,reason in [
        ('unsupported_protocol','protocol',2,'protocol_unsupported'),
        ('forged_sender','sender',str(uuid.uuid4()),'sender_mismatch'),
        ('wrong_host','host',str(uuid.uuid4()),'host_mismatch'),
        ('stale_incarnation','incarnation',str(uuid.uuid4()),'incarnation_stale'),
        ('replayed_sequence','sequence',0,'sequence_replay_or_gap'),
        ('sequence_gap','sequence',2,'sequence_replay_or_gap'),
        ('wrong_project','project','other-project','project_denied'),
        ('wrong_recipient','recipient',str(uuid.uuid4()),'recipient_denied'),
        ('worker_cannot_interrupt','operation','interrupt','operation_denied'),
    ]:
        r={**request,field:value};run(name,reason,r=r)
    run('wrong_peer_uid','peer_uid_denied',peer=12346)
    run('sequence_exhausted','sequence_replay_or_gap',last=2**64-1)
    g={**grant,'operations':['queue']};r={**request,'operation':'observe'}
    run('ungranted_operation','operation_denied',g=g,r=r)
    run('unknown_request_field','unknown field',r={**request,'admin':True})
    run('oversized_frame','frame_bound',r={**request,'padding':'x'*70000})
    run('empty_grant','operation_denied',g={**grant,'operations':[]})
    run('unknown_operation','unknown variant',r={**request,'operation':'shell'})
    run('path_not_project','project_id_invalid',g={**grant,'projects':['../../tmp']})
    run('nil_seat','identity_nil',g={**grant,'seat':str(uuid.UUID(int=0))})
    for name,field,value,reason in [
        ('envelope_version','protocol',2,'protocol_unsupported'),
        ('envelope_sender','sender',str(uuid.uuid4()),'envelope_sender_mismatch'),
        ('envelope_scope','project','other-project','envelope_scope_mismatch'),
        ('expired_message','expiresAt',now,'message_expired_or_invalid_time'),
        ('future_creation','createdAt',now+61,'message_expired_or_invalid_time'),
        ('oversized_text','text','x'*16385,'payload_bound'),
        ('too_many_artifacts','artifactRefs',['artifact']*9,'artifact_reference_bound'),
    ]:
        run(name,reason,env={**envelope,field:value})
    for state,writer in [('local_accepted','outbox'),('service_accepted','service'),
                         ('delivered','inbox'),('consumed','runtime'),('completed','worker')]:
        run('receipt_'+state,rec={**receipt,'state':state,'writer':writer,
                                  'turnId':'fixture-turn' if state in ['consumed','completed'] else None})
    run('receipt_forged_writer','receipt_writer_denied',rec={**receipt,'writer':'worker'})
    run('receipt_turn_required','receipt_turn_required',rec={**receipt,'state':'consumed','writer':'runtime'})
    run('receipt_wrong_message','receipt_message_mismatch',rec={**receipt,'messageId':str(uuid.uuid4())})
    matrix = {'observer':{'observe'},'controller':{'observe','queue','interrupt','approve'},
              'manager':{'observe','queue','interrupt'},'foreman':{'observe','queue','interrupt'},
              'worker':{'observe','queue'},'ops':{'observe'}}
    # Each explicit operation grant must also fit the role's upper bound.
    for role,allowed in matrix.items():
        for operation in ['observe','queue','interrupt','approve','execute']:
            g={**grant,'role':role,'operations':[operation]}
            r={**request,'operation':operation}
            if operation not in allowed:
                expected='role_capability_denied'
            else:expected=None
            run('role_'+role+'_'+operation,expected,g=g,r=r,
                env=False if operation != 'queue' else None,
                rec=False if operation != 'queue' else None)
    result={'passed':all(r['passed'] for r in results),'checks':len(results),
            'failed':[r for r in results if not r['passed']],
            'command':[sys.executable,*sys.argv],
            'binarySha256':hashlib.sha256(args.binary.read_bytes()).hexdigest(),
            'testSha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            'exit_code':0 if all(r['passed'] for r in results) else 1}
    (e/'result.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result,indent=2));return 0 if result['passed'] else 1

if __name__ == '__main__':raise SystemExit(main())
