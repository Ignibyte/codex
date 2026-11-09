#!/usr/bin/env python3
"""Bounded CF-011 operator test. Never copy a model-login credential."""
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import select
import signal
import subprocess
import sys
import time
import traceback
import tomllib
import uuid
from types import SimpleNamespace

from fleet_fixture import Fleet, GROUP, SERVICE
from session_fixture import Rpc, Tui, incarnation, sha256


def save(path, value):
    path.write_text(json.dumps(value, indent=2) + '\n')
    path.chmod(0o600)


def identity(profile, salt):
    """Read identity in memory. Retain neither JWT nor decoded private claims."""
    path = profile / 'auth.json'
    if path.is_symlink() or path.stat().st_mode & 0o077:
        raise RuntimeError('The login file permissions are invalid.')
    tokens = json.loads(path.read_text()).get('tokens') or {}
    segment = tokens['id_token'].split('.')[1]
    claims = json.loads(base64.urlsafe_b64decode(segment + '=' * (-len(segment) % 4)))
    subject, account = claims.get('sub'), tokens.get('account_id')
    if not subject or not account or not tokens.get('access_token'):
        raise RuntimeError('The profile requires a ChatGPT login.')
    return {key: hashlib.sha256((salt + value).encode()).hexdigest()
            for key, value in [('subject', subject), ('account', account)]}


def redact(value):
    if isinstance(value, dict):
        return {key: '[redacted]' if any(part in key.lower() for part in
                ['token', 'credential', 'email', 'authorization', 'privatekey'])
                else redact(item) for key, item in value.items()}
    if isinstance(value, list):
        return [redact(item) for item in value]
    if isinstance(value, str):
        return re.sub(r'\beyJ[A-Za-z0-9_-]{16,}\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+',
                      '[redacted-jwt]', value)
    return value


class Budget:
    def __init__(self, path, turns, seconds):
        self.path, self.turns, self.seconds = path, turns, seconds
        if path.exists():
            self.record = json.loads(path.read_text())
            if self.record['limitTurns'] != turns or self.record['limitSeconds'] != seconds:
                raise RuntimeError('The existing live budget differs.')
        else:
            self.record = {'limitTurns': turns, 'limitSeconds': seconds,
                           'startedAt': None, 'admissions': []}
            save(path, self.record)

    def check(self):
        started = self.record['startedAt']
        if started is not None and time.time() - started >= self.seconds:
            raise RuntimeError('The live time budget expired.')

    def admit(self, seat, kind):
        self.check()
        if len(self.record['admissions']) >= self.turns:
            raise RuntimeError('The submitted-turn budget expired.')
        if self.record['startedAt'] is None:
            self.record['startedAt'] = time.time()
        self.record['admissions'].append({'seat': seat, 'kind': kind, 'at': time.time()})
        save(self.path, self.record)
        # Interrupt blocked RPC/tool waits at the total run deadline as well.
        signal.signal(signal.SIGALRM, lambda *_: (_ for _ in ()).throw(
            TimeoutError('The live time budget expired.')))
        signal.setitimer(signal.ITIMER_REAL,
                         max(.001, self.seconds - (time.time() - self.record['startedAt'])))


