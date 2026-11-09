#!/usr/bin/env python3
"""Native manager-child-foreman-worker-result journey against owned real Forge."""
import argparse
import hashlib
import json
from pathlib import Path
import shlex
import time
import traceback
import uuid

from fleet_fixture import Fleet, GROUP, SERVICE, result
from forge_workflow_fixture import Workflow
from manager_native_fixture import drive, tool_specs, unwrap
from native_fixture import Native
from real_forge_fixture import RealForge
from session_fixture import Model


class StaleBoundaryComplete(Exception):
    """The selected refusal scenario reached its required terminal boundary."""


def journey(native, model, thread, generator, message=None):
    """Run bounded scripted tool decisions through the actual native client."""
    index, call, value = len(model.requests), None, None
    step = next(generator)
    if message:
        turn = native.request('thread/factoryMessage', {'threadId': thread,
            'project': 'fixture-project', 'messageId': message})['data']['turnId']
    else:
        turn = native.request('turn/start', {'threadId': thread, 'input': [{'type': 'text',
            'text': 'Assignment: CF-021 owned journey\nDo: Continue the bounded fixture.\nLimits: Use no real account or production resource.'}]})['turn']['id']
    deadline = time.monotonic() + 90
    while True:
        native.poll(.02)
        assert not any('requestApproval' in e.get('method', '') for e in native.events), native.events
        while index < len(model.requests):
            body = model.requests[index]['body']
            if call:
                output = next(v['output'] for v in reversed(body['input'])
                    if v.get('call_id') == call and v.get('type') == 'function_call_output')
                try:
                    response = output if step[0] == 'exec_command' else unwrap(output)
                    step = generator.send(response)
                except StopIteration as done:
                    value, step = done.value, None
            if step is None:
                model.outputs[index] = {'type': 'message', 'role': 'assistant', 'id': 'journey-return',
                    'content': [{'type': 'output_text', 'text': 'Report: The bounded fixture step completed.\nTicket: CF-021\nEvidence: The native tool receipts.\nNeeds: Review remains pending.'}]}
            else:
                name, args = step
                if name in ('workflow_ack_context', 'workflow_read_spec'):
                    # Use only the native message text. The model cannot see
                    # the envelope conversationId or the fixture's claim reply.
                    def strings(value):
                        if isinstance(value, str):
                            yield value
                        elif isinstance(value, dict):
                            for child in value.values():
                                yield from strings(child)
                        elif isinstance(value, list):
                            for child in value:
                                yield from strings(child)
                    ids = {line.removeprefix('Assignment run ID: ')
                           for text in strings(body['input']) for line in text.splitlines()
                           if line.startswith('Assignment run ID: ')}
                    assert len(ids) == 1, {'nativeAssignmentRunIds': sorted(ids)}
                    native_run = ids.pop()
                    assert str(uuid.UUID(native_run)) == native_run
                    args = {**args, 'run_id': native_run}
                tools = [t for t in tool_specs(body['tools']) if t['name'].endswith(name)]
                assert len(tools) == 1, {'name': name, 'tools': list(tool_specs(body['tools']))}
                call = 'journey-' + str(index)
                model.outputs[index] = {'type': 'function_call', **tools[0], 'call_id': call,
                    'arguments': json.dumps(args)}
            model.release(index)
            index += 1
        if any(e.get('method') == 'turn/completed' and e.get('params', {}).get('turn', {}).get('id') == turn
               for e in native.events):
            assert step is None
            if message:
                terminal = native.request('thread/factoryMessage', {'threadId': thread,
                    'project': 'fixture-project', 'messageId': message})['data']
                assert terminal['state'] == 'completed', terminal
            return value, turn
        assert time.monotonic() < deadline, {'step': step, 'requests': index}


