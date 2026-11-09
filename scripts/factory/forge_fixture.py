"""Bounded non-idempotent Forge HTTP contract fixture with real Ed25519 passes."""
import base64
import http.server
import json
import socket
import threading
import time
import uuid
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey


def decode(value):
    return base64.urlsafe_b64decode(value + '=' * (-len(value) % 4))


class Forge:
    def __init__(self, public_key):
        self.key = Ed25519PublicKey.from_public_bytes(decode(public_key))
        self.project = str(uuid.uuid4())
        self.rows, self.calls = [], []
        self.mode, self.revoked = 'normal', False
        self.hit, self.release = threading.Event(), threading.Event()
        owner = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def do_POST(self):
                try:
                    name = self.headers.get('X-Forge-Identity')
                    segment, signature = self.headers.get('X-Forge-Assertion', '').split('.')
                    owner.key.verify(decode(signature), b'forge-pass|' + segment.encode())
                    passed = json.loads(decode(segment))
                    assert not owner.revoked and name == passed['name'] == 'factory-fixture'
                    assert passed['iat'] - 60 <= time.time() < passed['exp']
                    assert passed['exp'] - passed['iat'] <= 60
                except Exception:
                    self.reply(401, {'error': 'The fixture identity is not authorized.'})
                    return
                if owner.mode == 'unavailable':
                    self.reply(503, {'error': 'The fixture service is unavailable.'})
                    return
                length = int(self.headers.get('Content-Length', 0))
                assert length <= 65536
                request = json.loads(self.rfile.read(length))
                method = request['params']['name']
                args = request['params']['arguments']
                owner.calls.append({'tool': method, 'authenticated': True})
                if method == 'seat-events-list':
                    rows = [row for row in owner.rows if row['session_id'] == args['session_id']]
                    data = {'events': rows[-1000:][::-1]}
                elif method == 'seat-event-emit':
                    # Match the audited writer: every call inserts a new UUID.
                    row = {'id': str(uuid.uuid4()), 'project_id': owner.project, **args,
                           'ts': time.time()}
                    owner.rows.append(row)
                    data = {'event': row}
                    if owner.mode in ['lost_ack', 'hold_ack']:
                        owner.hit.set()
                        if owner.mode == 'hold_ack':
                            owner.release.wait(20)
                        else:
                            self.connection.shutdown(socket.SHUT_RDWR)
                            self.connection.close()
                            return
                else:
                    self.reply(400, {'error': 'The fixture tool is not supported.'})
                    return
                self.reply(200, {'jsonrpc': '2.0', 'id': request['id'], 'result': {
                    'content': [{'type': 'text', 'text': json.dumps(data)}], 'isError': False}})

            def reply(self, status, value):
                body = json.dumps(value).encode()
                try:
                    self.send_response(status)
                    self.send_header('Content-Type', 'application/json')
                    self.send_header('Content-Length', str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                except (BrokenPipeError, ConnectionResetError):
                    pass

        self.server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def close(self):
        self.release.set()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
