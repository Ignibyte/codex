#!/usr/bin/env python3
"""A closed HTTP admission gateway for the shared bounded operator pilot.

The native clients own their original logins. Credentials and request/response
bodies pass through memory and are never retained by this gateway.
"""
import contextlib
import fcntl
import http.client
import http.server
import json
import os
from pathlib import Path
import socket
import threading
import time
import urllib.parse
import uuid


class Refused(RuntimeError):
    pass


class PilotBudget:
    def __init__(self, path):
        self.path = Path(path).resolve()
        info = self.path.stat()
        assert not Path(path).is_symlink() and info.st_uid == os.getuid() and info.st_mode & 0o077 == 0
        info = self.path.parent.stat()
        assert info.st_uid == os.getuid() and info.st_mode & 0o077 == 0
        self.boot = Path('/proc/sys/kernel/random/boot_id').read_text().strip()

    @contextlib.contextmanager
    def locked(self):
        fd = os.open(str(self.path) + '.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            info = os.fstat(fd)
            assert info.st_uid == os.getuid() and info.st_mode & 0o077 == 0
            fcntl.flock(fd, fcntl.LOCK_EX)
            with self.path.open() as stream:
                record = json.load(stream)
            original = json.dumps(record, sort_keys=True)
            assert record.get('authorized') is True
            assert 1 <= record['limitTurns'] <= 24 and 0 < record['limitSeconds'] <= 1800
            yield record
            if json.dumps(record, sort_keys=True) == original:
                return
            temporary = self.path.with_name(self.path.name + '.tmp-' + str(uuid.uuid4()))
            with temporary.open('x') as stream:
                os.chmod(temporary, 0o600)
                json.dump(record, stream, indent=2)
                stream.flush()
                os.fsync(stream.fileno())
            temporary.replace(self.path)
            directory = os.open(self.path.parent, os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            os.close(fd)

    def remaining(self, record=None):
        if record is None:
            with self.locked() as current:
                return self.remaining(current)
        if record.get('stopReason'):
            raise Refused(record['stopReason'])
        if record.get('startedAt') is None:
            return record['limitSeconds']
        if record.get('bootId') != self.boot or record.get('startedMonotonic') is None:
            raise Refused('pilot_clock_identity_missing')
        remaining = record['limitSeconds'] - max(time.monotonic() - record['startedMonotonic'],
                                                time.time() - record['startedAt'])
        if remaining <= 0:
            raise Refused('pilot_time_limit')
        return remaining

    def halt(self, reason):
        with self.locked() as record:
            record.setdefault('stopReason', reason)

    def admit(self, profile, header):
        try:
            raw = json.loads(header)
            identity = {key: raw.get(key) for key in ('turn_id', 'thread_id', 'parent_turn_id', 'root_turn_id')}
            for key, value in identity.items():
                if value is None and key in ('parent_turn_id', 'root_turn_id'):
                    continue
                if not isinstance(value, str) or str(uuid.UUID(value)) != value:
                    raise ValueError('invalid_identity')
            if profile not in ('foreman', 'worker'):
                raise ValueError('unknown_profile')
        except (ValueError, TypeError, KeyError, AttributeError):
            self.halt('pilot_turn_identity_missing')
            raise Refused('pilot_turn_identity_missing') from None
        refused = None
        with self.locked() as record:
            try:
                self.remaining(record)
                key = profile + ':' + identity['turn_id']
                admissions = record['admissions']
                existing = next((a for a in admissions if a['key'] == key), None)
                if existing is not None:
                    if existing['identity'] != identity:
                        raise Refused('pilot_turn_identity_conflict')
                    existing['requests'] += 1
                else:
                    if len(admissions) >= record['limitTurns']:
                        raise Refused('pilot_turn_limit')
                    if record.get('startedAt') is None:
                        record.update(startedAt=time.time(), startedMonotonic=time.monotonic(), bootId=self.boot)
                    admissions.append({'key': key, 'profile': profile, 'identity': identity,
                                       'requests': 1, 'at': time.time()})
            except Refused as exc:
                record.setdefault('stopReason', str(exc))
                refused = str(exc)
        if refused:
            raise Refused(refused)
        return key


class Gateway:
    def __init__(self, budget, profile, upstream):
        self.budget, self.profile = budget, profile
        target = urllib.parse.urlsplit(upstream)
        assert not target.username and not target.password and not target.query and not target.fragment
        assert target.scheme == 'https' or (target.scheme == 'http' and target.hostname == '127.0.0.1')
        self.records = []
        self.closed = threading.Event()
        owner = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def do_GET(self):
                self.send_error(405)

            def do_POST(self):
                connection = None
                done = threading.Event()
                started = False
                record = {'profile': profile, 'status': None, 'usage': [], 'bodyRetained': False,
                          'credentialsRetained': False, 'bytesForwarded': 0}
                try:
                    if self.path not in ('/responses', '/v1/responses') or self.headers.get('Upgrade'):
                        raise Refused('pilot_transport_denied')
                    length = int(self.headers.get('Content-Length', '0'))
                    if not 0 < length <= 16 * 1024 * 1024:
                        raise Refused('pilot_request_bound')
                    body = self.rfile.read(length)
                    if len(body) != length:
                        raise Refused('pilot_request_incomplete')
                    record['turnKey'] = budget.admit(profile, self.headers.get('x-codex-turn-metadata'))
                    transport = http.client.HTTPSConnection if target.scheme == 'https' else http.client.HTTPConnection
                    connection = transport(target.hostname, target.port, timeout=min(30, budget.remaining()))
                    headers = {k: v for k, v in self.headers.items() if k.lower() not in
                               ('host', 'connection', 'content-length', 'transfer-encoding', 'proxy-authorization')}
                    headers['Content-Length'] = str(length)
                    headers['Accept-Encoding'] = 'identity'

                    def fence():
                        while not done.wait(.1):
                            try:
                                budget.remaining()
                                if owner.closed.is_set():
                                    raise Refused('pilot_gateway_closed')
                            except Refused:
                                if connection.sock:
                                    try:
                                        connection.sock.shutdown(socket.SHUT_RDWR)
                                    except OSError:
                                        pass
                                return

                    watcher = threading.Thread(target=fence, daemon=True)
                    watcher.start()
                    connection.request('POST', target.path.rstrip('/') + '/responses', body=body, headers=headers)
                    del body, headers
                    response = connection.getresponse()
                    record['status'] = response.status
                    if response.status == 429:
                        budget.halt('pilot_rate_limit')
                    self.send_response(response.status)
                    self.send_header('Content-Type', response.getheader('Content-Type', 'application/json'))
                    self.send_header('Connection', 'close')
                    self.end_headers()
                    started = True
                    carry = b''
                    while True:
                        chunk = response.read1(65536)
                        if not chunk:
                            break
                        record['bytesForwarded'] += len(chunk)
                        if record['bytesForwarded'] > 32 * 1024 * 1024:
                            raise Refused('pilot_response_bound')
                        self.wfile.write(chunk)
                        self.wfile.flush()
                        carry += chunk
                        while b'\n' in carry:
                            line, carry = carry.split(b'\n', 1)
                            if not line.startswith(b'data: '):
                                continue
                            try:
                                event = json.loads(line[6:])
                            except (ValueError, TypeError):
                                continue
                            error = event.get('error') or (event.get('response') or {}).get('error')
                            if isinstance(error, dict) and any(word in str(error.get('code', '')).lower()
                                                               for word in ('rate', 'quota', 'limit')):
                                budget.halt('pilot_rate_limit')
                            if event.get('type') == 'response.completed':
                                usage = (event.get('response') or {}).get('usage') or {}
                                record['usage'].append({k: v for k, v in usage.items()
                                    if k in ('input_tokens', 'output_tokens', 'total_tokens') and isinstance(v, int)})
                        if len(carry) > 2 * 1024 * 1024:
                            raise Refused('pilot_event_bound')
                except Refused as exc:
                    record['refused'] = str(exc)
                    if not started:
                        self.send_error(503, str(exc))
                except (OSError, ValueError, http.client.HTTPException):
                    record['refused'] = 'pilot_transport_error'
                    budget.halt('pilot_transport_error')
                    if not started:
                        try:
                            self.send_error(503, 'pilot_transport_error')
                        except OSError:
                            pass
                finally:
                    done.set()
                    if connection:
                        connection.close()
                    owner.records.append(record)
                    self.close_connection = True

        self.server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def close(self):
        self.closed.set()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
