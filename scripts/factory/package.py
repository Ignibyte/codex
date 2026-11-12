#!/usr/bin/env python3
"""The only fork dist creator: build upstream's complete Linux package plus the bridge."""
import argparse
import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

from check_package import REPO, SUPPORTED_TARGETS, check, upstream_contract
from codex_package.targets import TARGET_SPECS
from codex_package.v8 import resolve_codex_v8_cargo_env
from codex_package.version import read_workspace_version


def run(args, *, cwd=REPO, env=None):
    subprocess.run(list(map(str, args)), cwd=cwd, env=env, check=True)


def output(args, *, cwd=REPO, env=None):
    return subprocess.check_output(list(map(str, args)), cwd=cwd, env=env, text=True).strip()


def digest(path):
    with path.open('rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()


def inventory(directory):
    result = {}
    for path in sorted(directory.rglob('*')):
        if not path.is_file() or path.name in ('MANIFEST.json', 'SHA256SUMS'):
            continue
        item = dict(sha256=digest(path), bytes=path.stat().st_size)
        with path.open('rb') as f:
            is_elf = f.read(4) == b'\x7fELF'
        if is_elf:
            raw = output(['readelf', '--version-info', path])
            versions = sorted(set(re.findall(r'Name: ((?:GLIBC_|GCC_|OPENSSL_)[A-Za-z0-9_.]+)', raw)))
            glibc = sorted((x for x in versions if x.startswith('GLIBC_')),
                           key=lambda x: tuple(map(int, x[6:].split('.'))))
            item.update(file=output(['file', '-b', path]), requiredSymbolVersions=versions,
                        glibcMinimum=glibc[-1] if glibc else None,
                        neededLibraries=re.findall(r'\(NEEDED\).*?\[(.*?)\]', output(['readelf', '--dynamic', path])))
        result[str(path.relative_to(directory))] = item
    return result


def seal(directory, manifest):
    manifest['files'] = inventory(directory)
    (directory / 'MANIFEST.json').write_text(json.dumps(manifest, indent=2) + '\n')
    paths = sorted(p for p in directory.rglob('*') if p.is_file() and p.name != 'SHA256SUMS')
    (directory / 'SHA256SUMS').write_text(''.join(digest(p) + '  ' + str(p.relative_to(directory)) + '\n' for p in paths))


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output', required=True, type=Path)
    p.add_argument('--source-commit', default='HEAD')
    p.add_argument('--target', default='x86_64-unknown-linux-gnu', choices=SUPPORTED_TARGETS)
    p.add_argument('--target-dir', type=Path, default=Path(os.environ.get('CARGO_TARGET_DIR', REPO / 'codex-rs/target')))
    p.add_argument('--work-dir', type=Path)
    p.add_argument('--record-qualification', type=Path,
                   help='Seal qualification results into an existing package; never rebuild or replace it')
    a = p.parse_args(); dest = a.output.resolve()
    if dest.parent != REPO / 'dist':
        raise RuntimeError('Output must be a direct child of the fork dist directory')
    if a.record_qualification:
        manifest = json.loads((dest / 'MANIFEST.json').read_text())
        run(['sha256sum', '-c', 'SHA256SUMS'], cwd=dest)
        result = check(dest, manifest['target'])
        if not result['passed']:
            raise RuntimeError('Package contract failed before qualification seal')
        manifest['qualification'] = json.loads(a.record_qualification.read_text())
        manifest['completeness'] = result
        manifest['qualificationCommit'] = output(['git', 'rev-parse', 'HEAD'])
        manifest['packagingScriptsSha256'] = {str(path.relative_to(REPO)): digest(path)
                                            for path in (Path(__file__).resolve(), REPO / 'scripts/factory/check_package.py')}
        shutil.copy2(REPO / 'docs/fork-mailbox.md', dest / 'INTERFACE.md')
        seal(dest, manifest)
        return
    if dest.exists():
        raise RuntimeError('Refusing to replace any existing dist folder')
    if not a.work_dir or a.work_dir.exists():
        raise RuntimeError('--work-dir must name a fresh isolated build/staging directory')
    work = a.work_dir.resolve(); work.mkdir(mode=0o700)
    contract = upstream_contract(a.target)
    source = output(['git', 'rev-parse', a.source_commit + '^{commit}'])
    tree = output(['git', 'rev-parse', source + ':codex-rs'])
    if tree != output(['git', 'rev-parse', 'HEAD:codex-rs']):
        raise RuntimeError('Selected runtime source differs from the current Rust tree')
    run(['git', 'diff', '--exit-code', 'HEAD', '--', 'codex-rs'])
    env = os.environ.copy()
    env.update(CODEX_FACTORY_REVISION=source, STABLE_GIT_COMMIT=source, CARGO_BUILD_JOBS='8')
    env.update(resolve_codex_v8_cargo_env(TARGET_SPECS[a.target]))
    target_dir = a.target_dir.resolve(); native = target_dir / a.target / 'release'
    cargo_base = ['cargo', 'build', '--locked', '--release', '--target', a.target, '--target-dir', target_dir]
    # This order follows upstream's Linux Cargo release action. Never strip bwrap after hashing.
    run([*cargo_base, '-p', 'codex-bwrap', '--bin', 'bwrap'], cwd=REPO / 'codex-rs', env=env)
    bwrap = work / 'bwrap'; shutil.copy2(native / 'bwrap', bwrap)
    run(['strip', '--strip-debug', '--strip-unneeded', bwrap])
    env['CODEX_BWRAP_SHA256'] = digest(bwrap)
    metadata = json.loads(output(['cargo', 'metadata', '--locked', '--no-deps', '--format-version', '1'], cwd=REPO / 'codex-rs', env=env))
    owners = {t['name']: pkg['name'] for pkg in metadata['packages'] for t in pkg['targets'] if 'bin' in t['kind']}
    binaries = [*contract['binaries'], 'codex-factory-bridge']
    args = [*cargo_base, '--features', 'codex-install-context/factory']
    for package in sorted({owners[b] for b in binaries}):
        args.extend(['-p', package])
    for binary in binaries:
        args.extend(['--bin', binary])
    run(args, cwd=REPO / 'codex-rs', env=env)
    stage = work / 'package'
    # Same canonical builder called by build-codex-package-archive.sh, preserving prebuilt bytes.
    run([sys.executable, REPO / 'scripts/build_codex_package.py', '--target', a.target, '--variant', 'codex',
         '--package-dir', stage, '--package-version', read_workspace_version(), '--cargo-profile', 'release',
         '--entrypoint-bin', native / 'codex', '--code-mode-host-bin', native / 'codex-code-mode-host',
         '--exec-server-bin', native / 'codex-exec-server', '--bwrap-bin', bwrap, '--strip', 'none'], env=env)
    for binary in binaries:
        if not (stage / 'bin' / binary).exists():
            shutil.copy2(native / binary, stage / 'bin' / binary)
    shutil.copy2(REPO / 'docs/fork-mailbox.md', stage / 'INTERFACE.md')
    result = check(stage, a.target)
    print(json.dumps(result, indent=2), flush=True)
    if not result['passed']:
        raise RuntimeError('Complete-package check failed; nothing published')
    home = work / 'metadata-home'; home.mkdir(mode=0o700)
    metaenv = dict(env, CODEX_HOME=str(home))
    factory = json.loads(output([stage / 'bin/codex', 'factory-info'], env=metaenv))
    if factory['revision'] != source or factory['publicUpdatesAllowed']:
        raise RuntimeError('Factory identity mismatch')
    manifest = dict(schema=2, builtAtUtc=datetime.datetime.now(datetime.timezone.utc).isoformat(),
                    runtimeSourceCommit=source, sourceCommit=source, rustSourceTree=tree,
                    packagingCommit=output(['git', 'rev-parse', 'HEAD']), target=a.target, profile='release',
                    workspaceVersion=read_workspace_version(), factoryIdentity=factory,
                    codexVersion=output([stage / 'bin/codex', '--version'], env=metaenv),
                    features=['codex-install-context/factory'], fixtureFaults=False,
                    bwrapEmbeddedSha256=env['CODEX_BWRAP_SHA256'], completeness=result,
                    rustc=output(['rustc', '--version'], cwd=REPO / 'codex-rs'),
                    cargo=output(['cargo', '--version'], cwd=REPO / 'codex-rs'),
                    qualification={'status': 'pending packaged tool turns and all seven mailbox qualifiers'},
                    priorWorkspaceFailures={'count': 14, 'status': 'failures, not waived; see plan 002'})
    old_cli = REPO / 'dist/1112b/codex'
    manifest['previousCliSha256'] = digest(old_cli) if old_cli.is_file() else None
    manifest['cliByteIdenticalTo1112b'] = (digest(stage / 'bin/codex') == manifest['previousCliSha256']
                                         if old_cli.is_file() else None)
    result['directory'] = str(dest)
    seal(stage, manifest)
    dest.parent.mkdir(exist_ok=True)
    os.rename(stage, dest)
    print('Complete package created:', dest, flush=True)


if __name__ == '__main__':
    main()
