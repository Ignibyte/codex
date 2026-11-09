"""One actual native spec child, scripted provider, actual private Forge engine."""
import json
import time
import uuid

from fleet_fixture import GROUP, SERVICE
from native_fixture import Native
from session_fixture import Model


def tool_specs(value, namespace=None):
    if isinstance(value, dict):
        if value.get('type') == 'namespace':
            namespace = value['name']
        if value.get('type') == 'function' and isinstance(value.get('name'), str):
            yield {'name': value['name'], **({'namespace': namespace} if namespace else {})}
        for child in value.values():
            yield from tool_specs(child, namespace)
    elif isinstance(value, list):
        for child in value:
            yield from tool_specs(child, namespace)


def unwrap(value):
    for _ in range(8):
        if isinstance(value, str):
            if value.startswith('Wall time:') and '\nOutput:\n' in value:
                value = value.split('\nOutput:\n', 1)[1]
            value = json.loads(value)
        elif isinstance(value, list):
            value = next(x['text'] for x in value if isinstance(x, dict) and 'text' in x)
        elif 'structuredContent' in value:
            assert not value.get('isError'), value
            return value['structuredContent']
        elif 'content' in value:
            assert not value.get('isError'), value
            value = value['content']
        elif 'result' in value:
            value = value['result']
        else:
            return value
    raise AssertionError('The native tool response nesting exceeds the fixture bound.')


def steps(body=None):
    started = yield 'run-start', {}
    run = started['run_id']
    served = yield 'served-list', {'unanswered_only': True}
    for item in served['items']:
        reason = ('The source failed in this owned fixture. Historical research is missing. This is not a successful empty search.'
            if item['item_kind'] == 'source_error' else
            'This is owned fixture history. It supplies context for documentation only. It grants no production authority.')
        yield 'served-verdict', {'run_id': run, 'item_id': item['item_id'], 'verdict': 'relevant', 'reason': reason}
    advanced = yield 'phase-complete', {'run_id': run, 'phase': 'research'}
    assert advanced['ready'], advanced
    yield 'ticket-get', {}
    citations = yield 'run-calls', {'tool': 'ticket-get'}
    assert citations['flushed'] and citations['calls'], citations
    draft = yield 'spec-draft', {'run_id': run, 'type': 'docs', 'ui': False, 'schema': False, 'auth': False,
        'api': False, 'pipeline_infra': False, 'docs': True, 'tests_only': False,
        'scope': 'Document this owned native child fixture.',
        'done_when': 'The report contains the fixture source and result. It states the limits.',
        'evidence': 'The report cites the actual ticket-get call. Research source failures remain explicit.',
        'not_in_scope': 'No runtime change, real model judgment or production effect.',
        'body': body or '# Owned native child fixture\nDocument the actual isolated run. State that the provider is scripted.\n',
        'regression_suites': [], 'named_symbols': [], 'changed_symbols': [],
        'research_citations': [{'call_id': citations['calls'][0]['call_id'],
            'shows': 'The actual owned ticket requests documentation. It requests no application source change.'}]}
    for _ in range(12):
        status = yield 'run-status', {}
        if status['open_run'] is None:
            break
        phase = status['open_run']['phase']
        if phase == 'verify':
            yield 'checker-record', {'run_id': run, 'checker': 'premise-check', 'result': {'claims': [{
                'claim': 'The owned ticket requests documentation.', 'verdict': 'confirmed',
                'evidence': 'This deterministic provider uses the actual ticket-get response. This is not independent model judgment.'}]}}
        elif phase == 'challenge':
            yield 'checker-record', {'run_id': run, 'checker': 'skeptic', 'result': {
                'verdict': 'survives', 'attack': 'The fixture changes no runtime source. This deterministic assertion does not qualify model judgment.'}}
        completed = yield 'phase-complete', {'run_id': run, 'phase': phase}
        assert completed['ready'], completed
    else:
        raise AssertionError('The native child did not close its bounded spec graph.')
    sealed = yield 'spec-sealed', {}
    assert sealed['run_id'] == run and sealed['spec']['id'] == draft['spec']['id'], sealed
    return sealed