class LiveSeat:
    """Own a native daemon in an operator-authenticated, dedicated profile."""
    def __init__(self, fleet, package, profile, name):
        self.fleet, self.profile, self.name = fleet, profile, name
        self.cli = package / 'bin/codex'
        self.subject = fleet.subjects[name]
        self.evidence = fleet.e / name
        self.evidence.mkdir(mode=0o700)
        self.project = fleet.base / name / 'project'
        fleet.sudo('install', '-d', '-o', os.getuid(), '-g', GROUP, '-m', '0700', self.project)
        fleet.run(['git', '-C', self.project, 'init', '--quiet'])
        self.binding = fleet.base / 'public' / (name + '-binding.json')
        fleet.sudo('install', '-o', SERVICE, '-g', GROUP, '-m', '0640', fleet.publish({
            'serviceUid': SERVICE, 'socket': str(fleet.socket),
            'credential': str(self.subject['credential']),
            'state': str(fleet.base / name / 'native-ledger'),
            'grant': self.subject['grant']}), self.binding)
        self.env = {'PATH': os.environ['PATH'], 'HOME': str(profile),
                    'CODEX_HOME': str(profile), 'TERM': 'xterm-256color',
                    'CODEX_FACTORY_BINDING': str(self.binding),
                    'CODEX_FACTORY_SERVICE_UID': str(SERVICE)}
        self.processes, self.connections, self.tuis = {}, [], []
        self.transcript, self.commands = [], []
        # Refuse to replace operator configuration or use an existing daemon.
        config = profile / 'config.toml'
        if config.exists() or (profile / 'app-server-daemon/daemon.pid').exists():
            raise RuntimeError('The dedicated profile must have no configuration or daemon.')
        self.config = config
        config.write_text('''model_provider = "factory-live"
approval_policy = "on-request"
sandbox_mode = "workspace-write"
cli_auth_credentials_store = "file"
[features]
plugins = false
multi_agent = false
api_key_model_discovery = false
[analytics]
enabled = false
[model_providers.factory-live]
name = "OpenAI"
wire_api = "responses"
requires_openai_auth = true
supports_websockets = true
supports_standalone_web_search = true
request_max_retries = 0
stream_max_retries = 0
''' + f'\n[projects.{json.dumps(str(self.project))}]\ntrust_level = "trusted"\n')
        config.chmod(0o600)
        self.config_hash = sha256(config)
        self.config_semantics = tomllib.loads(config.read_text())
        save(self.evidence / 'operator-config.json', self.config_semantics)
        state = profile / 'app-server-daemon'
        state.mkdir(mode=0o700, exist_ok=True)
        self.settings = state / 'settings.json'
        if self.settings.exists():
            self.config.unlink()
            raise RuntimeError('The dedicated profile already has daemon settings.')
        save(self.settings, {'shutdownGraceSeconds': 1, 'remoteControlEnabled': False,
                             'updater': {'autoUpdateEnabled': False}})
        self.started = False

    def lifecycle(self, action):
        argv = list(map(str, [self.cli, 'app-server', 'daemon', action]))
        spec = self.fleet.publish({'argv': argv, 'cwd': str(self.project), 'env': self.env})
        command = [*self.fleet.prefix(os.getuid()), sys.executable,
                   Path(__file__).with_name('live_command.py'), '--spec', spec]
        result = subprocess.run(list(map(str, command)), cwd=self.project, env=self.env,
                                capture_output=True, text=True, timeout=45)
        self.commands.append({'command': list(map(str, command)), 'exitCode': result.returncode,
                              'stdout': redact(result.stdout), 'stderr': redact(result.stderr)})
        if result.returncode:
            raise RuntimeError('The native daemon command failed: ' + action + ' ' + result.stderr[-1000:])
        value = json.loads(result.stdout)
        if value.get('pid'):
            self.processes[value['pid']] = incarnation(value['pid'])
        if action in ['start', 'restart']:
            self.started = True
        return value

    def connect(self):
        path = self.lifecycle('version')['socketPath']
        rpc = Rpc(path, self.transcript)
        self.connections.append(rpc)
        return rpc

    def start(self):
        self.lifecycle('start')
        self.rpc = self.connect()
        account = self.rpc.call('account/read', {'refreshToken': False})
        if (account.get('account') or {}).get('type') != 'chatgpt':
            raise RuntimeError('The native seat did not load its ChatGPT account.')
        thread = self.rpc.call('thread/start', {'cwd': str(self.project),
            'approvalPolicy': 'on-request', 'sandbox': 'workspace-write'})
        self.thread = thread['thread']['id']
        self.model = thread['model']
        save(self.evidence / 'thread-start.json', redact(thread))

    def user_turn(self, text, budget):
        budget.admit(self.name, 'user')
        return self.rpc.call('turn/start', {'threadId': self.thread,
            'input': [{'type': 'text', 'text': text}]})['turn']['id']

    def receive(self, message, budget, new=True):
        if new:
            budget.admit(self.name, 'coworker')
        result = self.rpc.call('thread/factoryMessage', {'threadId': self.thread,
            'project': 'fixture-project', 'messageId': message})['data']
        if new and result['state'] != 'started':
            raise RuntimeError('The coworker turn did not start: ' + json.dumps(result))
        return result

    def history(self):
        return self.rpc.call('thread/turns/list', {'threadId': self.thread, 'limit': 100})

    def wait(self, turn, budget, status='completed'):
        materialization_deadline = time.monotonic() + 10
        while True:
            budget.check()
            # Rpc.call retains server requests. Refuse unexpected approval, never answer it.
            for event in self.rpc.events:
                if 'id' in event and 'method' in event:
                    raise RuntimeError('The native seat requires an unexpected approval.')
                if event.get('method') == 'error':
                    raise RuntimeError('The native seat reported an error: ' + json.dumps(redact(event)))
            try:
                history = self.history()
            except RuntimeError as error:
                # Core can publish first-turn admission before the rollout header.
                # Poll that same turn. Never submit replacement work.
                if time.monotonic() < materialization_deadline and any(fragment in str(error) for fragment in
                        ['is not materialized yet', 'rollout at']) and any(fragment in str(error) for fragment in
                        ['before first user message', 'is empty']):
                    time.sleep(.1)
                    continue
                raise
            match = next((item for item in history['data'] if item['id'] == turn), None)
            terminal = next((event for event in self.rpc.events
                if event.get('method') == 'turn/completed'
                and event.get('params', {}).get('turn', {}).get('id') == turn), None)
            if terminal and match and match['status'] != 'inProgress':
                save(self.evidence / (turn + '.json'), redact(match))
                if terminal['params']['turn']['status'] != status or match['status'] != status:
                    raise RuntimeError('The native turn ended with ' + match['status'])
                return match
            time.sleep(.2)

    def text(self, turn):
        text = '\n'.join(item.get('text', '') for item in turn.get('items', [])
                         if item.get('type') == 'agentMessage')
        if not text.strip():
            raise RuntimeError('The native turn has no agent reply.')
        return text

    def reconnect_ui(self, expected):
        facade = SimpleNamespace(evidence=self.evidence, home=self.project.parent,
                                 cli=self.cli, env=self.env)
        for number in [1, 2]:
            tui = Tui(facade, self.thread)
            self.tuis.append(tui)
            tui.capture = self.evidence / ('tui-' + str(number) + '.raw')
            tui.wait_text(expected)
            tui.send(b'\x03')
            for _ in range(3):
                tui.pump(.1)
            if b'press Ctrl+C again' in tui.data or b'Press Ctrl+C again' in tui.data:
                tui.send(b'\x03')
            # Idle native UI normally exits directly. Record its actual exit.
            code = tui.wait_exit()
            if code != 0:
                raise RuntimeError('The native UI did not detach cleanly.')
            save(self.evidence / ('tui-' + str(number) + '.json'),
                 {'exitCode': code, 'identity': tui.identity, 'expectedTextVisible': True})
            # Preserve bytes and readable text separately.
            (self.evidence / ('tui-' + str(number) + '.txt')).write_text(
                re.sub(r'\x1b\[[0-?]*[ -/]*[@-~]', '', tui.data.decode('utf-8', 'replace')))

    def restart(self):
        self.rpc.close()
        self.lifecycle('restart')
        self.rpc = self.connect()
        resumed = self.rpc.call('thread/resume', {'threadId': self.thread})
        if resumed['thread']['id'] != self.thread:
            raise RuntimeError('The recipient conversation changed after restart.')
        save(self.evidence / 'resumed.json', redact(resumed))

    def close(self):
        for tui in self.tuis:
            tui.close()
        for rpc in self.connections:
            try:
                rpc.close()
            except OSError:
                pass
        try:
            if self.started:
                self.lifecycle('stop')
        finally:
            for pid, known in self.processes.items():
                if known and incarnation(pid) == known:
                    fd = os.pidfd_open(pid)
                    try:
                        if incarnation(pid) == known:
                            signal.pidfd_send_signal(fd, signal.SIGKILL)
                    finally:
                        os.close(fd)
            save(self.evidence / 'rpc.json', redact(self.transcript))
            save(self.evidence / 'commands.json', redact(self.commands))
            save(self.evidence / 'processes.json', self.processes)
            if self.config.exists():
                current = tomllib.loads(self.config.read_text())
                if current.get('tui') == {'screen_reader_detection_done': True}:
                    current.pop('tui')
                if current == self.config_semantics:
                    self.config.unlink()
                else:
                    raise RuntimeError('The operator configuration changed. Preserve it for review.')
            # Keep native history and operator logins. Settings belong only to this run.
            if self.settings.exists():
                self.settings.unlink()


