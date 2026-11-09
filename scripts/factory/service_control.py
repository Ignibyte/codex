#!/usr/bin/env python3
"""Render bounded systemd ownership for an explicitly pinned Linux package."""
import argparse
import json
from pathlib import Path
import re

from package_control import verify


def safe_path(path):
    value = str(Path(path).absolute())
    if not re.fullmatch(r'/[A-Za-z0-9_./-]+', value) or '..' in Path(value).parts:
        raise ValueError('service_path_invalid')
    return value


def render(package, pin, name, bridge_uid, agent_uid, state, profile, output):
    if not re.fullmatch(r'cf-[a-z0-9-]{1,40}', name):
        raise ValueError('service_name_invalid')
    if not (1 <= bridge_uid < 2**32-1 and 1 <= agent_uid < 2**32-1 and bridge_uid != agent_uid):
        raise ValueError('service_uid_boundary')
    package, state, profile = map(safe_path, (package, state, profile))
    manifest = verify(Path(package), pin)
    if manifest['storageEpoch'] not in (6, 7, 8, 9, 10, 11):
        raise ValueError('service_recovery_epoch_required')
    output = Path(output)
    output.mkdir(mode=0o700, parents=True, exist_ok=False)
    common = '''[Unit]
Description=Bounded Codex factory service
After=local-fs.target
StartLimitIntervalSec=60
StartLimitBurst=3

[Service]
Type=simple
Restart=on-failure
RestartSec=2
KillMode=control-group
TimeoutStopSec=15
SendSIGKILL=yes
UMask=0077
NoNewPrivileges=yes
ProtectSystem=strict
ProtectHome=yes
RestrictSUIDSGID=yes
CPUQuota=100%
MemoryMax=768M
TasksMax=128
LimitNOFILE=4096
RuntimeDirectoryPreserve=no
'''
    for role, uid, directory, argv in (
        ('bridge', bridge_uid, state,
         f'{package}/bin/codex-factory-bridge serve --state {state} --socket /run/{name}-bridge/bridge.sock'),
        ('native', agent_uid, profile,
         f'{package}/bin/codex app-server --listen unix:///run/{name}-native/native.sock --managed-daemon'),
    ):
        unit = common + f'''User={uid}
Group={bridge_uid}
RuntimeDirectory={name}-{role}
RuntimeDirectoryMode={'0750' if role == 'bridge' else '0700'}
PrivateTmp={'yes' if role == 'bridge' else 'no'}
ReadWritePaths={directory}{' /tmp' if role == 'native' else ''}
WorkingDirectory={directory}
Environment=HOME={profile}
Environment=CODEX_HOME={profile}
Environment=NO_PROXY=127.0.0.1,localhost
ExecStart={argv}

[Install]
WantedBy=multi-user.target
'''
        (output / f'{name}-{role}.service').write_text(unit)
    record = {'schema': 1, 'name': name, 'package': package, 'manifestSha256': pin,
              'storageEpoch': manifest['storageEpoch'], 'bridgeUid': bridge_uid, 'agentUid': agent_uid,
              'state': state, 'profile': profile, 'installation': 'explicit_operator_only',
              'rebootDisposition': 'fence_old_authority_quarantine_pending_reconcile_active',
              'nativeDisposition': 'reload_native_history_without_resubmitting_turns',
              'units': [name + '-bridge.service', name + '-native.service']}
    (output / 'units.json').write_text(json.dumps(record, indent=2) + '\n')
    return record


def main():
    p = argparse.ArgumentParser()
    for name in ('package', 'state', 'profile', 'output'):
        p.add_argument('--' + name, type=Path, required=True)
    p.add_argument('--manifest-sha256', required=True)
    p.add_argument('--name', required=True)
    p.add_argument('--bridge-uid', required=True, type=int)
    p.add_argument('--agent-uid', required=True, type=int)
    a = p.parse_args()
    print(json.dumps(render(a.package, a.manifest_sha256, a.name, a.bridge_uid,
                            a.agent_uid, a.state, a.profile, a.output), indent=2))


if __name__ == '__main__':
    main()
