#!/usr/bin/env python3
"""Own a bounded factory-tool session for the existing desktop conversation.

No remote host, pairing, real login, vendor request or app restart is needed.
The desktop agent invokes request in separate tool calls against one live owner.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import signal
import sys
import time
import traceback
import uuid

from fleet_fixture import Fleet, GROUP, result
from native_fixture import Native
from qualify_observation import Observer
from session_fixture import Model, incarnation


def write(path, value):
    temporary = path.with_name(path.name + '.tmp-' + str(uuid.uuid4()))
    temporary.write_text(json.dumps(value, indent=2) + '\n')
    temporary.chmod(0o600)
    temporary.replace(path)


def request(args):
    evidence = Path(args.evidence).resolve()
    ready = json.loads((evidence / 'ready.json').read_text())
    owner = ready['owner']
    assert incarnation(owner['pid']) == [owner['bootId'], owner['startTicks']], 'owner_not_live'
    name = str(uuid.uuid4()) + '.json'
    command = {'run': ready['run'], 'action': args.action}
    write(evidence / 'requests' / name, command)
    deadline = time.monotonic() + 25
    response = evidence / 'responses' / name
    while not response.exists():
        assert incarnation(owner['pid']) == [owner['bootId'], owner['startTicks']], 'owner_stopped'
        assert time.monotonic() < deadline, 'request_timeout'
        time.sleep(.05)
    value = json.loads(response.read_text())
    print(json.dumps(value, indent=2), flush=True)
    return 0 if value['ok'] else 1


def serve(args):
    evidence = Path(args.evidence).resolve()
    fleet = Fleet(args.binary, evidence)
    model = Model()
    native = observer = None
    checks, error, actions = [], None, []
    stopping = False
    run = str(uuid.uuid4())
    owner = {'pid': os.getpid(), 'bootId': incarnation(os.getpid())[0],
             'startTicks': incarnation(os.getpid())[1]}

    def stop_signal(_number, _frame):
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, stop_signal)
    signal.signal(signal.SIGINT, stop_signal)
    try:
        for directory in ('requests', 'responses'):
            (evidence / directory).mkdir(mode=0o700)
        fleet.subjects['observer'] = {'uid': 65531, 'seat': str(uuid.uuid4()), 'sequence': 0}
        fleet.sudo('install', '-d', '-o', 65531, '-g', GROUP, '-m', '0700', fleet.base / 'observer')
        if args.manager_control:
            fleet.subjects['contender'] = {'uid': 65530, 'seat': str(uuid.uuid4()), 'sequence': 0}
            fleet.sudo('install', '-d', '-o', 65530, '-g', GROUP, '-m', '0700', fleet.base / 'contender')
        manager, foreman = fleet.subjects['sender'], fleet.subjects['receiver']
        fleet.enroll('sender', ['observe', 'queue', 'acknowledge'], role='controller' if args.manager_control else 'manager',
                     recipients=[manager['seat'], foreman['seat']])
        fleet.enroll('receiver', ['observe', 'queue', 'acknowledge'], role='manager' if args.manager_control else 'foreman',
                     recipients=[manager['seat'], foreman['seat']])
        fleet.enroll('observer', ['observe'], role='observer',
                     recipients=[fleet.subjects['observer']['seat'], foreman['seat']])
        if args.manager_control:
            fleet.enroll('contender', ['observe', 'queue', 'acknowledge'], role='controller',
                recipients=[fleet.subjects['contender']['seat'], foreman['seat']])
        native = Native(fleet, args.cli, model, supervised=True)
        observer = Observer(fleet)
        thread = native.request('thread/start', {'cwd': str(native.home / 'project')})['thread']['id']
        cursor = None
        active = None

        def deliver(message):
            return native.request('thread/factoryMessage', {'threadId': thread,
                'project': 'fixture-project', 'messageId': message['messageId']})['data']

        def begin():
            message = fleet.envelope(mode='wake_when_idle', kind='note',
                text='Status: CF-020\nState: ready\nDone: The desktop connection fixture is ready.\n'
                     'Next: Report the fixture marker. Use no tools.')
            fleet.send(message)
            index = len(model.requests)
            started = deliver(message)
            assert started['state'] == 'started', started
            model.wait_request(index)
            return message, started, index

        marker, started, index = begin()
        model.release(index)
        native.event('turn/completed', started['turnId'])
        assert deliver(marker)['state'] == 'completed'
        checks.append('actual_native_manager_consumes_authenticated_controller_message' if args.manager_control
            else 'actual_native_foreman_consumes_authenticated_manager_message')
        ready = {'run': run, 'owner': owner, 'threadId': thread, 'foremanSeat': foreman['seat'],
            'nativeIdentity': native.identity, 'marker': 'fixture-result-0',
            'packageCliSha256': hashlib.sha256(Path(args.cli).read_bytes()).hexdigest(),
            'packageBridgeSha256': hashlib.sha256(Path(args.binary).read_bytes()).hexdigest(),
            'driverSha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            'route': 'existing desktop conversation -> command tool -> authenticated factory bridge',
            'newHostPaired': False, 'realAccountModelRequests': 0,
            'targetRole': 'manager' if args.manager_control else 'foreman', 'targetSeat': foreman['seat'],
            'expiresAt': time.time() + args.seconds}
        write(evidence / 'ready.json', ready)
        print(json.dumps({'ready': True, 'evidence': str(evidence), 'threadId': thread,
                          'marker': ready['marker']}), flush=True)
        deadline = time.monotonic() + args.seconds
        while not stopping and time.monotonic() < deadline:
            assert native.process.poll() is None, 'native_owner_exited'
            for path in sorted((evidence / 'requests').glob('*.json')):
                command = json.loads(path.read_text())
                assert command['run'] == run and set(command) == {'run', 'action'}
                action = command['action']
                try:
                    if action == 'observe':
                        foreman['sequence'] = next(s['lastSequence'] for s in fleet.inspect()['seats']
                            if s['seat'] == foreman['seat'])
                        fleet.wire('receiver', {'method': 'report_presence', 'protocol': 1,
                            'request': fleet.request('receiver', 'acknowledge'),
                            'report': {'process': {k: native.identity[k] for k in ('pid', 'bootId', 'startTicks')},
                                'modelActivity': 'busy' if active else 'idle', 'taskProgress': 'unknown'}})
                        roster = observer.tool('work_agents')
                        cursor = roster['cursor']
                        history = native.request('thread/read', {'threadId': thread, 'includeTurns': True})
                        assert 'fixture-result-0' in json.dumps(history)
                        response = {'roster': roster, 'threadId': thread, 'markerPresent': True,
                                    'nativeIdentity': native.identity}
                    elif action == 'hold':
                        assert active is None, 'already_running'
                        active = begin()
                        response = {'threadId': thread, 'turnId': active[1]['turnId'], 'state': 'running'}
                    elif action == 'conflict':
                        assert args.manager_control and active is not None, 'no_active_manager_turn'
                        message, started, index = active
                        fleet.wire('contender', {'method': 'cancel', 'protocol': 1,
                            'request': fleet.request('contender', 'queue', foreman['seat']),
                            'message_id': message['messageId']}, expected='cancel_scope_denied')
                        subject = fleet.subjects['observer']
                        subject['sequence'] = next(s['lastSequence'] for s in fleet.inspect()['seats']
                            if s['seat'] == subject['seat'])
                        fleet.wire('observer', {'method': 'cancel', 'protocol': 1,
                            'request': fleet.request('observer', 'queue', foreman['seat']),
                            'message_id': message['messageId']}, expected='operation_denied')
                        response = deliver(message)
                        assert response['turnId'] == started['turnId'] and response['cancellation'] is None
                        assert not any(e.get('method') == 'turn/completed' and
                            e.get('params', {}).get('turn', {}).get('id') == started['turnId'] for e in native.events)
                        response = {'competingControllerDenied': True, 'observerControlDenied': True,
                            'owner': manager['seat'], 'threadId': thread, 'turnId': started['turnId'],
                            'sameNativeIncarnation': incarnation(native.identity['pid']) ==
                                [native.identity['bootId'], native.identity['startTicks']]}
                    elif action == 'interrupt':
                        assert active is not None, 'no_active_turn'
                        message, started, index = active
                        requested = fleet.wire('sender', {'method': 'cancel', 'protocol': 1,
                            'request': fleet.request('sender', 'queue', foreman['seat']),
                            'message_id': message['messageId']})
                        assert requested['cancellation']['state'] == 'requested'
                        deliver(message)
                        terminal = native.event('turn/completed', started['turnId'])
                        response = deliver(message)
                        assert terminal['params']['turn']['status'] == 'interrupted'
                        assert response['cancellation']['state'] == 'applied'
                        model.release(index)
                        active = None
                    elif action == 'reconnect':
                        assert cursor is not None, 'observe_first'
                        original = native.identity.copy()
                        observer.close()
                        observer = Observer(fleet)
                        changes = observer.tool('work_changes', {'after': cursor})
                        assert not changes['reset'] and changes['gap'] is None
                        assert incarnation(original['pid']) == [original['bootId'], original['startTicks']]
                        assert 'fixture-result-0' in json.dumps(native.request('thread/read',
                            {'threadId': thread, 'includeTurns': True}))
                        response = {'sameNativeIncarnation': True, 'threadId': thread,
                                    'markerPresent': True, 'changes': changes}
                    elif action == 'denied':
                        observer.tool('work_agent', {'id': manager['seat']}, error=True)
                        unknown = fleet.envelope(recipient='receiver')
                        unknown['recipient'] = str(uuid.uuid4())
                        fleet.send(unknown, expected='recipient_denied')
                        response = {'ungrantedReadDenied': True, 'ungrantedSendDenied': True}
                    elif action == 'stop':
                        assert active is None, 'interrupt_before_stop'
                        stopping = True
                        response = {'stopping': True}
                    else:
                        raise ValueError('action_not_supported')
                    output = {'ok': True, 'run': run, 'action': action, 'result': response}
                    checks.append('desktop_tool_' + action)
                except Exception:
                    output = {'ok': False, 'run': run, 'action': action, 'error': traceback.format_exc()}
                actions.append({'request': command, 'response': output})
                write(evidence / 'responses' / path.name, output)
                path.unlink()
                print(json.dumps({'action': action, 'ok': output['ok']}), flush=True)
            time.sleep(.05)
        assert stopping, 'desktop_session_timeout'
    except Exception:
        error = traceback.format_exc()
    finally:
        for owned in (observer, native, model, fleet):
            if owned is not None:
                try:
                    owned.close()
                except Exception:
                    error = (error or '') + traceback.format_exc()
        write(evidence / 'desktop-actions.json', actions)
        write(evidence / 'cleanup.json', {'scratchRemoved': not fleet.base.exists(),
            'nativeIncarnationGone': native is None or incarnation(native.identity['pid']) !=
                [native.identity['bootId'], native.identity['startTicks']],
            'newHostPaired': False, 'realAccountModelRequests': 0})
    if any(not row['response']['ok'] for row in actions):
        error = (error or '') + 'One desktop request failed. Read desktop-actions.json.'
    return result(evidence, checks, error)


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest='mode', required=True)
    owner = sub.add_parser('serve')
    for name in ('binary', 'cli', 'evidence'):
        owner.add_argument('--' + name, required=True)
    owner.add_argument('--seconds', type=int, default=300)
    owner.add_argument('--manager-control', action='store_true')
    client = sub.add_parser('request')
    client.add_argument('--evidence', required=True)
    client.add_argument('--action', required=True,
                        choices=['observe', 'hold', 'interrupt', 'reconnect', 'denied', 'conflict', 'stop'])
    args = parser.parse_args()
    return serve(args) if args.mode == 'serve' else request(args)


if __name__ == '__main__':
    raise SystemExit(main())
