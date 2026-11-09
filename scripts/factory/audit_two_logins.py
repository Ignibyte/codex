#!/usr/bin/env python3
"""Audit CF-011 criteria from native traces, retained artifacts and package pins."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

from session_fixture import incarnation, sha256

ROOT = Path(__file__).resolve().parents[2]


def load(path):
    return json.loads(path.read_text())


def transactions(path):
    pending = {}
    for item in load(path):
        value = item['value']
        if 'id' in value and 'method' in value:
            pending[value['id']] = value
        elif 'id' in value and ('result' in value or 'error' in value):
            request = pending.pop(value['id'], None)
            if request:
                yield request, value


def main():
    parser = argparse.ArgumentParser()
    for name in ['flow', 'interrupt', 'rate-limit', 'package', 'evidence']:
        parser.add_argument('--' + name, type=Path, required=True)
    args = parser.parse_args()
    os.umask(0o077)
    args.evidence.mkdir(mode=0o700, parents=True, exist_ok=False)
    checks = []
    package = load(args.package / 'factory-package.json')
    assert sha256(args.package / 'factory-package.json') == '4390ffb1a7e780023054e447f98943b1316f702110e293425ddab389fb592ddb'
    for path, expected in package['files'].items():
        assert sha256(args.package / path) == expected, path
    product_files = {path: digest for path, digest in package['source']['files'].items()
                     if path.startswith('codex-rs/')}
    for path, expected in product_files.items():
        assert sha256(ROOT / path) == expected, path
        assert oct((ROOT / path).stat().st_mode & 0o777) == package['source']['modes'][path], path
    checks.append({'criterion': 'source_and_executables', 'productFilesMatch': len(product_files),
                   'baseCommit': package['source']['commit']})
    identity = load(args.flow / 'identities.json')
    assert identity['distinctAccountAndSubject'] and not identity['credentialTransfer']
    for key in ['subject', 'account']:
        assert identity['digests']['foreman'][key] != identity['digests']['worker'][key]
    for seat in ['sender', 'receiver']:
        auth = [response['result']['account'] for request, response in transactions(args.flow / seat / 'rpc.json')
                if request['method'] == 'account/read' and 'result' in response]
        assert auth and all(item['type'] == 'chatgpt' for item in auth)
    checks.append({'criterion': 'independent_native_logins', 'passed': True})
    sends, dispatches = [], []
    for seat in ['sender', 'receiver']:
        for request, response in transactions(args.flow / seat / 'rpc.json'):
            if request['method'] == 'factory/send':
                assert 'result' in response, response
                sends.append((seat, request['params'], response['result']['data']))
            if request['method'] == 'thread/factoryMessage' and 'result' in response:
                dispatches.append((seat, response['result']['data']))
    kinds = {message['kind']: (seat, message) for seat, message, _ in sends}
    assert set(kinds) == {'assignment', 'clarification', 'reply', 'result'}, kinds.keys()
    assignment = kinds['assignment'][1]
    assert kinds['assignment'][0] == kinds['reply'][0] == 'sender'
    assert kinds['clarification'][0] == kinds['result'][0] == 'receiver'
    assert len({message['conversationId'] for _, message, _ in sends}) == 1
    assert kinds['clarification'][1]['replyTo'] == assignment['messageId']
    assert kinds['reply'][1]['replyTo'] == kinds['clarification'][1]['messageId']
    assert kinds['result'][1]['replyTo'] == assignment['messageId']
    for _, message, _ in sends:
        receipts = [state for _, state in dispatches if state['envelope']['messageId'] == message['messageId']]
        assert any(state['state'] == 'started' for state in receipts)
        assert any(state['state'] == 'completed' and state['consumptionAcked']
                   and state['completionAcked'] for state in receipts)
        assert len({state['turnId'] for state in receipts}) == 1
    checks.append({'criterion': 'assignment_clarification_reply_result', 'messages': len(sends),
                   'nativeCompletionReceipts': 4})
    receipt = load(args.flow / 'artifact.json')
    with tempfile.TemporaryDirectory(prefix='cf011-artifact-audit-') as temporary:
        subprocess.run(['git', 'init', '--quiet', temporary], check=True)
        subprocess.run(['git', '-C', temporary, 'apply', str((args.flow / 'worker-artifact.patch').resolve())], check=True)
        path = Path(temporary) / 'greeting.txt'
        assert path.read_bytes() == b'CF-011 two-account proof\n'
        assert sha256(path) == receipt['sha256']
    assert receipt['bytes'] == 25
    confirmation = load(args.flow / 'foreman-confirmation.json')
    assert confirmation['status'] == 'completed'
    assert any('CF-011 two-account proof' in item.get('text', '')
               for item in confirmation['items'] if item['type'] == 'agentMessage')
    checks.append({'criterion': 'isolated_repository_artifact_and_result', 'sha256': receipt['sha256']})
    for number in [1, 2]:
        capture = args.flow / 'receiver' / ('tui-' + str(number))
        assert load(capture.with_suffix('.json'))['exitCode'] == 0
        assert b'greeting.txt' in capture.with_suffix('.raw').read_bytes()
        assert 'greeting.txt' in capture.with_suffix('.txt').read_text()
    resumed = load(args.flow / 'receiver/resumed.json')
    states = [state for seat, state in dispatches if seat == 'receiver'
              and state['envelope']['messageId'] == assignment['messageId']]
    assert len(states) >= 4 and len({state['turnId'] for state in states}) == 1
    assert states[-1] == states[-2] and states[-1]['state'] == 'completed'
    assert resumed['thread']['id'] == states[-1]['threadId']
    restarts = [response['result'] for request, response in transactions(args.flow / 'receiver/rpc.json')
                if request['method'] == 'thread/resume' and 'result' in response]
    assert restarts and restarts[-1]['thread']['id'] == resumed['thread']['id']
    checks.append({'criterion': 'native_ui_reconnect_duplicate_delivery_recipient_restart', 'passed': True})
    interrupt_result = load(args.interrupt / 'result.json')
    assert interrupt_result['passed'] and interrupt_result['live']
    interrupted_calls = [(request, response) for request, response in transactions(args.interrupt / 'receiver/rpc.json')
                         if request['method'] == 'turn/interrupt' and 'result' in response]
    assert len(interrupted_calls) == 1
    turn_id = interrupted_calls[0][0]['params']['turnId']
    terminal = load(args.interrupt / 'receiver' / (turn_id + '.json'))
    assert terminal['status'] == 'interrupted'
    assert load(args.rate_limit / 'result.json')['passed']
    rate = load(args.rate_limit / 'rate-limit.json')
    assert rate['providerRequestCount'] == 1
    assert rate['completed']['params']['turn']['status'] == 'failed'
    checks.append({'criterion': 'interrupted_live_turn_and_synthetic_rate_limit', 'passed': True})
    for run in [args.flow, args.interrupt, args.rate_limit]:
        for path in run.glob('*/processes.json'):
            for pid, known in load(path).items():
                assert incarnation(int(pid)) != tuple(known), (pid, known)
        for path in run.glob('*/operator-config.json'):
            assert 'model' not in load(path), 'An operator model override is forbidden.'
    checks.append({'criterion': 'owned_process_cleanup_and_no_model_override', 'passed': True})
    # Retain exact operator sources alongside the already reconstructable CF-010 source overlay.
    sources = args.evidence / 'operator-sources'
    sources.mkdir(mode=0o700)
    for name in ['qualify_two_logins.py', 'live_command.py', 'qualify_rate_limit.py', 'qualify_live_driver.py',
                 'fleet_fixture.py', 'native_fixture.py', 'native_owner.py', 'session_fixture.py', 'audit_two_logins.py']:
        shutil.copyfile(ROOT / 'scripts/factory' / name, sources / name)
    diff = subprocess.run(['git', 'diff', '--binary'], cwd=ROOT, capture_output=True, check=True).stdout
    (args.evidence / 'tracked-source.patch').write_bytes(diff)
    record = {'passed': True, 'criteria': checks, 'flowRunPassedAsWhole': load(args.flow / 'result.json')['passed'],
              'explanation': 'The flow run passed its main criteria and failed at the interrupt admission race. '
                             'The separate live interrupt run closes that boundary. Historical failures remain retained.',
              'packageManifestSha256': sha256(args.package / 'factory-package.json'),
              'operatorSourceHashes': {p.name: sha256(p) for p in sources.iterdir()},
              'scopeLimits': ['same-UID profiles do not isolate hostile unrestricted shells',
                              'rate limiting uses a synthetic provider, not quota exhaustion',
                              'operator relays model replies through authenticated factory APIs',
                              'no multi-host, scale, deployment or publication qualification']}
    (args.evidence / 'result.json').write_text(json.dumps(record, indent=2) + '\n')
    print(json.dumps({'passed': True, 'criteria': len(checks)}))


if __name__ == '__main__':
    main()
