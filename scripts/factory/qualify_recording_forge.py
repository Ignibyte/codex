#!/usr/bin/env python3
"""Exercise recording against the pinned real Forge, signer and owned Postgres."""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import traceback
import uuid

from fleet_fixture import Fleet, SERVICE, GROUP, result
from native_fixture import Native
from real_forge_fixture import RealForge, ForgeProxy, rpc
from session_fixture import Model, sha256


def main():
    parser = argparse.ArgumentParser()
    for name in ['binary', 'cli', 'forge-binary', 'forge-source', 'evidence']:
        parser.add_argument('--' + name, required=True)
    args = parser.parse_args()
    fleet, model = Fleet(args.binary, args.evidence), Model()
    native = forge = proxy = None
    checks, error = [], None
    try:
        fleet.enroll('sender')
        fleet.enroll('receiver', ['observe', 'queue', 'acknowledge'])
        forge = RealForge(fleet, args.forge_binary, args.forge_source)
        proxy=ForgeProxy(forge.endpoint)
        checks.append('pinned_real_forge_signer_enrollment_and_narrow_identity_approval')
        native = Native(fleet, args.cli, model)
        thread = native.request('thread/start', {'cwd': str(native.home / 'project')})['thread']['id']
        envelope = fleet.envelope(mode='wake_when_idle', assignmentId=str(uuid.uuid4()),
            text='Assignment: CF-013 — Real Forge recording\nDo: Reply READY. Use no tools. Do not create another agent.')
        fleet.send(envelope)
        params = {'threadId': thread, 'project': 'fixture-project', 'messageId': envelope['messageId']}
        started = native.request('thread/factoryMessage', params)['data']
        model.wait_request(0)
        model.release(0)
        native.event('turn/completed', started['turnId'])
        assert native.request('thread/factoryMessage', params)['data']['state'] == 'completed'
        receiver = fleet.subjects['receiver']
        collected = json.loads(fleet.run([*fleet.prefix(receiver['uid']), fleet.binary,
            'collect-recording', '--native-state', fleet.base / 'receiver/native-ledger',
            '--grant', fleet.publish(receiver['grant']), '--socket', fleet.socket,
            '--service-uid', SERVICE, '--credential', receiver['credential']]).stdout)
        assert len(collected['records']) == 1
        event_id = collected['records'][0]['eventId']
        config = fleet.state / 'real-forge-config.json'
        config_value = {'endpoint': proxy.endpoint, 'forgeIdentity': 'factory-fixture',
            'forgeProject': forge.project, 'localProject': 'fixture-project',
            'signerArgv': [str(forge.binary), 'sign', 'headers', '--name', 'factory-fixture',
                           '--key-dir', str(forge.key_dir), '--ttl', '60']}
        fleet.sudo('install', '-o', SERVICE, '-g', GROUP, '-m', '0600', fleet.publish(config_value), config)
        state = fleet.state / 'recording'
        pending = fleet.base / 'pending-recording-backup'
        fleet.run([*fleet.prefix(SERVICE), 'cp', '-a', '--', state, pending])
        def flush():
            return json.loads(fleet.admin('flush-recording', '--state', state,
                '--config', config, '--event-id', event_id).stdout)
        proxy.mode='unavailable'
        assert flush()['state']=='pending' and forge.sql('SELECT count(*) FROM seat_events;')=='0'
        proxy.mode='normal'
        first = flush()
        assert first['state'] == 'recorded', first
        count = forge.sql('SELECT count(*) FROM seat_events;')
        assert count == '1', count
        data = json.loads(forge.sql("SELECT payload::text FROM seat_events LIMIT 1;"))['factory']
        event = data['event']
        assert data['eventId'] == event_id and event['threadId'] == thread
        assert event['turnId'] == started['turnId'] and event['messageId'] == envelope['messageId']
        assert event['assignmentId'] == envelope['assignmentId']
        assert event['seat'] == receiver['seat'] and event['incarnation'] == receiver['grant']['incarnation']
        checks.append('real_native_event_reaches_real_forge_with_all_correlation_fields')
        assert flush()['remoteIds'] == first['remoteIds'] and forge.sql('SELECT count(*) FROM seat_events;') == '1'
        fleet.run([*fleet.prefix(SERVICE), 'cp', '-p', '--', pending / 'registry.json', state / 'registry.json'])
        assert flush()['remoteIds'] == first['remoteIds'] and forge.sql('SELECT count(*) FROM seat_events;') == '1'
        checks.append('real_retained_lookup_reconciles_restored_pending_event_without_another_insert')
        checks.append('real_backend_network_outage_preserves_local_completion_and_visible_pending_state')
        # A second actual native turn supplies a new event. The proxy drops
        # only its response after the real Forge completed its database insert.
        second=fleet.envelope(mode='wake_when_idle',assignmentId=str(uuid.uuid4()),
            text='Assignment: CF-013 — Real acknowledgement loss\nDo: Reply READY. Use no tools. Do not create another agent.')
        fleet.send(second)
        second_params={'threadId':thread,'project':'fixture-project','messageId':second['messageId']}
        second_started=native.request('thread/factoryMessage',second_params)['data']
        model.wait_request(1);model.release(1)
        native.event('turn/completed',second_started['turnId'])
        assert native.request('thread/factoryMessage',second_params)['data']['state']=='completed'
        fleet.run([*fleet.prefix(receiver['uid']),fleet.binary,'collect-recording',
            '--native-state',fleet.base/'receiver/native-ledger','--grant',fleet.publish(receiver['grant']),
            '--socket',fleet.socket,'--service-uid',SERVICE,'--credential',receiver['credential']])
        outbox=json.loads(fleet.admin('recording-outbox','--state',state).stdout)
        event_id=next(entry['eventId'] for entry in outbox['entries'].values()
                      if entry['event']['messageId']==second['messageId'])
        proxy.mode='lost_ack'
        assert flush()['state']=='uncertain'
        assert forge.sql('SELECT count(*) FROM seat_events;')=='2'
        proxy.mode='normal'
        recovered=flush()
        assert recovered['state']=='recorded' and forge.sql('SELECT count(*) FROM seat_events;')=='2'
        checks.append('real_database_commit_then_lost_ack_reconciles_without_duplicate_logical_record')
        # A narrow emitter cannot approve or revoke an identity. Workers never
        # obtain the bootstrap bearer or database connection.
        denied = False
        headers_command = list(map(str,[*fleet.prefix(SERVICE), forge.binary, 'sign', 'headers',
            '--name', 'factory-fixture', '--key-dir', forge.key_dir]))
        headers_result = subprocess.run(headers_command,capture_output=True,text=True,timeout=5)
        assert headers_result.returncode == 0, 'The real signer failed.'
        fleet.commands.append({'command':headers_command,'exitCode':headers_result.returncode,
                               'stdout':'[redacted signed headers]'})
        headers = json.loads(headers_result.stdout)
        try:
            rpc(forge.endpoint, 'identity-revoke', {'name': 'factory-fixture'}, headers)
        except (AssertionError, OSError):
            denied = True
        assert denied, 'The narrow signer acquired identity administration.'
        checks.append('real_forge_identity_has_emit_permission_without_administration')
        visibility=json.loads(fleet.run([*fleet.prefix(receiver['uid']),sys.executable,fleet.helper,
            '--inspect',fleet.state/'forge-spec.json',forge.key_dir/'factory-fixture.key']).stdout)
        assert not any(visibility['readable'].values()) and visibility['noNewPrivs']
        checks.append('worker_cannot_read_real_forge_database_configuration_or_private_signing_key')
        (fleet.e / 'real-forge-proof.json').write_text(json.dumps({'passed': True,
            'forgeBinarySha256': sha256(args.forge_binary), 'sourceCommit': '97270040748040af4d7a9e7bd4ec9dd5e6af5b5e',
            'event': data, 'physicalRows': 2, 'reconciledRemoteIds': first['remoteIds'],
            'lostAckRecovery':recovered,'forwardedRealCalls':proxy.forwarded,
            'nativeTurn': started['turnId'], 'productionAccess': False, 'paidModelCalls': 0}, indent=2) + '\n')
    except Exception:
        error = traceback.format_exc()
    finally:
        model.close()
        if native:
            native.close()
        if proxy:
            proxy.close()
        if forge:
            forge.close()
        fleet.close()
    return result(args.evidence, checks, error)


if __name__ == '__main__':
    raise SystemExit(main())
