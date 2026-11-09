#!/usr/bin/env python3
"""Observe Forge's canonical run uniqueness before implementing its consumer."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import traceback
import uuid

from fleet_fixture import Fleet, result
from real_forge_fixture import RealForge
from forge_workflow_fixture import Workflow


def main():
    p = argparse.ArgumentParser()
    for name in ('binary', 'forge-binary', 'forge-source', 'evidence'):
        p.add_argument('--' + name, required=True)
    a = p.parse_args()
    f = Fleet(a.binary, a.evidence)
    forge, workflow, checks, error = None, None, [], None
    try:
        f.enroll('sender')
        forge = RealForge(f, a.forge_binary, a.forge_source)
        workflow = Workflow(f, forge)
        tools = workflow.request('tools/list', {})['tools']
        names = {'ticket-create', 'ticket-get', 'ticket-update', 'pipeline-run-start',
                 'pipeline-context', 'pipeline-run-event', 'pipeline-run-complete'}
        selected = [t for t in tools if t['name'] in names]
        assert {t['name'] for t in selected} == names
        (f.e / 'actual-schemas.json').write_text(json.dumps(selected, indent=2))
        ticket = workflow.call('ticket-create', {'title': 'Owned CF-017 exclusivity probe',
            'description': 'Do: Verify one canonical active run. Use no model.', 'type': 'spike',
            'project_id': forge.project})['ticket']
        owners = [str(uuid.uuid4()), str(uuid.uuid4())]
        def start(owner):
            return workflow.request('tools/call', {'name': 'pipeline-run-start', 'arguments': {
                'ticket_number': ticket['number'], 'project_id': forge.project,
                'owner_id': owner, 'adopt': False, 'pipeline_type': 'work',
                'bridge_session_id': 'factory-probe/' + owner}})
        with ThreadPoolExecutor(max_workers=2) as pool:
            replies = list(pool.map(start, owners))
        accepted = [r for r in replies if not r.get('isError')]
        refused = [r for r in replies if r.get('isError')]
        assert len(accepted) == len(refused) == 1, replies
        assert 'active' in json.dumps(refused).lower() or 'concurrent' in json.dumps(refused).lower()
        winner = accepted[0].get('structuredContent') or json.loads(accepted[0]['content'][0]['text'])
        run = winner['run']
        context = workflow.call('pipeline-context', {'ticket_number': ticket['number'], 'project_id': forge.project})
        assert context['run']['id'] == run['id'] and context['run']['owner_id'] == run['owner_id']
        assert forge.sql('SELECT count(*) FROM pipeline_runs WHERE status IN (\'running\',\'held\');') == '1'
        checks.append('two_signed_concurrent_starts_create_one_canonical_active_run_and_one_conflict')
        workflow.call('pipeline-run-complete', {'run_id': run['id'], 'project_id': forge.project,
            'outcome': 'abandoned', 'summary': 'The owned probe stopped without executing work.'})
        next_owner = str(uuid.uuid4())
        replacement = workflow.call('pipeline-run-start', {'ticket_number': ticket['number'],
            'project_id': forge.project, 'owner_id': next_owner, 'adopt': False})['run']
        assert replacement['id'] != run['id'] and replacement['owner_id'] == next_owner
        checks.append('abandoned_canonical_generation_releases_slot_for_a_distinct_run_without_adoption')
        (f.e / 'race.json').write_text(json.dumps({'ticket': ticket, 'owners': owners,
            'responses': replies, 'context': context, 'replacement': replacement}, indent=2))
    except Exception:
        error = traceback.format_exc()
    finally:
        if workflow:
            (f.e / 'workflow-calls.json').write_text(json.dumps(workflow.calls, indent=2))
        if forge:
            forge.close()
        f.close()
    return result(a.evidence, checks, error)


if __name__ == '__main__':
    raise SystemExit(main())
