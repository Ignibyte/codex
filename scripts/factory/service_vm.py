#!/usr/bin/env python3
"""Supervise one private KVM guest until its explicit stop file appears."""
import argparse
import functools
import hashlib
import http.server
import json
import os
from pathlib import Path
import socket
import subprocess
import threading
import time
import uuid


def port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--image', type=Path, required=True)
    parser.add_argument('--evidence', type=Path, required=True)
    args = parser.parse_args()
    e = args.evidence.resolve()
    e.mkdir(mode=0o700, parents=True, exist_ok=False)
    image = args.image.resolve()
    expected = next(line.split()[0] for line in image.with_name('SHA256SUMS').read_text().splitlines()
                    if line.endswith(' *' + image.name))
    assert hashlib.sha256(image.read_bytes()).hexdigest() == expected
    seed = e / 'seed'
    seed.mkdir(mode=0o700)
    subprocess.run(['ssh-keygen', '-q', '-t', 'ed25519', '-N', '', '-f', str(e / 'key')], check=True)
    instance = 'cf016-' + uuid.uuid4().hex
    (seed / 'meta-data').write_text(json.dumps({'instance-id': instance, 'local-hostname': instance}))
    (seed / 'user-data').write_text('''#cloud-config
users:
  - name: cfoperator
    sudo: ALL=(ALL) NOPASSWD:ALL
    shell: /bin/bash
    lock_passwd: true
    ssh_authorized_keys:
      - ''' + (e / 'key.pub').read_text().strip() + '''
ssh_pwauth: false
disable_root: true
''')
    (seed / 'vendor-data').write_text('{}')
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(seed))
    server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    subprocess.run(['qemu-img', 'create', '-f', 'qcow2', '-F', 'qcow2', '-b', str(image),
                    str(e / 'disk.qcow2'), '12G'], check=True)
    ssh_port = port()
    command = ['qemu-system-x86_64', '-name', instance, '-machine', 'q35,accel=kvm',
               '-cpu', 'host', '-smp', '2', '-m', '2048', '-display', 'none', '-monitor', 'none',
               '-serial', 'file:' + str(e / 'serial.log'),
               '-qmp', 'unix:' + str(e / 'qmp.sock') + ',server=on,wait=off',
               '-drive', 'file=' + str(e / 'disk.qcow2') + ',if=virtio,format=qcow2',
               '-netdev', 'user,id=net0,hostfwd=tcp:127.0.0.1:' + str(ssh_port) + '-:22',
               '-device', 'virtio-net-pci,netdev=net0',
               '-smbios', 'type=1,serial=ds=nocloud;s=http://10.0.2.2:' + str(server.server_port) + '/']
    ssh = ['ssh', '-T', '-i', str(e / 'key'), '-p', str(ssh_port),
           '-o', 'BatchMode=yes', '-o', 'IdentitiesOnly=yes', '-o', 'IdentityAgent=none',
           '-o', 'StrictHostKeyChecking=accept-new',
           '-o', 'UserKnownHostsFile=' + str(e / 'known_hosts'), '-o', 'ConnectTimeout=3',
           'cfoperator@127.0.0.1']
    process = None
    try:
        with (e / 'qemu.log').open('x') as log:
            process = subprocess.Popen(command, stdout=log, stderr=log)
            owner = {'pid': process.pid,
                     'bootId': Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
                     'startTicks': Path(f'/proc/{process.pid}/stat').read_text().rsplit(')', 1)[1].split()[19],
                     'exe': os.readlink(f'/proc/{process.pid}/exe'), 'uid': os.getuid()}
            (e / 'owner.json').write_text(json.dumps(owner, indent=2))
            (e / 'vm.json').write_text(json.dumps({'instance': instance, 'imageSha256': expected,
                'command': command, 'ssh': ssh, 'source': 'https://cloud-images.ubuntu.com/noble/current/'}, indent=2))
            deadline = time.monotonic() + 240
            while time.monotonic() < deadline:
                assert process.poll() is None, 'guest_exited_before_ready'
                result = subprocess.run([*ssh, 'cat /proc/sys/kernel/random/boot_id; uname -a'],
                                        capture_output=True, text=True, timeout=8)
                if result.returncode == 0:
                    (e / 'ready.json').write_text(json.dumps({'ready': True, 'guest': result.stdout}, indent=2))
                    print(json.dumps({'ready': True, 'evidence': str(e)}), flush=True)
                    break
                time.sleep(2)
            else:
                raise RuntimeError('guest_boot_timeout')
            while not (e / 'stop').exists():
                assert process.poll() is None, 'guest_exited'
                time.sleep(0.5)
    finally:
        server.shutdown()
        if process is not None:
            # The unreaped child is this supervisor's exact process incarnation.
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
            (e / 'cleanup.json').write_text(json.dumps({'qemuExit': process.returncode, 'reaped': True}))
        (e / 'key').unlink(missing_ok=True)


if __name__ == '__main__':
    main()
