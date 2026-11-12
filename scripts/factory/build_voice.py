#!/usr/bin/env python3
"""Build and seal voice inputs with upstream's Linux release job."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

REPO = Path(__file__).resolve().parents[2]
VOICE = REPO / 'third_party/voice'
sys.path.insert(0, str(VOICE))
from release_runtime import seal, stage

BAZEL_VERSION = '9.2.0'
BAZEL_SHA256 = '7668a95db1250f12c40407251e4e203b4ec8bf39bc495d2f485b2d8c99048694'


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def run(command, **kwargs):
    subprocess.run(list(map(str, command)), check=True, **kwargs)


def build(work, source, target, bazel):
    if target != 'x86_64-unknown-linux-gnu':
        raise ValueError('Only the reviewed native GNU target is supported')
    source = subprocess.check_output(['git', 'rev-parse', source + '^{commit}'], cwd=REPO, text=True).strip()
    paths = ['codex-rs', 'third_party/voice', 'MODULE.bazel', 'MODULE.bazel.lock', 'defs.bzl',
             '.bazelrc', '.bazelversion', 'scripts/workspace-status.sh',
             ':(exclude)third_party/voice/assemble_package.py',
             ':(exclude)third_party/voice/test_assemble_package.py']
    run(['git', 'diff', '--exit-code', source, '--', *paths], cwd=REPO)
    if (REPO / '.bazelversion').read_text().strip() != BAZEL_VERSION:
        raise ValueError('Review the upstream Bazel version and its official asset digest')
    work.mkdir(mode=0o700)
    for name in ('tmp', 'sandbox'):
        (work / name).mkdir(mode=0o700)
    if bazel is None:
        bazel = work / 'bazel'
        run(['curl', '--fail', '--location', '--retry', '2', '--connect-timeout', '15',
             '--max-time', '120', '--silent', '--show-error', '--output', bazel,
             f'https://github.com/bazelbuild/bazel/releases/download/{BAZEL_VERSION}/bazel-{BAZEL_VERSION}-linux-x86_64'])
        bazel.chmod(0o755)
    if digest(bazel) != BAZEL_SHA256:
        raise ValueError('Official Bazel asset digest mismatch')
    env = dict(os.environ, TMPDIR=str(work / 'tmp'), STABLE_GIT_COMMIT=source,
               CODEX_BAZEL_BIN=str(bazel), BAZEL_OUTPUT_USER_ROOT=str(work / 'bazel-root'))
    command = [sys.executable, REPO / '.github/scripts/run_bazel_with_buildbuddy.py',
               '--batch', '--noexperimental_remote_repo_contents_cache',
               '--host_jvm_args=-Djava.io.tmpdir=' + str(work / 'tmp'), 'build',
               '--config=release', '--jobs=8', '--lockfile_mode=off',
               '--repository_cache=' + str(work / 'repo-cache'),
               '--repo_contents_cache=' + str(work / 'repo-contents'),
               '--disk_cache=' + str(work / 'disk-cache'),
               '--sandbox_base=' + str(work / 'sandbox'),
               '--symlink_prefix=' + str(work / 'bazel-'),
               '//codex-rs/voice-host:codex-voice-host', '//third_party/voice:native_runtime']
    run(command, cwd=REPO, env=env)
    native = work / 'bazel-bin/third_party/voice/native_runtime_linux_x86_64'
    stage(native, work / 'release-runtime', target)
    seal(work / 'release-runtime', target)
    shutil.copy2(work / 'bazel-bin/codex-rs/voice-host/codex-voice-host', work / 'codex-voice-host')
    runtime = json.loads((work / 'release-runtime/runtime.json').read_text())
    if runtime['sourceCommit'] != source:
        raise ValueError('Native runtime build identity mismatch')
    probe_env = dict(env, LD_LIBRARY_PATH=str(work / 'release-runtime/lib'))
    reported = subprocess.check_output([work / 'codex-voice-host', '--build-commit'], env=probe_env, text=True).strip()
    if reported != source:
        raise ValueError('Voice helper build identity mismatch')
    (work / 'build.json').write_text(json.dumps(dict(sourceCommit=source, target=target,
        recipe='.github/workflows/rust-release.yml: Build Linux voice runtime; upstream Bazel targets then release_runtime.py stage/seal',
        command=list(map(str, command)), bazelVersion=BAZEL_VERSION, bazelSha256=BAZEL_SHA256,
        sourceManifestSha256=digest(VOICE / 'sources.json'),
        runtimeSha256=digest(work / 'release-runtime/runtime.json'),
        helperSha256=digest(work / 'codex-voice-host')), indent=2) + '\n')


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--work-dir', required=True, type=Path)
    p.add_argument('--source-commit', required=True)
    p.add_argument('--target', default='x86_64-unknown-linux-gnu')
    p.add_argument('--bazel-bin', type=Path, help='Existing official Bazel binary, verified against the pinned digest')
    a = p.parse_args()
    build(a.work_dir.resolve(), a.source_commit, a.target, a.bazel_bin.resolve() if a.bazel_bin else None)
