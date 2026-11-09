#!/usr/bin/env python3
"""Exercise a real native receiver against one deterministic HTTP 429 response."""
import argparse
import http.server
import json
from pathlib import Path
import time
import traceback

from fleet_fixture import Fleet, result
from native_fixture import Native
from session_fixture import Model


def main():
    parser = argparse.ArgumentParser()
    for name in ['binary', 'cli', 'evidence']:
        parser.add_argument('--' + name, required=True)
    args = parser.parse_args()
    fleet = Fleet(args.binary, args.evidence)
    model = Model()
    native, checks, error = None, [], None

    class RateLimit(http.server.BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_POST(self):
            body = self.rfile.read(int(self.headers.get('Content-Length', '0')))
            model.requests.append({'path': self.path, 'body': json.loads(body)})
            payload = json.dumps({'error': {'type': 'rate_limit_exceeded',
                'code': 'rate_limit_exceeded', 'message': 'The fixture receiver reached its rate limit.'}}).encode()
            self.send_response(429)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(payload)))
            self.send_header('Retry-After', '0')
            self.end_headers()
            self.wfile.write(payload)

    model.server.RequestHandlerClass = RateLimit
    try:
        fleet.enroll('sender')
        fleet.enroll('receiver', ['observe', 'queue', 'acknowledge'])
        native = Native(fleet, args.cli, model)
        thread = native.request('thread/start', {'cwd': str(native.home / 'project')})['thread']['id']
        message = fleet.envelope(mode='wake_when_idle', text='''Assignment: CF-011 — Rate limit boundary
Do: Reply READY. Use no tools. Do not create another agent.
Done when: Your reply contains READY.''')
        fleet.send(message)
        params = {'threadId': thread, 'project': 'fixture-project', 'messageId': message['messageId']}
        started = native.request('thread/factoryMessage', params)['data']
        assert started['state'] == 'started', started
        completed = native.event('turn/completed', started['turnId'])
        assert completed['params']['turn']['status'] == 'failed', completed
        time.sleep(.2)
        assert len(model.requests) == 1, model.requests
        before = native.request('thread/factoryMessage', params)['data']
        assert before['state'] != 'completed', before
        native.stop()
        native.start()
        native.request('thread/resume', {'threadId': thread})
        after = native.request('thread/factoryMessage', params)['data']
        assert after['state'] != 'completed', after
        assert before.get('turnId') == after.get('turnId') == started['turnId']
        time.sleep(.2)
        assert len(model.requests) == 1
        mail = next(item for item in fleet.inspect()['mail'] if item['messageId'] == message['messageId'])
        assert not any(receipt['state'] == 'completed' for receipt in mail['receipts'])
        checks.extend(['real_native_receiver_stops_after_one_http_429',
                       'rate_limited_turn_fails_without_successful_completion_receipt',
                       'same_assignment_after_restart_does_not_repeat_the_provider_request'])
        (Path(args.evidence) / 'rate-limit.json').write_text(json.dumps({
            'completed': completed, 'beforeRestart': before, 'afterRestart': after,
            'providerRequestCount': len(model.requests), 'mail': mail}, indent=2) + '\n')
    except Exception:
        error = traceback.format_exc()
    finally:
        model.close()
        if native:
            native.close()
        fleet.close()
    return result(args.evidence, checks, error)


if __name__ == '__main__':
    raise SystemExit(main())
