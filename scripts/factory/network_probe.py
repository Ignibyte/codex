#!/usr/bin/env python3
"""Private synthetic-key SSH protocol probe. Never print credential values."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import time


def redact(value):
    if isinstance(value, dict):
        return {key: '<redacted>' if key in ['credential', 'bootstrap', 'nonce', 'privateKey'] else redact(child)
                for key, child in value.items()}
    if isinstance(value, list):
        return [redact(child) for child in value]
    return value


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--spec', type=Path, required=True)
    args = parser.parse_args()
    spec = json.loads(args.spec.read_text())
    config = spec['config']
    command = ['/usr/bin/ssh', '-F', '/dev/null', '-T', '-oBatchMode=yes', '-oIdentitiesOnly=yes',
        '-oIdentityAgent=none', '-oPasswordAuthentication=no', '-oKbdInteractiveAuthentication=no',
        '-oStrictHostKeyChecking=yes', '-oUpdateHostKeys=no', '-oGlobalKnownHostsFile=/dev/null',
        '-oUserKnownHostsFile=' + config['knownHosts'], '-oHostKeyAlias=factory-hub-' + config['hub'],
        '-oConnectTimeout=1', '-oConnectionAttempts=1', '-oLogLevel=ERROR',
        '-i', config['identityFile'], '-l', config['user'], '-p', str(config['port'])]
    if spec.get('mode') == 'forward':
        command += ['-W', '127.0.0.1:' + str(config['port']), '--', config['address']]
    else:
        command += ['--', config['address'], 'id' if spec.get('mode') == 'command' else 'codex-factory-v1']
    wire = spec.get('wire', {})
    if spec.get('credential'):
        credential = json.loads(Path(spec['credential']).read_text())
        request = wire.pop('request')
        wire['auth'] = {'seat': credential['seat'], 'credential': credential['credential'], 'request': request}
    packet = {'protocol': 1, 'sourceHost': config['sourceHost'], 'keyId': config['transportKey'],
              'peerUid': spec['peerUid'], 'wire': wire}
    started = time.monotonic()
    child = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    record = spec.get('record')
    if record:
        fields = Path(f'/proc/{child.pid}/stat').read_text().rsplit(')', 1)[1].split()
        Path(record).write_text(json.dumps({'pid': child.pid, 'startTicks': fields[19],
            'bootId': Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
            'uid': os.getuid(), 'exe': '/usr/bin/ssh'}))
    try:
        if spec.get('holdReady'):
            Path(spec['holdReady']).touch(exist_ok=False)
            deadline = time.monotonic() + 7
            while not Path(spec['holdRelease']).exists() and child.poll() is None:
                assert time.monotonic() < deadline, 'The owned probe release did not arrive.'
                time.sleep(.01)
        payload = None if spec.get('mode') in ['command', 'forward'] else json.dumps(packet).encode() + b'\n'
        try:
            stdout, stderr = child.communicate(payload, timeout=8)
        except BrokenPipeError:
            stdout, stderr = child.communicate(timeout=8)
        try:
            response = json.loads(stdout)
        except (ValueError, UnicodeError):
            response = None
        print(json.dumps({'sshExit': child.returncode, 'response': redact(response),
            'stderr': stderr.decode(errors='replace')[:2048], 'elapsedSeconds': time.monotonic() - started}))
    finally:
        if child.poll() is None:
            child.kill()
        child.wait(timeout=5)


if __name__ == '__main__':
    main()
