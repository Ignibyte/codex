#!/usr/bin/env python3
"""Actual native recording and bridge recovery, with secret-free replacement archives."""
import argparse
import json
from pathlib import Path
import traceback
import uuid

from fleet_fixture import Fleet, SERVICE, result
from native_fixture import Native
from session_fixture import Model


def main():
    p = argparse.ArgumentParser()
    for name in ('binary', 'cli', 'evidence'):
        p.add_argument('--' + name, required=True)
    a = p.parse_args()
    f, model = Fleet(a.binary, a.evidence), Model()
    native, checks, error = None, [], None
    try:
        f.enroll('sender')
        f.enroll('receiver', ['observe', 'queue', 'acknowledge'])
        native = Native(f, a.cli, model)
        thread = native.request('thread/start', {'cwd': str(native.home / 'project')})['thread']['id']
        done = f.envelope(mode='wake_when_idle', assignmentId=str(uuid.uuid4()),
                          text='Assignment: CF-016\nDo: Reply READY. Use no tools. Do not create another agent.')
        f.send(done)
        params = {'threadId': thread, 'project': 'fixture-project', 'messageId': done['messageId']}
        turn = native.request('thread/factoryMessage', params)['data']
        model.wait_request(0)
        model.release(0)
        native.event('turn/completed', turn['turnId'])
        assert native.request('thread/factoryMessage', params)['data']['state'] == 'completed'
        receiver = f.subjects['receiver']
        f.run([*f.prefix(receiver['uid']), f.binary, 'collect-recording',
               '--native-state', f.base / 'receiver/native-ledger', '--grant', f.publish(receiver['grant']),
               '--socket', f.socket, '--service-uid', SERVICE, '--credential', receiver['credential']])
        receiver['sequence'] = next(s['lastSequence'] for s in f.inspect()['seats'] if s['seat'] == receiver['seat'])
        pending, delivered = f.envelope(), f.envelope()
        f.send(pending)
        f.send(delivered)
        f.receive(delivered['messageId'])
        active = f.envelope(mode='wake_when_idle')
        f.send(active)
        active_params = {**params, 'messageId': active['messageId']}
        active_turn = native.request('thread/factoryMessage', active_params)['data']
        model.wait_request(1)
        assert active_turn['state'] == 'started'
        before = f.inspect()
        old_key = f.sudo('sha256sum', f.state / 'service.key').stdout.split()[0]
        old_credential = json.loads(f.sudo('cat', receiver['credential']).stdout)['credential']
        archive = f.base / 'snapshot.json'
        f.admin('backup', '--state', f.state, '--output', archive)
        raw = f.sudo('cat', archive).stdout
        snapshot = json.loads(raw)
        assert old_credential not in raw
        def keys(value):
            if isinstance(value, dict):
                return set(value).union(*(keys(v) for v in value.values()))
            if isinstance(value, list):
                return set().union(*(keys(v) for v in value))
            return set()
        assert not keys(snapshot).intersection({'nonce', 'attestation', 'claimOwner', 'credential'})
        assert all(name not in raw for name in ['service.key', 'auth.json', 'claimOwner', 'startTicks'])
        assert len(snapshot['history']['recording']) == 1
        assert snapshot['history']['recording'][0]['event']['messageId'] == done['messageId']
        (f.e / 'archive-sanitized.json').write_text(raw)
        checks.append('actual_native_terminal_and_recording_archived_without_keys_credentials_or_process_claims')
        f.stop()
        f.start()
        assert f.inspect()['mail'] == before['mail']
        assert len(model.requests) == 2
        checks.append('same_boot_bridge_restart_preserves_receipts_and_does_not_submit_a_turn')
        f.admin('backup', '--state', f.state, '--output', archive, expected=1)
        parent = f.base / 'replacements'
        f.sudo('install', '-d', '-o', SERVICE, '-g', SERVICE, '-m', '0700', parent)
        restored = parent / 'host-a'
        restoration = json.loads(f.admin('restore', '--input', archive, '--destination', restored).stdout)
        assert restoration['host'] != f.host and not restoration['automaticReplay']
        assert restoration['pendingQuarantined'] == 1 and restoration['activeRequiresReconciliation'] == 2
        f.stop()
        original = f.state
        f.state = restored
        f.start()
        after = f.inspect()
        mails = {m['messageId']: m for m in after['mail']}
        old_mails = {m['messageId']: m for m in before['mail']}
        assert mails[done['messageId']] == old_mails[done['messageId']]
        assert mails[pending['messageId']]['state'] == 'quarantined'
        assert all(mails[m['messageId']]['reconcileRequired'] for m in (active, delivered))
        assert all(s['revoked'] for s in after['seats'])
        assert f.sudo('sha256sum', restored / 'service.key').stdout.split()[0] != old_key
        f.wire('receiver', {'method': 'peers', 'protocol': 1,
                           'request': f.request('receiver')}, expected='credential_denied')
        assert len(model.requests) == 2
        archived = json.loads(f.admin('recovery-status', '--state', restored).stdout)['archive']
        assert archived['authority'] == 'archive_only' and len(archived['recording']) == 1
        assert f.run(['sudo', '-n', 'test', '!', '-e', restored / 'recording/registry.json']).returncode == 0
        repeated_archive = parent / 'after-replacement.json'
        f.admin('backup', '--state', restored, '--output', repeated_archive)
        retained = json.loads(f.sudo('cat', repeated_archive).stdout)
        assert len(retained['history']['recording']) == 1
        assert retained['history']['recording'][0] == snapshot['history']['recording'][0]
        checks.append('replacement_host_revokes_authority_quarantines_pending_reconciles_active_and_preserves_terminal_receipt')
        f.admin('restore', '--input', archive, '--destination', restored, expected=1)
        second = json.loads(f.admin('restore', '--input', archive, '--destination', parent / 'host-b').stdout)
        assert second['host'] != restoration['host']
        linked = parent / 'symlink'
        f.sudo('ln', '-s', restored, linked)
        f.admin('restore', '--input', archive, '--destination', linked, expected=1)
        corrupt = json.loads(raw)
        corrupt['history']['createdAt'] += 1
        source = f.publish(corrupt)
        private = parent / 'corrupt.json'
        f.sudo('install', '-o', SERVICE, '-g', SERVICE, '-m', '0600', source, private)
        refused = f.admin('restore', '--input', private, '--destination', parent / 'corrupt-host', expected=1)
        assert 'backup_checksum_mismatch' in refused.stderr
        assert f.sudo('test', '!', '-e', parent / 'corrupt-host').returncode == 0
        checks.append('old_backup_reuse_creates_distinct_fenced_hosts_existing_symlink_and_corruption_refused')
        f.stop()
        f.state = original
    except Exception:
        error = traceback.format_exc()
    finally:
        model.close()
        if native:
            native.close()
        f.close()
    return result(a.evidence, checks, error)


if __name__ == '__main__':
    raise SystemExit(main())
