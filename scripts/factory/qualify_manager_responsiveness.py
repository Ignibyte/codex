#!/usr/bin/env python3
"""Actual private Forge reply hold, six core seats, admission bounds and revocation."""
import argparse
import json
from pathlib import Path
import select
import subprocess
import sys
import time
import traceback
import uuid

from fleet_fixture import Fleet, GROUP, SERVICE, result
from forge_workflow_fixture import Workflow
from real_forge_fixture import ForgeProxy, RealForge


def main():
    parser = argparse.ArgumentParser()
    for key in ('binary', 'forge-binary', 'forge-source', 'evidence'):
        parser.add_argument('--' + key, required=True)
    parser.add_argument('--expect-delayed', action='store_true')
    a = parser.parse_args()
    f, forge, proxy = Fleet(a.binary, a.evidence), None, None
    pending, flood, checks, error = None, None, [], None
    observed = {}
    try:
        workers = ['worker' + str(i) for i in range(4)]
        for i, name in enumerate(workers):
            f.subjects[name] = {'uid': 65531 - i, 'seat': str(uuid.uuid4()), 'sequence': 0}
            f.sudo('install', '-d', '-o', str(65531 - i), '-g', GROUP, '-m', '0700', f.base / name)
        contacts = {'sender': ['receiver'], 'receiver': ['sender', *workers],
            **{name: ['receiver'] for name in workers}}
        for name, peers in contacts.items():
            f.enroll(name, ['observe', 'queue', 'acknowledge'],
                role='manager' if name == 'sender' else 'foreman' if name == 'receiver' else 'worker',
                recipients=[f.subjects[x]['seat'] for x in [name, *peers]])
        forge = RealForge(f, a.forge_binary, a.forge_source)
        workflow = Workflow(f, forge)
        ticket = workflow.call('ticket-create', {'title': 'Held specification reply concurrency fixture',
            'description': 'Hold one actual spec acknowledgement while the four workers communicate.',
            'type': 'docs', 'project_id': forge.project})['ticket']
        proxy = ForgeProxy(forge.endpoint)
        proxy.mode = 'hold_spec_ack'
        job, operation = str(uuid.uuid4()), str(uuid.uuid4())
        forge_config, config = f.state / 'manager-forge.json', f.state / 'manager-config.json'
        value = {'endpoint': proxy.endpoint, 'forgeIdentity': 'factory-fixture',
            'forgeProject': forge.project, 'localProject': 'fixture-project',
            'signerArgv': [str(forge.binary), 'sign', 'headers', '--name', 'factory-fixture',
                '--key-dir', str(forge.key_dir), '--ttl', '60', '--session', 'cf-spec-' + job]}
        f.sudo('install', '-o', SERVICE, '-g', GROUP, '-m', '0600', f.publish(value), forge_config)
        value = {'id': job, 'bridgeState': str(f.state), 'forgeConfig': str(forge_config),
            'manager': f.subjects['sender']['seat'], 'foreman': f.subjects['receiver']['seat'], 'ticket': ticket['number']}
        f.sudo('install', '-o', SERVICE, '-g', GROUP, '-m', '0600', f.publish(value), config)
        f.admin('manager-prepare', '--state', f.state / 'manager', '--config', config)
        actor = f.subjects['sender']
        frame = {'method': 'manager_spec', 'protocol': 1, 'request': f.request('sender', 'acknowledge'),
            'job_id': job, 'operation_id': operation, 'tool': 'run-start', 'arguments': {}}
        command = [*f.prefix(actor['uid']), sys.executable, f.helper, '--socket', f.socket,
            '--credential', actor['credential'], '--wire', f.publish(frame)]
        pending = subprocess.Popen(list(map(str, command)), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        f.commands.append({'command': list(map(str, command))})
        assert proxy.blocked.wait(8), 'The actual Forge did not reach the held reply boundary.'
        start = time.monotonic()
        latencies, envelopes = [], []
        for name in workers:
            before = time.monotonic()
            envelope = f.envelope(name, 'receiver')
            f.send(envelope, name=name)
            latencies.append(time.monotonic() - before)
            envelopes.append(envelope)
        for envelope in envelopes:
            f.receive(envelope['messageId'], name='receiver')
        elapsed = time.monotonic() - start
        observed.update(workerSendSeconds=latencies, allWorkerRoundTripsSeconds=elapsed,
            heldReplyTimedOut=proxy.hold_timed_out, blocked=proxy.blocked.is_set(),
            releasedByTest=proxy.resume.is_set())
        if a.expect_delayed:
            assert elapsed > 3 and proxy.hold_timed_out, observed
            checks.append('previous_sequential_bridge_reproduces_cross_seat_delay_while_forge_reply_is_held')
        else:
            assert elapsed < 1.5 and not proxy.hold_timed_out and not proxy.resume.is_set(), observed
            checks.append('four_workers_send_and_foreman_receives_while_actual_manager_forge_reply_remains_held')
            actor['sequence'] = next(s['lastSequence'] for s in f.inspect()['seats'] if s['seat'] == actor['seat'])
            receipt = f.wire('sender', {'method': 'manager_operation', 'protocol': 1,
                'request': f.request('sender'), 'job_id': job, 'operation_id': operation})
            assert receipt['state'] == 'uncertain', receipt
            f.wire('sender', {'method': 'manager_spec', 'protocol': 1,
                'request': f.request('sender', 'acknowledge'), 'job_id': job,
                'operation_id': str(uuid.uuid4()), 'tool': 'run-status', 'arguments': {}}, expected='manager_job_busy')
            actor['sequence'] = next(s['lastSequence'] for s in f.inspect()['seats'] if s['seat'] == actor['seat'])
            checks.append('pending_receipt_is_readable_and_second_same_job_operation_returns_busy_without_waiting')
            f.admin('revoke', '--state', f.state, '--seat', actor['seat'])
            f.wire('sender', {'method': 'manager_operation', 'protocol': 1,
                'request': f.request('sender'), 'job_id': job, 'operation_id': operation}, expected='credential_revoked')
            checks.append('revocation_blocks_new_frames_while_an_earlier_admitted_effect_remains_in_progress')
        proxy.resume.set()
        stdout, stderr = pending.communicate(timeout=12)
        f.commands.append({'held_manager_exit': pending.returncode, 'stdout': stdout, 'stderr': stderr})
        assert pending.returncode == 0 and not stderr, stderr
        returned = json.loads(stdout)
        assert returned['ok'] and returned['state'] == 'returned', returned
        assert sum(x['method'] == 'run-start' for x in proxy.forwarded) == 1
        pending = None
        if not a.expect_delayed:
            # One bounded UID-owned process holds 33 transports. At least one exceeds
            # sixteen request owners plus sixteen queued connections.
            code = '''import json,select,socket,sys
sockets=[]
try:
 for _ in range(33):
  s=socket.socket(socket.AF_UNIX);s.settimeout(2);s.connect(sys.argv[1]);sockets.append(s)
 ready=select.select(sockets,[],[],2)[0]
 assert ready,'No excess transport returned a response.'
 print(json.dumps({'responses':[s.recv(256).decode() for s in ready]}),flush=True)
 sys.stdin.readline()
finally:
 for s in sockets:s.close()
'''
            command = [*f.prefix(f.subjects['worker0']['uid']), sys.executable, '-c', code, str(f.socket)]
            flood = subprocess.Popen(list(map(str, command)), stdin=subprocess.PIPE,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            f.commands.append({'command': list(map(str, command))})
            assert select.select([flood.stdout], [], [], 3)[0]
            busy = json.loads(flood.stdout.readline())
            assert any(json.loads(reply) == {'ok': False, 'error': 'service_busy'} for reply in busy['responses']), busy
            task_names = f.sudo('python3', '-c', 'import json,sys;from pathlib import Path;print(json.dumps([(p/"comm").read_text().strip() for p in (Path("/proc")/sys.argv[1]/"task").iterdir()]))', str(f.record['pid']))
            names = json.loads(task_names.stdout)
            assert sum(n.startswith('factory-request') for n in names) == 16, names
            observed.update(threadNames=names, overloadResponse=busy)
            flood.stdin.write('release\n')
            flood.stdin.flush()
            stdout, stderr = flood.communicate(timeout=8)
            f.commands.append({'flood_exit': flood.returncode, 'stdout': stdout, 'stderr': stderr})
            assert flood.returncode == 0 and not stderr, stderr
            flood = None
            f.send(f.envelope('worker0', 'receiver'), name='worker0')
            checks.append('sixteen_request_owners_and_sixteen_queued_transports_refuse_excess_and_resume_after_release')
            f.stop()
            f.start()
            f.send(f.envelope('worker1', 'receiver'), name='worker1')
            checks.append('worker_traffic_and_revocation_survive_service_restart_after_held_and_overloaded_connections')
    except Exception:
        error = traceback.format_exc()
    finally:
        if proxy:
            proxy.resume.set()
        for process in (pending, flood):
            if process is not None:
                if process.stdin is not None:
                    process.stdin.close()
                    process.stdin = None
                stdout, stderr = process.communicate(timeout=15)
                f.commands.append({'remaining_owner_exit': process.returncode, 'stdout': stdout, 'stderr': stderr})
        if proxy:
            observed['forwarded'] = proxy.forwarded
            proxy.close()
        if forge:
            forge.close()
        (f.e / 'observed.json').write_text(json.dumps(observed, indent=2) + '\n')
        f.close()
    return result(a.evidence, checks, error)


if __name__ == '__main__':
    raise SystemExit(main())
