#!/usr/bin/env python3
"""Actual four/seven native workers, canonical ownership, isolation and worker loss.

Providers are scripted. Account entitlement and subscription capacity remain live
criteria and cannot be established by this fixture.
"""
import argparse
import hashlib
import json
from pathlib import Path
import shlex
import time
import traceback
import uuid

from fleet_fixture import GROUP, SERVICE, result
from forge_workflow_fixture import ForemanFleet
from native_fixture import Native
from session_fixture import Model, incarnation


class ScaleFleet(ForemanFleet):
    workers = ['receiver', 'replacement', *['worker' + str(i) for i in range(2, 7)]]

    def enroll(self, name, operations=None, role='worker', projects=None, recipients=None):
        if name == 'sender':
            for index, extra in enumerate([*self.workers[2:], 'manager']):
                self.subjects[extra] = {'uid': 65530 - index, 'seat': str(uuid.uuid4()), 'sequence': 0}
                self.sudo('install', '-d', '-o', 65530 - index, '-g', GROUP, '-m', '0700', self.base / extra)
        peers = [name, 'sender']
        if name == 'sender':
            peers = ['sender', 'manager', *self.workers]
        return super().enroll(name, operations or ['observe', 'queue', 'acknowledge'], role,
                              projects, [self.subjects[p]['seat'] for p in peers])

    def complete_roster(self):
        for name in self.workers[2:]:
            self.enroll(name)
        self.enroll('manager', role='manager')


