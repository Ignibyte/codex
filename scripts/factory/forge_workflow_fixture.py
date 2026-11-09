"""Signed calls to the owned real Forge; never retain a bearer assertion."""
import json
import subprocess
import urllib.request
import uuid
from pathlib import Path

from fleet_fixture import Fleet, SERVICE, GROUP
from real_forge_fixture import RealForge


class Workflow:
    def __init__(self, fleet, forge):
        self.f, self.forge = fleet, forge
        self.calls = []

    def headers(self):
        argv = [*self.f.prefix(65534), self.forge.binary, 'sign', 'headers', '--name',
                'factory-fixture', '--key-dir', self.forge.key_dir, '--ttl', '60']
        response = subprocess.run(list(map(str, argv)), capture_output=True, text=True, timeout=5)
        self.f.commands.append({'command': list(map(str, argv)), 'exit_code': response.returncode,
                                'stdout': 'credential headers redacted'})
        assert response.returncode == 0, 'The owned signer failed.'
        return json.loads(response.stdout)

    def request(self, method, params):
        request_id = str(uuid.uuid4())
        data = json.dumps({'jsonrpc': '2.0', 'id': request_id, 'method': method, 'params': params}).encode()
        request = urllib.request.Request(self.forge.endpoint, data=data, headers={
            **self.headers(), 'Content-Type': 'application/json', 'Accept': 'application/json, text/event-stream'})
        with urllib.request.urlopen(request, timeout=12) as response:
            text = response.read(4 * 1024 * 1024).decode()
        wire = json.loads(text) if text.lstrip().startswith('{') else next(
            json.loads(line[6:]) for line in text.splitlines() if line.startswith('data: '))
        assert wire['id'] == request_id and 'error' not in wire, wire
        return wire['result']

    def call(self, name, arguments, expect_error=False):
        response = self.request('tools/call', {'name': name, 'arguments': arguments})
        self.calls.append({'tool': name, 'arguments': arguments, 'response': response})
        if expect_error:
            assert response.get('isError'), response
            return response
        assert not response.get('isError'), response
        return response.get('structuredContent') or json.loads(response['content'][0]['text'])


class ForemanFleet(Fleet):
    """Owned canonical Forge, isolated repository and two explicitly scoped worker seats."""
    def __init__(self, binary, forge_binary, forge_source, evidence, foreman_ack=False):
        super().__init__(binary, evidence)
        self.forge = None
        try:
            self.subjects['replacement'] = {'uid': 65531, 'seat': str(uuid.uuid4()), 'sequence': 0}
            self.sudo('install', '-d', '-o', '65531', '-g', GROUP, '-m', '0700', self.base / 'replacement')
            self.enroll('sender', ['observe', 'queue', 'acknowledge'] if foreman_ack else None, role='foreman')
            for name in ('receiver', 'replacement'):
                self.enroll(name, ['observe', 'queue', 'acknowledge'])
            self.forge = RealForge(self, forge_binary, forge_source)
            self.workflow = Workflow(self, self.forge)
            forge_config = self.state / 'workflow-forge.json'
            value = {'endpoint': self.forge.endpoint, 'forgeIdentity': 'factory-fixture',
                'forgeProject': self.forge.project, 'localProject': 'fixture-project',
                'signerArgv': [str(self.forge.binary), 'sign', 'headers', '--name', 'factory-fixture',
                               '--key-dir', str(self.forge.key_dir), '--ttl', '60']}
            self.sudo('install', '-o', SERVICE, '-g', GROUP, '-m', '0600', self.publish(value), forge_config)
            self.repository, self.worktrees = self.base / 'repository', self.base / 'worktrees'
            self.sudo('install', '-d', '-o', SERVICE, '-g', GROUP, '-m', '0750', self.repository)
            self.sudo('install', '-d', '-o', SERVICE, '-g', GROUP, '-m', '0711', self.worktrees)
            self.git('init', '-b', 'main')
            self.git('-c', 'user.name=Fixture', '-c', 'user.email=fixture@invalid',
                     'commit', '--allow-empty', '-m', 'Owned fixture baseline')
            self.config = self.state / 'foreman-config.json'
            value = {'bridgeState': str(self.state), 'forgeConfig': str(forge_config),
                'foreman': self.subjects['sender']['seat'], 'repository': str(self.repository),
                'worktreeRoot': str(self.worktrees)}
            self.sudo('install', '-o', SERVICE, '-g', GROUP, '-m', '0600', self.publish(value), self.config)
        except Exception:
            self.close()
            raise

    def git(self, *args):
        return self.run([*self.prefix(SERVICE), 'git', '-C', self.repository, *args])

    def action(self, value, expected=0):
        response = self.admin('foreman', '--state', self.state / 'foreman', '--config', self.config,
                              '--input', self.publish(value), expected=expected)
        return json.loads(response.stdout) if not response.returncode else response

    def ticket(self, label):
        return self.workflow.call('ticket-create', {'title': label,
            'description': 'Do: Execute only the bounded fixture specification. Retain the evidence.',
            'type': 'spike', 'project_id': self.forge.project})['ticket']

    def claim(self, ticket, name='receiver', operation=None):
        return self.action({'action': 'claim', 'operation_id': operation or str(uuid.uuid4()),
            'ticket': ticket['number'], 'worker': self.subjects[name]['seat'],
            'context': 'This is an isolated local fixture. Use no real account or production data.',
            'brief': 'Acknowledge the required context. Wait for the executable specification.'})

    def workspace(self, claim, name='receiver'):
        path = self.worktrees / claim['runId']
        self.git('worktree', 'add', '-b', claim['workBranch'], path, claim['baseCommit'])
        self.sudo('chown', '-R', str(self.subjects[name]['uid']) + ':' + str(GROUP), path)
        self.sudo('chmod', '0750', path)
        self.action({'action': 'register_workspace', 'run_id': claim['runId'], 'workspace': str(path)})
        return path

    def synchronize(self, name='receiver'):
        actor = self.subjects[name]
        actor['sequence'] = next(seat['lastSequence'] for seat in self.inspect()['seats'] if seat['seat'] == actor['seat'])

    def close(self):
        if hasattr(self, 'workflow'):
            (self.e / 'workflow-calls.json').write_text(json.dumps(self.workflow.calls, indent=2))
        if self.forge:
            self.forge.close()
        super().close()
