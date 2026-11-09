#!/usr/bin/env python3
"""CF-013 native recording/outage E2E. The Forge endpoint is synthetic, not a live proof."""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import time
import traceback
import uuid

from fleet_fixture import Fleet, SERVICE, GROUP, result
from forge_fixture import Forge
from native_fixture import Native
from session_fixture import Model


def main():
    parser = argparse.ArgumentParser()
    for name in ['binary', 'cli', 'evidence']:
        parser.add_argument('--' + name, required=True)
    args = parser.parse_args()
    fleet, model = Fleet(args.binary, args.evidence), Model()
    native, forge, error, checks = None, None, None, []
    try:
        fleet.enroll('sender')
        fleet.enroll('receiver', ['observe', 'queue', 'acknowledge'])
        receiver = fleet.subjects['receiver']
        native = Native(fleet, args.cli, model)
        thread = native.request('thread/start', {'cwd': str(native.home / 'project')})['thread']['id']
        native_state = fleet.base / 'receiver/native-ledger'
        state = fleet.state / 'recording'
        grant = fleet.publish(receiver['grant'])
        helper = fleet.base / 'bin/forge-sign-fixture'
        fleet.sudo('install', '-o', SERVICE, '-g', GROUP, '-m', '0555',
                   Path(__file__).with_name('fixture_forge_signer.py'), helper)
        private_key = fleet.state / 'forge-fixture.key'
        key = json.loads(fleet.run([*fleet.prefix(SERVICE), helper, '--key', private_key, '--keygen']).stdout)
        forge = Forge(key['publicKey'])
        config = fleet.state / 'forge-config.json'
        fleet.sudo('install', '-o', SERVICE, '-g', GROUP, '-m', '0600', fleet.publish({
            'endpoint': f'http://127.0.0.1:{forge.server.server_port}/mcp/forge',
            'forgeIdentity': 'factory-fixture', 'forgeProject': forge.project,
            'localProject': 'fixture-project', 'signerArgv': [str(helper), '--key', str(private_key)]}), config)

        def outbox():
            return json.loads(fleet.admin('recording-outbox', '--state', state).stdout)

        def flush(event_id):
            return json.loads(fleet.admin('flush-recording', '--state', state, '--config', config,
                                         '--event-id', event_id).stdout)

        def synchronize():
            receiver['sequence'] = next(seat['lastSequence'] for seat in fleet.inspect()['seats']
                                        if seat['seat'] == receiver['seat'])

        def wire(record, expected=None, name='receiver'):
            synchronize()
            return fleet.wire(name, {'method': 'record_native', 'protocol': 1,
                'request': fleet.request(name, 'acknowledge'), 'record': record}, expected=expected)

        def complete(label, expect_full=False):
            message = fleet.envelope(mode='wake_when_idle', assignmentId=str(uuid.uuid4()),
                text=f'Assignment: CF-013 — {label}\nDo: Reply READY. Use no tools. Do not create another agent.')
            fleet.send(message)
            params = {'threadId': thread, 'project': 'fixture-project', 'messageId': message['messageId']}
            index = len(model.requests)
            started = native.request('thread/factoryMessage', params)['data']
            model.wait_request(index)
            model.release(index)
            native.event('turn/completed', started['turnId'])
            reconciled = native.request('thread/factoryMessage', params)['data']
            assert reconciled['state'] == 'completed', reconciled
            before = len(outbox()['entries'])
            collected_process = fleet.run([*fleet.prefix(receiver['uid']), fleet.binary,
                'collect-recording', '--native-state', native_state, '--grant', grant,
                '--socket', fleet.socket, '--service-uid', SERVICE,
                '--credential', receiver['credential']], expected=None if expect_full else 0)
            if expect_full:
                assert collected_process.returncode and 'recording_buffer_full' in collected_process.stderr
                assert len(outbox()['entries']) == before
                return reconciled
            collected = json.loads(collected_process.stdout)
            assert collected['ok']
            entries = outbox()['entries']
            assert len(entries) == before + 1
            entry = next(entry for entry in entries.values() if entry['event']['messageId'] == message['messageId'])
            event = entry['event']
            assert event['seat'] == receiver['seat'] and event['incarnation'] == receiver['grant']['incarnation']
            assert event['threadId'] == thread and event['turnId'] == started['turnId']
            assert event['assignmentId'] == message['assignmentId']
            assert not any(secret in json.dumps(event) for secret in ['READY', 'access_token', 'credential', 'reasoning'])
            return entry

        # Native execution and reconciliation finish with zero Forge calls.
        forge.mode = 'unavailable'
        first = complete('Offline recording')
        assert not forge.calls
        event_id = first['eventId']
        assert flush(event_id)['state'] == 'pending'
        assert outbox()['forgeDegraded']
        forge.mode = 'normal'
        assert flush(event_id)['state'] == 'recorded'
        assert flush(event_id)['state'] == 'recorded' and len(forge.rows) == 1
        checks.append('real_native_terminal_metadata_correlates_all_ids_without_network_callbacks')
        checks.append('outage_is_visible_and_recovery_records_one_logical_event')

        record = {'messageId': first['event']['messageId'], 'threadId': thread,
                  'turnId': first['event']['turnId'], 'outcome': 'completed'}
        assert wire(record)['duplicate']
        wire({**record, 'threadId': str(uuid.uuid4())}, expected='recording_event_conflict')
        wire({**record, 'turnId': str(uuid.uuid4())}, expected='recording_receipt_unproven')
        wire({**record, 'credential': 'forbidden-fixture-sentinel'}, expected='invalid_request')
        synchronize()
        fleet.wire('sender', {'method': 'record_native', 'protocol': 1,
            'request': fleet.request('sender'), 'record': record}, expected='recording_scope_denied')
        checks.append('wrong_turn_changed_thread_unknown_secret_field_and_wrong_scope_are_refused')

        second = complete('Lost acknowledgement')
        forge.mode = 'lost_ack'
        assert flush(second['eventId'])['state'] == 'uncertain'
        assert len(forge.rows) == 2
        forge.mode = 'normal'
        assert flush(second['eventId'])['state'] == 'recorded'
        assert len(forge.rows) == 2
        checks.append('commit_then_lost_ack_reconciles_authenticated_row_without_another_insert')

        third = complete('Courier crash')
        forge.mode = 'hold_ack'
        forge.hit.clear()
        command = [*fleet.prefix(SERVICE), fleet.binary, 'flush-recording', '--state', state,
                   '--config', config, '--event-id', third['eventId']]
        process = subprocess.Popen(list(map(str, command)), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        fleet.children.append(process)
        assert forge.hit.wait(8)
        current = outbox()['entries'][third['eventId']]
        owner = current['claimOwner']
        stopped = fleet.e / 'courier-stop.json'
        stopped.write_text(json.dumps({'pid': owner['pid'], 'startTicks': owner['startTicks'],
            'bootId': owner['bootId'], 'uid': SERVICE, 'exe': str(fleet.binary)}))
        # A second courier must not steal the first process's live claim.
        denied = fleet.admin('flush-recording', '--state', state, '--config', config,
                            '--event-id', third['eventId'], expected=None)
        assert denied.returncode and 'recording_courier_busy' in denied.stderr
        fleet.sudo(sys.executable, fleet.helper, '--stop-record', stopped, '--stop-signal', 'KILL')
        stdout, stderr = process.communicate(timeout=10)
        fleet.commands.append({'courierExit': process.returncode, 'stdout': stdout, 'stderr': stderr,
                               'incarnation': owner})
        assert process.returncode != 0
        forge.release.set()
        forge.mode = 'normal'
        assert flush(third['eventId'])['state'] == 'recorded' and len(forge.rows) == 3
        checks.append('incarnation_owned_sigkill_and_competing_courier_recover_without_reinsertion')

        fourth = complete('Restore')
        backup = fleet.base / 'recording-backup'
        fleet.run([*fleet.prefix(SERVICE), 'cp', '-a', '--', state, backup])
        assert flush(fourth['eventId'])['state'] == 'recorded'
        fleet.run([*fleet.prefix(SERVICE), 'cp', '-p', '--', backup / 'registry.json', state / 'registry.json'])
        assert flush(fourth['eventId'])['state'] == 'recorded' and len(forge.rows) == 4
        checks.append('restored_pending_state_reuses_uuid_and_reconciles_one_remote_logical_record')

        # The signature covers exact metadata. A changed remote claim cannot
        # turn uncertainty into successful recording.
        fifth = complete('Tampered remote record')
        forge.mode = 'lost_ack'
        assert flush(fifth['eventId'])['state'] == 'uncertain'
        forge.rows[-1]['payload']['factory']['event']['threadId'] = str(uuid.uuid4())
        forge.mode = 'normal'
        assert flush(fifth['eventId'])['state'] == 'uncertain'
        assert len(forge.rows) == 5
        checks.append('altered_remote_body_remains_uncertain_and_never_causes_a_blind_retry')
        forge.revoked = True
        assert flush(fifth['eventId'])['state'] == 'uncertain' and len(forge.rows) == 5
        forge.revoked = False
        checks.append('revoked_forge_identity_does_not_record_or_expose_helper_credentials')

        # Fill through real authenticated service operations, not edits to the
        # recording registry. Transport receipts here are explicit fixture
        # attestations, not extra native/model completion claims.
        parent_backup = fleet.base / 'parent-backup.json'
        fleet.run([*fleet.prefix(SERVICE), 'cp', '-p', '--', fleet.state / 'registry.json', parent_backup])
        sender = fleet.subjects['sender']
        while len(outbox()['entries']) < 128:
            envelope = fleet.envelope()
            fleet.send(envelope)
            synchronize()
            fleet.receive(envelope['messageId'])
            synchronize()
            turn = str(uuid.uuid4())
            for delivery, authority in [('consumed', 'runtime'), ('completed', 'worker')]:
                fleet.wire('receiver', {'method': 'ack', 'protocol': 1,
                    'request': fleet.request('receiver', 'acknowledge'),
                    'receipt': {'protocol': 1, 'messageId': envelope['messageId'],
                        'state': delivery, 'writer': authority, 'turnId': turn}})
            wire({'messageId': envelope['messageId'], 'threadId': str(uuid.uuid4()),
                  'turnId': turn, 'outcome': 'completed'})
        assert len(outbox()['entries']) == 128
        # Restore a valid earlier transport snapshot while retaining its
        # independent historical recording projection. New work can complete
        # locally but cannot receive a false recording acknowledgement.
        fleet.stop()
        fleet.run([*fleet.prefix(SERVICE), 'cp', '-p', '--', parent_backup, fleet.state / 'registry.json'])
        fleet.start()
        sender['sequence'] = next(seat['lastSequence'] for seat in fleet.inspect()['seats']
                                  if seat['seat'] == sender['seat'])
        assert complete('Full recording buffer', expect_full=True)['state'] == 'completed'
        assert len(outbox()['entries']) == 128 and outbox()['forgeDegraded']
        checks.append('full_buffer_is_visible_without_blocking_native_completion_or_false_recording_ack')
        synchronize()
        fleet.admin('revoke', '--state', fleet.state, '--seat', receiver['seat'])
        wire(record, expected='credential_revoked')
        checks.append('revoked_native_source_cannot_append_or_replay_recording_metadata')
        (fleet.e / 'forge-summary.json').write_text(json.dumps({'fixtureOnly': True,
            'authenticatedCalls': forge.calls, 'physicalRows': len(forge.rows),
            'logicalEventIds': [row['payload']['factory']['eventId'] for row in forge.rows]}, indent=2) + '\n')
    except Exception:
        error = traceback.format_exc()
    finally:
        if forge:
            forge.close()
        model.close()
        if native:
            native.close()
        fleet.close()
    return result(args.evidence, checks, error)


if __name__ == '__main__':
    raise SystemExit(main())