def drive(f, forge, workflow, cli, body=None, handoff=False):
    ticket = workflow.call('ticket-create', {'title': 'Native child documentation fixture',
        'description': 'Document the owned native spec child. Preserve its result and limits.',
        'type': 'docs', 'project_id': forge.project})['ticket']
    job = str(uuid.uuid4())
    forge_config, config = f.state / ('native-forge-' + job), f.state / ('native-manager-' + job)
    value = {'endpoint': forge.endpoint, 'forgeIdentity': 'factory-fixture', 'forgeProject': forge.project,
        'localProject': 'fixture-project', 'signerArgv': [str(forge.binary), 'sign', 'headers', '--name',
            'factory-fixture', '--key-dir', str(forge.key_dir), '--ttl', '60', '--session', 'cf-spec-' + job]}
    f.sudo('install', '-o', SERVICE, '-g', GROUP, '-m', '0600', f.publish(value), forge_config)
    value = {'id': job, 'bridgeState': str(f.state), 'forgeConfig': str(forge_config),
        'manager': f.subjects['sender']['seat'], 'foreman': f.subjects['receiver']['seat'], 'ticket': ticket['number'],
        'intent': {'id': str(uuid.uuid4()), 'reference': 'owned-fixture:CF-021',
            'text': 'Document this owned run in report.md. State that the provider is scripted. Return the report for review.'}}
    f.sudo('install', '-o', SERVICE, '-g', GROUP, '-m', '0600', f.publish(value), config)
    f.admin('manager-prepare', '--state', f.state / 'manager', '--config', config)
    model, native = Model(), None
    sealed, agent, seen, results, actions = None, None, set(), [], []
    generator, action, child_count, spawn_count = steps(body), None, 0, 0
    approved, approval_id = None, str(uuid.uuid4())
    try:
        native = Native(f, cli, model, name='sender', manager_job=job)
        thread = native.request('thread/start', {'cwd': str(native.home / 'project')})['thread']['id']
        turn = native.request('turn/start', {'threadId': thread, 'input': [{'type': 'text',
            'text': 'Assignment: CF-021 native parent\nDo: Start one local spec child. Wait for its sealed result.\nLimits: Use the owned fixture only.'}]})['turn']['id']
        deadline = time.monotonic() + 100
        while True:
            native.poll(.02)
            for index, entry in enumerate(model.requests):
                if index in seen:
                    continue
                seen.add(index)
                body = entry['body']
                catalog = list(tool_specs(body['tools']))
                users = [v for v in body['input'] if v.get('role') == 'user']
                child = any('CF021_SPEC_CHILD' in json.dumps(user) for user in users)
                if child:
                    child_count += 1
                    if action is not None:
                        output = next(v['output'] for v in reversed(body['input'])
                            if v.get('call_id') == 'manager-child-' + str(child_count - 1)
                            and v.get('type') == 'function_call_output')
                        value = unwrap(output)
                        results.append({'tool': action[0], 'response': value})
                        try:
                            action = generator.send(value)
                        except StopIteration as done:
                            sealed, action = done.value, None
                    else:
                        action = next(generator)
                    if sealed is not None:
                        model.outputs[index] = {'type': 'message', 'role': 'assistant', 'id': 'sealed-child',
                            'content': [{'type': 'output_text', 'text': 'Handoff: CF-021\nWorks: The owned spec sealed.\nEvidence: ' + sealed['spec']['id'] + '\nLimits: The provider is scripted.'}]}
                    else:
                        tool, arguments = action
                        candidates = [t for t in catalog if t['name'].endswith(tool) or t['name'].endswith(tool.replace('-', '_'))]
                        assert len(candidates) == 1, {'tool': tool, 'catalogue': catalog}
                        model.outputs[index] = {'type': 'function_call', **candidates[0],
                            'call_id': 'manager-child-' + str(child_count),
                            'arguments': json.dumps({**arguments, '_factory_operation_id': str(uuid.uuid4())})}
                        actions.append(tool)
                elif spawn_count == 0:
                    spawn = next(t for t in catalog if t['name'] == 'spawn_agent')
                    model.outputs[index] = {'type': 'function_call', **spawn, 'call_id': 'spec-child-spawn',
                        'arguments': json.dumps({'message': 'Assignment: CF021_SPEC_CHILD\nDo: Research the owned ticket. Draft the spec. Record self-checks. Seal the spec.\nDone when: Forge returns the sealed spec.\nLimits: Use only the scoped manager tools. The provider is scripted.', 'fork_context': False})}
                    spawn_count += 1
                else:
                    if agent is None:
                        output = next(v['output'] for v in body['input'] if v.get('call_id') == 'spec-child-spawn'
                            and v.get('type') == 'function_call_output')
                        agent = unwrap(output)['agent_id']
                    if sealed is None:
                        wait = next(t for t in catalog if t['name'] == 'wait_agent')
                        model.outputs[index] = {'type': 'function_call', **wait, 'call_id': 'wait-' + str(index),
                            'arguments': json.dumps({'targets': [agent], 'timeout_ms': 10000})}
                    elif handoff and approved is None:
                        previous = next((v for v in body['input'] if v.get('call_id') == 'parent-handoff'
                            and v.get('type') == 'function_call_output'), None)
                        if previous:
                            approved = unwrap(previous['output'])
                            assert approved['state'] == 'queued', approved
                        else:
                            tool = next(t for t in catalog if t['name'].endswith('factory_handoff'))
                            model.outputs[index] = {'type': 'function_call', **tool, 'call_id': 'parent-handoff',
                                'arguments': json.dumps({'operation_id': approval_id})}
                            model.release(index)
                            continue
                        model.outputs[index] = {'type': 'message', 'role': 'assistant', 'id': 'approved-manager-return',
                            'content': [{'type': 'output_text', 'text': 'Report: The manager approved the sealed spec.\nTicket: CF-021\nEvidence: ' + sealed['spec']['id'] + '\nNeeds: Foreman assignment'}]}
                    else:
                        model.outputs[index] = {'type': 'message', 'role': 'assistant', 'id': 'manager-return',
                            'content': [{'type': 'output_text', 'text': 'Report: The owned child returned a sealed spec.\nTicket: CF-021\nEvidence: ' + sealed['spec']['id'] + '\nNeeds: Nothing'}]}
                model.release(index)
            done = any(e.get('method') == 'turn/completed' and e.get('params', {}).get('turn', {}).get('id') == turn
                for e in native.events)
            if done and sealed is not None:
                break
            assert time.monotonic() < deadline, {'actions': actions, 'child': agent, 'requests': len(model.requests)}
        assert spawn_count == 1 and agent is not None
        child_thread = native.request('thread/read', {'threadId': agent, 'includeTurns': True})['thread']
        source = child_thread['source']
        assert thread in json.dumps(source), source
        assert {'run-start', 'served-list', 'spec-draft', 'checker-record', 'phase-complete', 'spec-sealed'} <= set(actions)
        return {'jobId': job, 'parentThread': thread, 'childThread': agent, 'source': source,
            'actions': actions, 'sealedSpec': sealed['spec']['id'], 'modelRequests': len(model.requests),
            'provider': 'scripted', 'independentModelJudgment': False, 'handoff': approved}
    finally:
        if native:
            native.close()
        model.close()
        (f.e / 'native-manager-model.json').write_text(json.dumps(model.requests, indent=2) + '\n')
        (f.e / 'native-manager-tools.json').write_text(json.dumps(results, indent=2) + '\n')
