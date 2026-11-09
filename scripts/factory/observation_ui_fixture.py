#!/usr/bin/env python3
"""Own a temporary Marley profile, display and synthetic native observation fixture.

The foreground owner stops on a release file or timeout. Screenshots and UI
actions use only the private display named in ready.json. No user setting is read.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import tempfile
import time
import traceback
import uuid

from fleet_fixture import Fleet, SERVICE, GROUP
from native_fixture import Native
from session_fixture import Model


def identity(process):
    return {'pid': process.pid, 'startTicks': Path(f'/proc/{process.pid}/stat').read_text().rsplit(')', 1)[1].split()[19],
            'bootId': Path('/proc/sys/kernel/random/boot_id').read_text().strip(), 'uid': os.getuid()}


def stop(process, lease):
    if process is None or process.poll() is not None:
        return
    fd = os.pidfd_open(lease['pid'])
    try:
        assert identity(process) == lease
        signal.pidfd_send_signal(fd, signal.SIGTERM)
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            signal.pidfd_send_signal(fd, signal.SIGKILL)
            process.wait(timeout=10)
    finally:
        os.close(fd)


def main():
    parser = argparse.ArgumentParser()
    for name in ['binary', 'cli', 'evidence']:
        parser.add_argument('--' + name, required=True)
    parser.add_argument('--auto', action='store_true')
    parser.add_argument('--seconds', type=int, default=600)
    args = parser.parse_args()
    fleet, model = Fleet(args.binary, args.evidence), Model()
    native, sway, app, sway_lease, app_lease = None, None, None, None, None
    root = Path(tempfile.mkdtemp(prefix='cf014-ui-'))
    error = None
    try:
        for directory in ['runtime', 'home', 'profile/config', 'xdg-data', 'xdg-cache', 'xdg-config', 'project']:
            (root / directory).mkdir(mode=0o700, parents=True, exist_ok=True)
        fleet.subjects['observer'] = {'uid': 65531, 'seat': str(uuid.uuid4()), 'sequence': 0}
        fleet.sudo('install', '-d', '-o', 65531, '-g', GROUP, '-m', '0700', fleet.base / 'observer')
        fleet.enroll('sender')
        fleet.enroll('receiver', ['observe', 'queue', 'acknowledge'])
        fleet.enroll('observer', ['observe'], role='observer',
            recipients=[fleet.subjects['observer']['seat'], fleet.subjects['receiver']['seat']])
        native = Native(fleet, args.cli, model)
        receiver = fleet.subjects['receiver']
        pulse = {'process': {key: native.identity[key] for key in ['pid', 'bootId', 'startTicks']},
                 'modelActivity': 'idle', 'taskProgress': 'blocked'}

        def report():
            receiver['sequence'] = next(s['lastSequence'] for s in fleet.inspect()['seats'] if s['seat'] == receiver['seat'])
            fleet.wire('receiver', {'method': 'report_presence', 'protocol': 1,
                'request': fleet.request('receiver', 'acknowledge'), 'report': pulse})

        report()
        actor = fleet.subjects['observer']
        provider = [*fleet.prefix(actor['uid']), fleet.binary, 'observe-work', '--grant', fleet.publish(actor['grant']),
            '--socket', fleet.socket, '--service-uid', SERVICE, '--credential', actor['credential'], '--project', 'fixture-project']
        settings = {'auto_update': False, 'telemetry': {'diagnostics': False, 'metrics': False},
            'base_keymap': 'VSCode', 'theme': 'One Dark', 'ui_font_size': 16,
            'marley': {'fleet': {'providers': [{'kind': 'mcp', 'name': 'CF-014 observer',
                'command': str(provider[0]), 'args': list(map(str, provider[1:]))}]}}}
        (root / 'profile/config/settings.json').write_text(json.dumps(settings, indent=2))
        config = root / 'sway.conf'
        config.write_text('output HEADLESS-1 mode 1600x1000\nseat seat0 fallback true\n'
                          'font monospace 12\ndefault_border none\nfocus_follows_mouse no\n')
        environment = {**os.environ, 'HOME': str(root / 'home'), 'XDG_RUNTIME_DIR': str(root / 'runtime'),
            'XDG_DATA_HOME': str(root / 'xdg-data'), 'XDG_CACHE_HOME': str(root / 'xdg-cache'),
            'XDG_CONFIG_HOME': str(root / 'xdg-config'), 'WLR_BACKENDS': 'headless', 'WLR_RENDERER': 'pixman',
            'WLR_LIBINPUT_NO_DEVICES': '1', 'ZED_ALLOW_EMULATED_GPU': '1', 'DISPLAY': '', 'WAYLAND_DISPLAY': ''}
        with (fleet.e / 'sway.log').open('w') as log:
            sway = subprocess.Popen(['sway', '--config', str(config)], env=environment, stdout=log, stderr=log)
        sway_lease = identity(sway)
        deadline = time.monotonic() + 15
        while not list((root / 'runtime').glob('sway-ipc.*.sock')):
            assert sway.poll() is None, (fleet.e / 'sway.log').read_text()
            assert time.monotonic() < deadline
            time.sleep(.1)
        environment['SWAYSOCK'] = str(next((root / 'runtime').glob('sway-ipc.*.sock')))
        environment['WAYLAND_DISPLAY'] = next(p.name for p in (root / 'runtime').glob('wayland-*') if not p.name.endswith('.lock'))
        executable = Path('/home/cpeppers/.local/lib/marley/marley')
        with (fleet.e / 'marley.log').open('w') as log:
            app = subprocess.Popen([str(executable), '--user-data-dir', str(root / 'profile'), str(root / 'project')],
                env=environment, stdout=log, stderr=log)
        app_lease = identity(app)
        ready = {'root': str(root), 'environment': {key: environment[key] for key in
            ['HOME', 'XDG_RUNTIME_DIR', 'SWAYSOCK', 'WAYLAND_DISPLAY']}, 'sway': sway_lease, 'app': app_lease,
            'seat': receiver['seat'], 'release': str(fleet.e / 'release'), 'stale': str(fleet.e / 'stale'),
            'appSha256': hashlib.sha256(executable.read_bytes()).hexdigest(),
            'settings': settings, 'liveAccountModelCalls': 0}
        (fleet.e / 'ready.json').write_text(json.dumps(ready, indent=2))
        print(json.dumps({'ready': str(fleet.e / 'ready.json')}), flush=True)
        if args.auto:
            actions = []
            def ui(command):
                run = subprocess.run(command, env=environment, capture_output=True, text=True, timeout=30)
                actions.append({'command': command, 'exit_code': run.returncode, 'stdout': run.stdout, 'stderr': run.stderr})
                assert run.returncode == 0, actions[-1]
            def capture(name):
                ui(['grim', str((fleet.e / name).resolve())])
            # Wait for the owned app's window; input starts after its first frame.
            until = time.monotonic() + 20
            while True:
                tree = subprocess.run(['swaymsg', '-t', 'get_tree'], env=environment,
                    capture_output=True, text=True, check=True).stdout
                if 'com.ignibyte.Marley' in tree:
                    break
                assert time.monotonic() < until, 'The owned Marley window did not appear.'
                time.sleep(.1)
            report()
            ui(['wtype', '-s', '1000', '-k', 'Tab', '-s', '300', '-k', 'Return', '-s', '1000',
                '-M', 'ctrl', '-M', 'shift', '-k', 'p', '-m', 'shift', '-m', 'ctrl', '-s', '500',
                'marley toggle fleet', '-s', '500', '-k', 'Return', '-s', '3000', '-k', 'Down',
                '-s', '300', '-k', 'Return', '-s', '3000'])
            capture('fresh.png')
            time.sleep(18)
            capture('stale.png')
            fleet.admin('prune-observation', '--state', fleet.state / 'observation', '--keep', 0)
            time.sleep(6)
            capture('stale-gap.png')
            (fleet.e / 'ui-actions.json').write_text(json.dumps(actions, indent=2))
            (fleet.e / 'release').touch()
        deadline = time.monotonic() + args.seconds
        next_report = time.monotonic() + 5
        while time.monotonic() < deadline and not (fleet.e / 'release').exists():
            assert sway.poll() is None, 'The private display stopped.'
            assert app.poll() is None, 'The temporary Marley process stopped.'
            if time.monotonic() >= next_report and not (fleet.e / 'stale').exists():
                report()
                next_report = time.monotonic() + 5
            time.sleep(.2)
    except Exception:
        error = traceback.format_exc()
    finally:
        for process, lease in [(app, app_lease), (sway, sway_lease)]:
            try:
                stop(process, lease)
            except Exception:
                error = (error or '') + traceback.format_exc()
        for owned in [native, model, fleet]:
            if owned is not None:
                try:
                    owned.close()
                except Exception:
                    error = (error or '') + traceback.format_exc()
        shutil.rmtree(root)
        record = {'error': error, 'exit_code': 0 if error is None else 1, 'scratchRemoved': not root.exists()}
        (Path(args.evidence) / 'owner-result.json').write_text(json.dumps(record, indent=2))
        (Path(args.evidence) / 'result.json').write_text(json.dumps({**record, 'passed': error is None, 'visualReviewRequired': True}, indent=2))
        print(json.dumps(record), flush=True)
    return 0 if error is None else 1


if __name__ == '__main__':
    raise SystemExit(main())
