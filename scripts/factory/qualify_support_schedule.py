"""Real read-only observations, durable one-shot slots, pause and recovery."""
import argparse
import hashlib
import json
from pathlib import Path
import time
import traceback

from fleet_fixture import Fleet, GROUP, result
from native_fixture import Native
from session_fixture import Model


def main():
    parser = argparse.ArgumentParser()
    for key in ('binary', 'evidence'):
        parser.add_argument('--' + key, required=True)
    parser.add_argument('--cli')
    args = parser.parse_args()
    fleet = Fleet(args.binary, args.evidence)
    checks, error, native, model = [], None, None, None
    try:
        fleet.enroll('sender', ['observe', 'queue', 'acknowledge'], role='support')
        fleet.enroll('receiver', ['observe', 'queue', 'acknowledge'], role='manager')
        actor = fleet.subjects['sender']
        probe = fleet.base / 'sender' / 'owned-probe'
        fleet.sudo('install', '-d', '-o', actor['uid'], '-g', GROUP, '-m', '0700', probe)
        quiet_probes = {'disks': [{'label': 'owned', 'path': str(probe)}],
                        'usage': {'warning': 9999, 'critical': 10000},
                        'pressure': {'warning': 9999, 'critical': 10000}}
        def config(probes=None, interval=1, archives=16):
            return {'grant': actor['grant'], 'project': 'fixture-project',
                    'recipient': fleet.subjects['receiver']['seat'],
                    'socket': str(fleet.socket), 'credential': str(actor['credential']),
                    'serviceUid': 65534, 'startAt': int(time.time()) - 20,
                    'intervalSeconds': interval, 'retentionSlots': 2,
                    'maxArchives': archives, 'probes': probes or quiet_probes}
        def action(name, value, expected=0):
            state = fleet.base / 'sender' / name
            response = fleet.run([*fleet.prefix(actor['uid']), fleet.binary, 'support',
                                  '--state', state, '--input', fleet.publish(value)], expected=expected)
            return json.loads(response.stdout) if response.returncode == 0 else response
        def next_tick(name):
            before = action(name, {'action': 'status', 'after': None})['lastSlot']
            deadline = time.monotonic() + 5
            while True:
                reply = action(name, {'action': 'tick'})
                if ('lastSlot' in reply and reply['lastSlot'] != before) or reply.get('paused'):
                    return reply
                if time.monotonic() >= deadline:
                    raise RuntimeError('The next owned schedule slot was not observed.')
                time.sleep(.1)
        prepared = action('readings', {'action': 'prepare', 'config': config()})
        assert prepared['paused'] and prepared['timerInstalled'] is False
        assert action('readings', {'action': 'tick'})['runs'] == []
        action('readings', {'action': 'resume'})
        first = next_tick('readings')
        assert first['runs'][-1]['disposition'] == 'quiet', first
        assert first['runs'][-1]['missedSlots'] >= 20
        assert not fleet.inspect()['mail']
        baseline = first['lastGood']['disk.owned.blocks']
        assert baseline['readAt'] == first['runs'][-1]['readAt']
        early_gap = action('readings', {'action': 'read', 'slot': 0})
        assert early_gap['state'] == 'gap' and early_gap['run'] is None
        if args.cli:
            model = Model()
            native = Native(fleet, args.cli, model, name='receiver')
            thread = native.request('thread/start', {'cwd': str(native.home / 'project')})['thread']['id']
            assert not model.requests
        checks.append('actual_mount_kernel_readings_and_coalesced_initial_downtime_are_quiet')

        # Two separate owners in the same long slot return its saved receipt, with no write.
        action('duplicate', {'action': 'prepare', 'config': config(interval=60)})
        action('duplicate', {'action': 'resume'})
        duplicate_first = action('duplicate', {'action': 'tick'})
        duplicate_second = action('duplicate', {'action': 'tick'})
        assert duplicate_second['state'] == 'duplicate_slot'
        assert duplicate_second['receipt']['slot'] == duplicate_first['lastSlot']
        assert duplicate_second['receipt']['disposition'] == 'quiet'
        assert not fleet.inspect()['mail']
        checks.append('repeated_slot_after_owner_restart_returns_its_saved_receipt_without_another_observation')

        # Pause is durable across independent CLI invocations and does not sample the changed path.
        action('readings', {'action': 'pause'})
        fleet.sudo('rmdir', probe)
        paused = action('readings', {'action': 'tick'})
        assert paused['paused'] and paused['lastGood']['disk.owned.blocks'] == baseline
        assert not fleet.inspect()['mail']
        action('readings', {'action': 'resume'})
        missing = next_tick('readings')
        assert not missing['paused'] and missing['lastGood']['disk.owned.blocks'] == baseline
        missing_slot = missing['lastSlot']
        reading = action('readings', {'action': 'read', 'slot': missing_slot})['run']
        missing_metric = next(m for m in reading['snapshot']['metrics'] if m['key'] == 'disk.owned.blocks')
        assert missing_metric['basisPoints'] is None and missing_metric['state'] == 'unavailable'
        notice = missing['runs'][-1]['messageId']
        assert len(fleet.inspect()['mail']) == 1 and notice
        if args.cli:
            assert not model.requests
            started = native.request('thread/factoryMessage', {'threadId': thread,
                'project': 'fixture-project', 'messageId': notice})['data']
            model.wait_request(0)
            assert 'disk.owned.blocks' in json.dumps(model.requests[0]['body'])
            assert 'Read at: ' + str(reading['snapshot']['readAt']) in json.dumps(model.requests[0]['body'])
            model.release(0)
            native.event('turn/completed', started['turnId'])
        same = next_tick('readings')
        assert same['runs'][-1]['disposition'] == 'quiet' and len(fleet.inspect()['mail']) == 1
        fleet.sudo('install', '-d', '-o', actor['uid'], '-g', GROUP, '-m', '0700', probe)
        recovered = next_tick('readings')
        assert recovered['runs'][-1]['messageId'] and len(fleet.inspect()['mail']) == 2
        checks.append('pause_failed_read_keeps_baseline_changed_issue_and_resolution_notify_once')

        # A low explicit threshold is a controlled disk alert, not a fabricated host reading.
        alert_probes = {**quiet_probes, 'usage': {'warning': 1, 'critical': 2}}
        action('delivery', {'action': 'prepare', 'config': config(alert_probes)})
        action('delivery', {'action': 'resume'})
        fleet.stop()
        held = action('delivery', {'action': 'tick'})
        assert held['paused'] and held['hold'] == 'support_delivery_unresolved' and held['pending']
        pending_id = held['pending']['messageId']
        pending_slot = held['pending']['slot']
        pending_read = action('delivery', {'action': 'read', 'slot': pending_slot})['pending']
        assert any(m['key'] == 'disk.owned.blocks' and m['state'] == 'critical'
                   for m in pending_read['snapshot']['metrics'])
        # Restart and retry the exact same envelope after the outbox backoff.
        fleet.start()
        time.sleep(1.05)
        action('delivery', {'action': 'resume'})
        delivered = action('delivery', {'action': 'tick'})
        assert not delivered['paused'] and delivered['pending'] is None
        assert delivered['runs'][-1]['messageId'] == pending_id
        assert sum(m['messageId'] == pending_id for m in fleet.inspect()['mail']) == 1
        checks.append('actual_disk_threshold_outage_pause_and_restart_reuse_one_durable_envelope')

        # A retained cursor fences old slots; complete batches are archived before removal.
        archive_probes = {**quiet_probes, 'disks': []}
        action('retention', {'action': 'prepare', 'config': config(archive_probes, archives=2)})
        action('retention', {'action': 'resume'})
        original_slot = None
        for _ in range(10):
            row = next_tick('retention')
            original_slot = row.get('lastSlot') if original_slot is None else original_slot
            if row['paused']:
                break
        assert row['paused'] and row['hold'] == 'support_archive_capacity'
        assert len(row['archives']) == 2 and len(row['runs']) == 4
        assert action('retention', {'action': 'status', 'after': original_slot})['historyGap']
        archived = action('retention', {'action': 'read', 'slot': original_slot})
        assert archived['state'] == 'archived' and archived['historyGap']
        assert archived['run']['slot'] == original_slot
        gap = action('retention', {'action': 'read', 'slot': 0})
        assert gap['state'] == 'gap' and gap['archive'] is None
        count = len(fleet.inspect()['mail'])
        action('retention', {'action': 'tick'})
        assert len(fleet.inspect()['mail']) == count
        output = fleet.base / 'sender' / 'history-export.json'
        archive_path = fleet.base / 'sender' / 'retention' / 'archives' / row['archives'][0]['file']
        tamper = 'from pathlib import Path; p=Path(' + repr(str(archive_path)) + '); saved=p.with_suffix(".saved"); saved.write_bytes(p.read_bytes()); p.write_bytes(p.read_bytes()+b" ")'
        fleet.run([*fleet.prefix(actor['uid']), 'python3', '-c', tamper])
        rejected = action('retention', {'action': 'export', 'output': str(output)}, expected=None)
        assert rejected.returncode and 'support_archive_changed' in rejected.stderr
        rejected_read = action('retention', {'action': 'read', 'slot': original_slot}, expected=None)
        assert rejected_read.returncode and 'support_archive_changed' in rejected_read.stderr
        restore = 'from pathlib import Path; p=Path(' + repr(str(archive_path)) + '); saved=p.with_suffix(".saved"); p.write_bytes(saved.read_bytes()); saved.unlink()'
        fleet.run([*fleet.prefix(actor['uid']), 'python3', '-c', restore])
        assert action('retention', {'action': 'status', 'after': None})['lowerCursor'] == row['lowerCursor']
        checks.append('changed_archive_is_refused_without_replacing_its_cursor_or_baseline')
        exported = action('retention', {'action': 'export', 'output': str(output)})
        raw = fleet.run([*fleet.prefix(actor['uid']), 'cat', output]).stdout
        history = json.loads(raw)
        assert hashlib.sha256(raw.encode()).hexdigest() == exported['sha256']
        assert history['executableAuthority'] is False and history['restorable'] is False
        assert not any(key in history for key in ('grant', 'credential', 'config', 'socket'))
        (fleet.e / 'read-only-history-export.json').write_text(raw)
        checks.append('bounded_archives_explicit_cursor_gap_and_capacity_pause_preserve_facts_without_authority')

        # A revoked credential must leave the new observation unresolved and held.
        action('revoked', {'action': 'prepare', 'config': config(alert_probes)})
        action('revoked', {'action': 'resume'})
        fleet.admin('revoke', '--state', fleet.state, '--seat', actor['seat'])
        revoked = action('revoked', {'action': 'tick'})
        assert revoked['paused'] and revoked['pending'] and revoked['hold'] == 'support_delivery_unresolved'
        assert len(fleet.inspect()['mail']) == count
        if model:
            assert len(model.requests) == 1
        checks.append('revoked_credential_holds_the_exact_unresolved_notice_without_account_or_identity_fallback')
    except Exception:
        error = traceback.format_exc()
    finally:
        if model:
            model.close()
        if native:
            native.close()
        fleet.close()
    return result(args.evidence, checks, error)


if __name__ == '__main__':
    raise SystemExit(main())
