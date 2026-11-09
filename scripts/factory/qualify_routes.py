#!/usr/bin/env python3
"""Actual bridge/native core roster contacts and independent temporary routes."""
import argparse
import json
from pathlib import Path
import time
import traceback
import uuid

from fleet_fixture import Fleet, GROUP, result
from native_fixture import Native
from session_fixture import Model


def main():
    parser = argparse.ArgumentParser()
    for name in ('binary', 'cli', 'evidence'):
        parser.add_argument('--' + name, required=True)
    parser.add_argument('--old-binary')
    args = parser.parse_args()
    fleet, model = Fleet(args.old_binary or args.binary, args.evidence), Model()
    natives, checks, error = [], [], None
    try:
        workers = ['worker' + str(i) for i in range(4)]
        for i, name in enumerate(workers):
            fleet.subjects[name] = {'uid': 65531 - i, 'seat': str(uuid.uuid4()), 'sequence': 0}
            fleet.sudo('install', '-d', '-o', 65531 - i, '-g', GROUP, '-m', '0700', fleet.base / name)
        contacts = {'sender': ['receiver'], 'receiver': ['sender', *workers],
                    **{name: ['receiver'] for name in workers}}
        for name, peers in contacts.items():
            fleet.enroll(name, ['observe', 'queue', 'acknowledge'],
                         role='manager' if name == 'sender' else 'foreman' if name == 'receiver' else 'worker',
                         recipients=[fleet.subjects[p]['seat'] for p in [name, *peers]])
        edges = []
        for sender in contacts:
            for recipient in contacts:
                if sender == recipient:
                    continue
                envelope = fleet.envelope(sender, recipient)
                allowed = recipient in contacts[sender]
                fleet.send(envelope, name=sender, **({} if allowed else {'expected': 'recipient_denied'}))
                if allowed:
                    received = fleet.receive(envelope['messageId'], name=recipient)
                    assert received['envelope']['sender'] == fleet.subjects[sender]['seat']
                edges.append({'sender': sender, 'recipient': recipient, 'allowed': allowed})
        assert len(edges) == 30 and sum(e['allowed'] for e in edges) == 10
        checks.append('six_core_seats_test_all_30_directed_routes_10_allowed_20_denied')

        if args.old_binary:
            fleet.stop()
            fleet.sudo('install', '-o', 65534, '-g', GROUP, '-m', '0555', Path(args.binary).resolve(), fleet.binary)
            fleet.start()
            fleet.wire('sender', {'method': 'peers', 'protocol': 1, 'request': fleet.request('sender')})
            downgrade = fleet.run([*fleet.prefix(65534), Path(args.old_binary).resolve(),
                                  'inspect', '--state', fleet.state], expected=1)
            assert 'storage_version_unsupported' in downgrade.stderr or 'unknown field' in downgrade.stderr
            checks.append('actual_previous_bridge_state_upgrades_and_old_binary_refuses_the_new_registry')

        actor, target = fleet.subjects['worker0'], fleet.subjects['worker1']
        conversation = str(uuid.uuid4())
        request = {'id': str(uuid.uuid4()), 'sender': actor['seat'], 'recipient': target['seat'],
                   'project': 'fixture-project', 'conversation_id': conversation,
                   'ttl_seconds': 60, 'reason': 'Coordinate one shared interface in this task conversation.'}

        def grant(value=request, expected=0):
            response = fleet.admin('route-grant', '--state', fleet.state, '--input', fleet.publish(value), expected=expected)
            return json.loads(response.stdout) if expected == 0 else response

        issued = grant()
        assert grant()['route']['expiresAt'] == issued['route']['expiresAt']
        conflict = grant({**request, 'reason': 'A different purpose.'}, expected=1)
        assert 'route_id_conflict' in conflict.stderr
        denied = fleet.run([*fleet.prefix(actor['uid']), fleet.binary, 'route-grant', '--state', fleet.state,
                           '--input', fleet.publish({**request, 'id': str(uuid.uuid4())})], expected=1)
        assert denied.returncode == 1
        fleet.wire('worker0', {'method': 'route_grant', 'protocol': 1,
                   'request': fleet.request('worker0'), 'route': request}, expected='invalid_request')
        checks.append('operator_only_route_grant_has_reason_scope_idempotency_and_no_self_grant_wire_method')

        wrong_task = fleet.envelope('worker0', 'worker1')
        fleet.send(wrong_task, name='worker0', expected='route_task_denied')
        reply = fleet.envelope('worker1', 'worker0', conversationId=conversation)
        fleet.send(reply, name='worker1', expected='recipient_denied')
        for operation in ('interrupt', 'approve', 'execute'):
            fleet.wire('worker0', {'method': 'peers', 'protocol': 1,
                'request': fleet.request('worker0', operation, target['seat'])}, expected='recipient_denied')
        checks.append('exception_is_one_direction_one_conversation_and_grants_no_interrupt_approval_or_execution')

        cli = Native(fleet, args.cli, model, name='worker0')
        natives.append(cli)
        peers = cli.request('factory/peers', {'project': 'fixture-project'})['data']['peers']
        assert {p['seat'] for p in peers} == {
            actor['seat'], target['seat'], fleet.subjects['receiver']['seat']}
        peer = Native(fleet, args.cli, model, name='worker1')
        natives.append(peer)
        message = {'messageId': str(uuid.uuid4()), 'conversationId': conversation,
            'recipient': target['seat'], 'project': 'fixture-project', 'kind': 'clarification',
            'mode': 'queue_only', 'text': 'Question: CF-019\nNeed: Confirm the shared interface.',
            'replyTo': None, 'expiresInSeconds': 60}
        sent = cli.request('factory/send', message)['data']
        assert sent['state'] == 'service_accepted', sent
        thread = peer.request('thread/start', {'cwd': str(peer.home / 'project')})['thread']['id']
        received = peer.request('thread/factoryMessage', {'threadId': thread, 'project': 'fixture-project',
                                                        'messageId': message['messageId']})['data']
        assert received['state'] == 'queue_only' and not model.requests
        status = cli.request('factory/status', {'project': 'fixture-project',
                                               'messageId': message['messageId']})['data']
        assert status['state'] == 'delivered' and 'envelope' not in status
        actor['sequence'] = next(s['lastSequence'] for s in fleet.inspect()['seats'] if s['seat'] == actor['seat'])
        target['sequence'] = next(s['lastSequence'] for s in fleet.inspect()['seats'] if s['seat'] == target['seat'])
        checks.append('actual_native_client_uses_temporary_task_route_without_a_model_turn_or_new_binding')

        pending = fleet.envelope('worker0', 'worker1', conversationId=conversation)
        fleet.send(pending, name='worker0')
        waiting = fleet.envelope('worker0', 'worker1', conversationId=conversation)
        held, release, _command = fleet.wire('worker0', {'method': 'send', 'protocol': 1,
            'request': fleet.request('worker0', 'queue', target['seat']), 'envelope': waiting}, held=True)
        revoked = json.loads(fleet.admin('route-revoke', '--state', fleet.state, '--id', request['id'],
                                        '--reason', 'The interface discussion is complete.').stdout)
        assert revoked['pendingQuarantined'] == 1 and revoked['deliveredRequiresReconciliation'] == 1, revoked
        fleet.run([*fleet.prefix(actor['uid']), 'touch', release])
        stdout, stderr = held.communicate(timeout=5)
        assert held.returncode == 0 and not stderr and json.loads(stdout)['error'] == 'recipient_denied'
        fleet.send(fleet.envelope('worker0', 'worker1', conversationId=conversation),
                   name='worker0', expected='recipient_denied')
        fleet.receive(pending['messageId'], name='worker1', expected='route_inactive')
        cli.request('factory/send', {**message, 'messageId': str(uuid.uuid4())}, expected_error='recipient_denied')
        actor['sequence'] = next(s['lastSequence'] for s in fleet.inspect()['seats'] if s['seat'] == actor['seat'])
        ordinary = fleet.envelope('worker0', 'receiver')
        fleet.send(ordinary, name='worker0')
        fleet.receive(ordinary['messageId'], name='receiver')
        checks.append('route_revocation_quarantines_pending_marks_delivered_and_keeps_foreman_contact_live')

        temporary = {**request, 'id': str(uuid.uuid4()), 'conversation_id': None, 'ttl_seconds': 2}
        expiration = grant(temporary)['route']['expiresAt']
        expiring = fleet.envelope('worker0', 'worker1')
        fleet.send(expiring, name='worker0')
        fleet.stop()
        fleet.start()
        while time.time() <= expiration:
            time.sleep(min(.1, expiration - time.time() + .05))
        fleet.send(fleet.envelope('worker0', 'worker1'), name='worker0', expected='recipient_denied')
        fleet.receive(expiring['messageId'], name='worker1', expected='route_inactive')
        assert grant(temporary)['route']['expiresAt'] == expiration
        checks.append('restart_preserves_exact_expiry_and_replayed_grant_never_renews_permission')
        snapshot = fleet.inspect()
        assert any(e['event']['kind'] == 'routes_expired' for e in snapshot['audit'])
        (fleet.e / 'route-matrix.json').write_text(json.dumps(edges, indent=2) + '\n')
        (fleet.e / 'policy-result.json').write_text(json.dumps({'issued': issued, 'revoked': revoked,
            'expiry': expiration, 'checkedAfter': time.time(), 'audit': snapshot['audit']}, indent=2) + '\n')
    except Exception:
        error = traceback.format_exc()
    finally:
        for native in reversed(natives):
            native.close()
        model.close()
        fleet.close()
    return result(args.evidence, checks, error)


if __name__ == '__main__':
    raise SystemExit(main())
