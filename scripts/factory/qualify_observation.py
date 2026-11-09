#!/usr/bin/env python3
"""CF-014 UID-separated native/bridge/MCP journeys. No real account requests."""
import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import select
import subprocess
import sys
import time
import traceback
import uuid

from fleet_fixture import Fleet, SERVICE, GROUP, result
from native_fixture import Native
from session_fixture import Model


class Observer:
    def __init__(self, fleet, name='observer'):
        self.f, self.name, self.serial, self.buffer = fleet, name, 0, b''
        self.closed = False
        actor = fleet.subjects[name]
        self.command = [*fleet.prefix(actor['uid']), fleet.binary, 'observe-work',
            '--grant', fleet.publish(actor['grant']), '--socket', fleet.socket,
            '--service-uid', SERVICE, '--credential', actor['credential'], '--project', 'fixture-project']
        self.process = subprocess.Popen(list(map(str, self.command)), stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        fleet.children.append(self.process)
        self.transcript = []
        initialized = self.rpc('initialize', {'protocolVersion': '2025-11-25',
            'capabilities': {}, 'clientInfo': {'name': 'cf-014-native-fixture', 'version': '1'}})
        assert initialized['result']['protocolVersion'] == '2025-11-25', initialized

    def rpc(self, method, params, error=False):
        self.serial += 1
        request = {'jsonrpc': '2.0', 'id': self.serial, 'method': method, 'params': params}
        self.process.stdin.write(json.dumps(request).encode() + b'\n')
        self.process.stdin.flush()
        deadline = time.monotonic() + 8
        while b'\n' not in self.buffer:
            assert time.monotonic() < deadline, 'MCP response timeout'
            if select.select([self.process.stdout], [], [], .1)[0]:
                part = os.read(self.process.stdout.fileno(), 65536)
                if not part:
                    raise RuntimeError('MCP stopped: ' + self.process.stderr.read().decode())
                self.buffer += part
        line, self.buffer = self.buffer.split(b'\n', 1)
        response = json.loads(line)
        self.transcript.append({'request': request, 'response': response})
        assert response['id'] == self.serial, response
        assert ('error' in response) == error, response
        return response

    def tool(self, name, arguments=None, error=False):
        response = self.rpc('tools/call', {'name': name, 'arguments': arguments or {}}, error)
        if error:
            return response
        value = response['result']['structuredContent']
        assert json.loads(response['result']['content'][0]['text']) == value
        return value

    def close(self):
        if self.closed:
            return
        self.closed = True
        if self.process.poll() is None:
            self.process.stdin.close()
        stdout, stderr = self.process.stdout.read(), self.process.stderr.read()
        code = self.process.wait(timeout=8)
        self.process.stdout.close()
        self.process.stderr.close()
        self.f.commands.append({'observerCommand': list(map(str, self.command)), 'exit': code,
                                'trailingStdout': stdout.decode(), 'stderr': stderr.decode()})
        self.f.counter += 1
        (self.f.e / f'mcp-{self.name}-{self.f.counter}.json').write_text(json.dumps(self.transcript, indent=2))
        assert code == 0, stderr


def main():
    parser = argparse.ArgumentParser()
    for name in ['binary', 'cli', 'evidence']:
        parser.add_argument('--' + name, required=True)
    args = parser.parse_args()
    fleet, model = Fleet(args.binary, args.evidence), Model()
    native, observer, error, checks = None, None, None, []
    readers = []
    try:
        for name, uid in [('observer', 65531), ('reader2', 65530), ('foreign', 65529)]:
            fleet.subjects[name] = {'uid': uid, 'seat': str(uuid.uuid4()), 'sequence': 0}
            fleet.sudo('install', '-d', '-o', uid, '-g', GROUP, '-m', '0700', fleet.base / name)
        fleet.enroll('sender')
        fleet.enroll('receiver', ['observe', 'queue', 'acknowledge'])
        fleet.enroll('foreign', ['observe', 'acknowledge'], projects=['foreign-project'])
        for name in ['observer', 'reader2']:
            fleet.enroll(name, ['observe'], role='observer',
                recipients=[fleet.subjects[name]['seat'], fleet.subjects['receiver']['seat']])
        receiver = fleet.subjects['receiver']
        native = Native(fleet, args.cli, model)
        observer = Observer(fleet)
        readers.append(observer)
        handshake = observer.tool('work_handshake')
        assert handshake['contract'] == 'marley.work/v1'
        assert handshake['capabilities'] == ['agents', 'events', 'changes']
        tools = observer.rpc('tools/list', {})['result']['tools']
        assert {tool['name'] for tool in tools} == {'work_handshake', 'work_agents', 'work_agent', 'work_changes'}

        def synchronize(name):
            fleet.subjects[name]['sequence'] = next(s['lastSequence'] for s in fleet.inspect()['seats']
                if s['seat'] == fleet.subjects[name]['seat'])

        def report(value, expected=None, name='receiver'):
            synchronize(name)
            return fleet.wire(name, {'method': 'report_presence', 'protocol': 1,
                'request': fleet.request(name, 'acknowledge'), 'report': value}, expected=expected)

        def detail():
            return observer.tool('work_agent', {'id': receiver['seat']})

        lease = {key: native.identity[key] for key in ['pid', 'bootId', 'startTicks']}
        pulse = {'process': lease, 'modelActivity': 'busy', 'taskProgress': 'in_progress'}
        report(pulse)
        current = detail()
        assert current['observation'] == {'connectivity': 'fresh_report', 'processHealth': 'alive',
            'modelActivity': 'busy', 'taskProgress': 'in_progress', 'fresh': True,
            'incarnation': receiver['grant']['incarnation'], 'readOnly': True}, current
        assert [event['kind'] for event in current['recent_events'][:4]] == [
            'Connectivity', 'Process health', 'Model activity', 'Task progress']
        roster = observer.tool('work_agents')
        assert [agent['id'] for agent in roster['agents']] == [receiver['seat']], roster
        observer.tool('work_agent', {'id': fleet.subjects['sender']['seat']}, error=True)
        observer.tool('work_agent', {'id': fleet.subjects['foreign']['seat']}, error=True)
        observer.tool('work_agents', {'project': 'foreign-project'}, error=True)
        checks.append('fixed_read_only_tool_set_and_exact_recipient_project_filters')

        cursor = roster['cursor']
        assert str(uuid.UUID(cursor)) == cursor
        observer.close()
        observer = Observer(fleet)
        readers.append(observer)
        resumed = observer.tool('work_changes', {'after': cursor})
        assert not resumed['reset'] and resumed['gap'] is None, resumed
        fleet.stop()
        fleet.start()
        assert not observer.tool('work_changes', {'after': cursor})['reset']
        checks.append('opaque_cursor_resumes_after_adapter_and_bridge_restart')

        # The actual native worker completes a synthetic turn. No business completion is inferred.
        thread = native.request('thread/start', {'cwd': str(native.home / 'project')})['thread']['id']
        message = fleet.envelope(mode='wake_when_idle', assignmentId=str(uuid.uuid4()),
            text='Assignment: CF-014\nDo: Reply READY. Use no tools. Do not create another agent.')
        fleet.send(message)
        params = {'threadId': thread, 'project': 'fixture-project', 'messageId': message['messageId']}
        started = native.request('thread/factoryMessage', params)['data']
        model.wait_request(0)
        model.release(0)
        native.event('turn/completed', started['turnId'])
        assert native.request('thread/factoryMessage', params)['data']['state'] == 'completed'
        fleet.run([*fleet.prefix(receiver['uid']), fleet.binary, 'collect-recording',
            '--native-state', fleet.base / 'receiver/native-ledger', '--grant', fleet.publish(receiver['grant']),
            '--socket', fleet.socket, '--service-uid', SERVICE, '--credential', receiver['credential']])
        report({**pulse, 'modelActivity': 'idle'})
        current = detail()
        event = next(event for event in current['recent_events'] if event['kind'] == 'Native terminal')
        for identifier in [message['messageId'], message['assignmentId'], thread, started['turnId']]:
            assert identifier in event['text']
        assert current['observation']['taskProgress'] == 'in_progress'
        assert current['observation']['modelActivity'] == 'idle'
        assert message['text'] not in json.dumps(current) and 'READY' not in json.dumps(current)
        (fleet.e / 'native-observation.json').write_text(json.dumps(current, indent=2))
        checks.append('actual_native_terminal_metadata_correlates_without_prompt_or_task_completion_claim')

        synchronize('reader2')
        fleet.wire('reader2', {'method': 'observe_work', 'protocol': 1,
            'request': fleet.request('reader2'), 'after': cursor}, expected='observation_cursor_scope_denied')
        second = Observer(fleet, 'reader2')
        readers.append(second)
        second.tool('work_changes', {'after': str(uuid.uuid4())})
        second.close()
        synchronize('sender')
        fleet.wire('sender', {'method': 'observe_work', 'protocol': 1,
            'request': fleet.request('sender')}, expected='observation_reader_role_denied')
        synchronize('observer')
        foreign_request = fleet.request('observer')
        foreign_request['project'] = 'foreign-project'
        fleet.wire('observer', {'method': 'observe_work', 'protocol': 1,
            'request': foreign_request}, expected='project_denied')
        checks.append('other_reader_cursor_worker_role_and_foreign_project_are_refused')

        fake = copy.deepcopy(pulse)
        fake['process']['startTicks'] = str(int(lease['startTicks']) + 1)
        report(fake, 'observation_process_unproven')
        fake['process'] = {key: fleet.record[key] for key in ['pid', 'bootId', 'startTicks']}
        report(fake, 'observation_process_unproven')
        fake = {**pulse, 'prompt': 'forbidden-sentinel'}
        report(fake, 'invalid_request')
        checks.append('reused_pid_wrong_process_uid_and_arbitrary_presence_fields_are_refused')

        for method, fields, operation in [
            ('cancel', {'message_id': message['messageId']}, 'interrupt'),
            ('cancel_result', {'message_id': message['messageId'], 'state': 'interrupted',
                'turn_id': started['turnId'], 'reason': 'fixture'}, 'acknowledge'),
            ('report_presence', {'report': pulse}, 'acknowledge'),
            ('record_native', {'record': {'messageId': message['messageId'], 'threadId': thread,
                'turnId': started['turnId'], 'outcome': 'completed'}}, 'acknowledge'),
            ('send', {'envelope': fleet.envelope(name='observer')}, 'queue'),
            ('ack', {'receipt': {'protocol': 1, 'messageId': message['messageId'],
                'state': 'completed', 'writer': 'inbox', 'turnId': started['turnId']}}, 'acknowledge'),
        ]:
            synchronize('observer')
            fleet.wire('observer', {'method': method, 'protocol': 1,
                'request': fleet.request('observer', operation), **fields}, expected='operation_denied')
        synchronize('observer')
        fleet.wire('observer', {'method': 'receive', 'protocol': 1,
            'request': fleet.request('observer'), 'message_id': message['messageId']}, expected='observer_delivery_denied')
        for tool in ['turn_start', 'turn_interrupt', 'approve', 'execute', 'work_run', 'report_presence']:
            observer.tool(tool, {}, error=True)
        observer.rpc('send', {'operation': 'execute'}, error=True)
        checks.append('observer_wire_and_mcp_credentials_cannot_send_acknowledge_interrupt_approve_execute_or_receive_payload')

        # Every poll refreshes visible status even when no new journal event exists.
        report(pulse)
        quiet_cursor = observer.tool('work_agents')['cursor']
        time.sleep(16)
        quiet_changes = observer.tool('work_changes', {'after': quiet_cursor})
        assert any(change['id'] == receiver['seat'] for change in quiet_changes['changed'])
        stale = detail()['observation']
        assert stale['connectivity'] == 'stale_report' and stale['processHealth'] == 'alive', stale
        assert stale['modelActivity'] == stale['taskProgress'] == 'unknown', stale
        report(pulse)
        native.stop()
        dead = detail()['observation']
        assert dead['processHealth'] == 'dead' and dead['modelActivity'] == 'unknown', dead
        checks.append('time_only_staleness_and_process_death_do_not_imply_activity_or_task_completion')

        fleet.admin('prune-observation', '--state', fleet.state / 'observation', '--keep', 0)
        pruned = observer.tool('work_changes', {'after': cursor})
        assert pruned['reset'] and pruned['gap'] in ['history_pruned', 'anchor_unavailable'], pruned
        assert any(event['kind'] == 'History gap' for event in detail()['recent_events'])
        unknown = observer.tool('work_changes', {'after': str(uuid.uuid4())})
        assert unknown['reset'] and unknown['gap'] == 'cursor_unknown_or_expired', unknown
        checks.append('retention_prune_and_unknown_cursor_report_explicit_gap_in_feed_and_agent_detail')

        # A restored valid journal has a new epoch. It must not reuse a prior continuation silently.
        journal_path = fleet.state / 'observation/registry.json'
        journal = json.loads(fleet.sudo('cat', '--', journal_path).stdout)
        journal['epoch'] = str(uuid.uuid4())
        fleet.sudo('install', '-o', SERVICE, '-g', GROUP, '-m', '0600', fleet.publish(journal), journal_path)
        restored = observer.tool('work_changes', {'after': pruned['cursor']})
        assert restored['reset'] and restored['gap'] == 'journal_restored', restored
        checks.append('restored_journal_epoch_refuses_silent_cursor_continuation')

        private = fleet.run([*fleet.prefix(fleet.subjects['observer']['uid']), sys.executable,
            fleet.helper, '--inspect', fleet.state / 'registry.json', '--inspect', journal_path,
            '--inspect', fleet.state / 'recording/attestation.key'])
        assert not any(json.loads(private.stdout)['readable'].values())
        fleet.admin('revoke', '--state', fleet.state, '--seat', receiver['seat'])
        assert detail()['observation']['connectivity'] == 'revoked'
        fleet.admin('revoke', '--state', fleet.state, '--seat', fleet.subjects['observer']['seat'])
        observer.tool('work_agents', error=True)
        observer.close()
        observer = None
        checks.append('revocation_applies_on_next_call_and_reader_cannot_open_service_keys_or_state')
        (fleet.e / 'binaries.json').write_text(json.dumps({str(path): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in [Path(args.binary).resolve(), Path(args.cli).resolve()]}, indent=2))
    except Exception:
        error = traceback.format_exc()
    finally:
        for item in [*reversed(readers), native, model, fleet]:
            if item is not None:
                try:
                    item.close()
                except Exception:
                    error = (error or '') + traceback.format_exc()
    return result(args.evidence, checks, error)


if __name__ == '__main__':
    raise SystemExit(main())
