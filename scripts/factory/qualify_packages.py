#!/usr/bin/env python3
"""Real-package update refusals, pin validation, and shared-history canary rollback."""
import argparse
import json
import os
from pathlib import Path
import shutil
import signal
import socket
import subprocess
import sys
import time
import traceback

from package_control import digest, select_package, selected, verify
from session_fixture import Daemon, Model, Rpc, incarnation
from qualify_sessions import wait_turn


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--package-a', required=True, type=Path)
    parser.add_argument('--package-b', required=True, type=Path)
    parser.add_argument('--evidence', required=True, type=Path)
    args = parser.parse_args()
    args.package_a = args.package_a.resolve()
    args.package_b = args.package_b.resolve()
    args.evidence.mkdir(mode=0o700, parents=True, exist_ok=False)
    e = args.evidence
    checks = []
    commands = []
    model = Model()
    fixture = Daemon(args.package_a, e, model)
    server = None
    pid_identity = None
    connections = []
    try:
        pins = {p: digest(p/'factory-package.json') for p in [args.package_a, args.package_b]}
        for p, pin in pins.items():
            manifest = verify(p, pin)
            checks.append({'check':'complete_package', 'revision':manifest['revision'], 'manifestSha256':pin})
        assert digest(args.package_a/'bin/codex') != digest(args.package_b/'bin/codex')
        for argv in [['update'], ['app-server','daemon','update'],
                     ['app-server','daemon','update','--from-cli','--yes'],
                     ['app-server','daemon','pid-update-loop']]:
            cmd = [str(args.package_a/'bin/codex'), *argv]
            r = subprocess.run(cmd, env=fixture.env, cwd=fixture.home, capture_output=True, text=True, timeout=15)
            commands.append({'command':cmd,'exit_code':r.returncode,'stdout':r.stdout,'stderr':r.stderr})
            assert r.returncode != 0 and 'factory_update_pinned' in r.stderr, commands[-1]
            checks.append({'check':'native_update_refused','args':argv,'exit_code':r.returncode})
        # Make the native daemon eligible for public latest-channel updating, with
        # the setting explicitly enabled. The compiled policy must still win.
        managed = fixture.home/'packages/app-server-daemon'
        release_name = '0.0.0-x86_64-unknown-linux-gnu'
        release = managed/'releases'/release_name
        release.parent.mkdir(parents=True)
        shutil.copytree(args.package_a,release)
        (managed/'current').symlink_to(Path('releases')/release_name)
        (managed/'auto-update-version').write_text(release_name)
        settings = fixture.home/'app-server-daemon/settings.json'
        settings.write_text(json.dumps({'shutdownGraceSeconds':1,'updater':{'autoUpdateEnabled':True}}))
        fixture.lifecycle('start')
        assert not (fixture.home/'app-server-daemon/daemon-updater.pid').exists()
        fixture.lifecycle('stop')
        checks.append({'check':'automatic_updater_not_launched_even_when_enabled_and_latest'})
        shutil.rmtree(fixture.home/'packages')
        # Failed selections preserve the accepted pin, including a wrong helper.
        selector = e/'selection'
        select_package(selector,args.package_a,pins[args.package_a])
        before = (selector/'selection.json').read_bytes()
        for label, package, pin in [
            ('wrong_manifest',args.package_b,'0'*64),
            ('upstream_without_factory_identity',Path('.artifacts/factory/cf-001/package').resolve(),'0'*64),
        ]:
            try:
                select_package(selector,package,pin)
                raise AssertionError('bad selection accepted')
            except (AssertionError,ValueError,FileNotFoundError) as exc:
                assert str(exc) != 'bad selection accepted'
                assert (selector/'selection.json').read_bytes() == before
                checks.append({'check':label+'_refused','error':str(exc)})
        broken = e/'wrong-helper'
        shutil.copytree(args.package_b,broken)
        with (broken/'bin/codex-code-mode-host').open('ab') as out:out.write(b'wrong-version')
        try:
            select_package(selector,broken,pins[args.package_b])
            raise AssertionError('wrong helper accepted')
        except ValueError as exc:
            assert 'factory_helper_mismatch' in str(exc), str(exc)
            assert (selector/'selection.json').read_bytes() == before
            checks.append({'check':'wrong_helper_refused','error':str(exc)})
        cmd = [sys.executable, '-O', str(Path(__file__).with_name('package_control.py')),
               'select', '--state', str(selector), '--package', str(broken),
               '--manifest-sha256', pins[args.package_b]]
        optimized = subprocess.run(cmd, env=fixture.env, capture_output=True, text=True, timeout=20)
        commands.append({'command':cmd,'exit_code':optimized.returncode,'stderr':optimized.stderr})
        assert optimized.returncode != 0 and 'factory_helper_mismatch' in optimized.stderr
        assert (selector/'selection.json').read_bytes() == before
        checks.append({'check':'optimized_python_keeps_pin_checks'})
        manifest_path = broken/'factory-package.json'
        manifest = json.loads(manifest_path.read_text())
        manifest['storageEpoch'] = 99
        manifest_path.write_text(json.dumps(manifest))
        try:
            select_package(selector,broken,digest(manifest_path))
            raise AssertionError('incompatible storage accepted')
        except ValueError as exc:
            assert 'factory_storage_epoch_unsupported' in str(exc)
            assert (selector/'selection.json').read_bytes() == before
            checks.append({'check':'incompatible_storage_refused'})
        shutil.rmtree(broken)
        cmd = [sys.executable, str(Path(__file__).with_name('package_control.py')),
               'run', '--state', str(selector), '--', 'factory-info']
        launched = subprocess.run(cmd, env=fixture.env, capture_output=True, text=True, timeout=20)
        commands.append({'command':cmd,'exit_code':launched.returncode,'stdout':launched.stdout,'stderr':launched.stderr})
        assert launched.returncode == 0 and json.loads(launched.stdout)['revision'] == verify(args.package_a,pins[args.package_a])['revision']
        checks.append({'check':'pinned_cli_launcher'})
        # Use the same persisted state through A -> B -> A; every selected binary
        # is started directly and supervised, without native daemon package reuse.
        thread = None
        for phase, package in [('a',args.package_a),('b',args.package_b),('rollback-a',args.package_a)]:
            record = select_package(selector,package,pins[package])
            assert selected(selector) == record
            socket_path = fixture.home/'app-server-control/app-server-control.sock'
            with (e/(phase+'-server.log')).open('wb') as output:
                command = [str(package/'bin/codex'),'app-server','--listen','unix://']
                server = subprocess.Popen(command,env=fixture.env,cwd=fixture.home/'project',stdin=subprocess.DEVNULL,stdout=output,stderr=output)
                pid_identity = incarnation(server.pid)
                deadline = time.monotonic()+20
                while True:
                    assert server.poll() is None, (e/(phase+'-server.log')).read_text()[-3000:]
                    try:
                        rpc = Rpc(socket_path,fixture.transcript)
                        connections.append(rpc)
                        break
                    except (FileNotFoundError,ConnectionRefusedError):
                        assert time.monotonic()<deadline
                        time.sleep(.05)
                if thread is None:
                    thread = rpc.call('thread/start',{'cwd':str(fixture.home/'project'),'historyMode':'legacy'})['thread']['id']
                else:
                    state = rpc.call('thread/resume',{'threadId':thread})['thread']
                    assert state['id'] == thread
                    for index in range(len(model.requests)):
                        assert f'fixture-result-{index}' in json.dumps(state)
                index = len(model.requests)
                turn = rpc.call('turn/start',{'threadId':thread,'input':[{'type':'text','text':'Synthetic package canary; no tools.'}]})['turn']['id']
                model.wait_request(index)
                model.release(index)
                wait_turn(rpc,thread,turn,{'completed'})
                rpc.close()
                assert incarnation(server.pid) == pid_identity
                server.send_signal(signal.SIGTERM)
                code = server.wait(timeout=15)
                assert code == 0, code
                commands.append({'command':command,'exit_code':code,'phase':phase})
                server = None
                checks.append({'check':'shared_history_'+phase,'revision':record['revision'],'thread':thread,'turn':turn,'status':'completed'})
        result = {'passed':True,'checks':checks,'command':[sys.executable,*sys.argv]}
    except Exception:
        result = {'passed':False,'checks':checks,'error':traceback.format_exc()}
    finally:
        for rpc in connections:rpc.close()
        if server is not None and server.poll() is None:
            assert incarnation(server.pid) == pid_identity
            server.kill();server.wait()
        fixture.close()
        model.close()
        (e/'package-commands.json').write_text(json.dumps(commands,indent=2)+'\n')
        (e/'model-requests.json').write_text(json.dumps(model.requests,indent=2)+'\n')
    result['exit_code'] = 0 if result['passed'] else 1
    result['test_source'] = {p.name:digest(p) for p in Path(__file__).parent.glob('*.py')}
    (e/'result.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result,indent=2))
    return result['exit_code']

if __name__ == '__main__':
    raise SystemExit(main())
