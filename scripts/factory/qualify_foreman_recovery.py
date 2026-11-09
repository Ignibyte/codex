#!/usr/bin/env python3
"""Lost canonical claim acknowledgement and cold-cache recovery without model dispatch."""
import argparse
import json
from pathlib import Path
import traceback
import uuid

from fleet_fixture import SERVICE, GROUP, result
from forge_workflow_fixture import ForemanFleet
from real_forge_fixture import ForgeProxy


def main():
    p = argparse.ArgumentParser()
    for name in ('binary', 'forge-binary', 'forge-source', 'evidence'):
        p.add_argument('--' + name, required=True)
    a = p.parse_args()
    f = ForemanFleet(a.binary, a.forge_binary, a.forge_source, a.evidence)
    proxy, error, checks = None, None, []
    try:
        proxy = ForgeProxy(f.forge.endpoint)
        config = json.loads(f.sudo('cat', f.config).stdout)
        forge_path = Path(config['forgeConfig'])
        forge_config = json.loads(f.sudo('cat', forge_path).stdout)
        forge_config['endpoint'] = proxy.endpoint
        f.sudo('install', '-o', SERVICE, '-g', GROUP, '-m', '0600', f.publish(forge_config), forge_path)
        ticket = f.ticket('Owned claim acknowledgement loss and cold recovery')
        operation = str(uuid.uuid4())
        args = {'action': 'claim', 'operation_id': operation, 'ticket': ticket['number'],
            'worker': f.subjects['receiver']['seat'], 'context': 'This is an isolated fixture.',
            'brief': 'Acknowledge context. Wait for the executable specification.'}
        proxy.mode = 'lost_workflow_ack'
        f.action(args, expected=1)
        assert f.forge.sql('SELECT count(*) FROM pipeline_runs;') == '1'
        assert not f.inspect()['mail']
        proxy.mode = 'normal'
        recovered = f.action(args)
        assert f.action(args)['briefMessageId'] == recovered['briefMessageId']
        assert f.forge.sql('SELECT count(*) FROM pipeline_runs;') == '1'
        assert sum(row['method'] == 'pipeline-run-start' for row in proxy.forwarded) == 1
        checks.append('lost_start_ack_reconciles_the_canonical_owner_without_another_start_or_duplicate_brief')
        run = recovered['runId']
        f.workflow.call('pipeline-run-complete', {'run_id': run, 'project_id': f.forge.project,
            'outcome': 'abandoned', 'summary': 'The owned probe ended before executable dispatch.'})
        refused = f.action(args, expected=1)
        assert 'foreman_operation_already_terminal' in refused.stderr
        assert f.forge.sql('SELECT count(*) FROM pipeline_runs;') == '1'
        checks.append('a_terminal_canonical_operation_cannot_be_replayed_as_fresh_work')
        # Discard only the private dispatch projection, with no native worker/model ever started.
        state = f.state / 'foreman'
        f.run([*f.prefix(SERVICE), 'mv', state / 'registry.json', state / 'registry-retained.json'])
        next_args = {**args, 'operation_id': str(uuid.uuid4()), 'worker': f.subjects['replacement']['seat']}
        refused = f.action(next_args, expected=1)
        assert 'foreman_prior_generation_recovery_required' in refused.stderr
        directory = state / 'recoveries'
        f.sudo('install', '-d', '-o', SERVICE, '-g', GROUP, '-m', '0700', directory)
        report = directory / (str(ticket['number']) + '.json')
        receipt = {'ticket': ticket['number'], 'priorRuns': [run],
            'confirmedAllPriorProcessesStopped': True, 'confirmedNoOutstandingEffects': True,
            'evidence': ['No native process or model started. The canonical run ended before executable dispatch.']}
        f.sudo('install', '-o', SERVICE, '-g', GROUP, '-m', '0600', f.publish(receipt), report)
        assert not f.action({'action': 'recover_ticket', 'ticket': ticket['number'], 'report': str(report)})['newOwnerGranted']
        replacement = f.action(next_args)
        assert replacement['runId'] != run
        checks.append('cold_cache_cannot_replace_a_prior_generation_without_protected_operator_recovery_evidence')
        (f.e / 'observed.json').write_text(json.dumps({'recovered': recovered, 'replacement': replacement,
            'receipt': receipt, 'forwarded': proxy.forwarded, 'realAccountTurns': 0}, indent=2))
    except Exception:
        error = traceback.format_exc()
    finally:
        if proxy:
            proxy.close()
        f.close()
    return result(a.evidence, checks, error)


if __name__ == '__main__':
    raise SystemExit(main())
