"""Owned real Forge/Postgres fixture. No production endpoint, key or database."""
import json
import http.server
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import threading
import time
import urllib.request
import uuid

from fleet_fixture import SERVICE, GROUP


class ForgeProxy:
    """Owned loopback fault boundary. All successful calls use the real Forge."""
    def __init__(self, endpoint):
        owner=self
        self.mode='normal'
        self.forwarded=[]
        self.blocked=threading.Event()
        self.resume=threading.Event()
        self.hold_timed_out=False
        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self,*_):
                pass
            def do_POST(self):
                if owner.mode=='unavailable':
                    self.send_response(503);self.end_headers();return
                size=int(self.headers.get('Content-Length','0'))
                assert size<=65536
                data=self.rfile.read(size)
                method=json.loads(data)['params']['name']
                headers={key:value for key,value in self.headers.items()
                         if key.lower() not in ['host','content-length','connection','transfer-encoding']}
                request=urllib.request.Request(endpoint,data=data,headers=headers)
                opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
                try:
                    response=opener.open(request,timeout=10)
                except urllib.error.HTTPError as error:
                    response=error
                with response:
                    body=response.read(2*1024*1024)
                    status=response.status
                    content_type=response.headers.get('Content-Type','application/json')
                owner.forwarded.append({'method':method,'status':status})
                if owner.mode=='hold_spec_ack' and method=='run-start' and status==200:
                    owner.blocked.set()
                    owner.hold_timed_out=not owner.resume.wait(6)
                if ((owner.mode=='lost_ack' and method=='seat-event-emit') or
                    (owner.mode=='lost_workflow_ack' and method=='pipeline-run-start') or
                    (owner.mode=='lost_spec_ack' and method=='run-start')) and status==200:
                    self.connection.shutdown(socket.SHUT_RDWR)
                    self.connection.close();return
                self.send_response(status);self.send_header('Content-Type',content_type)
                self.send_header('Content-Length',str(len(body)));self.end_headers()
                try:self.wfile.write(body)
                except (BrokenPipeError,ConnectionResetError):pass
        self.server=http.server.ThreadingHTTPServer(('127.0.0.1',0),Handler)
        self.server.daemon_threads=True
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True)
        self.thread.start()
        self.endpoint=f'http://127.0.0.1:{self.server.server_port}/mcp/forge'
    def close(self):
        self.resume.set()
        self.server.shutdown();self.server.server_close();self.thread.join()


def free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


def rpc(endpoint, name, arguments, headers):
    request_id = str(uuid.uuid4())
    body = json.dumps({'jsonrpc': '2.0', 'id': request_id, 'method': 'tools/call',
                      'params': {'name': name, 'arguments': arguments}}).encode()
    request = urllib.request.Request(endpoint, data=body, headers={
        **headers, 'Content-Type': 'application/json', 'Accept': 'application/json, text/event-stream'})
    with urllib.request.urlopen(request, timeout=10) as response:
        text = response.read(2 * 1024 * 1024).decode()
    if text.lstrip().startswith('{'):
        wire = json.loads(text)
    else:
        wire = next(json.loads(line[6:]) for line in text.splitlines() if line.startswith('data: '))
    assert wire['id'] == request_id and 'error' not in wire, 'The owned Forge RPC failed.'
    result = wire['result']
    assert not result.get('isError'), 'The owned Forge tool refused the request.'
    return result.get('structuredContent') or json.loads(result['content'][0]['text'])


