#!/usr/bin/env python3
"""Actual native root/child/tool requests and closed shared-pilot HTTP boundaries."""
import argparse
import http.server
import json
import os
from pathlib import Path
import tempfile
import threading
import time
import traceback
import urllib.error
import urllib.request
import uuid

from fleet_fixture import Fleet, result
from native_fixture import Native
from pilot_gateway import Gateway, PilotBudget
from session_fixture import Model


class Upstream:
    def __init__(self, status=200, hold=0):
        self.calls, self.status, self.hold = [], status, hold
        owner = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def do_POST(self):
                self.rfile.read(int(self.headers['Content-Length']))
                owner.calls.append({'authorizationPresent': self.headers.get('Authorization') is not None})
                self.send_response(status)
                self.send_header('Content-Type', 'text/event-stream')
                self.end_headers()
                try:
                    self.wfile.write(b'event: response.created\ndata: {"type":"response.created"}\n\n')
                    self.wfile.flush()
                    if hold:
                        time.sleep(hold)
                    self.wfile.write(b'data: {"type":"response.completed","response":{"usage":{"input_tokens":1,"output_tokens":1,"total_tokens":2}}}\n\n')
                except (BrokenPipeError, ConnectionResetError):
                    pass

        self.server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()


def main():
    parser = argparse.ArgumentParser()
    for name in ('binary', 'cli', 'evidence'):
        parser.add_argument('--' + name, required=True)
    args = parser.parse_args()
    os.umask(0o077)
    fleet = Fleet(args.binary, args.evidence)
    scratch = Path(tempfile.mkdtemp(prefix='cf019-guard-', dir='/run/user/' + str(os.getuid())))
    resources, checks, records, error = [], [], [], None
    native = None

    def budget(name, turns=24, seconds=30):
        path = scratch / (name + '.json')
        path.write_text(json.dumps({'authorized': True, 'limitTurns': turns, 'limitSeconds': seconds,
                                  'startedAt': None, 'admissions': []}))
        path.chmod(0o600)
        return PilotBudget(path)

    def gateways(b, upstream):
        source = 'http://127.0.0.1:' + str(upstream.server.server_port) + '/v1'
        pair = [Gateway(b, profile, source) for profile in ('foreman', 'worker')]
        resources.extend(pair)
        return pair

    def metadata(**changes):
        return {'turn_id': str(uuid.uuid4()), 'thread_id': str(uuid.uuid4()), **changes}

    def post(gateway, identity, upgrade=False):
        headers = {'Content-Type': 'application/json', 'Authorization': 'Bearer cf019-not-a-real-credential'}
        if identity is not None:
            headers['x-codex-turn-metadata'] = json.dumps(identity)
        if upgrade:
            headers['Upgrade'] = 'websocket'
        request = urllib.request.Request('http://127.0.0.1:' + str(gateway.server.server_port) + '/v1/responses',
                                         data=b'{"input":"fixture-only"}', headers=headers)
        try:
            response = urllib.request.urlopen(request, timeout=5)
        except urllib.error.HTTPError as exc:
            response = exc
        with response:
            response.read()
            return response.status

    try:
        upstream = Upstream()
        resources.append(upstream)
        b = budget('shared', turns=2)
        first, second = gateways(b, upstream)
        root = metadata()
        assert post(first, root) == 200 and post(first, root) == 200
        child = metadata(parent_turn_id=root['turn_id'], root_turn_id=root['turn_id'])
        assert post(second, child) == 200
        assert post(first, metadata()) == 503 and len(upstream.calls) == 3
        data = json.loads(b.path.read_text())
        assert len(data['admissions']) == 2 and data['admissions'][0]['requests'] == 2
        original_start = data['startedAt']
        restarted = PilotBudget(b.path)
        replacement = gateways(restarted, upstream)[0]
        assert post(replacement, metadata()) == 503
        assert json.loads(b.path.read_text())['startedAt'] == original_start
        records.append({'case': 'shared', 'budget': json.loads(b.path.read_text())})
        checks.append('two_profile_gateways_count_distinct_turns_and_children_not_http_followups_and_never_reset')

        missing = budget('missing')
        malformed = gateways(missing, upstream)[0]
        count = len(upstream.calls)
        assert post(malformed, None) == 503 and len(upstream.calls) == count
        assert json.loads(missing.path.read_text())['startedAt'] is None
        websocket = gateways(budget('websocket'), upstream)[0]
        assert post(websocket, metadata(), upgrade=True) == 503 and len(upstream.calls) == count
        conflict = budget('conflict')
        conflicting = gateways(conflict, upstream)[0]
        identity = metadata()
        assert post(conflicting, identity) == 200
        assert post(conflicting, {**identity, 'root_turn_id': str(uuid.uuid4())}) == 503
        assert json.loads(conflict.path.read_text())['stopReason'] == 'pilot_turn_identity_conflict'
        checks.append('missing_ids_conflicting_identity_and_websocket_upgrade_cannot_bypass_admission')

        limited = Upstream(status=429)
        resources.append(limited)
        limit_budget = budget('rate')
        rate_a, rate_b = gateways(limit_budget, limited)
        assert post(rate_a, metadata()) == 429
        assert post(rate_b, metadata()) == 503 and len(limited.calls) == 1
        assert json.loads(limit_budget.path.read_text())['stopReason'] == 'pilot_rate_limit'
        held = Upstream(hold=2)
        resources.append(held)
        timed = budget('deadline', seconds=.4)
        timed_gate = gateways(timed, held)[0]
        before = time.monotonic()
        post(timed_gate, metadata())
        assert time.monotonic() - before < 1.5
        assert post(timed_gate, metadata()) == 503 and len(held.calls) == 1
        checks.append('actual_http_429_latches_both_profiles_and_continuous_deadline_cuts_off_a_held_stream')

        model = Model()
        resources.append(model)
        native_budget = budget('native')
        gate = gateways(native_budget, model)[1]
        fleet.enroll('receiver', ['observe', 'queue', 'acknowledge'])
        native = Native(fleet, args.cli, gate)
        thread = native.request('thread/start', {'cwd': str(native.home / 'project')})['thread']['id']
        model.outputs[0] = {'type': 'function_call', 'namespace': 'multi_agent_v1', 'name': 'spawn_agent',
            'call_id': 'guard-child', 'arguments': json.dumps({'message':
                'Assignment: CF-019 — Child admission\nDo: Report READY.\nDone when: The report is complete.\nLimits: Use no tools.',
                'fork_context': False})}
        turn = native.request('turn/start', {'threadId': thread, 'input': [{'type': 'text',
            'text': 'Do: Test the local child admission path. Use the fixture only.'}]})['turn']['id']
        deadline = time.monotonic() + 20
        while True:
            for index in range(len(model.gates)):
                model.release(index)
            native.poll(.05)
            admissions = json.loads(native_budget.path.read_text())['admissions']
            done = any(e.get('method') == 'turn/completed' and e.get('params', {}).get('turn', {}).get('id') == turn
                       for e in native.events)
            if done and len(admissions) >= 2 and len(model.requests) >= 3:
                break
            assert time.monotonic() < deadline, 'native_child_admission_timeout'
        assert len(admissions) == 2 and any(a['identity']['parent_turn_id'] for a in admissions)
        assert max(a['requests'] for a in admissions) >= 2
        native_budget.halt('pilot_unexpected_approval')
        count = len(model.requests)
        assert post(gate, metadata()) == 503 and len(model.requests) == count
        records.append({'case': 'actual-native', 'budget': json.loads(native_budget.path.read_text())})
        checks.append('actual_native_root_child_and_tool_followup_headers_are_counted_and_external_approval_stop_is_shared')
        native.close()
        native = None
        for resource in resources:
            if isinstance(resource, Gateway):
                records.append({'gateway': resource.profile, 'records': resource.records})
        encoded = json.dumps(records)
        assert 'cf019-not-a-real-credential' not in encoded and 'fixture-only' not in encoded
        assert 'Bearer ' not in encoded
        (fleet.e / 'guard-evidence.json').write_text(json.dumps(records, indent=2) + '\n')
    except Exception:
        error = traceback.format_exc()
    finally:
        if native:
            native.close()
        for resource in reversed(resources):
            resource.close()
        for path in scratch.iterdir():
            path.unlink()
        scratch.rmdir()
        fleet.close()
    return result(args.evidence, checks, error)


if __name__ == '__main__':
    raise SystemExit(main())
