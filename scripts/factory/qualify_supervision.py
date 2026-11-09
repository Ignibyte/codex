#!/usr/bin/env python3
"""Actual blocked native worker, durable decision notice, restart and human escalation."""
import argparse
import json
import subprocess
import time
import traceback
import uuid
from pathlib import Path

from fleet_fixture import GROUP, SERVICE, result
from forge_workflow_fixture import ForemanFleet
from native_fixture import Native
from session_fixture import Model


def main():
    p = argparse.ArgumentParser()
    for name in ('binary', 'fault-binary', 'cli', 'forge-binary', 'forge-source', 'evidence'):
        p.add_argument('--' + name, required=True)
    a = p.parse_args()
    f = ForemanFleet(a.fault_binary, a.forge_binary, a.forge_source, a.evidence, foreman_ack=True)
    worker_model, foreman_model = Model(), Model()
    worker, foreman, extra, crashed = None, None, None, None
    checks, error = [], None
    try:
        claim = f.claim(f.ticket('Deterministic supervision with changed facts'))
        run = claim['runId']
        workspace = f.workspace(claim)
        worker = Native(f, a.cli, worker_model, managed=True, supervised=True)
        foreman = Native(f, a.cli, foreman_model, name='sender', supervised=True, workflow=True)
        thread = worker.request('thread/start', {'cwd': str(worker.home / 'project')})['thread']['id']
        params = {'threadId': thread, 'project': 'fixture-project', 'messageId': claim['briefMessageId']}
        brief = worker.request('thread/factoryMessage', params)['data']
        worker_model.wait_request(0)
        worker_model.release(0)
        worker.event('turn/completed', brief['turnId'])
        f.synchronize()
        f.wire('receiver', {'method': 'acknowledge_context', 'protocol': 1,
            'request': f.request('receiver', 'acknowledge'), 'run_id': run, 'context_digest': claim['contextDigest']})
        f.action({'action': 'register_execution', 'run_id': run, 'pid': worker.identity['pid']})
        spec = f.action({'action': 'issue_spec', 'run_id': run, 'lease_seconds': 120,
                         'spec': 'Do: Wait for the fixture result. Use no tools.'})
        work_thread = worker.request('thread/start', {'cwd': str(workspace)})['thread']['id']
        work = worker.request('thread/factoryMessage', {'threadId': work_thread, 'project': 'fixture-project',
                                                      'messageId': spec['messageId']})['data']
        worker_model.wait_request(1)  # Deliberately held while its process and heartbeat remain live.
        state = f.state / 'foreman'

        def action(value, expected=0):
            r = f.admin('supervise', '--state', state, '--config', f.config, '--input', f.publish(value), expected=expected)
            return json.loads(r.stdout) if not r.returncode else r

        def tick(**changes):
            return {'action': 'tick', 'run_id': run, 'tick_id': str(uuid.uuid4()),
                    'stall_seconds': 1, 'foreman_pid': foreman.identity['pid'], **changes}

        def call(name, frame, expected=0):
            actor = f.subjects[name]
            command = [*f.prefix(actor['uid']), f.binary, 'workflow-call', '--grant', f.publish(actor['grant']),
                '--socket', f.socket, '--service-uid', SERVICE, '--credential', actor['credential'],
                '--project', 'fixture-project', '--input', f.publish(frame)]
            r = f.run(command, expected=expected)
            f.synchronize(name)
            return json.loads(r.stdout) if not r.returncode else r

        def pulse():
            f.synchronize()
            return f.wire('receiver', {'method': 'report_presence', 'protocol': 1,
                'request': f.request('receiver', 'acknowledge'),
                'report': {'process': {k: worker.identity[k] for k in ('pid', 'bootId', 'startTicks')},
                           'modelActivity': 'busy', 'taskProgress': 'in_progress'}})

        pulse()
        initial = action(tick())
        assert initial['state'] == 'no_decision_required' and not initial['taskProductive']
        time.sleep(1.1)
        pulse()
        request = tick()
        arm, hit = state / 'fault.supervision_after_delivery', state / 'hit.supervision_after_delivery'
        f.sudo('install', '-o', SERVICE, '-g', GROUP, '-m', '0600', '/dev/null', arm)
        command = [*f.prefix(SERVICE), f.binary, 'supervise', '--state', state, '--config', f.config,
                   '--input', f.publish(request)]
        log = (f.e / 'crashed-supervision.log').open('w')
        unit = 'cf-tick-' + str(uuid.uuid4()) + '.service'
        command = ['sudo', '-n', 'systemd-run', '--quiet', '--wait', '--pipe', '--collect',
                   '--service-type=exec', '--unit', unit, '--property=KillMode=control-group',
                   '--property=TimeoutStopSec=3', '--property=MemoryMax=256M', '--property=TasksMax=32', *command[2:]]
        crashed = subprocess.Popen(list(map(str, command)), stdout=log, stderr=log)
        f.commands.append({'command': list(map(str, command)), 'unit': unit})
        deadline = time.monotonic() + 15
        while f.run(['sudo', '-n', 'test', '-f', hit], expected=None).returncode:
            assert crashed.poll() is None and time.monotonic() < deadline
            time.sleep(.025)
        pid = int(f.sudo('systemctl', 'show', '--property=MainPID', '--value', unit).stdout)
        owner_code = """import json,os,sys
from pathlib import Path
pid=int(sys.argv[1]);p=Path('/proc')/str(pid)
print(json.dumps({'pid':pid,'bootId':Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
 'startTicks':(p/'stat').read_text().rsplit(')',1)[1].split()[19],
 'uid':p.stat().st_uid,'exe':os.readlink(p/'exe')}))
"""
        owner = json.loads(f.sudo('python3', '-c', owner_code, pid).stdout)
        assert owner['uid'] == SERVICE and owner['exe'] == str(f.binary)
        stop = f.e / 'supervision-stop.json'
        stop.write_text(json.dumps(owner))
        f.sudo('python3', f.helper, '--stop-record', stop, '--stop-signal', 'KILL')
        crashed.wait(timeout=10)
        log.close()
        f.commands.append({'supervisionCrashExit': crashed.returncode, 'incarnation': owner, 'unit': unit})
        f.sudo('rm', '--', arm, hit)
        f.stop()
        f.start()
        recovered = action(request)
        assert recovered['state'] == 'queued' and recovered['facts']['stalled']
        assert recovered['facts']['processLive'] and not recovered['taskProductive']
        assert 'no_verified_progress' in recovered['facts']['reasons']
        assert action(request)['noticeId'] == recovered['noticeId']
        conflict = action({**request, 'stall_seconds': 2}, expected=1)
        assert 'supervision_tick_conflict' in conflict.stderr
        assert sum(m['messageId'] == recovered['messageId'] for m in f.inspect()['mail']) == 1
        decision = {'method': 'blockage_decision', 'run_id': run, 'notice_id': recovered['noticeId'],
                    'decision': {'choice': 'request_human', 'explanation': 'The worker has no verified progress. The user must choose the next action.'}}
        foreman_model.outputs[1] = {'type': 'message', 'role': 'assistant', 'id': 'decision-recorded',
            'content': [{'type': 'output_text', 'text': 'State: waiting\nEvidence: ' + recovered['noticeId'] + '\nAsk: The user must choose the next action.'}]}
        foreman_thread = foreman.request('thread/start', {'cwd': str(foreman.home / 'project')})['thread']['id']
        notice_params = {'threadId': foreman_thread, 'project': 'fixture-project', 'messageId': recovered['messageId']}
        started = foreman.request('thread/factoryMessage', notice_params)['data']
        foreman_model.wait_request(0)
        assert recovered['noticeId'] in json.dumps(foreman_model.requests[0])
        def tool_specs(value, namespace=None):
            if isinstance(value, dict):
                if value.get('type') == 'namespace':
                    namespace = value['name']
                if value.get('type') == 'function' and isinstance(value.get('name'), str):
                    yield {'name': value['name'], 'namespace': namespace}
                for child in value.values():
                    yield from tool_specs(child, namespace)
            elif isinstance(value, list):
                for child in value:
                    yield from tool_specs(child, namespace)
        catalog = list(tool_specs(foreman_model.requests[0]['body']['tools']))
        actual = [spec for spec in catalog if spec['name'].endswith('workflow_decision')]
        assert len(actual) == 1, catalog
        foreman_model.outputs[0] = {'type': 'function_call', 'call_id': 'record-decision', **actual[0],
            'arguments': json.dumps({k: v for k, v in decision.items() if k != 'method'})}
        foreman_model.release(0)
        deadline = time.monotonic() + 25
        while len(foreman_model.requests) < 2:
            foreman.poll(.05)
            assert time.monotonic() < deadline, foreman.events[-5:]
        assert recovered['noticeId'] in json.dumps(foreman_model.requests[1])
        foreman_model.release(1)
        foreman.event('turn/completed', started['turnId'])
        pulse()
        assert action(tick())['state'] == 'unchanged'
        assert foreman.request('thread/factoryMessage', notice_params)['data']['state'] == 'completed'
        assert len(foreman_model.requests) == 2
        checks.append('live_native_heartbeat_is_not_verified_progress_and_exact_notice_survives_real_dispatch_crash_and_restart')
        checks.append('repeated_and_unchanged_ticks_retain_one_mail_and_one_actual_foreman_model_turn')

        decision = {'method': 'blockage_decision', 'run_id': run, 'notice_id': recovered['noticeId'],
                    'decision': {'choice': 'request_human', 'explanation': 'The worker has no verified progress. The user must choose the next action.'}}
        denied = call('receiver', decision, expected=1)
        assert 'supervision_decision_scope_denied' in denied.stderr
        recorded = action({'action': 'status', 'run_id': run})
        assert recorded['notices'][0]['record']['decision'] == decision['decision'], [item for item in foreman_model.requests[1]['body'].get('input', []) if item.get('type') == 'function_call_output']
        answer = call('sender', decision)
        assert not answer['executionResumed'] and not answer['ticketCompleted']
        assert call('sender', decision)['decision'] == answer['decision']
        checks.append('only_the_granted_foreman_records_an_idempotent_advisory_decision_without_execution_or_ticket_authority')

        incident = {'incidentId': str(uuid.uuid4()), 'reason': 'blocked',
                    'summary': 'The worker needs a user decision before it continues.'}
        report = {'method': 'report_blockage', 'run_id': run, 'report': incident}
        denied = call('replacement', report, expected=1)
        assert 'supervision_worker_denied' in denied.stderr
        call('receiver', report)
        assert call('receiver', report)['incidentId'] == incident['incidentId']
        foreman.stop()
        unavailable = action(tick())
        assert unavailable['humanEscalation'] and unavailable['state'] == 'human_escalated'
        assert not unavailable['facts']['foremanAvailable'] and unavailable['facts']['workerReport'] == incident
        assert action(tick())['noticeId'] == unavailable['noticeId']
        stale = call('sender', decision, expected=1)
        assert 'supervision_notice_superseded' in stale.stderr
        human = {**decision, 'notice_id': unavailable['noticeId']}
        call('sender', human)
        assert len(foreman_model.requests) == 2
        checks.append('authenticated_changed_blockage_with_unavailable_foreman_has_a_traceable_human_escalation_and_refuses_stale_decisions')

        incident = {'incidentId': str(uuid.uuid4()), 'reason': 'rate_limit', 'summary': 'The scripted account limit prevents further work.'}
        call('receiver', {**report, 'report': incident})
        limited = action(tick())
        assert limited['humanEscalation'] and limited['stopRequired']
        refused = call('sender', {**decision, 'notice_id': limited['noticeId'],
            'decision': {'choice': 'wait', 'explanation': 'Wait for capacity.'}}, expected=1)
        assert 'supervision_human_hold_latched' in refused.stderr
        call('sender', {**human, 'notice_id': limited['noticeId']})
        fresh = {'incidentId': str(uuid.uuid4()), 'reason': 'blocked', 'summary': 'The worker asks for another task.'}
        refused = call('receiver', {**report, 'report': fresh}, expected=1)
        assert 'supervision_human_hold_latched' in refused.stderr
        assert action(tick())['stopRequired'] and len(foreman_model.requests) == 2
        status = action({'action': 'status', 'run_id': run})
        assert status['report']['reason'] == 'rate_limit' and len(status['notices']) == 3
        checks.append('scripted_rate_limit_remains_visible_and_latched_after_a_decision_without_another_model_poll')
        approval_claim = f.claim(f.ticket('Unexpected approval has an independent human hold'), name='replacement')
        approval_run = approval_claim['runId']
        f.workspace(approval_claim, name='replacement')
        extra = Native(f, a.cli, worker_model, name='replacement', supervised=True)
        received = f.receive(approval_claim['briefMessageId'], name='replacement')
        assert approval_claim['contextDigest'] in json.dumps(received)
        f.wire('replacement', {'method': 'acknowledge_context', 'protocol': 1,
            'request': f.request('replacement', 'acknowledge'), 'run_id': approval_run,
            'context_digest': approval_claim['contextDigest']})
        f.action({'action': 'register_execution', 'run_id': approval_run, 'pid': extra.identity['pid']})
        f.action({'action': 'issue_spec', 'run_id': approval_run, 'lease_seconds': 120,
                  'spec': 'Do: Wait for the operator. Do not request an approval.'})
        approval = {'incidentId': str(uuid.uuid4()), 'reason': 'approval_required',
                    'summary': 'The scripted tool requires an unexpected approval.'}
        call('replacement', {'method': 'report_blockage', 'run_id': approval_run, 'report': approval})
        held = action(tick(run_id=approval_run))
        assert held['stopRequired'] and held['humanEscalation']
        assert held['facts']['workerReport']['reason'] == 'approval_required'
        assert len(foreman_model.requests) == 2
        checks.append('unexpected_approval_on_an_independent_generation_requires_human_handling_without_model_polling')
        for name, expected_tool, denied_tool in (
                ('receiver', 'workflow_report_blockage', 'workflow_decision'),
                ('sender', 'workflow_decision', 'workflow_report_blockage')):
            actor = f.subjects[name]
            command = [*f.prefix(actor['uid']), f.binary, 'workflow-mcp', '--grant', f.publish(actor['grant']),
                '--socket', f.socket, '--service-uid', SERVICE, '--credential', actor['credential'], '--project', 'fixture-project']
            requests = [
                {'jsonrpc': '2.0', 'id': 1, 'method': 'initialize', 'params': {'protocolVersion': '2025-06-18'}},
                {'jsonrpc': '2.0', 'id': 2, 'method': 'tools/list'},
                {'jsonrpc': '2.0', 'id': 3, 'method': 'tools/call', 'params': {'name': denied_tool, 'arguments': {}}},
                {'jsonrpc': '2.0', 'id': 4, 'method': 'tools/call', 'params': {'name': 'exec_command', 'arguments': {'cmd': 'false'}}}]
            response = subprocess.run(list(map(str, command)), input=''.join(json.dumps(v) + '\n' for v in requests),
                                      capture_output=True, text=True, timeout=10)
            f.commands.append({'command': list(map(str, command)), 'exit_code': response.returncode,
                               'stdout': response.stdout, 'stderr': response.stderr})
            assert response.returncode == 0
            replies = [json.loads(line) for line in response.stdout.splitlines()]
            assert [t['name'] for t in replies[1]['result']['tools']] == [expected_tool]
            assert replies[1]['result']['tools'][0]['annotations'] == {
                'readOnlyHint': False, 'destructiveHint': False, 'idempotentHint': True, 'openWorldHint': False}
            assert all(reply['error']['message'] == 'The workflow request is denied.' for reply in replies[2:])
        checks.append('role_scoped_plugin_catalogs_refuse_other_roles_and_arbitrary_commands_without_a_sandbox_override')
        (f.e / 'verified-flow.json').write_text(json.dumps({'claim': claim, 'work': work, 'recovered': recovered,
            'decision': answer, 'unavailable': unavailable, 'limited': limited, 'status': status,
            'workerProviderRequests': len(worker_model.requests), 'foremanProviderRequests': len(foreman_model.requests), 'foremanSubmittedTurns': 1,
            'realAccountTurns': 0}, indent=2))
    except Exception:
        error = traceback.format_exc()
    finally:
        worker_model.close()
        foreman_model.close()
        if crashed and crashed.poll() is None:
            crashed.wait(timeout=35)
        if extra:
            extra.close()
        if foreman:
            foreman.close()
        if worker:
            worker.close()
        f.close()
    return result(a.evidence, checks, error)


if __name__ == '__main__':
    raise SystemExit(main())
