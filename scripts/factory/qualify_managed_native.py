#!/usr/bin/env python3
"""Actual native work, write isolation, lease expiry and complete process-set fencing."""
import argparse
import hashlib
import json
from pathlib import Path
import shlex
import time
import traceback
import uuid

from fleet_fixture import SERVICE, GROUP, result
from forge_workflow_fixture import ForemanFleet
from native_fixture import Native
from session_fixture import Model


def main():
    p = argparse.ArgumentParser()
    for name in ('binary', 'cli', 'forge-binary', 'forge-source', 'evidence'):
        p.add_argument('--' + name, required=True)
    a = p.parse_args()
    f = ForemanFleet(a.binary, a.forge_binary, a.forge_source, a.evidence)
    model, native, error, checks = Model(), None, None, []
    try:
        ticket = f.ticket('Owned managed native work and expiry')
        claim = f.claim(ticket)
        run, generation = claim['runId'], claim['generation']
        workspace = f.workspace(claim)
        native = Native(f, a.cli, model, managed=True, supervised=True, sandbox='workspace-write')
        brief_thread = native.request('thread/start', {'cwd': str(native.home / 'project'), 'sandbox': 'read-only'})['thread']['id']
        model.outputs[0] = {'type': 'message', 'role': 'assistant', 'id': 'context-ack',
            'content': [{'type': 'output_text', 'text': 'Context acknowledged: ' + claim['contextDigest']}]}
        params = {'threadId': brief_thread, 'project': 'fixture-project', 'messageId': claim['briefMessageId']}
        started = native.request('thread/factoryMessage', params)['data']
        model.wait_request(0)
        assert claim['contextDigest'] in json.dumps(model.requests[0])
        model.release(0)
        native.event('turn/completed', started['turnId'])
        assert native.request('thread/factoryMessage', params)['data']['state'] == 'completed'
        f.synchronize()
        receiver = f.subjects['receiver']
        binding_args = ['--grant', f.publish(receiver['grant']), '--socket', f.socket,
                        '--service-uid', SERVICE, '--credential', receiver['credential'], '--project', 'fixture-project']
        f.run([*f.prefix(receiver['uid']), f.binary, 'ack-context', *binding_args,
               '--run-id', run, '--context-digest', claim['contextDigest']])
        f.synchronize()
        f.action({'action': 'register_execution', 'run_id': run, 'pid': native.identity['pid']})
        dispatch = f.state / 'foreman/registry.json'
        before_spec = f.state / 'foreman/before-spec.json'
        after_spec = f.state / 'foreman/after-spec.json'
        f.run([*f.prefix(SERVICE), 'cp', '-p', dispatch, before_spec])
        spec_args = {'action': 'issue_spec', 'run_id': run, 'lease_seconds': 24,
            'spec': 'Do: Execute the isolated fixture commands. Retain the result file and the process evidence.'}
        spec = f.action(spec_args)
        f.run([*f.prefix(SERVICE), 'cp', '-p', dispatch, after_spec])
        f.run([*f.prefix(SERVICE), 'cp', '-p', before_spec, dispatch])
        refused = f.action(spec_args, expected=1)
        assert 'foreman_spec_recovery_required' in refused.stderr
        assert len(model.requests) == 1
        f.run([*f.prefix(SERVICE), 'cp', '-p', after_spec, dispatch])
        checks.append('rolled_back_dispatch_cache_cannot_publish_a_second_specification_over_existing_authoritative_mail')
        # A wrong thread must not poison the never-submitted message's durable thread binding.
        native.request('thread/factoryMessage', {**params, 'messageId': spec['messageId']},
                       expected_error='foreman_execution_workspace_denied')
        assert len(model.requests) == 1
        thread = native.request('thread/start', {'cwd': str(workspace)})['thread']['id']
        outside = f.base / 'receiver/outside-worktree'
        model.outputs[1] = {'type': 'function_call', 'call_id': 'outside-write', 'name': 'exec_command',
            'arguments': json.dumps({'cmd': 'touch ' + shlex.quote(str(outside)), 'yield_time_ms': 1000})}
        artifact, counter = workspace / 'result.txt', workspace / 'counter.txt'
        writer = ("import os,time\nfrom pathlib import Path\na=Path(" + repr(str(artifact)) + ")\n"
                  "a.write_text('READY\\n');a.chmod(0o640)\np=Path(" + repr(str(counter)) + ")\n"
                  "i=0\nwhile True:\n p.write_text(str(i));p.chmod(0o640);i+=1;time.sleep(.05)\n")
        model.outputs[2] = {'type': 'function_call', 'call_id': 'owned-writer', 'name': 'exec_command',
            'arguments': json.dumps({'cmd': 'python3 -c ' + shlex.quote(writer), 'yield_time_ms': 1000})}
        params = {'threadId': thread, 'project': 'fixture-project', 'messageId': spec['messageId']}
        work = native.request('thread/factoryMessage', params)['data']
        assert work['state'] == 'started'
        model.wait_request(1)
        assert 'This is an isolated local fixture. Use no real account or production data.' in json.dumps(model.requests[1])
        assert claim['contextDigest'] in json.dumps(model.requests[1])
        model.release(1)
        model.wait_request(2)
        assert f.run(['sudo', '-n', 'test', '!', '-e', outside]).returncode == 0
        model.release(2)
        model.wait_request(3)
        deadline = time.monotonic() + 8
        while f.run(['sudo', '-n', 'test', '-s', counter], expected=None).returncode:
            assert time.monotonic() < deadline
            time.sleep(.05)
        model.release(3)
        native.event('turn/completed', work['turnId'])
        assert native.request('thread/factoryMessage', params)['data']['state'] == 'completed'
        receiver = f.subjects['receiver']
        f.run([*f.prefix(receiver['uid']), f.binary, 'collect-recording',
            '--native-state', f.base / 'receiver/native-ledger', '--grant', f.publish(receiver['grant']),
            '--socket', f.socket, '--service-uid', SERVICE, '--credential', receiver['credential']])
        f.synchronize()
        payload = {'method': 'assignment_result', 'protocol': 1, 'request': f.request('receiver', 'acknowledge'),
            'run_id': run, 'thread_id': thread, 'turn_id': work['turnId'],
            'artifacts': [{'path': str(artifact), 'sha256': hashlib.sha256(b'READY\n').hexdigest()}]}
        artifact_input = f.publish(payload['artifacts'])
        result_command = [*f.prefix(receiver['uid']), f.binary, 'assignment-result', *binding_args,
                          '--run-id', run, '--thread-id', thread, '--turn-id', work['turnId'], '--artifacts', artifact_input]
        candidate = json.loads(f.run(result_command).stdout)
        assert candidate['state'] == 'result_pending_review' and not candidate['ticketCompleted']
        assert json.loads(f.run(result_command).stdout)['eventId'] == candidate['eventId']
        f.synchronize()
        checks.append('actual_native_spec_obeys_worktree_write_boundary_and_records_only_a_verified_review_candidate')
        before = int(f.sudo('cat', counter).stdout)
        status = f.action({'action': 'status', 'run_id': run})
        wait = status['dispatch']['leaseTick'] - time.clock_gettime(time.CLOCK_BOOTTIME)
        if wait > 0:
            time.sleep(wait + .1)
        f.action({'action': 'quarantine_expired', 'run_id': run})
        assert int(f.sudo('cat', counter).stdout) > before
        refused = f.action({'action': 'fence', 'run_id': run}, expected=1)
        assert 'foreman_process_set_still_live' in refused.stderr
        new_args = {'action': 'claim', 'operation_id': str(uuid.uuid4()), 'ticket': ticket['number'],
            'worker': f.subjects['replacement']['seat'], 'context': 'This is an isolated fixture.',
            'brief': 'Wait for the executable specification.'}
        refused = f.action(new_args, expected=1)
        assert 'foreman_prior_effects_unreconciled' in refused.stderr
        # The exact registered unit owns the native process and all of its tool descendants.
        f.sudo('systemctl', 'stop', native.unit)
        exit_code = native.process.wait(timeout=12)
        f.commands.append({'supervisedNativeExit': exit_code, 'unit': native.unit, 'incarnation': native.identity})
        f.action({'action': 'fence', 'run_id': run})
        stopped = f.sudo('cat', counter).stdout
        time.sleep(.25)
        assert f.sudo('cat', counter).stdout == stopped
        recon_dir = f.state / 'foreman/reconciliations'
        f.sudo('install', '-d', '-o', SERVICE, '-g', GROUP, '-m', '0700', recon_dir)
        receipt = {'runId': run, 'generation': generation, 'confirmedNoOutstandingEffects': True,
            'evidence': ['The actual native cgroup is empty. The tool writer stopped.',
                         'Counter SHA256: ' + hashlib.sha256(stopped.encode()).hexdigest(),
                         'The READY artifact matches its verified native result.']}
        report = recon_dir / (run + '.json')
        f.sudo('install', '-o', SERVICE, '-g', GROUP, '-m', '0600', f.publish(receipt), report)
        f.action({'action': 'reconcile', 'run_id': run, 'report': str(report)})
        replacement = f.action(new_args)
        assert replacement['runId'] != run
        payload['request'] = f.request('receiver', 'acknowledge')
        f.wire('receiver', payload, expected='foreman_result_generation_inactive')
        current = f.action({'action': 'status', 'run_id': replacement['runId']})
        assert current['canonical']['run']['id'] == replacement['runId']
        checks.append('lease_expiry_does_not_imply_stopped_writer_actual_cgroup_fence_and_reconciliation_precede_replacement')
        checks.append('actual_old_native_terminal_and_artifact_cannot_complete_the_new_generation')
        (f.e / 'verified-flow.json').write_text(json.dumps({'claim': claim, 'spec': spec, 'native': native.identity,
            'thread': thread, 'turn': work['turnId'], 'candidate': candidate, 'receipt': receipt,
            'replacement': replacement, 'providerRequests': len(model.requests), 'realAccountTurns': 0}, indent=2))
    except Exception:
        error = traceback.format_exc()
    finally:
        model.close()
        if native:
            native.close()
        f.close()
    return result(a.evidence, checks, error)


if __name__ == '__main__':
    raise SystemExit(main())
