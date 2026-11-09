#!/usr/bin/env python3
"""Canonical Forge ownership and native briefing before managed assignment dispatch."""
import argparse
import json
from pathlib import Path
import time
import traceback
import uuid

from fleet_fixture import Fleet, SERVICE, GROUP, result
from forge_workflow_fixture import Workflow
from native_fixture import Native
from real_forge_fixture import RealForge
from session_fixture import Model


def main():
    p = argparse.ArgumentParser()
    for name in ('binary', 'cli', 'forge-binary', 'forge-source', 'evidence'):
        p.add_argument('--' + name, required=True)
    a = p.parse_args()
    f, model = Fleet(a.binary, a.evidence), Model()
    forge, workflow, native, checks, error = None, None, None, [], None
    try:
        f.enroll('sender', role='foreman')
        f.enroll('receiver', ['observe', 'queue', 'acknowledge'])
        receiver = f.subjects['receiver']
        forge = RealForge(f, a.forge_binary, a.forge_source)
        workflow = Workflow(f, forge)
        forge_config = f.state / 'foreman-forge-config.json'
        forge_value = {'endpoint': forge.endpoint, 'forgeIdentity': 'factory-fixture',
            'forgeProject': forge.project, 'localProject': 'fixture-project',
            'signerArgv': [str(forge.binary), 'sign', 'headers', '--name', 'factory-fixture',
                           '--key-dir', str(forge.key_dir), '--ttl', '60']}
        f.sudo('install', '-o', SERVICE, '-g', GROUP, '-m', '0600', f.publish(forge_value), forge_config)
        repository, worktrees = f.base / 'repository', f.base / 'worktrees'
        f.sudo('install', '-d', '-o', SERVICE, '-g', GROUP, '-m', '0750', repository)
        f.sudo('install', '-d', '-o', SERVICE, '-g', GROUP, '-m', '0711', worktrees)
        def git(*args):
            return f.run([*f.prefix(SERVICE), 'git', '-C', repository, *args])
        git('init', '-b', 'main')
        git('-c', 'user.name=Fixture', '-c', 'user.email=fixture@invalid', 'commit', '--allow-empty', '-m', 'Owned fixture baseline')
        config = f.state / 'foreman-config.json'
        value = {'bridgeState': str(f.state), 'forgeConfig': str(forge_config),
            'foreman': f.subjects['sender']['seat'], 'repository': str(repository), 'worktreeRoot': str(worktrees)}
        f.sudo('install', '-o', SERVICE, '-g', GROUP, '-m', '0600', f.publish(value), config)
        state = f.state / 'foreman'
        def action(value, expected=0):
            r = f.admin('foreman', '--state', state, '--config', config, '--input', f.publish(value), expected=expected)
            return json.loads(r.stdout) if not r.returncode else r
        ticket = workflow.call('ticket-create', {'title': 'Owned foreman development journey',
            'description': 'Do: Verify briefing and canonical ownership. Use the isolated worktree.',
            'type': 'spike', 'project_id': forge.project})['ticket']
        operation = str(uuid.uuid4())
        claim_args = {'action': 'claim', 'operation_id': operation, 'ticket': ticket['number'],
            'worker': receiver['seat'], 'context': 'The repository is an isolated fixture. Do not use a real account.',
            'brief': 'Read the required context. Wait for the executable specification.'}
        claimed = action(claim_args)
        run, context_digest = claimed['runId'], claimed['contextDigest']
        duplicate = action(claim_args)
        assert duplicate['runId'] == run and duplicate['briefMessageId'] == claimed['briefMessageId']
        context = workflow.call('pipeline-context', {'ticket_number': ticket['number'], 'project_id': forge.project})
        assert context['run']['id'] == run and context['run']['owner_id'] == operation
        assert forge.sql('SELECT count(*) FROM pipeline_runs;') == '1'
        checks.append('canonical_run_and_owner_are_reused_on_identical_claim_without_another_run_or_brief')
        workspace = worktrees / run
        git('worktree', 'add', '-b', claimed['workBranch'], workspace, 'HEAD')
        f.sudo('chown', '-R', str(receiver['uid']) + ':' + str(GROUP), workspace)
        f.sudo('chmod', '0750', workspace)
        action({'action': 'register_workspace', 'run_id': run, 'workspace': str(workspace)})
        spec_args = {'action': 'issue_spec', 'run_id': run, 'spec': 'Do: Reply READY. Use no tools.', 'lease_seconds': 8}
        refused = action(spec_args, expected=1)
        assert 'foreman_context_not_acknowledged' in refused.stderr
        assert len(f.inspect()['mail']) == 1
        native = Native(f, a.cli, model, supervised=True)
        thread = native.request('thread/start', {'cwd': str(native.home / 'project')})['thread']['id']
        model.outputs[0] = {'type': 'message', 'role': 'assistant', 'id': 'context-receipt',
            'content': [{'type': 'output_text', 'text': 'Context acknowledged: ' + context_digest}]}
        params = {'threadId': thread, 'project': 'fixture-project', 'messageId': claimed['briefMessageId']}
        started = native.request('thread/factoryMessage', params)['data']
        model.wait_request(0)
        assert context_digest in json.dumps(model.requests[0])
        model.release(0)
        native.event('turn/completed', started['turnId'])
        assert native.request('thread/factoryMessage', params)['data']['state'] == 'completed'
        receiver['sequence'] = next(s['lastSequence'] for s in f.inspect()['seats'] if s['seat'] == receiver['seat'])
        def ack(digest, expected=None):
            return f.wire('receiver', {'method': 'acknowledge_context', 'protocol': 1,
                'request': f.request('receiver', 'acknowledge'), 'run_id': run, 'context_digest': digest}, expected=expected)
        ack('0' * 64, 'foreman_context_mismatch')
        ack(context_digest)
        action({'action': 'register_execution', 'run_id': run, 'pid': native.identity['pid']})
        issued = action(spec_args)
        assert action(spec_args)['messageId'] == issued['messageId']
        changed = action({**spec_args, 'spec': 'Do: Execute another specification.'}, expected=1)
        assert 'foreman_spec_conflict' in changed.stderr
        checks.append('actual_native_brief_contains_required_context_and_spec_requires_authenticated_matching_ack')
        def authorize(cwd, expected=None):
            return f.wire('receiver', {'method': 'authorize_assignment', 'protocol': 1,
                'request': f.request('receiver'), 'message_id': issued['messageId'], 'cwd': str(cwd)}, expected=expected)
        authorize(native.home / 'project', 'foreman_execution_workspace_denied')
        assert authorize(workspace)['runId'] == run
        workflow.call('pipeline-run-event', {'run_id': run, 'project_id': forge.project, 'kind': 'run_held',
                      'summary': 'The owned fixture holds execution.'})
        authorize(workspace, 'foreman_canonical_owner_inactive')
        workflow.call('pipeline-run-event', {'run_id': run, 'project_id': forge.project, 'kind': 'run_resumed',
                      'summary': 'The owned fixture resumes execution.'})
        assert authorize(workspace)['generation'] == operation
        assert len(model.requests) == 1
        checks.append('exact_real_worktree_and_current_forge_owner_are_required_without_extra_model_submission')
        time.sleep(8.1)
        authorize(workspace, 'foreman_lease_inactive')
        checks.append('expired_lease_refuses_another_managed_dispatch')
        quarantined = action({'action': 'quarantine_expired', 'run_id': run})
        assert not quarantined['processStopped'] and not quarantined['replacementAllowed']
        refused = action({'action': 'fence', 'run_id': run}, expected=1)
        assert 'foreman_process_set_still_live' in refused.stderr
        recon_dir = state / 'reconciliations'
        f.sudo('install', '-d', '-o', SERVICE, '-g', GROUP, '-m', '0700', recon_dir)
        report = recon_dir / (run + '.json')
        receipt = {'runId': run, 'generation': operation, 'confirmedNoOutstandingEffects': True,
            'evidence': ['The native brief completed without tools. The executable specification never started.']}
        f.sudo('install', '-o', SERVICE, '-g', GROUP, '-m', '0600', f.publish(receipt), report)
        refused = action({'action': 'reconcile', 'run_id': run, 'report': str(report)}, expected=1)
        assert 'foreman_reconciliation_order_denied' in refused.stderr
        f.sudo('systemctl', 'stop', native.unit)
        code = native.process.wait(timeout=12)
        f.commands.append({'supervisedNativeExit': code, 'unit': native.unit, 'incarnation': native.identity})
        assert action({'action': 'fence', 'run_id': run})['disposition'] == 'fenced'
        reconciled = action({'action': 'reconcile', 'run_id': run, 'report': str(report)})
        assert reconciled['disposition'] == 'abandoned' and not reconciled['newOwnerGranted']
        assert action({'action': 'reconcile', 'run_id': run, 'report': str(report)})['receiptSha256'] == reconciled['receiptSha256']
        replacement = action({**claim_args, 'operation_id': str(uuid.uuid4())})
        assert replacement['runId'] != run and replacement['generation'] != operation
        refused = action({**spec_args, 'run_id': replacement['runId']}, expected=1)
        assert 'foreman_context_mismatch' in refused.stderr
        checks.append('an_old_context_ack_cannot_authorize_a_new_generation_with_identical_context')
        f.wire('receiver', {'method': 'assignment_result', 'protocol': 1,
            'request': f.request('receiver', 'acknowledge'), 'run_id': run,
            'thread_id': thread, 'turn_id': started['turnId'], 'artifacts': []},
            expected='foreman_result_generation_inactive')
        checks.append('actual_cgroup_fence_and_protected_effect_receipt_precede_distinct_canonical_generation_stale_result_refused')
        (f.e / 'assignment.json').write_text(json.dumps(action({'action': 'status', 'run_id': run}), indent=2))
    except Exception:
        error = traceback.format_exc()
    finally:
        model.close()
        if native:
            native.close()
        if workflow:
            (f.e / 'workflow-calls.json').write_text(json.dumps(workflow.calls, indent=2))
        if forge:
            forge.close()
        f.close()
    return result(a.evidence, checks, error)


if __name__ == '__main__':
    raise SystemExit(main())
