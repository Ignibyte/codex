#!/usr/bin/env python3
"""Private real Forge spec engine through authenticated executive-only bridge calls."""
import argparse
import hashlib
import json
from pathlib import Path
import traceback
import select
import subprocess
import sys
import uuid

from fleet_fixture import Fleet, GROUP, SERVICE, result
from forge_workflow_fixture import Workflow
from real_forge_fixture import ForgeProxy, RealForge


def main():
    p = argparse.ArgumentParser()
    for key in ('binary', 'forge-binary', 'forge-source', 'evidence'):
        p.add_argument('--' + key, required=True)
    p.add_argument("--cli")
    a = p.parse_args()
    f, forge, checks, error = Fleet(a.binary, a.evidence), None, [], None
    calls, mcp, proxy = [], None, None
    try:
        for name, role in [('sender', 'manager'), ('receiver', 'foreman')]:
            f.enroll(name, ['observe', 'queue', 'acknowledge'], role=role)
        forge = RealForge(f, a.forge_binary, a.forge_source)
        w = Workflow(f, forge)
        ticket = w.call('ticket-create', {'title': 'Isolated executive documentation specification',
            'description': 'Document the owned fixture and state its limitations. No application source changes.',
            'type': 'docs', 'project_id': forge.project})['ticket']
        job = str(uuid.uuid4())
        forge_config = f.state / 'manager-forge.json'
        value = {'endpoint': forge.endpoint, 'forgeIdentity': 'factory-fixture',
            'forgeProject': forge.project, 'localProject': 'fixture-project',
            'signerArgv': [str(forge.binary), 'sign', 'headers', '--name', 'factory-fixture',
                '--key-dir', str(forge.key_dir), '--ttl', '60', '--session', 'cf-spec-' + job]}
        f.sudo('install', '-o', SERVICE, '-g', GROUP, '-m', '0600', f.publish(value), forge_config)
        config = f.state / 'manager-job.json'
        value = {'id': job, 'bridgeState': str(f.state), 'forgeConfig': str(forge_config),
            'manager': f.subjects['sender']['seat'], 'foreman': f.subjects['receiver']['seat'],
            'ticket': ticket['number'], 'intent': {'id': str(uuid.uuid4()), 'reference': 'owned-fixture/intent',
                'text': 'Document the owned fixture. Preserve its results and limits.'}}
        f.sudo('install', '-o', SERVICE, '-g', GROUP, '-m', '0600', f.publish(value), config)
        prepare = ['manager-prepare', '--state', f.state / 'manager', '--config', config]
        assert json.loads(f.admin(*prepare).stdout)['prepared']
        assert json.loads(f.admin(*prepare).stdout)['duplicate']

        def wire(method, name='sender', expected=None, **fields):
            response = f.wire(name, {'method': method, 'protocol': 1,
                'request': f.request(name, 'queue' if method == 'manager_handoff' else 'observe' if method in ('manager_catalogue', 'manager_read', 'manager_operation') else 'acknowledge'),
                'job_id': job, **fields}, expected=expected)
            # The service consumes admission sequence before a scoped-tool refusal.
            f.subjects[name]['sequence'] = next(s['lastSequence'] for s in f.inspect()['seats']
                if s['seat'] == f.subjects[name]['seat'])
            return response

        def call(tool, args=None, expected=None, tool_error=False, operation=None):
            response = wire('manager_spec', operation_id=operation or str(uuid.uuid4()), tool=tool,
                            arguments=args or {}, expected=expected)
            calls.append({'tool': tool, 'arguments': args or {}, 'response': response})
            if expected:
                return response
            assert response['state'] == 'returned', response
            raw = response['response']
            if raw is None:
                data = bytearray()
                while len(data) < response['bytes']:
                    chunk = wire('manager_read', operation_id=response['responseId'], offset=len(data))
                    data.extend(bytes.fromhex(chunk['hex']))
                import hashlib
                assert hashlib.sha256(data).hexdigest() == response['sha256']
                raw = json.loads(data)
            assert bool(raw.get('isError')) == tool_error, raw
            return raw.get('structuredContent') or json.loads(raw['content'][0]['text'])

        catalogue, offset = [], 0
        while True:
            page = wire('manager_catalogue', offset=offset)
            catalogue.extend(page['tools'])
            if page['next'] is None:
                break
            offset = page['next']
        names = {t['name'] for t in catalogue}
        assert {'run-start', 'spec-draft', 'phase-complete', 'spec-sealed'} <= names
        assert not {'pipeline-run-start', 'pipeline-claim', 'ticket-create', 'sql-query'} & names
        wire('manager_catalogue', name='receiver', expected='manager_caller_denied')
        call('pipeline-run-start', expected='manager_tool_denied')
        call('run-start', {'graph': 'code'}, expected='manager_graph_denied')
        call('ticket-get', {'ticket_number': ticket['number'] + 1}, expected='manager_ticket_scope_denied')
        call('spec-draft', {'run_id': str(uuid.uuid4())}, expected='manager_run_not_started')
        call('spec-sealed', tool_error=True)
        checks.append('actual_forge_catalogue_is_filtered_and_foreman_dispatch_foreign_ticket_unsealed_paths_refuse')

        operation = str(uuid.uuid4())
        started = call('run-start', operation=operation)
        assert call('run-start', operation=operation) == started
        assert wire('manager_operation', operation_id=operation)['state'] == 'returned'
        assert wire('manager_operation', operation_id=str(uuid.uuid4()))['state'] == 'not_submitted'
        call('run-start', {'served': 'manifest'}, operation=operation, expected='manager_operation_conflict')
        call('run-start', expected='manager_run_start_already_attempted')
        run = started['run_id']
        wire('manager_handoff', operation_id=str(uuid.uuid4()), expected='manager_spec_not_current')
        call('run-note', {'run_id': str(uuid.uuid4()), 'note': 'Reject another run.'},
             expected='manager_run_scope_denied')
        checks.append('run_start_is_one_attempt_duplicate_operation_is_cached_and_foreign_run_is_denied')

        refused = call('phase-complete', {'run_id': run, 'phase': 'research'})
        assert not refused['ready'], refused
        served = call('served-list', {'unanswered_only': True})
        for item in served['items']:
            assert item['item_kind'] == 'source_error', item
            call('served-verdict', {'run_id': run, 'item_id': item['item_id'], 'verdict': 'relevant',
                'reason': 'This owned empty project has no distinctive ticket history. Research is missing; it is not a no-hit result.'})
        advanced = call('phase-complete', {'run_id': run, 'phase': 'research'})
        assert advanced['ready'], advanced
        call('ticket-get')
        citations = call('run-calls', {'tool': 'ticket-get'})
        assert citations['flushed'] and citations['calls'], citations
        grant_path = f.publish(f.subjects['sender']['grant'])
        command = [*f.prefix(f.subjects['sender']['uid']), f.binary, 'manager-mcp',
            '--grant', grant_path, '--socket', f.socket, '--service-uid', str(SERVICE),
            '--credential', f.subjects['sender']['credential'], '--project', 'fixture-project', '--job-id', job]
        mcp = subprocess.Popen(list(map(str, command)), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, text=True)
        f.commands.append({'command': list(map(str, command))})
        def rpc(method, params):
            rid = str(uuid.uuid4())
            mcp.stdin.write(json.dumps({'jsonrpc': '2.0', 'id': rid, 'method': method, 'params': params}) + '\n')
            mcp.stdin.flush()
            assert select.select([mcp.stdout], [], [], 15)[0], 'The scoped manager MCP did not answer.'
            response = json.loads(mcp.stdout.readline())
            assert response['id'] == rid and 'error' not in response, response
            return response['result']
        rpc('initialize', {'protocolVersion': '2025-06-18'})
        assert all('_factory_operation_id' in t['inputSchema']['required'] for t in rpc('tools/list', {})['tools'] if t['name'] not in ('factory_operation_status', 'factory_handoff', 'factory_read_result'))
        def mcp_call(tool, arguments, operation=None):
            response = rpc('tools/call', {'name': tool, 'arguments': {
                **arguments, '_factory_operation_id': operation or str(uuid.uuid4())}})
            assert not response.get('isError'), response
            f.subjects['sender']['sequence'] = next(s['lastSequence'] for s in f.inspect()['seats']
                if s['seat'] == f.subjects['sender']['seat'])
            return response.get('structuredContent') or json.loads(response['content'][0]['text'])
        draft = {'run_id': run, 'type': 'docs', 'ui': False, 'schema': False, 'auth': False,
            'api': False, 'pipeline_infra': False, 'docs': True, 'tests_only': False,
            'scope': 'Document the owned fixture result and its missing history.',
            'done_when': 'The fixture documentation states source, command, result and limitations.',
            'evidence': 'The actual ticket-get response and source-error research entries are retained.',
            'not_in_scope': 'No application change, external service, real model or deployment.',
            'body': '# Fixture documentation\n' + ('Record the owned fixture. Missing historical research remains missing.\n' * 1600),
            'regression_suites': [], 'named_symbols': [], 'changed_symbols': [],
            'research_citations': [{'call_id': citations['calls'][0]['call_id'],
                'shows': 'The owned ticket requests documentation only and no application source changes.'}]}
        draft_operation = str(uuid.uuid4())
        spec = mcp_call('spec-draft', draft, draft_operation)
        assert mcp_call('spec-draft', draft, draft_operation) == spec
        assert len(spec['spec']['body']) > 98000
        checks.append('actual_mcp_large_spec_chunk_upload_response_digest_and_duplicate_replay_preserve_full_body')
        assert spec['spec']['run_id'] == run
        phases = []
        for _ in range(12):
            status = call('run-status')
            if status['open_run'] is None:
                break
            phase = status['open_run']['phase']
            phases.append(phase)
            if phase == 'verify':
                call('checker-record', {'run_id': run, 'checker': 'premise-check', 'result': {'claims': [{
                    'claim': 'The fixture ticket asks for documentation only.', 'verdict': 'confirmed',
                    'evidence': 'This deterministic fixture uses the actual ticket-get response, not independent model review.'}]}})
            elif phase == 'challenge':
                call('checker-record', {'run_id': run, 'checker': 'skeptic', 'result': {
                    'verdict': 'survives', 'attack': 'Fixture assertion only: no runtime behavior is changed. This does not qualify native review judgment.'}})
            completed = call('phase-complete', {'run_id': run, 'phase': phase})
            assert completed['ready'], completed
        else:
            raise AssertionError('The canonical spec engine did not close within its bounded phases.')
        sealed = mcp_call('spec-sealed', {})
        assert sealed['run_id'] == run and sealed['spec']['id'] == spec['spec']['id'], sealed
        assert {'draft', 'verify', 'challenge', 'seal'} <= set(phases), phases
        approval_id = str(uuid.uuid4())
        approved_wire = wire('manager_handoff', operation_id=approval_id)
        approved_raw = rpc('tools/call', {'name': 'factory_handoff', 'arguments': {'operation_id': approval_id}})
        assert not approved_raw.get('isError'), approved_raw
        approved = approved_raw['structuredContent']
        assert approved == approved_wire
        assert approved['state'] == 'queued' and approved['binding']['specId'] == sealed['spec']['id'], approved
        f.subjects['sender']['sequence'] = next(s['lastSequence'] for s in f.inspect()['seats']
            if s['seat'] == f.subjects['sender']['seat'])
        assert wire('manager_handoff', operation_id=approval_id) == approved
        wire('manager_handoff', operation_id=str(uuid.uuid4()), expected='manager_handoff_operation_conflict')
        wire('manager_handoff', operation_id=operation, expected='manager_operation_conflict')
        delivered = f.receive(approval_id, name='receiver')
        assert delivered['envelope']['sender'] == f.subjects['sender']['seat']
        assert delivered['envelope']['assignmentId'] is None
        assert delivered['envelope']['kind'] == 'note' and delivered['envelope']['artifactRefs']
        assert len(delivered['envelope']['text']) < 1024 and spec['spec']['body'] not in delivered['envelope']['text']
        raw = f.admin('inspect', '--state', f.state)
        artifact_path = f.state / 'manager' / job / 'approved-spec/registry.json'
        artifact = f.sudo('cat', '--', artifact_path).stdout.encode()
        assert len(artifact) == approved['binding']['bytes']
        assert hashlib.sha256(artifact).hexdigest() == approved['binding']['sha256']
        denied = f.run([*f.prefix(f.subjects['receiver']['uid']), 'cat', '--', artifact_path], expected=1)
        assert denied.returncode == 1
        (f.e / 'approved-handoff.json').write_text(json.dumps({'approved': approved, 'received': delivered}, indent=2) + '\n')
        checks.append('authenticated_manager_approves_one_current_seal_and_queues_an_idempotent_versioned_foreman_reference')
        f.stop()
        f.start()
        assert call('spec-sealed') == sealed
        (f.e / 'canonical-spec.json').write_text(json.dumps({'started': started, 'phases': phases,
            'sealed': sealed, 'status': call('spec-status')}, indent=2) + '\n')
        checks.append('actual_engine_research_draft_verify_challenge_seal_and_restart_preserve_the_canonical_spec')
        checks.append('source_errors_remain_missing_research_and_fixture_checker_records_are_not_native_review_claims')
        if a.cli:
            mcp.stdin.close()
            assert mcp.wait(timeout=10) == 0
            mcp.stdout.close()
            mcp.stderr.close()
            mcp = None
            from manager_native_fixture import drive
            native_proof = drive(f, forge, w, a.cli)
            (f.e / 'native-child-proof.json').write_text(json.dumps(native_proof, indent=2) + '\n')
            checks.append('one_actual_native_child_keeps_upstream_lineage_and_context_through_all_canonical_spec_phases')
        proxy = ForgeProxy(forge.endpoint)
        proxy.mode = 'lost_spec_ack'
        uncertain_job, uncertain_operation = str(uuid.uuid4()), str(uuid.uuid4())
        target = w.call('ticket-create', {'title': 'Lost executive start acknowledgement fixture',
            'description': 'Test a single actual canonical start when its HTTP acknowledgement is lost.',
            'type': 'docs', 'project_id': forge.project})['ticket']
        forge_config = f.state / ('uncertain-forge-' + uncertain_job)
        value = {'endpoint': proxy.endpoint, 'forgeIdentity': 'factory-fixture',
            'forgeProject': forge.project, 'localProject': 'fixture-project',
            'signerArgv': [str(forge.binary), 'sign', 'headers', '--name', 'factory-fixture',
                '--key-dir', str(forge.key_dir), '--ttl', '60', '--session', 'cf-spec-' + uncertain_job]}
        f.sudo('install', '-o', SERVICE, '-g', GROUP, '-m', '0600', f.publish(value), forge_config)
        config = f.state / ('uncertain-manager-' + uncertain_job)
        value = {'id': uncertain_job, 'bridgeState': str(f.state), 'forgeConfig': str(forge_config),
            'manager': f.subjects['sender']['seat'], 'foreman': f.subjects['receiver']['seat'], 'ticket': target['number']}
        f.sudo('install', '-o', SERVICE, '-g', GROUP, '-m', '0600', f.publish(value), config)
        f.admin('manager-prepare', '--state', f.state / 'manager', '--config', config)
        job = uncertain_job
        f.subjects['sender']['sequence'] = next(s['lastSequence'] for s in f.inspect()['seats']
            if s['seat'] == f.subjects['sender']['seat'])
        start_frame = {'method': 'manager_spec', 'protocol': 1, 'request': f.request('sender', 'acknowledge'),
            'job_id': job, 'operation_id': uncertain_operation, 'tool': 'run-start', 'arguments': {}}
        actor = f.subjects['sender']
        lost = f.run([*f.prefix(actor['uid']), sys.executable, f.helper, '--socket', f.socket,
            '--credential', actor['credential'], '--wire', f.publish(start_frame)])
        lost_response = json.loads(lost.stdout)
        (f.e / 'lost-start-response.json').write_text(json.dumps(lost_response, indent=2) + '\n')
        assert not lost_response['ok'], lost_response
        assert sum(x['method'] == 'run-start' for x in proxy.forwarded) == 1
        f.stop()
        f.start()
        actor['sequence'] = next(s['lastSequence'] for s in f.inspect()['seats'] if s['seat'] == actor['seat'])
        receipt = wire('manager_operation', operation_id=uncertain_operation)
        assert receipt['state'] == 'uncertain'
        replay = wire('manager_spec', operation_id=uncertain_operation, tool='run-start', arguments={})
        assert replay['state'] == 'uncertain' and sum(x['method'] == 'run-start' for x in proxy.forwarded) == 1
        actual = call('run-status')
        assert actual['open_run']['ticket']['number'] == target['number']
        call('run-start', expected='manager_run_start_already_attempted')
        assert sum(x['method'] == 'run-start' for x in proxy.forwarded) == 1
        (f.e / 'uncertain-start.json').write_text(json.dumps({'receipt': receipt, 'replay': replay,
            'actual': actual, 'forwarded': proxy.forwarded}, indent=2) + '\n')
        checks.append('lost_actual_forge_start_ack_survives_restart_and_replay_never_resubmits_the_write')
    except Exception:
        error = traceback.format_exc()
    finally:
        if mcp is not None:
            mcp.stdin.close()
            code = mcp.wait(timeout=10)
            stderr = mcp.stderr.read()
            mcp.stdout.close()
            mcp.stderr.close()
            f.commands.append({'manager_mcp_exit': code, 'stderr': stderr})
        (f.e / 'manager-calls.json').write_text(json.dumps(calls, indent=2) + '\n')
        if proxy:
            (f.e / 'fault-proxy-forwarded.json').write_text(json.dumps(proxy.forwarded, indent=2) + '\n')
            proxy.close()
        if forge:
            forge.close()
        f.close()
    return result(a.evidence, checks, error)


if __name__ == '__main__':
    raise SystemExit(main())