def interrupt_live(worker, budget):
    turn = worker.user_turn('''Assignment: CF-011 — Interrupt boundary
Do: Run python3 -c "import time; time.sleep(8)" in the current repository.
Then reply READY. Do not change any files. Do not create another agent.''', budget)
    deadline = time.monotonic() + 15
    while True:
        budget.check()
        # This native inventory call drains notifications without reading a rollout
        # that can still be empty during first-turn admission.
        worker.rpc.call('thread/loaded/list', {})
        if any('id' in event and 'method' in event for event in worker.rpc.events):
            raise RuntimeError('The native seat requires an unexpected approval.')
        started = any(event.get('method') == 'turn/started'
                      and event.get('params', {}).get('turn', {}).get('id') == turn
                      for event in worker.rpc.events)
        if started:
            try:
                worker.rpc.call('turn/interrupt', {'threadId': worker.thread, 'turnId': turn})
                break
            except RuntimeError as error:
                if 'no active turn to interrupt' not in str(error):
                    raise
        if time.monotonic() > deadline:
            raise RuntimeError('The named turn did not become interruptible.')
        time.sleep(.05)
    worker.wait(turn, budget, status='interrupted')


def main():
    parser = argparse.ArgumentParser()
    for name in ['package', 'foreman-profile', 'worker-profile', 'evidence', 'budget']:
        parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--live', action='store_true')
    parser.add_argument('--interrupt-only', action='store_true')
    parser.add_argument('--max-turns', type=int, default=12)
    parser.add_argument('--max-seconds', type=int, default=1200)
    args = parser.parse_args()
    if not 1 <= args.max_turns <= 12 or not 1 <= args.max_seconds <= 1200:
        parser.error('The requested run exceeds the authorized bound.')
    os.umask(0o077)
    # A single operator owner prevents concurrent runs from racing the live budget.
    import fcntl
    args.budget.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    lock = (args.budget.parent / 'live-owner.lock').open('a')
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    salt = str(uuid.uuid4())
    identities = {'foreman': identity(args.foreman_profile, salt),
                  'worker': identity(args.worker_profile, salt)}
    if any(identities['foreman'][key] == identities['worker'][key]
           for key in ['account', 'subject']):
        raise RuntimeError('The two seats must use different accounts and subjects.')
    fleet = Fleet(args.package / 'bin/codex-factory-bridge', args.evidence)
    seats, checks, error = [], [], None
    budget = Budget(args.budget, args.max_turns, args.max_seconds)
    try:
        for name in ['sender', 'receiver']:
            fleet.subjects[name]['uid'] = os.getuid()
            fleet.sudo('chown', str(os.getuid()) + ':' + str(GROUP), fleet.base / name)
            fleet.enroll(name, ['observe', 'queue', 'acknowledge'])
            profile = args.foreman_profile if name == 'sender' else args.worker_profile
            seat = LiveSeat(fleet, args.package.resolve(), profile.resolve(), name)
            seats.append(seat)
            seat.start()
        foreman, worker = seats
        save(fleet.e / 'identities.json', {'salt': salt, 'digests': identities,
            'distinctAccountAndSubject': True, 'credentialTransfer': False,
            'sameUidIsolationLimit': True})
        checks.append('both_native_seats_load_distinct_operator_chatgpt_profiles')
        if args.live and args.interrupt_only:
            interrupt_live(worker, budget)
            checks.append('explicit_named_live_turn_interrupt_has_a_durable_interrupted_result')
        elif args.live:
            conversation = str(uuid.uuid4())
            messages = []

            def send(sender, receiver, kind, text, reply=None):
                message = {'messageId': str(uuid.uuid4()), 'conversationId': conversation,
                    'recipient': receiver.subject['seat'], 'project': 'fixture-project',
                    'kind': kind, 'mode': 'wake_when_idle', 'text': text,
                    'replyTo': reply, 'expiresInSeconds': 1200}
                sender.rpc.call('factory/send', message)
                messages.append(message)
                return message

            def consume(receiver, message):
                start = receiver.receive(message['messageId'], budget)
                turn = receiver.wait(start['turnId'], budget)
                state = receiver.receive(message['messageId'], budget, new=False)
                if state['state'] != 'completed':
                    raise RuntimeError('The native completed receipt is absent: ' + json.dumps(state))
                return turn, state

            ready = worker.user_turn('''Assignment: CF-011 — Worker scope
Do: Receive the next assignment from the foreman in this conversation.
Limits: Write only greeting.txt in the current repository. Use no network tools.
Do not read login files or other repositories. Do not create another agent.
Ask the foreman for any missing required value before you write.
Done when: Reply READY now. Do not create a file yet.''', budget)
            worker.wait(ready, budget)
            assignment = foreman.user_turn('''Assignment: CF-011 — Foreman scope
Do: Assign the worker to create greeting.txt in its current repository.
The exact greeting is CF-011 two-account proof followed by one newline.
First omit the greeting value from your assignment. Tell the worker to ask you for that value.
When the worker asks, reply with the exact value. When the worker reports its result, confirm receipt.
Limits: Use no tools. Do not create another agent. Return only your message to the worker.
Done when: Your first reply contains the assignment with the greeting value omitted.''', budget)
            first = send(foreman, worker, 'assignment', foreman.text(foreman.wait(assignment, budget)))
            question, original = consume(worker, first)
            if (worker.project / 'greeting.txt').exists():
                raise RuntimeError('The worker wrote before clarification.')
            clarification = send(worker, foreman, 'clarification', worker.text(question), first['messageId'])
            answer, _ = consume(foreman, clarification)
            reply = send(foreman, worker, 'reply', foreman.text(answer), clarification['messageId'])
            produced, _ = consume(worker, reply)
            artifact = worker.project / 'greeting.txt'
            if artifact.is_symlink() or artifact.read_bytes() != b'CF-011 two-account proof\n':
                raise RuntimeError('The worker artifact differs from the requested bytes.')
            save(fleet.e / 'artifact.json', {'path': 'greeting.txt', 'sha256': sha256(artifact),
                'bytes': artifact.stat().st_size, 'content': artifact.read_text()})
            fleet.run(['git', '-C', worker.project, 'add', '--', 'greeting.txt'])
            diff = fleet.run(['git', '-C', worker.project, 'diff', '--cached', '--binary']).stdout
            (fleet.e / 'worker-artifact.patch').write_text(diff)
            result = send(worker, foreman, 'result', worker.text(produced), first['messageId'])
            confirmation, _ = consume(foreman, result)
            save(fleet.e / 'foreman-confirmation.json', confirmation)
            checks.append('live_assignment_clarification_reply_artifact_and_correlated_result')
            worker.reconnect_ui('greeting.txt')
            checks.append('two_native_tui_attachments_display_the_preserved_worker_history')
            duplicate = worker.receive(first['messageId'], budget, new=False)
            if duplicate.get('turnId') != original['turnId'] or duplicate['state'] != 'completed':
                raise RuntimeError('The repeated assignment changed its actual turn.')
            worker.restart()
            after = worker.receive(first['messageId'], budget, new=False)
            if after != duplicate or sha256(artifact) != json.loads((fleet.e / 'artifact.json').read_text())['sha256']:
                raise RuntimeError('The restart changed the assignment or artifact.')
            checks.append('recipient_restart_and_repeated_assignment_preserve_turn_receipts_and_artifact')
            interrupt_live(worker, budget)
            checks.append('explicit_named_live_turn_interrupt_has_a_durable_interrupted_result')
            save(fleet.e / 'messages.json', messages)
            save(fleet.e / 'service-summary.json', fleet.inspect())
        else:
            checks.append('preflight_only_no_model_turns_submitted')
    except Exception:
        error = traceback.format_exc()
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        cleanup_errors = []
        for seat in reversed(seats):
            try:
                seat.close()
            except Exception:
                cleanup_errors.append(traceback.format_exc())
        try:
            fleet.close()
        except Exception:
            cleanup_errors.append(traceback.format_exc())
        if cleanup_errors:
            error = (error or '') + '\n'.join(cleanup_errors)
        save(args.evidence / 'result.json', {'passed': error is None, 'live': args.live,
            'checks': checks, 'error': redact(error), 'budget': budget.record,
            'packageManifestSha256': sha256(args.package / 'factory-package.json'),
            'runnerSha256': sha256(__file__), 'profilesRetained': True,
            'privateScratchRemoved': not fleet.base.exists()})
    print(json.dumps({'passed': error is None, 'checks': checks, 'error': redact(error)}), flush=True)
    return 0 if error is None else 1


if __name__ == '__main__':
    raise SystemExit(main())
