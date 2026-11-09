#!/usr/bin/env python3
"""Apply the operator test environment after sudo/setpriv, without login material."""
import argparse
import json
import os
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument('--spec', type=Path, required=True)
args = parser.parse_args()
spec = json.loads(args.spec.read_text())
if spec.get('record'):
    fields = Path('/proc/self/stat').read_text().rsplit(')', 1)[1].split()
    record = {'pid': os.getpid(), 'startTicks': fields[19],
              'bootId': Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
              'uid': os.geteuid(), 'exe': spec['argv'][0]}
    fd = os.open(spec['record'], os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w') as stream:
        json.dump(record, stream)
os.chdir(spec['cwd'])
os.execve(spec['argv'][0], spec['argv'], spec['env'])
