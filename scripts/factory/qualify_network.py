#!/usr/bin/env python3
"""Actual owned OpenSSH/native journey on one machine; real second host is pending."""
import argparse
import json
from pathlib import Path
import subprocess
import select
import sys
import time
import traceback
import uuid

from fleet_fixture import SERVICE, GROUP, result
from qualify_observation import Observer
from native_fixture import Native
from session_fixture import Model
from ssh_fixture import RoutedFleet, SshHost


def main():
    parser = argparse.ArgumentParser()
    for name in ['binary', 'cli', 'evidence']:
        parser.add_argument('--' + name, required=True)
    args = parser.parse_args()
    fleet, model = RoutedFleet(args.binary, args.evidence), Model()
    host, native, foreman, observer, error, checks = None, None, None, None, None, []
    try:
        fleet.subjects['observer'] = {'uid': 65531, 'seat': str(uuid.uuid4()), 'sequence': 0}
        fleet.sudo('install', '-d', '-o', 65531, '-g', GROUP, '-m', '0700', fleet.base / 'observer')
        fleet.enroll('sender', ['observe', 'queue', 'acknowledge'])
        host = SshHost(fleet)
        host.enroll()
        fleet.enroll('observer', ['observe'], role='observer', recipients=[fleet.subjects['observer']['seat'], fleet.subjects['receiver']['seat']])
        observer = Observer(fleet)
        receiver = fleet.subjects['receiver']
        assert receiver['grant']['host'] != fleet.subjects['sender']['grant']['host']
        original = fleet.socket
        fleet.socket = host.proxy_socket
        try:
            native = Native(fleet, args.cli, model)
        finally:
            fleet.socket = original
        foreman = Native(fleet, args.cli, model, name='sender')
        foreman_thread = foreman.request('thread/start', {'cwd': str(foreman.home / 'project')})['thread']['id']
        thread = native.request('thread/start', {'cwd': str(native.home / 'project')})['thread']['id']
        message = fleet.envelope(mode='wake_when_idle', assignmentId=str(uuid.uuid4()),
            text='Assignment: CF-015 — Private transport fixture\nDo: Reply READY. Use no tools. Do not create another agent.')
        fleet.send(message)
        params = {'threadId': thread, 'project': 'fixture-project', 'messageId': message['messageId']}
        started = native.request('thread/factoryMessage', params)['data']
        model.wait_request(0)
        model.release(0)
        native.event('turn/completed', started['turnId'])
        assert native.request('thread/factoryMessage', params)['data']['state'] == 'completed'
        def exchange_message(sender, recipient_name, kind, reply_to, text):
            outgoing = {'messageId': str(uuid.uuid4()), 'conversationId': message['conversationId'],
                'recipient': fleet.subjects[recipient_name]['seat'], 'project': 'fixture-project',
                'kind': kind, 'mode': 'wake_when_idle', 'replyTo': reply_to, 'text': text,
                'expiresInSeconds': 3600}
            assert sender.request('factory/send', outgoing)['data']['state'] == 'service_accepted'
            return outgoing

        def consume(actor, actor_thread, outgoing):
            index = len(model.requests)
            incoming = {'threadId': actor_thread, 'project': 'fixture-project', 'messageId': outgoing['messageId']}
            admitted = actor.request('thread/factoryMessage', incoming)['data']
            model.wait_request(index)
            model.release(index)
            actor.event('turn/completed', admitted['turnId'])
            assert actor.request('thread/factoryMessage', incoming)['data']['state'] == 'completed'
            return admitted['turnId']

        question = exchange_message(native, 'sender', 'clarification', message['messageId'],
            'Question: CF-015\nAsk: Is this the owned transport fixture?')
        question_turn = consume(foreman, foreman_thread, question)
        answer = exchange_message(foreman, 'receiver', 'reply', question['messageId'],
            'Answer: CF-015\nChoice: Use the owned transport fixture.')
        answer_turn = consume(native, thread, answer)
        final_result = exchange_message(native, 'sender', 'result', message['messageId'],
            'Done: CF-015\nWorks: The fixture turn completed.\nLimits: This is one machine.')
        result_turn = consume(foreman, foreman_thread, final_result)
        checks.append('actual_native_assignment_clarification_reply_and_result_cross_host_boundaries_with_four_durable_turns')

        # Both native ledgers collect at the hub with their original source-host bindings.
        for actor, actor_name, endpoint in [(native, 'receiver', host.proxy_socket), (foreman, 'sender', original)]:
            subject = fleet.subjects[actor_name]
            fleet.run([*fleet.prefix(subject['uid']), fleet.binary, 'collect-recording',
                '--native-state', fleet.base / actor_name / 'native-ledger', '--grant', fleet.publish(subject['grant']),
                '--socket', endpoint, '--service-uid', SERVICE, '--credential', subject['credential']])
        recorded = json.loads(fleet.admin('recording-outbox', '--state', fleet.state / 'recording').stdout)['entries']
        assert len(recorded) == 4, recorded
        expected_turns = {message['messageId']: (host.host, started['turnId']),
            question['messageId']: (fleet.host, question_turn), answer['messageId']: (host.host, answer_turn),
            final_result['messageId']: (fleet.host, result_turn)}
        for entry in recorded.values():
            event = entry['event']
            expected_host, expected_turn = expected_turns[event['messageId']]
            assert event['host'] == expected_host and event['turnId'] == expected_turn, event
        checks.append('both_native_host_ledgers_collect_four_correlated_terminal_records_without_model_credentials')

        fleet.subjects['sender']['sequence'] = next(seat['lastSequence'] for seat in fleet.inspect()['seats']
            if seat['seat'] == fleet.subjects['sender']['seat'])

        def sync_receiver():
            receiver['sequence'] = next(seat['lastSequence'] for seat in fleet.inspect()['seats']
                                        if seat['seat'] == receiver['seat'])

        pulse = {'process': {key: native.identity[key] for key in ['pid', 'bootId', 'startTicks']},
                 'modelActivity': 'idle', 'taskProgress': 'in_progress'}
        sync_receiver()
        fleet.wire('receiver', {'method': 'report_presence', 'protocol': 1,
            'request': fleet.request('receiver', 'acknowledge'), 'report': pulse})
        fake = json.loads(json.dumps(pulse))
        fake['process']['startTicks'] = str(int(fake['process']['startTicks']) + 1)
        sync_receiver()
        fleet.wire('receiver', {'method': 'report_presence', 'protocol': 1,
            'request': fleet.request('receiver', 'acknowledge'), 'report': fake}, expected='observation_process_unproven')
        checks.append('source_proxy_checks_reported_process_incarnation_before_host_attestation')
        remote_detail = observer.tool('work_agent', {'id': receiver['seat']})
        assert remote_detail['observation']['processHealth'] == 'reported_alive', remote_detail
        assert any('authenticated host report' in event['text'] for event in remote_detail['recent_events'])
        time.sleep(16)
        stale_detail = observer.tool('work_agent', {'id': receiver['seat']})['observation']
        assert stale_detail['connectivity'] == 'stale_report' and stale_detail['processHealth'] == 'unknown', stale_detail
        assert stale_detail['modelActivity'] == stale_detail['taskProgress'] == 'unknown'
        checks.append('remote_presence_is_explicitly_host_reported_and_stale_health_never_probes_the_hub_kernel')

        stolen = fleet.base / 'sender/stolen-fixture-credential'
        fleet.sudo('install', '-o', fleet.subjects['sender']['uid'], '-g', GROUP, '-m', '0600', receiver['credential'], stolen)
        copied_frame = fleet.publish({'method': 'sync', 'protocol': 1, 'request': fleet.request('receiver')})
        copied = fleet.run([*fleet.prefix(fleet.subjects['sender']['uid']), sys.executable, fleet.helper,
            '--socket', host.proxy_socket, '--credential', stolen, '--wire', copied_frame])
        assert json.loads(copied.stdout)['error'] == 'network_peer_uid_denied', copied.stdout
        opened = fleet.run([*fleet.prefix(receiver['uid']), sys.executable, fleet.helper,
            '--inspect', host.client_key, '--inspect', host.configuration, '--inspect', fleet.state / 'service.key'])
        assert not any(json.loads(opened.stdout)['readable'].values())
        checks.append('copied_seat_credential_cannot_change_kernel_peer_uid_or_open_transport_and_service_keys')

        # An SSH-network member without the admitted client key cannot use the bridge.
        untrusted = host.key('untrusted-client')
        host.proxy_config['identityFile'] = str(untrusted)
        host.write_proxy_config()
        sync_receiver()
        fleet.wire('receiver', {'method': 'sync', 'protocol': 1,
            'request': fleet.request('receiver')}, expected='network_transport_unavailable')
        host.proxy_config['identityFile'] = str(host.client_key)
        host.write_proxy_config()
        checks.append('network_membership_without_the_admitted_ssh_client_key_does_not_authorize_a_seat')

        # The signed/forced transport context must match the operator's declared source and key.
        for field in ['sourceHost', 'transportKey']:
            original_field = host.proxy_config[field]
            host.proxy_config[field] = str(uuid.uuid4())
            host.write_proxy_config()
            sync_receiver()
            fleet.wire('receiver', {'method': 'sync', 'protocol': 1,
                'request': fleet.request('receiver')}, expected='network_transport_unavailable')
            host.proxy_config[field] = original_field
            host.write_proxy_config()
        checks.append('forced_ssh_host_and_key_context_are_checked_in_both_directions')

        host.proxy_config['address'] = '8.8.8.8'
        host.write_proxy_config()
        sync_receiver()
        fleet.wire('receiver', {'method': 'sync', 'protocol': 1,
            'request': fleet.request('receiver')}, expected='network_private_address_required')
        host.proxy_config['address'] = '127.0.0.1'
        host.write_proxy_config()
        checks.append('public_ip_configuration_is_refused_before_any_ssh_connection')


        # A stolen remote credential cannot be used directly against the hub's Unix socket.
        sync_receiver()
        fleet.remote_sockets.pop(host.host)
        try:
            fleet.wire('receiver', {'method': 'sync', 'protocol': 1,
                'request': fleet.request('receiver')}, expected='authenticated_host_denied')
        finally:
            fleet.remote_sockets[host.host] = host.proxy_socket
        checks.append('remote_seat_credential_cannot_cross_the_local_host_boundary')

        # Admitted host transport does not authorize a local-hub seat or an unknown bootstrap.
        forged = host.probe({'method': 'sync', 'protocol': 1, 'request': fleet.request('sender')},
            fleet.subjects['sender']['uid'], fleet.subjects['sender']['credential'])
        assert forged['response']['response']['error'] == 'authenticated_host_denied', forged
        denied = host.probe({'method': 'enroll', 'protocol': 1, 'bootstrap': '0' * 64}, receiver['uid'])
        assert denied['response']['response']['error'] == 'bootstrap_unknown', denied
        for mode in ['command', 'forward']:
            denied = host.probe({}, receiver['uid'], mode=mode)
            assert denied['sshExit'] != 0 and denied['response'] is None, denied
            if mode == 'command':
                assert 'network_forced_command_required' in denied['stderr'], denied
        checks.append('admitted_host_cannot_impersonate_local_seat_invent_bootstrap_execute_commands_or_forward_ports')

        # An authenticated channel that sends no frame closes within the documented bound.
        sync_receiver()
        stalled, stalled_spec = host.probe({'method': 'sync', 'protocol': 1, 'request': fleet.request('receiver')},
            receiver['uid'], receiver['credential'], held=True)
        stdout, stderr = stalled.communicate(timeout=8)
        closed = json.loads(stdout)
        assert stalled.returncode == 0 and not stderr, (stalled.returncode, stderr)
        assert closed['elapsedSeconds'] < 5 and closed['response']['response']['error'] == 'network_timeout', closed
        checks.append('an_authenticated_idle_ssh_channel_expires_without_a_frame_or_authority_cache')

        # A server admission deadline that expires during a registry lock wait must prevent a later commit.
        guarded = fleet.envelope()
        fleet.send(guarded)
        sync_receiver()
        hold_code = "import fcntl,json,os,sys; f=open(sys.argv[1],'r+b'); fcntl.flock(f,fcntl.LOCK_EX); print(json.dumps({'pid':os.getpid(),'locked':True}),flush=True); sys.stdin.read(1)"
        holder = subprocess.Popen(list(map(str, [*fleet.prefix(SERVICE), sys.executable, '-c', hold_code,
            fleet.state / 'registry.lock'])), stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        fleet.children.append(holder)
        assert select.select([holder.stdout], [], [], 5)[0]
        locked = json.loads(holder.stdout.readline())
        host.record(locked['pid'], 'registry-lock-holder', SERVICE, Path(sys.executable).resolve())
        try:
            before_wait = time.monotonic()
            request = fleet.publish({'method': 'receive', 'protocol': 1, 'request': fleet.request('receiver'),
                                     'message_id': guarded['messageId']})
            waited = fleet.run([*fleet.prefix(receiver['uid']), sys.executable, fleet.helper,
                '--socket', host.proxy_socket, '--credential', receiver['credential'], '--wire', request])
            assert json.loads(waited.stdout)['error'] in ['network_timeout', 'network_transport_unavailable'], waited.stdout
            assert time.monotonic() - before_wait < 4
            time.sleep(3.0)
        finally:
            stdout, stderr = holder.communicate('x', timeout=5)
            assert holder.returncode == 0 and not stderr, (holder.returncode, stderr)
        time.sleep(.1)
        assert next(mail for mail in fleet.inspect()['mail'] if mail['messageId'] == guarded['messageId'])['state'] == 'service_accepted'
        assert fleet.receive(guarded['messageId'])['envelope']['messageId'] == guarded['messageId']
        checks.append('expired_registry_lock_wait_is_refused_before_admission_and_never_commits_after_unlock')

        # Capture actual owned listener and route facts, with no unrelated socket inventory.
        tcp = fleet.sudo('ss', '-H', '-ltnp', f'sport = :{host.port}').stdout
        assert tcp.strip() and all(line.split()[3] == f'127.0.0.1:{host.port}' for line in tcp.splitlines()), tcp
        all_tcp = subprocess.run(['sudo', '-n', 'ss', '-H', '-ltnp'], capture_output=True, text=True, check=True).stdout
        assert f'pid={native.identity["pid"]},' not in all_tcp
        unix = subprocess.run(['sudo', '-n', 'ss', '-H', '-lxnp'], capture_output=True, text=True, check=True).stdout
        owned_unix = [line for line in unix.splitlines() if str(fleet.base) in line]
        assert any(str(host.proxy_socket) in line for line in owned_unix)
        route = json.loads(fleet.run(['ip', '-j', 'route', 'get', '127.0.0.1']).stdout)
        assert route[0]['type'] == 'local' and route[0]['dev'] == 'lo', route
        (fleet.e / 'owned-listeners-and-route.json').write_text(json.dumps({'sshTcp': tcp,
            'unixSockets': owned_unix, 'nativePidHasNoTcpListener': True, 'route': route}, indent=2))
        checks.append('actual_listener_and_route_inspection_shows_loopback_ssh_local_unix_endpoints_and_no_native_tcp_listener')

        # Network outage preserves the accepted message and a retry resumes the same ID.
        host.stop_sshd()
        pending = fleet.envelope()
        fleet.send(pending)
        sync_receiver()
        fleet.wire('receiver', {'method': 'receive', 'protocol': 1,
            'request': fleet.request('receiver'), 'message_id': pending['messageId']}, expected='network_transport_unavailable')
        assert next(mail for mail in fleet.inspect()['mail'] if mail['messageId'] == pending['messageId'])['state'] == 'service_accepted'
        host.start_sshd()
        assert fleet.receive(pending['messageId'])['envelope']['messageId'] == pending['messageId']
        checks.append('partition_does_not_accept_delivery_and_same_message_recovers_after_owned_sshd_restart')

        # A wrong SSH host pin fails closed before a seat frame can be accepted.
        wrong = host.key('wrong-host-pin')
        host.install_pin(wrong)
        sync_receiver()
        fleet.wire('receiver', {'method': 'sync', 'protocol': 1,
            'request': fleet.request('receiver')}, expected='network_transport_unavailable')
        host.install_pin()
        assert fleet.wire('receiver', {'method': 'sync', 'protocol': 1,
            'request': fleet.request('receiver')})['ok']
        checks.append('pinned_ssh_server_identity_is_required_without_tofu_or_agent_fallback')

        host.rotate_server_key()
        sync_receiver()
        fleet.wire('receiver', {'method': 'sync', 'protocol': 1,
            'request': fleet.request('receiver')}, expected='network_transport_unavailable')
        host.install_pin()
        assert fleet.wire('receiver', {'method': 'sync', 'protocol': 1,
            'request': fleet.request('receiver')})['ok']
        checks.append('actual_ssh_server_key_rotation_requires_an_explicit_operator_pin_update')

        # Application host-key rotation applies even while sshd still accepts the old public key.
        new_key, new_id = host.key('client-key-2'), str(uuid.uuid4())
        host.keys.append((new_key, host.host, new_id))
        host.write_authorized()
        fleet.admin('rotate-host', '--state', fleet.state, '--host', host.host, '--key-id', new_id)
        sync_receiver()
        fleet.wire('receiver', {'method': 'sync', 'protocol': 1,
            'request': fleet.request('receiver')}, expected='host_credential_inactive')
        host.proxy_config['identityFile'] = str(new_key)
        host.proxy_config['transportKey'] = new_id
        host.write_proxy_config()
        assert fleet.wire('receiver', {'method': 'sync', 'protocol': 1,
            'request': fleet.request('receiver')})['ok']
        checks.append('transport_key_rotation_invalidates_old_key_id_and_accepts_explicit_new_key_without_daemon_restart')

        overlap_key, overlap_id = host.key('client-key-3'), str(uuid.uuid4())
        host.keys.append((overlap_key, host.host, overlap_id))
        host.write_authorized()
        fleet.admin('rotate-host', '--state', fleet.state, '--host', host.host, '--key-id', overlap_id, '--overlap-seconds', 2)
        sync_receiver()
        assert fleet.wire('receiver', {'method': 'sync', 'protocol': 1, 'request': fleet.request('receiver')})['ok']
        time.sleep(2.1)
        sync_receiver()
        fleet.wire('receiver', {'method': 'sync', 'protocol': 1, 'request': fleet.request('receiver')}, expected='host_credential_inactive')
        host.proxy_config['identityFile'] = str(overlap_key)
        host.proxy_config['transportKey'] = overlap_id
        host.write_proxy_config()
        assert fleet.wire('receiver', {'method': 'sync', 'protocol': 1, 'request': fleet.request('receiver')})['ok']
        reused = fleet.admin('rotate-host', '--state', fleet.state, '--host', host.host, '--key-id', host.key_id, expected=None)
        assert reused.returncode and 'host_rotation_denied' in reused.stderr
        checks.append('key_overlap_expires_within_its_bound_and_retired_key_ids_cannot_be_reactivated')

        # Revocation must apply after SSH authentication but before a delayed application frame.
        sync_receiver()
        log_offset = len(host.sshd_log.read_text())
        active, active_spec = host.probe({'method': 'sync', 'protocol': 1, 'request': fleet.request('receiver')},
            receiver['uid'], receiver['credential'], held=True)
        deadline = time.monotonic() + 2
        while 'Starting session:' not in host.sshd_log.read_text()[log_offset:]:
            assert active.poll() is None and time.monotonic() < deadline, 'The owned authenticated channel did not open.'
            time.sleep(.01)
        pending = fleet.envelope()
        fleet.send(pending)
        revoked = json.loads(fleet.admin('revoke-host', '--state', fleet.state, '--host', host.host).stdout)
        fleet.sudo('touch', active_spec['holdRelease'])
        stdout, stderr = active.communicate(timeout=8)
        held_result = json.loads(stdout)
        assert active.returncode == 0 and not stderr, (active.returncode, stderr)
        assert held_result['response']['response']['error'] == 'host_credential_inactive', held_result
        checks.append('host_revocation_rechecks_a_previously_authenticated_open_ssh_channel_before_accepting_its_frame')
        assert revoked['seatsRevoked'] == 1 and revoked['pendingQuarantined'] >= 1
        sync_receiver()
        fleet.wire('receiver', {'method': 'sync', 'protocol': 1,
            'request': fleet.request('receiver')}, expected='host_credential_inactive')
        assert next(mail for mail in fleet.inspect()['mail'] if mail['messageId'] == pending['messageId'])['state'] == 'quarantined'
        checks.append('host_revocation_refuses_new_frames_and_quarantines_pending_work')
        (fleet.e / 'qualification-scope.json').write_text(json.dumps({'secondPhysicalHost': False,
            'transport': 'actual OpenSSH over owned loopback listener', 'realAccountModelCalls': 0,
            'liveTarget': None, 'requiredLiveAcceptance': 'blocked until a second SSH test host is selected'}, indent=2))
    except Exception:
        error = traceback.format_exc()
    finally:
        for owned in [observer, native, foreman, host, model, fleet]:
            if owned is not None:
                try:
                    owned.close()
                except Exception:
                    error = (error or '') + traceback.format_exc()
    return result(args.evidence, checks, error)


if __name__ == '__main__':
    raise SystemExit(main())
