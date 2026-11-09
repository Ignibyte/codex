"""Resume an actual legacy factory rollout without changing its delivery identity."""
import argparse
import json
from pathlib import Path
import traceback

from fleet_fixture import Fleet, result
from native_fixture import Native
from session_fixture import Model


def main():
    parser = argparse.ArgumentParser()
    for key in ('binary', 'cli', 'old-cli', 'evidence'):
        parser.add_argument('--' + key, required=True)
    args = parser.parse_args()
    fleet = Fleet(args.binary, args.evidence)
    model = Model()
    native = None
    checks = []
    error = None
    try:
        fleet.enroll('sender')
        fleet.enroll('receiver', ['observe', 'queue', 'acknowledge'])
        native = Native(fleet, args.old_cli, model)
        thread = native.request('thread/start', {
            'cwd': str(native.home / 'project'), 'model': 'mock-model',
        })['thread']['id']
        envelope = fleet.envelope(mode='wake_when_idle', text=(
            'Assignment: Legacy delivery\nDo: Read this fixture message.\n'
            'Done when: Report that the message is received.\nLimits: Use no tools.'))
        fleet.send(envelope)
        params = {'threadId': thread, 'project': 'fixture-project',
                  'messageId': envelope['messageId']}
        started = native.request('thread/factoryMessage', params)['data']
        assert started['state'] == 'started'
        model.wait_request(0)
        legacy_id = 'factory_' + envelope['messageId']
        assert any(i.get('type') == 'agent_message' and i.get('id') == legacy_id
                   for i in model.requests[0]['body']['input'])
        model.release(0)
        native.event('turn/completed', started['turnId'])
        old_history = native.request('thread/read', {'threadId': thread, 'includeTurns': True})
        rollout = Path(old_history['thread']['path'])
        assert rollout.is_relative_to(native.home)
        native.stop()
        fleet.sudo('install', '-m', '0555', Path(args.cli).resolve(), native.binary)
        native.start()
        native.request('thread/resume', {'threadId': thread})
        reconciled = native.request('thread/factoryMessage', params)['data']
        assert reconciled['state'] in ('consumed', 'completed'), reconciled
        assert reconciled['turnId'] == started['turnId'], reconciled
        assert len(model.requests) == 1
        checks.append('legacy_delivery_reconciles_to_original_turn_without_reinjection')
        followup = native.request('turn/start', {'threadId': thread, 'input': [
            {'type': 'text', 'text': 'Report the preceding fixture message. Use no tools.'},
        ]})['turn']['id']
        model.wait_request(1)
        authored = [i for i in model.requests[1]['body']['input']
                    if i.get('type') == 'agent_message']
        assert len(authored) == 1, authored
        assert authored[0]['id'] == 'amsg_' + envelope['messageId'], authored
        original = next(i for i in model.requests[0]['body']['input']
                        if i.get('id') == legacy_id)
        assert {k: v for k, v in original.items() if k != 'id'} == {
            k: v for k, v in authored[0].items() if k != 'id'}
        model.release(1)
        native.event('turn/completed', followup)
        raw = fleet.run([*fleet.prefix(native.actor['uid']), 'cat', '--', rollout]).stdout
        assert legacy_id in raw
        mail = next(m for m in fleet.inspect()['mail'] if m['messageId'] == envelope['messageId'])
        assert sum(r['state'] == 'consumed' for r in mail['receipts']) == 1, mail
        checks.append('outbound_prefix_changes_without_rewriting_history_author_content_or_receipts')
        (fleet.e / 'reconciled.json').write_text(json.dumps(reconciled, indent=2))
        (fleet.e / 'provider-requests.json').write_text(json.dumps(model.requests, indent=2))
    except Exception:
        error = traceback.format_exc()
    finally:
        model.close()
        if native:
            native.close()
        fleet.close()
    return result(args.evidence, checks, error)


if __name__ == '__main__':
    raise SystemExit(main())