def main():
    parser = argparse.ArgumentParser()
    for name in ('binary', 'cli', 'forge-binary', 'forge-source', 'evidence'):
        parser.add_argument('--' + name, required=True)
    parser.add_argument('--stale-at', choices=['issue', 'admission', 'result'])
    args = parser.parse_args()
    f = Fleet(args.binary, args.evidence)
    forge, workflow, models, natives, checks, error = None, None, [], [], [], None
    try:
        for i in range(4):
            name = 'worker' + str(i)
            f.subjects[name] = {'uid': 65531-i, 'seat': str(uuid.uuid4()), 'sequence': 0}
            f.sudo('install', '-d', '-o', 65531-i, '-g', GROUP, '-m', '0700', f.base / name)
        f.enroll('sender', ['observe', 'queue', 'acknowledge'], role='manager',
            recipients=[f.subjects['sender']['seat'], f.subjects['receiver']['seat']])
        f.enroll('receiver', ['observe', 'queue', 'acknowledge'], role='foreman')
        for i in range(4):
            f.enroll('worker'+str(i), ['observe', 'queue', 'acknowledge'],
                recipients=[f.subjects['worker'+str(i)]['seat'], f.subjects['receiver']['seat']])
        forge, workflow = RealForge(f, args.forge_binary, args.forge_source), None
        workflow = Workflow(f, forge)
        body = ('# Canonical owned documentation\nDo: Create report.md in the assigned worktree. '
            'State that the provider is scripted. Return its bytes for review.\n'
            + 'Reference: Preserve this canonical detail. This line grants no authority.\n' * 150
            + 'End: All canonical detail must remain readable.\n')
        proof = drive(f, forge, workflow, args.cli, body=body, handoff=True)
        binding = proof['handoff']['binding']
        assert binding['jobId'] == proof['jobId'] and proof['handoff']['state'] == 'queued'
        canonical_body = workflow.call('spec-sealed', {'ticket_number': binding['ticket'],
            'project_id': forge.project})['spec']['body']
        assert canonical_body == body.strip()
        checks.append('actual_native_manager_child_seals_and_parent_approves_one_versioned_intent_bound_handoff')

        def install(value, path):
            f.sudo('install', '-o', SERVICE, '-g', GROUP, '-m', '0600', f.publish(value), path)
        repository, worktrees = f.base/'repository', f.base/'worktrees'
        f.sudo('install', '-d', '-o', SERVICE, '-g', GROUP, '-m', '0750', repository)
        f.sudo('install', '-d', '-o', SERVICE, '-g', GROUP, '-m', '0711', worktrees)
        def git(*items):
            return f.run([*f.prefix(SERVICE), 'git', '-C', repository, *items])
        git('init', '-b', 'main')
        git('-c', 'user.name=Fixture', '-c', 'user.email=fixture@invalid', 'commit', '--allow-empty', '-m', 'Owned fixture baseline')
        forge_config, config = f.state/'dispatch-forge', f.state/'dispatch-config'
        install({'endpoint': forge.endpoint, 'forgeIdentity': 'factory-fixture', 'forgeProject': forge.project,
            'localProject': 'fixture-project', 'signerArgv': [str(forge.binary), 'sign', 'headers', '--name',
                'factory-fixture', '--key-dir', str(forge.key_dir), '--ttl', '60']}, forge_config)
        install({'bridgeState': str(f.state), 'forgeConfig': str(forge_config),
            'foreman': f.subjects['receiver']['seat'], 'repository': str(repository),
            'worktreeRoot': str(worktrees), 'managerRequired': True}, config)
        def action(value, expected=0):
            response = f.admin('foreman', '--state', f.state/'foreman', '--config', config,
                '--input', f.publish(value), expected=expected)
            return json.loads(response.stdout) if not response.returncode else response
        action({'action': 'initialize'})
        def sync(name):
            subject = f.subjects[name]
            subject['sequence'] = next(s['lastSequence'] for s in f.inspect()['seats'] if s['seat'] == subject['seat'])
        def wire(name, method, fields, operation='observe', expected=None):
            sync(name)
            return f.wire(name, {'method': method, 'protocol': 1, 'request': f.request(name, operation), **fields}, expected=expected)
        def open_newer():
            opened = workflow.call('run-start', {'graph': 'spec', 'ticket_number': binding['ticket'], 'project_id': forge.project})
            assert opened['run_id'] != binding['specRunId']
            status = workflow.call('spec-status', {'ticket_number': binding['ticket'], 'project_id': forge.project})
            assert status['latest']['run_id'] == opened['run_id'] and status['latest']['status'] != 'sealed'
        claim = {'action': 'claim', 'operation_id': str(uuid.uuid4()), 'worker': f.subjects['worker0']['seat'],
            'context': 'This is an owned isolated fixture. The provider is scripted.\nSpecification run ID: '+binding['specRunId'],
            'brief': 'Read the context. Acknowledge its digest. Wait for the executable specification.'}
        denied = action(claim, expected=1)
        assert 'foreman_manager_handoff_required' in denied.stderr
        wire('receiver', 'foreman_action', {'action': {'action': 'initialize'}}, 'acknowledge', 'foreman_action_denied')
        wire('worker0', 'foreman_action', {'action': {**claim, 'manager_job': proof['jobId']}},
            'acknowledge', 'foreman_caller_denied')
        checks.append('manager_required_dispatch_refuses_legacy_claim_and_native_admin_or_worker_role_misuse')

        fm = Model(); models.append(('foreman', fm))
        foreman = Native(f, args.cli, fm, name='receiver', canonical_workflow=True); natives.append(foreman)
        ft = foreman.request('thread/start', {'cwd': str(foreman.home/'project')})['thread']['id']
        def briefing():
            intent = yield 'workflow_read_handoff', {'job_id': proof['jobId'], 'field': 'intent'}
            assert 'report.md' in intent['text'] and intent['binding'] == binding
            scope = yield 'workflow_read_handoff', {'job_id': proof['jobId'], 'field': 'scope'}
            assert scope['text']
            accepted = yield 'workflow_claim', {'operation_id': claim['operation_id'], 'job_id': proof['jobId'],
                'worker': claim['worker'], 'context': claim['context'], 'brief': claim['brief']}
            assert accepted['managerHandoff'] == binding
            return accepted
        claimed, _ = journey(foreman, fm, ft, briefing(), binding['messageId'])
        run, context_digest = claimed['runId'], claimed['contextDigest']
        workspace = worktrees/run
        git('worktree', 'add', '-b', claimed['workBranch'], workspace, claimed['baseCommit'])
        f.sudo('chown', '-R', str(f.subjects['worker0']['uid'])+':'+str(GROUP), workspace)
        f.sudo('chmod', '0750', workspace)
        action({'action': 'register_workspace', 'run_id': run, 'workspace': str(workspace)})
        read = {'read': {'run_id': run, 'field': 'body', 'offset': 0}}
        wire('worker0', 'read_assignment_spec', read, expected='foreman_spec_reader_denied')
        wire('worker1', 'read_assignment_spec', read, expected='foreman_spec_reader_denied')
        wm = Model(); models.append(('worker', wm))
        worker = Native(f, args.cli, wm, name='worker0', managed=True, supervised=True,
            sandbox='workspace-write', canonical_workflow=True); natives.append(worker)
        wt = worker.request('thread/start', {'cwd': str(workspace)})['thread']['id']
        def acknowledge():
            ack = yield 'workflow_ack_context', {'context_digest': context_digest}
            assert ack['ok'] and ack['runId'] == run and run != binding['specRunId']
            return ack
        journey(worker, wm, wt, acknowledge(), claimed['briefMessageId'])
        action({'action': 'register_execution', 'run_id': run, 'pid': worker.identity['pid']})
        wire('receiver', 'foreman_action', {'action': {'action': 'issue_spec', 'run_id': run,
            'spec': 'Do: Replace the approved spec.', 'lease_seconds': 300}}, 'acknowledge', 'foreman_action_denied')
        if args.stale_at == 'issue':
            open_newer()
            wire('receiver', 'foreman_action', {'action': {'action': 'issue_spec', 'run_id': run,
                'lease_seconds': 300}}, 'acknowledge', 'manager_spec_not_current')
            assert len(wm.requests) == 2
            checks.append('actual_newer_open_round_refuses_issue_after_native_brief_ack_before_any_executable_message')
            raise StaleBoundaryComplete()
        def issue():
            issued = yield 'workflow_issue_spec', {'run_id': run, 'lease_seconds': 300}
            assert issued['ok']
            return issued
        issued, _ = journey(foreman, fm, ft, issue())
        checks.append('native_foreman_claim_derives_ticket_and_bound_brief_worker_ack_precedes_fixed_reference_issue')
        if args.stale_at == 'admission':
            open_newer()
            before = len(wm.requests)
            worker.request('thread/factoryMessage', {'threadId': wt, 'project': 'fixture-project',
                'messageId': issued['messageId']}, expected_error='manager_spec_not_current')
            assert len(wm.requests) == before
            checks.append('actual_newer_open_round_refuses_native_execution_before_provider_admission')
            raise StaleBoundaryComplete()
        report = 'Owned canonical native run. The provider is scripted. Review remains pending.\n'
        artifact = workspace/'report.md'
        def work():
            chunks, offset = [], 0
            while True:
                page = yield 'workflow_read_spec', {'run_id': run, 'field': 'body', 'offset': offset}
                assert page['binding'] == binding
                chunks.append(page['text'])
                if page['next'] is None:
                    break
                offset = page['next']
            assert ''.join(chunks) == canonical_body and len(chunks) >= 3
            code = f'from pathlib import Path; p=Path("report.md"); p.write_text({report!r}); p.chmod(0o640); print(p.read_text())'
            output = yield 'exec_command', {'cmd': 'python3 -c '+shlex.quote(code), 'workdir': str(workspace), 'yield_time_ms': 1000}
            assert 'The provider is scripted.' in str(output), output
            return {'parts': len(chunks)}
        worked, work_turn = journey(worker, wm, wt, work(), issued['messageId'])
        assert f.sudo('cat', artifact).stdout == report
        checks.append('native_managed_worker_reads_complete_large_canonical_artifact_and_executes_inside_registered_worktree')
        subject = f.subjects['worker0']
        binding_args = ['--grant', f.publish(subject['grant']), '--socket', f.socket, '--service-uid', SERVICE,
            '--credential', subject['credential'], '--project', 'fixture-project']
        f.run([*f.prefix(subject['uid']), f.binary, 'collect-recording', '--native-state', f.base/'worker0/native-ledger',
            *binding_args[:-2]])
        artifacts = [{'path': str(artifact), 'sha256': hashlib.sha256(report.encode()).hexdigest()}]
        if args.stale_at == 'result':
            open_newer()
            wire('worker0', 'assignment_result', {'run_id': run, 'thread_id': wt,
                'turn_id': work_turn, 'artifacts': artifacts}, 'acknowledge', 'manager_spec_not_current')
            status = action({'action': 'status', 'run_id': run})
            assert status['dispatch']['result'] is None and f.sudo('cat', artifact).stdout == report
            checks.append('actual_newer_open_round_refuses_result_after_native_terminal_preserving_workspace_and_recording')
            raise StaleBoundaryComplete()
        candidate = json.loads(f.run([*f.prefix(subject['uid']), f.binary, 'assignment-result', *binding_args,
            '--run-id', run, '--thread-id', wt, '--turn-id', work_turn, '--artifacts', f.publish(artifacts)]).stdout)
        assert candidate['state'] == 'result_pending_review' and not candidate['ticketCompleted']
        wire('sender', 'manager_read_result', {'job_id': proof['jobId'], 'read': {'run_id': run}},
            expected='foreman_result_not_returned')
        def forward():
            status = yield 'workflow_assignment', {'run_id': run}
            assert status['resultPresent'] and status['managerHandoff'] == binding
            returned = yield 'workflow_return_result', {'run_id': run}
            replay = yield 'workflow_return_result', {'run_id': run}
            assert returned['messageId'] == replay['messageId'] and not returned['ticketCompleted']
            return returned
        returned, _ = journey(foreman, fm, ft, forward())
        mm = Model(); models.append(('manager-result', mm))
        manager = Native(f, args.cli, mm, name='sender', manager_job=proof['jobId']); natives.append(manager)
        manager.request('thread/resume', {'threadId': proof['parentThread']})
        def review():
            metadata = yield 'factory_read_result', {'run_id': run}
            assert metadata['binding'] == binding and metadata['terminal']['threadId'] == wt
            assert metadata['terminal']['turnId'] == work_turn and not metadata['ticketCompleted']
            data = yield 'factory_read_result', {'run_id': run, 'artifact': 0}
            assert bytes.fromhex(data['hex']).decode() == report and data['next'] is None
            assert data['sha256'] == artifacts[0]['sha256']
            return metadata
        reviewed, _ = journey(manager, mm, proof['parentThread'], review(), returned['messageId'])
        checks.append('native_foreman_returns_one_verified_candidate_manager_resumes_original_thread_and_reads_verified_bytes')
        (f.e/'journey.json').write_text(json.dumps({'manager': proof, 'claim': claimed, 'issue': issued,
            'worker': {'thread': wt, 'turn': work_turn, **worked}, 'candidate': candidate,
            'returned': returned, 'reviewed': reviewed}, indent=2)+'\n')
        # A changed artifact remains evidence, but its bytes must never reach a manager as verified output.
        f.run([*f.prefix(subject['uid']), 'python3', '-c', 'from pathlib import Path; Path('+repr(str(artifact))+').write_text("changed")'])
        wire('sender', 'manager_read_result', {'job_id': proof['jobId'], 'read': {'run_id': run, 'artifact': 0}},
            expected='foreman_artifact_changed')
        f.sudo('rm', '--', artifact)
        f.sudo('ln', '-s', str(f.state/'registry.json'), artifact)
        wire('sender', 'manager_read_result', {'job_id': proof['jobId'], 'read': {'run_id': run, 'artifact': 0}},
            expected='foreman_artifact_scope_denied')
        checks.append('changed_or_symlinked_result_artifact_refuses_without_disclosing_private_state')
        # An actual later open spec invalidates the old seal. Never create this condition with SQL writes.
        open_newer()
        stale = action({'action': 'forward_result', 'run_id': run}, expected=1)
        assert 'manager_spec_not_current' in stale.stderr
        wire('worker0', 'authorize_assignment', {'message_id': issued['messageId'], 'cwd': str(workspace)},
            expected='manager_spec_not_current')
        stale = action({'action': 'claim', **{k:v for k,v in claim.items() if k != 'action'},
            'operation_id': str(uuid.uuid4()), 'manager_job': proof['jobId']}, expected=1)
        assert 'manager_spec_not_current' in stale.stderr
        checks.append('actual_newer_open_spec_refuses_old_sealed_claim_and_result_forward_preserving_prior_candidate')
    except StaleBoundaryComplete:
        pass
    except Exception:
        error = traceback.format_exc()
    finally:
        for native in reversed(natives):
            native.close()
        for name, model in models:
            model.close()
            (f.e/(name+'-model.json')).write_text(json.dumps(model.requests, indent=2)+'\n')
        if workflow:
            (f.e/'workflow-calls.json').write_text(json.dumps(workflow.calls, indent=2)+'\n')
        if forge:
            forge.close()
        f.close()
    return result(args.evidence, checks, error)


if __name__ == '__main__':
    raise SystemExit(main())
