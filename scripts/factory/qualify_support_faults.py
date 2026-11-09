"""Kill exact one-shot owners at durable plan, enqueue, relay and archive boundaries."""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import time
import traceback
import uuid

from fleet_fixture import Fleet, GROUP, result
from session_fixture import incarnation


def main():
    parser = argparse.ArgumentParser()
    for key in ('binary', 'evidence'):
        parser.add_argument('--' + key, required=True)
    args = parser.parse_args()
    fleet = Fleet(args.binary, args.evidence)
    checks, error, active = [], None, None
    record_path = None
    active_unit = None
    try:
        fleet.enroll('sender', ['observe', 'queue', 'acknowledge'], role='support')
        fleet.enroll('receiver', ['observe', 'queue', 'acknowledge'], role='manager')
        actor = fleet.subjects['sender']
        def configuration(alert=True):
            return {'grant': actor['grant'], 'project': 'fixture-project',
                'recipient': fleet.subjects['receiver']['seat'], 'socket': str(fleet.socket),
                'credential': str(actor['credential']), 'serviceUid': 65534,
                'startAt': int(time.time()) - 10, 'intervalSeconds': 1,
                'retentionSlots': 2, 'maxArchives': 16,
                'probes': {'disks': [], 'usage': {'warning': 1 if alert else 9999,
                    'critical': 2 if alert else 10000}, 'pressure': {'warning': 9999, 'critical': 10000}}}
        def call(name, action):
            state = fleet.base / 'sender' / name
            r = fleet.run([*fleet.prefix(actor['uid']), fleet.binary, 'support', '--state', state,
                           '--input', fleet.publish(action)])
            return json.loads(r.stdout)
        def stored(name):
            return json.loads(fleet.run([*fleet.prefix(actor['uid']), 'cat',
                fleet.base / 'sender' / name / 'registry.json']).stdout)
        def crash(name, point):
            nonlocal active, record_path, active_unit
            state = fleet.base / 'sender' / name
            arm, hit = state / ('fault.' + point), state / ('hit.' + point)
            fleet.run([*fleet.prefix(actor['uid']), 'touch', arm])
            fleet.run([*fleet.prefix(actor['uid']), 'chmod', '0600', arm])
            record = fleet.base / 'sender' / (name + '-owner.json')
            argv = list(map(str, [fleet.binary, 'support', '--state', state,
                                 '--input', fleet.publish({'action': 'tick'})]))
            code = ('import os,json,pathlib\n'
                f'p=pathlib.Path({str(record)!r})\n'
                's=pathlib.Path("/proc/self/stat").read_text().rsplit(")",1)[1].split()\n'
                f'r={{"pid":os.getpid(),"uid":os.geteuid(),"exe":{str(fleet.binary)!r},'
                '"startTicks":s[19],"bootId":pathlib.Path("/proc/sys/kernel/random/boot_id").read_text().strip()}\n'
                'fd=os.open(p,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)\n'
                'with os.fdopen(fd,"w") as f: json.dump(r,f); f.flush(); os.fsync(f.fileno())\n'
                f'os.execv({str(fleet.binary)!r},{argv!r})\n')
            log = (fleet.e / (name + '-owner.log')).open('x')
            active_unit = 'cf-support-' + str(uuid.uuid4()) + '.service'
            command = ['sudo', '-n', 'systemd-run', '--quiet', '--wait', '--pipe', '--collect',
                '--service-type=exec', '--unit', active_unit, '--property=KillMode=control-group',
                '--property=TimeoutStopSec=3', '--property=RuntimeMaxSec=30', '--property=MemoryMax=256M',
                '--property=TasksMax=64', *fleet.prefix(actor['uid'])[2:], sys.executable, '-c', code]
            fleet.commands.append({'command':command, 'unit':active_unit, 'purpose':point})
            active = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT)
            log.close()
            deadline = time.monotonic() + 10
            while fleet.run(['sudo', '-n', 'test', '-f', hit], expected=None).returncode:
                if active.poll() is not None:
                    raise RuntimeError('The owned process ended before the fault barrier: ' + name)
                assert time.monotonic() < deadline, point
                time.sleep(.03)
            identity = json.loads(fleet.run([*fleet.prefix(actor['uid']), 'cat', record]).stdout)
            assert incarnation(identity['pid']) == [identity['bootId'], identity['startTicks']]
            record_path = fleet.e / ('stop-record-' + name + '.json')
            record_path.write_text(json.dumps(identity))
            fleet.sudo(sys.executable, fleet.helper, '--stop-record', record_path, '--stop-signal', 'KILL')
            active.wait(timeout=10)
            active = None
            fleet.run(['sudo', '-n', 'systemctl', 'stop', active_unit], expected=None)
            active_unit = None
            assert incarnation(identity['pid']) != [identity['bootId'], identity['startTicks']]
            fleet.run([*fleet.prefix(actor['uid']), 'rm', '--', arm, hit])
            return identity

        for point in ('support_after_plan', 'support_after_enqueue', 'support_after_relay'):
            name = point.replace('support_', '')
            call(name, {'action': 'prepare', 'config': configuration()})
            call(name, {'action': 'resume'})
            prior = len(fleet.inspect()['mail'])
            identity = crash(name, point)
            interrupted = stored(name)
            pending = interrupted['pending']
            assert pending and pending['envelope']['messageId'] == pending['run']['messageId']
            message = pending['envelope']['messageId']
            assert len(fleet.inspect()['mail']) == prior + (point == 'support_after_relay')
            recovered = call(name, {'action': 'tick'})
            assert recovered['pending'] is None and not recovered['paused']
            assert recovered['runs'][-1]['messageId'] == message
            assert sum(m['messageId'] == message for m in fleet.inspect()['mail']) == 1
            assert len(fleet.inspect()['mail']) == prior + 1
            checks.append({'point': point, 'sameMessage': message, 'ownedProcessGone': True,
                           'incarnation': identity, 'singleServiceEffect': True})

        name = 'archive'
        call(name, {'action': 'prepare', 'config': configuration(alert=False)})
        call(name, {'action': 'resume'})
        while len(stored(name)['runs']) < 4:
            call(name, {'action': 'tick'})
            time.sleep(.12)
        time.sleep(1.05)
        before = stored(name)
        crash(name, 'support_after_archive')
        assert stored(name)['runs'] == before['runs'] and not stored(name)['archives']
        recovered = call(name, {'action': 'tick'})
        assert len(recovered['archives']) == 1 and len(recovered['runs']) == 3
        directory = fleet.base / 'sender' / name / 'archives'
        files = json.loads(fleet.run([*fleet.prefix(actor['uid']), sys.executable, '-c',
            'import json,pathlib; print(json.dumps([p.name for p in pathlib.Path(' + repr(str(directory)) + ').glob("*.json")]))']).stdout)
        assert len(files) == 1
        checks.append('archive_write_before_cursor_commit_reuses_one_verified_batch_after_actual_owner_kill')

        # Use the actual issued expiration, rather than an assumed sleep deadline.
        fleet.rotate('sender', ttl=1)
        call('expired', {'action': 'prepare', 'config': configuration()})
        call('expired', {'action': 'resume'})
        expires = next(s['expiresAt'] for s in fleet.inspect()['seats'] if s['seat'] == actor['seat'])
        deadline = time.monotonic() + 5
        while int(time.time()) < expires:
            assert time.monotonic() < deadline
            time.sleep(.05)
        count = len(fleet.inspect()['mail'])
        expired = call('expired', {'action': 'tick'})
        assert expired['paused'] and expired['pending'] and expired['hold'] == 'support_delivery_unresolved'
        assert len(fleet.inspect()['mail']) == count
        checks.append('actual_issued_expiration_holds_pending_notice_without_refresh_or_identity_fallback')

        # The clock seam exists only in fixture builds. Never alter the host clock.
        call('clock', {'action': 'prepare', 'config': configuration()})
        call('clock', {'action': 'resume'})
        clock_path = fleet.base / 'sender' / 'clock' / 'fixture-support-time.json'
        fleet.run([*fleet.prefix(actor['uid']), sys.executable, '-c',
            'import os; p=' + repr(str(clock_path)) + '; fd=os.open(p,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600); os.write(fd,b"0"); os.close(fd)'])
        clock = call('clock', {'action': 'tick'})
        assert clock['paused'] and clock['hold'] == 'support_clock_regression'
        assert not clock['runs'] and clock['pending'] is None
        assert len(fleet.inspect()['mail']) == count
        checks.append('fixture_clock_regression_pauses_without_observation_delivery_or_host_clock_change')
    except Exception:
        error = traceback.format_exc()
    finally:
        if active is not None and active.poll() is None:
            if active_unit is not None:
                fleet.run(['sudo', '-n', 'systemctl', 'stop', active_unit], expected=None)
            elif record_path is not None:
                fleet.sudo(sys.executable, fleet.helper, '--stop-record', record_path, '--stop-signal', 'KILL')
            active.wait(timeout=10)
        fleet.close()
    return result(args.evidence, checks, error)


if __name__ == '__main__':
    raise SystemExit(main())
