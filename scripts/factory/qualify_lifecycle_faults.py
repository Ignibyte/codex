#!/usr/bin/env python3
"""Kill actual bridge processes between durable recovery writes, then restart."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import traceback
import uuid

from fleet_fixture import Fleet, SERVICE, result


def lane(binary, evidence, point):
    f = Fleet(binary, evidence)
    checks, error = [], None
    try:
        f.enroll('sender')
        f.enroll('receiver')
        message = f.envelope()
        f.send(message)
        old_key = f.sudo('sha256sum', f.state / 'service.key').stdout.split()[0]
        f.stop()
        epoch = json.loads(f.sudo('cat', f.state / 'lifecycle.json').stdout)
        epoch['boot'] = str(uuid.uuid4())
        source = f.publish(epoch)
        f.sudo('install', '-o', SERVICE, '-g', SERVICE, '-m', '0600', source, f.state / 'lifecycle.json')
        arm, hit = f.state / ('fault.' + point), f.state / ('hit.' + point)
        f.run([*f.prefix(SERVICE), 'touch', arm])
        f.run([*f.prefix(SERVICE), 'chmod', '0600', arm])
        owner = f.state / 'recovery-owner.json'
        spec = f.publish({'argv': [str(f.binary), 'serve', '--state', str(f.state), '--socket', str(f.socket)],
                          'cwd': str(f.state), 'env': {'PATH': os.environ['PATH']}, 'record': str(owner)})
        command = [*f.prefix(SERVICE), sys.executable, Path(__file__).with_name('live_command.py'), '--spec', spec]
        with (f.e / 'partial-start.log').open('w') as log:
            f.server = subprocess.Popen(list(map(str, command)), stdout=subprocess.PIPE, stderr=log, text=True)
        deadline = time.monotonic() + 8
        while f.run(['sudo', '-n', 'test', '-f', owner], expected=None).returncode:
            assert f.server.poll() is None and time.monotonic() < deadline
            time.sleep(.01)
        f.record = json.loads(f.sudo('cat', owner).stdout)
        (f.e / 'stop-record.json').write_text(json.dumps(f.record))
        f.commands.append({'command': list(map(str, command)), 'incarnation': f.record})
        while f.run(['sudo', '-n', 'test', '-f', hit], expected=None).returncode:
            assert f.server.poll() is None and time.monotonic() < deadline
            time.sleep(.01)
        f.sudo(sys.executable, f.helper, '--stop-record', f.e / 'stop-record.json', '--stop-signal', 'KILL')
        f.server.wait(timeout=10)
        f.stop()
        # Inspect after process death releases its registry lock, before another owner starts.
        fenced = f.inspect()
        assert all(s['revoked'] for s in fenced['seats'])
        assert fenced['mail'][0]['state'] == 'quarantined'
        assert json.loads(f.sudo('cat', f.state / 'lifecycle.json').stdout)['boot'] == epoch['boot']
        f.sudo('rm', '--', arm, hit)
        f.start()
        recovered = f.inspect()
        assert recovered['mail'] == fenced['mail']
        assert all(s['revoked'] for s in recovered['seats'])
        assert f.sudo('sha256sum', f.state / 'service.key').stdout.split()[0] != old_key
        f.wire('sender', {'method': 'peers', 'protocol': 1, 'request': f.request('sender')}, expected='credential_denied')
        actual = json.loads(f.admin('recovery-status', '--state', f.state).stdout)
        assert actual['epoch']['boot'] == actual['currentBoot']
        checks.append({'point': point, 'authorityRevokedBeforeEpochCommit': True,
                       'restartCompletesRecovery': True, 'pendingWorkReplayed': False,
                       'faultInput': 'operator injects a previous boot epoch; actual kernel reboot has a separate KVM journey'})
    except Exception:
        error = traceback.format_exc()
    finally:
        f.close()
    return result(evidence, checks, error)


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--binary', required=True)
    p.add_argument('--evidence', type=Path, required=True)
    a = p.parse_args()
    a.evidence.mkdir(mode=0o700, parents=True, exist_ok=False)
    exits = [lane(a.binary, a.evidence / point, point) for point in ('after_recovery_fence', 'before_recovery_epoch')]
    record = {'passed': not any(exits), 'exit_code': int(any(exits)), 'lanes': exits}
    (a.evidence / 'result.json').write_text(json.dumps(record, indent=2))
    return record['exit_code']


if __name__ == '__main__':
    raise SystemExit(main())