class RealForge:
    def __init__(self, fleet, binary, source):
        self.f, self.source = fleet, Path(source).resolve()
        self.container = self.process = None
        self.logs = []
        self.run_id = str(uuid.uuid4())
        self.password = secrets.token_hex(24)
        self.admin = 'forge_pk_' + secrets.token_hex(32)
        self.bootstrap, self.project = str(uuid.uuid4()), str(uuid.uuid4())
        self.binary = fleet.base / 'bin/forge-real'
        self.record = fleet.state / 'forge-process.json'
        self.native_record = None
        self.stopped = False
        try:
            self.setup(binary)
        except Exception:
            self.close()
            raise

    def docker(self, *args, input=None):
        result = subprocess.run(['docker', *map(str, args)], input=input, capture_output=True,
                                text=True, timeout=40)
        if result.returncode:
            raise RuntimeError('The owned database command failed: ' +
                               result.stderr.replace(self.password, '[redacted]')[-1200:])
        return result.stdout.strip()

    def sql(self, text):
        self.assert_container()
        return self.docker('exec', '-i', self.container, 'psql', '-U', 'factory_fixture',
                           '-d', 'factory_fixture', '-v', 'ON_ERROR_STOP=1', '-At', input=text)

    def assert_container(self):
        label = self.docker('inspect', '--format', '{{ index .Config.Labels "codex.factory.run" }}', self.container)
        assert label == self.run_id, 'The database container owner changed.'

    def setup(self, binary):
        f = self.f
        f.sudo('install', '-o', SERVICE, '-g', GROUP, '-m', '0555', Path(binary).resolve(), self.binary)
        # Exact image identity is retained. No image pull, shared volume or public bind.
        image = self.docker('image', 'inspect', '--format', '{{.Id}}', 'pgvector/pgvector:pg18-trixie')
        self.container = self.docker('run', '-d', '--name', 'cf13-pg-' + self.run_id[:12],
            '--label', 'codex.factory.run=' + self.run_id, '--memory', '512m', '--cpus', '1',
            '--tmpfs', '/var/lib/postgresql:rw,size=512m', '-p', '127.0.0.1::5432',
            '-e', 'POSTGRES_DB=factory_fixture', '-e', 'POSTGRES_USER=factory_fixture',
            '-e', 'POSTGRES_PASSWORD=' + self.password, image)
        deadline = time.monotonic() + 30
        while True:
            ready = subprocess.run(['docker', 'exec', self.container, 'pg_isready', '-h', '127.0.0.1',
                                    '-U', 'factory_fixture', '-d', 'factory_fixture'],
                                   capture_output=True, timeout=5)
            if ready.returncode == 0:
                break
            assert time.monotonic() < deadline, 'The owned database did not start.'
            time.sleep(.2)
        self.sql((self.source / 'migrations/00000000000000_init.sql').read_text())
        self.sql(f"INSERT INTO projects(id,slug,name,api_key_lookup,api_key_hash) VALUES "
                 f"('{self.bootstrap}','forge-bootstrap','Fixture bootstrap','unused-bootstrap','unused'),"
                 f"('{self.project}','factory-fixture','Fixture project','unused-project','unused');"
                 "INSERT INTO seats(ip,seat) VALUES ('127.0.0.1','fixture-operator');")
        ports = json.loads(self.docker('inspect', '--format', '{{json .NetworkSettings.Ports}}', self.container))
        binding = ports['5432/tcp'][0]
        assert binding['HostIp'] == '127.0.0.1'
        self.port = free_port()
        self.endpoint = f'http://127.0.0.1:{self.port}/mcp/forge'
        env = {'PATH': '/usr/bin:/bin', 'HOME': str(f.state), 'RUST_LOG': 'warn',
            'DATABASE_URL': f"postgres://factory_fixture:{self.password}@127.0.0.1:{binding['HostPort']}/factory_fixture",
            'FORGE_BOOTSTRAP_PROJECT_UUID': self.bootstrap, 'FORGE_ADMIN_BEARER': self.admin,
            'FORGE_BIND_ADDR': '127.0.0.1:' + str(self.port), 'FORGE_USE_FAKE_PROVIDERS': 'true',
            # Redis is optional for these row-backed tools. An explicitly invalid
            # fixture URL avoids its connection manager's long retry schedule.
            'REDIS_URL': 'fixture-redis-is-not-configured',
            'JEV_ENV': str(f.state / 'no-model-key'), 'TYPESAFE_API_KEY': ''}
        spec = f.state / 'forge-spec.json'
        value = json.dumps({'argv': [str(self.binary), 'mcp'], 'cwd': str(f.state),
                            'env': env, 'record': str(self.record)})
        # The database URL and bootstrap bearer never enter public fixture files,
        # command arguments, worker environments or retained command logs.
        writer = ['sudo', '-n', 'setpriv', '--reuid', str(SERVICE), '--regid', str(GROUP),
                  '--clear-groups', '--no-new-privs', sys.executable, '-c',
                  'import os,sys; fd=os.open(sys.argv[1],os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600); '
                  'os.write(fd,sys.stdin.buffer.read()); os.fsync(fd); os.close(fd)', str(spec)]
        written = subprocess.run(writer, input=value, text=True, capture_output=True, timeout=5)
        assert written.returncode == 0, 'The private process configuration was not written.'
        command = [*f.prefix(SERVICE), sys.executable, Path(__file__).with_name('live_command.py'), '--spec', spec]
        self.process = subprocess.Popen(list(map(str, command)), stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        def drain():
            for raw in self.process.stdout:
                self.logs.append(raw.decode('utf-8', 'replace').replace(self.password, '[redacted]').replace(self.admin, '[redacted]'))
        self.log_thread = threading.Thread(target=drain, daemon=True)
        self.log_thread.start()
        deadline = time.monotonic() + 30
        while True:
            if not self.native_record and f.run(['sudo', '-n', 'test', '-f', self.record], expected=None).returncode == 0:
                self.native_record = json.loads(f.sudo('cat', '--', self.record).stdout)
                self.stop_record = f.e / 'forge-stop-record.json'
                self.stop_record.write_text(json.dumps(self.native_record))
            if self.process.poll() is not None:
                raise RuntimeError('The owned Forge process ended: ' + ''.join(self.logs)[-1600:])
            try:
                with urllib.request.urlopen(f'http://127.0.0.1:{self.port}/health', timeout=1) as response:
                    if response.status == 200:
                        break
            except OSError:
                pass
            assert time.monotonic() < deadline, 'The owned Forge process did not become ready.'
            time.sleep(.1)
        self.native_record = json.loads(f.sudo('cat', '--', self.record).stdout)
        self.stop_record = f.e / 'forge-stop-record.json'
        self.stop_record.write_text(json.dumps(self.native_record))
        # Inspect actual help/version before key use. The binary is the full
        # single Forge artifact from the pinned, clean source export.
        for arguments in [('sign', '--version'), ('sign', 'keygen', '--help'), ('sign', 'headers', '--help')]:
            output = f.run([*f.prefix(SERVICE), self.binary, *arguments])
            (f.e / ('forge-' + '-'.join(arguments).replace('--', '') + '.txt')).write_text(output.stdout)
        self.key_dir = f.state / 'forge-identity'
        public = f.run([*f.prefix(SERVICE), self.binary, 'sign', 'keygen', '--name', 'factory-fixture',
                        '--key-dir', self.key_dir]).stdout.strip()
        # Exercise the real enrollment endpoint with the real signer challenge.
        help_text = f.run([self.binary, 'sign', 'enroll', '--help']).stdout
        (f.e / 'forge-enroll-help.txt').write_text(help_text)
        f.run([*f.prefix(SERVICE), self.binary, 'sign', 'enroll', '--name', 'factory-fixture',
               '--key-dir', self.key_dir, '--url', f'http://127.0.0.1:{self.port}'])
        rpc(self.endpoint, 'identity-approve', {'name': 'factory-fixture', 'public_key': public,
            'projects': ['factory-fixture'], 'capabilities': {'can_emit_seat_events': True}},
            {'Authorization': 'Bearer ' + self.admin})
        (f.e / 'real-forge-environment.json').write_text(json.dumps({'fixtureOnly': True,
            'sourceCommit': '97270040748040af4d7a9e7bd4ec9dd5e6af5b5e', 'containerId': self.container,
            'ownerLabel': self.run_id, 'imageId': image, 'databaseLoopbackOnly': True,
            'forgeLoopbackOnly': True, 'realModelProviders': False,
            'signer': str(self.binary), 'identity': 'factory-fixture'}, indent=2) + '\n')

    def close(self):
        if self.stopped:
            return
        self.stopped = True
        if self.process:
            if self.process.poll() is None:
                if not self.native_record and self.f.run(['sudo', '-n', 'test', '-f', self.record], expected=None).returncode == 0:
                    self.native_record = json.loads(self.f.sudo('cat', '--', self.record).stdout)
                    self.stop_record = self.f.e / 'forge-stop-record.json'
                    self.stop_record.write_text(json.dumps(self.native_record))
                if self.native_record:
                    self.f.sudo(sys.executable, self.f.helper, '--stop-record', self.stop_record)
            try:
                exit_code=self.process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                assert self.native_record, 'The Forge process identity is unavailable.'
                self.f.sudo(sys.executable,self.f.helper,'--stop-record',self.stop_record,'--stop-signal','KILL')
                exit_code=self.process.wait(timeout=10)
            self.f.commands.append({'ownedForgeExit':exit_code,'identity':self.native_record})
            self.log_thread.join(timeout=5)
            self.process.stdout.close()
            (self.f.e / 'forge-process.log').write_text(''.join(self.logs))
        if self.container:
            self.assert_container()
            self.docker('rm', '-f', '-v', self.container)