def main():
    parser = argparse.ArgumentParser()
    for name in ('binary', 'cli', 'forge-binary', 'forge-source', 'evidence'):
        parser.add_argument('--' + name, required=True)
    args = parser.parse_args()
    fleet = ScaleFleet(args.binary, args.forge_binary, args.forge_source, args.evidence, foreman_ack=True)
    fleet.complete_roster()
    natives, models, jobs, measurements, checks = [], [], [], [], []
    error = None
    try:
        for name in ('sender', 'manager'):
            model = Model()
            models.append(model)
            native = Native(fleet, args.cli, model, name=name, supervised=True)
            natives.append(native)
        coordinator_count = len(natives)

        def prepare(name):
            ticket = fleet.ticket('CF-019 concurrent isolated work for ' + name)
            claim = fleet.claim(ticket, name=name)
            duplicate = fleet.claim(ticket, name=name, operation=claim['generation'])
            assert duplicate['runId'] == claim['runId']
            workspace = fleet.workspace(claim, name=name)
            fleet.receive(claim['briefMessageId'], name=name)
            fleet.wire(name, {'method': 'acknowledge_context', 'protocol': 1,
                'request': fleet.request(name, 'acknowledge'), 'run_id': claim['runId'],
                'context_digest': claim['contextDigest']})
            model = Model()
            models.append(model)
            native = Native(fleet, args.cli, model, name=name, managed=True,
                            supervised=True, sandbox='workspace-write')
            natives.append(native)
            fleet.action({'action': 'register_execution', 'run_id': claim['runId'], 'pid': native.identity['pid']})
            queued = time.monotonic()
            spec = fleet.action({'action': 'issue_spec', 'run_id': claim['runId'], 'lease_seconds': 300,
                'spec': 'Do: Write the owned result. Verify the workspace boundary. Use only the local fixture.'})
            assert fleet.action({'action': 'issue_spec', 'run_id': claim['runId'], 'lease_seconds': 300,
                'spec': 'Do: Write the owned result. Verify the workspace boundary. Use only the local fixture.'})['messageId'] == spec['messageId']
            artifact = workspace / 'result.txt'
            outside = fleet.base / name / 'outside-worktree'
            other = jobs[0]['workspace'] / 'unexpected-peer-write' if jobs else fleet.base / name / 'outside-peer'
            code = ('from pathlib import Path\n'
                    f'p=Path({str(artifact)!r});p.write_text({name!r});p.chmod(0o640)\n'
                    f'for target in [{str(outside)!r},{str(other)!r}]:\n'
                    ' try: Path(target).write_text("UNEXPECTED")\n'
                    ' except PermissionError: pass\n'
                    ' else: raise RuntimeError("outside_write_permitted")\n')
            model.outputs[0] = {'type': 'function_call', 'name': 'exec_command', 'call_id': 'owned_' + name,
                'arguments': json.dumps({'cmd': 'python3 -c ' + shlex.quote(code), 'yield_time_ms': 1000})}
            thread = native.request('thread/start', {'cwd': str(workspace)})['thread']['id']
            params = {'threadId': thread, 'project': 'fixture-project', 'messageId': spec['messageId']}
            started = native.request('thread/factoryMessage', params)['data']
            model.wait_request(0)
            admission = time.monotonic()
            assert claim['contextDigest'] in json.dumps(model.requests[0])
            assert 'isolated local fixture' in json.dumps(model.requests[0])
            model.release(0)
            model.wait_request(1)
            assert fleet.sudo('cat', artifact).stdout == name
            for target in (outside, other):
                assert fleet.run(['sudo', '-n', 'test', '!', '-e', target], expected=None).returncode == 0
            job = {'name': name, 'ticket': ticket, 'claim': claim, 'workspace': workspace,
                'native': native, 'model': model, 'thread': thread, 'params': params,
                'turn': started['turnId'], 'artifact': artifact, 'queueSeconds': admission - queued}
            jobs.append(job)
            return job

        def measure(count):
            selected = jobs[:count]
            rows = []
            for native in [*natives[:coordinator_count], *[j['native'] for j in selected]]:
                owner = native.identity
                assert incarnation(owner['pid']) == [owner['bootId'], owner['startTicks']]
                cgroup = Path('/sys/fs/cgroup') / Path(f'/proc/{owner["pid"]}/cgroup').read_text().strip().split('::', 1)[1].lstrip('/')
                rows.append({'name': native.name, 'identity': owner, 'cgroup': str(cgroup),
                             'memoryCurrent': int((cgroup / 'memory.current').read_text()),
                             'memoryPeak': int((cgroup / 'memory.peak').read_text()),
                             'tasksCurrent': int((cgroup / 'pids.current').read_text()),
                             'cpuStat': (cgroup / 'cpu.stat').read_text()})
            assert len(rows) == count + 2
            measurements.append({'workers': count, 'coordinators': 2, 'at': time.time(),
                'processes': rows, 'queueSeconds': [j['queueSeconds'] for j in selected],
                'accountLimits': 'not measured: scripted providers', 'realAccountModelTurns': 0})

        for name in fleet.workers[:4]:
            prepare(name)
        measure(4)
        checks.append('four_actual_concurrent_workers_and_two_coordinators_have_unique_runs_worktrees_and_owned_results')
        for name in fleet.workers[4:]:
            prepare(name)
        measure(7)
        checks.append('temporary_seven_worker_native_capacity_and_real_cgroup_queue_measurements')

        lost = jobs[0]
        refused = fleet.action({'action': 'fence', 'run_id': lost['claim']['runId']}, expected=1)
        assert 'foreman_process_set_still_live' in refused.stderr
        lost['native'].stop(crash=True)
        for job in jobs[1:]:
            job['model'].release(1)
        for job in jobs[1:]:
            native = job['native']
            terminal = native.event('turn/completed', job['turn'])
            assert terminal['params']['turn']['status'] == 'completed'
            assert native.request('thread/factoryMessage', job['params'])['data']['state'] == 'completed'
            context = fleet.workflow.call('pipeline-context', {'ticket_number': job['ticket']['number'],
                                                               'project_id': fleet.forge.project})
            assert context['run']['id'] == job['claim']['runId'] and context['run']['status'] == 'running'
        checks.append('six_independent_workers_finish_after_one_native_worker_is_killed')

        run = lost['claim']['runId']
        assert fleet.action({'action': 'fence', 'run_id': run})['disposition'] == 'fenced'
        directory = fleet.state / 'foreman/reconciliations'
        fleet.sudo('install', '-d', '-o', SERVICE, '-g', GROUP, '-m', '0700', directory)
        report = directory / (run + '.json')
        receipt = {'runId': run, 'generation': lost['claim']['generation'],
            'confirmedNoOutstandingEffects': True,
            'evidence': ['The fixture only wrote its owned result. Both forbidden writes failed.',
                         'The exact native incarnation and its cgroup ended before replacement.']}
        fleet.sudo('install', '-o', SERVICE, '-g', GROUP, '-m', '0600', fleet.publish(receipt), report)
        fleet.action({'action': 'reconcile', 'run_id': run, 'report': str(report)})
        replacement = fleet.claim(lost['ticket'], name=lost['name'])
        assert replacement['runId'] != run and replacement['generation'] != lost['claim']['generation']
        current = fleet.workflow.call('pipeline-context', {'ticket_number': lost['ticket']['number'],
                                                          'project_id': fleet.forge.project})
        assert current['run']['id'] == replacement['runId'] and current['run']['status'] == 'running'
        assert fleet.forge.sql('SELECT count(*) FROM pipeline_runs WHERE status=\'running\';') == '7'
        checks.append('replacement_follows_exact_process_fence_and_effect_reconciliation_with_seven_canonical_owners')
        (fleet.e / 'scale-measurements.json').write_text(json.dumps(measurements, indent=2) + '\n')
        (fleet.e / 'ownership.json').write_text(json.dumps({'runs': [j['claim'] for j in jobs],
            'replacement': replacement, 'artifactHashes': {j['name']: hashlib.sha256(j['name'].encode()).hexdigest()
                                                          for j in jobs}}, indent=2) + '\n')
    except Exception:
        error = traceback.format_exc()
    finally:
        for model in models:
            model.close()
        for native in reversed(natives):
            native.close()
        fleet.close()
    return result(args.evidence, checks, error)


if __name__ == '__main__':
    raise SystemExit(main())
