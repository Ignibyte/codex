"""Actual scoped supporting profiles, service authority and native boundaries."""
import argparse
import json
from pathlib import Path
import traceback
import uuid

from fleet_fixture import Fleet, GROUP, SERVICE, result
from native_fixture import Native
from session_fixture import Model
from manager_native_fixture import tool_specs


def main():
    parser = argparse.ArgumentParser()
    for key in ('binary', 'old-binary', 'evidence'):
        parser.add_argument('--' + key, required=True)
    parser.add_argument('--cli')
    args = parser.parse_args()
    fleet = Fleet(args.binary, args.evidence)
    checks, natives, models = [], [], []
    error = None
    try:
        for name, uid in [('bot', 65531), ('ops', 65530), ('outside', 65533), ('oldops', 65528)]:
            fleet.subjects[name] = {'uid': uid, 'seat': str(uuid.uuid4()), 'sequence': 0}
            fleet.sudo('install', '-d', '-o', uid, '-g', GROUP, '-m', '0700', fleet.base / name)
        allowed = fleet.subjects['receiver']['seat']
        core = [fleet.subjects[n]['seat'] for n in ('sender', 'receiver', 'bot', 'ops')]
        fleet.enroll('receiver', ['observe', 'queue', 'acknowledge'], role='manager', recipients=core)
        fleet.enroll('outside', ['observe'], role='observer')
        fleet.enroll('oldops', ['observe'], role='ops')
        for name, function in [('sender', 'designer'), ('bot', 'forge_bot'), ('ops', 'ops')]:
            fleet.enroll(name, ['observe', 'queue', 'acknowledge'], role='support',
                         recipients=[fleet.subjects[name]['seat'], allowed])
            grant = fleet.subjects[name]['grant']
            profile = {'function': function, 'grant': grant, 'project': 'fixture-project'}
            reply = json.loads(fleet.admin('support-profile', '--input', fleet.publish(profile)).stdout)
            assert reply['started'] is False and reply['recurringActivation'] is False
            assert reply['nativeOverrides']['sandbox_mode'] == (
                'workspace-write' if function == 'designer' else 'read-only')
            assert not any('model' in k or 'reasoning' in k for k in reply['nativeOverrides'])
            assert set(reply['contacts']) == set(grant['recipients'])
            (fleet.e / ('profile-' + name + '.json')).write_text(json.dumps(reply, indent=2))
            invalid = fleet.admin('support-profile', '--input', fleet.publish({**profile, 'execute': True}), expected=None)
            assert invalid.returncode and 'unknown field' in invalid.stderr
            envelope = fleet.envelope(name=name, recipient='receiver', text=(
                'Status: Supporting fixture\nState: ready\n'
                'Done: The scoped observation is ready.\nNext: Review this observation.'))
            fleet.send(envelope, name=name)
            assert fleet.receive(envelope['messageId'], name='receiver')['envelope']['sender'] == grant['seat']
            fleet.wire(name, {'method': 'cancel', 'protocol': 1,
                'request': fleet.request(name, 'queue', allowed),
                'message_id': envelope['messageId']}, expected='support_interrupt_denied')
            for operation in ('interrupt', 'approve', 'execute'):
                fleet.wire(name, {'method': 'peers', 'protocol': 1,
                    'request': fleet.request(name, operation)}, expected='operation_denied')
            denied = fleet.envelope(name=name, recipient='outside')
            fleet.wire(name, {'method': 'send', 'protocol': 1,
                'request': fleet.request(name, 'queue', denied['recipient']), 'envelope': denied}, expected='recipient_denied')
            request = fleet.request(name)
            request['project'] = 'other-project'
            fleet.wire(name, {'method': 'peers', 'protocol': 1, 'request': request}, expected='project_denied')
            fleet.wire(name, {'method': 'foreman_action', 'protocol': 1,
                'request': fleet.request(name, 'acknowledge'),
                'action': {'action': 'issue_spec', 'run_id': str(uuid.uuid4()), 'spec': None, 'lease_seconds': 60}},
                expected='foreman_caller_denied')
            catalog = fleet.run([*fleet.prefix(grant['agentUid']), fleet.binary, 'workflow-mcp',
                '--grant', fleet.publish(grant), '--socket', fleet.socket, '--service-uid', SERVICE,
                '--credential', fleet.subjects[name]['credential'], '--project', 'fixture-project'], expected=None)
            assert catalog.returncode and 'workflow_role_denied' in catalog.stderr
            if args.cli:
                model = Model()
                models.append(model)
                native = Native(fleet, args.cli, model, name=name,
                    sandbox=reply['nativeOverrides']['sandbox_mode'],
                    multi_agent=reply['nativeOverrides']['features']['multi_agent'])
                natives.append(native)
                peers = native.request('factory/peers', {'project': 'fixture-project'})['data']['peers']
                assert {p['seat'] for p in peers} == {grant['seat'], allowed}
                thread = native.request('thread/start', {
                    'cwd': str(native.home / 'project'),
                    'sandbox': reply['nativeOverrides']['sandbox_mode'],
                    'developerInstructions': reply['developerInstructions'],
                })['thread']['id']
                assert not model.requests
                native.request('factory/send', {
                    'messageId': str(uuid.uuid4()), 'conversationId': str(uuid.uuid4()),
                    'recipient': fleet.subjects['outside']['seat'], 'project': 'fixture-project',
                    'kind': 'note', 'mode': 'queue_only', 'text': 'Status: Scoped fixture\nState: ready',
                    'expiresInSeconds': 60,
                }, expected_error='recipient_denied')
                assert thread
                turn = native.request('turn/start', {'threadId': thread, 'input': [
                    {'type': 'text', 'text': 'Report your assigned function. Use no tools.'}]})['turn']['id']
                model.wait_request(0)
                names = {t['name'] for t in tool_specs(model.requests[0]['body']['tools'])}
                assert 'spawn_agent' not in names, names
                model.release(0)
                native.event('turn/completed', turn)
                incoming = fleet.envelope(name='receiver', recipient=name, mode='wake_when_idle',
                    text='Assignment: Scoped supporting function\nDo: Read this workflow fact and test the assigned artifact boundary.\nFact: The owned fixture requires review.\nLimits: Use only this assigned workspace.')
                fleet.send(incoming, name='receiver')
                artifact = native.home / 'project' / 'support-artifact.txt'
                code = 'from pathlib import Path; p=Path(' + repr(str(artifact)) + '); p.write_text("DESIGN READY\\n"); p.chmod(0o640); print(p.read_text())'
                peer_artifact = fleet.base / 'outside' / ('unexpected-' + function)
                code += '; peer=Path(' + repr(str(peer_artifact)) + ')\ntry: peer.write_text("UNEXPECTED")\nexcept PermissionError: print("PEER WRITE DENIED")\nelse: raise RuntimeError("peer_write_permitted")'
                import shlex
                model.outputs[1] = {'type': 'function_call', 'name': 'exec_command',
                    'call_id': 'profile_artifact_' + function,
                    'arguments': json.dumps({'cmd': 'python3 -c ' + shlex.quote(code), 'yield_time_ms': 1000})}
                started = native.request('thread/factoryMessage', {'threadId': thread,
                    'project': 'fixture-project', 'messageId': incoming['messageId']})['data']
                model.wait_request(1)
                authored = [item for item in model.requests[1]['body']['input']
                    if item.get('type') == 'agent_message' and item.get('id') == 'amsg_' + incoming['messageId']]
                assert len(authored) == 1 and incoming['text'] in ''.join(c.get('text', '') for c in authored[0]['content'])
                model.release(1)
                model.wait_request(2)
                model.release(2)
                native.event('turn/completed', started['turnId'])
                present = fleet.run(['sudo', '-n', 'test', '-f', artifact], expected=None).returncode == 0
                assert present == (function == 'designer'), {'function':function, 'artifactPresent':present}
                assert fleet.run(['sudo', '-n', 'test', '-f', peer_artifact], expected=None).returncode != 0
                reply_id = str(uuid.uuid4())
                native.request('factory/send', {'messageId': reply_id,
                    'conversationId': incoming['conversationId'], 'recipient': allowed,
                    'project': 'fixture-project', 'kind': 'reply', 'mode': 'queue_only',
                    'replyTo': incoming['messageId'], 'expiresInSeconds': 60,
                    'text': 'Result: Supporting fixture\nState: reviewed\nDone: The assigned fact and artifact boundary were checked.'})
                received = fleet.receive(reply_id, name='receiver')['envelope']
                assert received['sender'] == grant['seat'] and received['replyTo'] == incoming['messageId']
                checks.append(function + '_actual_native_catalog_sandbox_received_fact_and_authenticated_reply')
            checks.append(function + '_explicit_contacts_profile_and_service_authority')
        old = fleet.run([*fleet.prefix(SERVICE), Path(args.old_binary).resolve(),
                         'inspect', '--state', fleet.state], expected=None)
        assert old.returncode, 'The old bridge accepted new supporting role state.'
        oldops = fleet.request('oldops', 'queue', allowed)
        fleet.wire('oldops', {'method': 'peers', 'protocol': 1, 'request': oldops}, expected='operation_denied')
        for name in ('sender', 'bot', 'ops'):
            fleet.admin('revoke', '--state', fleet.state, '--seat', fleet.subjects[name]['seat'])
            fleet.wire(name, {'method': 'peers', 'protocol': 1,
                             'request': fleet.request(name)}, expected='credential_revoked')
            for other in ('sender', 'bot', 'ops'):
                if other == name:
                    continue
                seat = next(s for s in fleet.inspect()['seats'] if s['seat'] == fleet.subjects[other]['seat'])
                assert seat['revoked'] == (('sender', 'bot', 'ops').index(other) < ('sender', 'bot', 'ops').index(name))
        checks.append('independent_revocation_and_old_runtime_refusal')
    except Exception:
        error = traceback.format_exc()
    finally:
        for model in models:
            model.close()
        for native in reversed(natives):
            native.close()
        fleet.close()
    return result(args.evidence, checks, error)


if __name__ == '__main__':
    raise SystemExit(main())
