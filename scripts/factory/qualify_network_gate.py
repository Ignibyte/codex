#!/usr/bin/env python3
"""Stable-source CF-015 local network/regression gate; real second host still required, with retained execution and real Forge proof."""
import argparse
import json
from pathlib import Path
import platform
import subprocess
import sys
import traceback

from package_control import ROOT, digest, source_identity, verify


def main():
    parser = argparse.ArgumentParser()
    for name in ['package', 'fault-cli', 'fault-bridge', 'old-cli', 'canary-a', 'canary-b',
                 'previous-package', 'forge-binary', 'forge-source', 'evidence']:
        parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--legacy-route-binary', type=Path)
    parser.add_argument('--legacy-message-cli', type=Path)
    args = parser.parse_args()
    for name, value in vars(args).items():
        if isinstance(value, Path):
            setattr(args, name, value.resolve())
    args.evidence.mkdir(mode=0o700, parents=True, exist_ok=False)
    before = source_identity()
    cli = args.package / 'bin/codex'
    bridge = args.package / 'bin/codex-factory-bridge'
    epoch = json.loads((args.package / 'factory-package.json').read_text())['storageEpoch']
    tests = [
        ('network', 'qualify_network.py', ['--binary', bridge, '--cli', cli]),
        ('observation', 'qualify_observation.py', ['--binary', bridge, '--cli', cli]),
        ('marley-ui', 'observation_ui_fixture.py', ['--binary', bridge, '--cli', cli, '--auto']),
        ('recording', 'qualify_recording.py', ['--binary', bridge, '--cli', cli]),
        ('real-forge', 'qualify_recording_forge.py', ['--binary', bridge, '--cli', cli,
            '--forge-binary', args.forge_binary, '--forge-source', args.forge_source]),
        ('reconciliation', 'qualify_reconciliation.py', ['--binary', args.fault_bridge, '--cli', args.fault_cli]),
        ('durability', 'qualify_durability.py', ['--binary', args.fault_bridge]),
        ('cancellation', 'qualify_cancellation.py', ['--binary', bridge, '--cli', cli]),
        ('default-fences', 'qualify_default_fences.py', ['--binary', bridge, '--cli', cli, '--old-cli', args.old_cli]),
        ('native', 'qualify_native.py', ['--binary', bridge, '--cli', cli]),
        ('native-controls', 'qualify_native_controls.py', ['--binary', bridge, '--cli', cli]),
        ('credentials', 'qualify_credentials.py', ['--binary', bridge]),
        ('enrollment', 'qualify_enrollment.py', ['--binary', bridge]),
        ('recovery', 'qualify_recovery.py', ['--binary', bridge]),
        ('contract', 'qualify_contract.py', ['--binary', bridge]),
        ('sessions', 'qualify_sessions.py', ['--package', args.package]),
        ('driver-interrupt', 'qualify_live_driver.py', ['--binary', bridge, '--cli', cli]),
        ('rate-limit', 'qualify_rate_limit.py', ['--binary', bridge, '--cli', cli]),
        ('package-regression', 'qualify_packages.py', ['--package-a', args.canary_a, '--package-b', args.canary_b]),
        ('delivery', 'qualify_delivery.py', ['--package', args.package, '--old-package', args.previous_package,
            '--expected-storage-epoch', str(epoch), '--archive-name', f'factory-local-epoch{epoch}.tar.gz']),
    ]
    if epoch >= 6:
        tests.insert(0, ('lifecycle', 'qualify_lifecycle.py', ['--binary', bridge, '--cli', cli]))
        tests.insert(1, ('lifecycle-faults', 'qualify_lifecycle_faults.py', ['--binary', args.fault_bridge]))
    if epoch >= 7:
        tests[0:0] = [
            ('foreman', 'qualify_foreman.py', ['--binary', bridge, '--cli', cli,
                '--forge-binary', args.forge_binary, '--forge-source', args.forge_source]),
            ('managed-native', 'qualify_managed_native.py', ['--binary', bridge, '--cli', cli,
                '--forge-binary', args.forge_binary, '--forge-source', args.forge_source]),
            ('foreman-recovery', 'qualify_foreman_recovery.py', ['--binary', bridge,
                '--forge-binary', args.forge_binary, '--forge-source', args.forge_source]),
            ('canonical-authority', 'probe_foreman_authority.py', ['--binary', bridge,
                '--forge-binary', args.forge_binary, '--forge-source', args.forge_source]),
        ]
    if epoch >= 8:
        tests.insert(0, ('supervision', 'qualify_supervision.py', ['--binary', bridge,
            '--fault-binary', args.fault_bridge, '--cli', cli,
                '--forge-binary', args.forge_binary, '--forge-source', args.forge_source]))
    if epoch >= 9:
        assert args.legacy_route_binary, 'The route downgrade check needs a bridge from before route storage epoch 6.'
        tests[0:0] = [
            ('communication-routes', 'qualify_routes.py', ['--binary', bridge, '--cli', cli,
                '--old-binary', args.legacy_route_binary]),
            ('native-scale', 'qualify_scale.py', ['--binary', bridge, '--cli', cli,
                '--forge-binary', args.forge_binary, '--forge-source', args.forge_source]),
            ('pilot-admission', 'qualify_pilot_gateway.py', ['--binary', bridge, '--cli', cli]),
        ]
    if epoch >= 10:
        canonical_args = ['--binary', bridge, '--cli', cli,
            '--forge-binary', args.forge_binary, '--forge-source', args.forge_source]
        tests[0:0] = [
            ('manager', 'qualify_manager.py', canonical_args),
            ('manager-dispatch', 'qualify_manager_dispatch.py', canonical_args),
            *[(f'manager-stale-{stage}', 'qualify_manager_dispatch.py', [*canonical_args, '--stale-at', stage])
              for stage in ('issue', 'admission', 'result')],
            ('manager-responsiveness', 'qualify_manager_responsiveness.py', ['--binary', bridge,
                '--forge-binary', args.forge_binary, '--forge-source', args.forge_source]),
        ]
    if epoch >= 11:
        assert args.legacy_message_cli, 'Legacy message recovery requires the pre-prefix-fix native CLI.'
        tests[0:0] = [
            ('support-profiles', 'qualify_support.py', ['--binary', bridge, '--cli', cli,
                '--old-binary', args.previous_package / 'bin/codex-factory-bridge']),
            ('support-schedule', 'qualify_support_schedule.py', ['--binary', bridge, '--cli', cli]),
            ('support-faults', 'qualify_support_faults.py', ['--binary', args.fault_bridge]),
            ('legacy-message', 'qualify_legacy_message.py', ['--binary', bridge, '--cli', cli,
                '--old-cli', args.legacy_message_cli]),
        ]
    results, error = [], None
    try:
        verify(args.package, digest(args.package / 'factory-package.json'))
        for name, script, extra in tests:
            command = list(map(str, [sys.executable, ROOT / 'scripts/factory' / script,
                                    *extra, '--evidence', args.evidence / name]))
            record = {'name': name, 'command': command, 'status': 'running'}
            results.append(record)
            (args.evidence / 'progress.json').write_text(json.dumps(results, indent=2) + '\n')
            with (args.evidence / (name + '.log')).open('w') as log:
                run = subprocess.run(command, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT)
            record.update(exitCode=run.returncode, status='passed' if run.returncode == 0 else 'failed')
            (args.evidence / 'progress.json').write_text(json.dumps(results, indent=2) + '\n')
            print(name, run.returncode, flush=True)
            if run.returncode:
                raise RuntimeError('The product gate failed: ' + name)
        assert before == source_identity(), 'The source changed during qualification.'
    except Exception:
        error = traceback.format_exc()
    record = {'passed': error is None, 'exit_code': 0 if error is None else 1,
              'error': error, 'lanes': results, 'source': before,
              'sourceUnchanged': before == source_identity(), 'platform': platform.platform(),
              'command': sys.argv, 'binaries': {str(p): digest(p) for p in
                  [cli, bridge, args.fault_cli, args.fault_bridge, args.forge_binary]},
              'runnerSha256': digest(__file__), 'liveAccountModelCalls': 0, 'secondPhysicalHost': False, 'requiredLiveHostAcceptance': 'pending target selection'}
    (args.evidence / 'result.json').write_text(json.dumps(record, indent=2) + '\n')
    print(json.dumps({'passed': error is None, 'lanes': len(results), 'error': error}), flush=True)
    return record['exit_code']


if __name__ == '__main__':
    raise SystemExit(main())
