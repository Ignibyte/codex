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
sys.path.insert(0, str(REPO / 'third_party/voice'))
from assemble_package import assemble
from package_runtime import runtime_files

REQUIRED_BWRAP = 'bd2ed101e19111f5c06ea4bb16ace19ca10693a56b343265b156937d4434f023'

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
        if path.is_symlink():
            item['symlinkTarget'] = os.readlink(path)
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


def operator_assets(directory):
    for source, name in [('scripts/factory/setup-forge-box.py', 'setup-forge-box.py'),
                         ('scripts/factory/test-setup-forge-box.py', 'test-setup-forge-box.py'),
                         ('docs/factory-setup.md', 'SETUP.md')]:
        shutil.copy2(REPO / source, directory / name)
    units = directory / 'systemd'
    units.mkdir(exist_ok=True)
    for name in ('fleet-manager-bridge-renew.service.in', 'fleet-manager-bridge-renew.timer.in'):
        shutil.copy2(REPO / 'scripts/factory/systemd' / name, units / name)


def packaging_hashes():
    paths = ['scripts/factory/package.py', 'scripts/factory/check_package.py',
             'scripts/factory/build_voice.py', 'scripts/factory/upstream-shipped-linux.json',
             'third_party/voice/assemble_package.py']
    return {path: digest(REPO / path) for path in paths}


def ready_line(directory, manifest):
    files = manifest['files']
    return (f"FORK PACKAGE READY {directory} {files['bin/codex']['sha256'][:12]} "
            f"{manifest['bwrapEmbeddedSha256'][:12]} BWRAP {manifest['bwrapEmbeddedSha256']}")


def verify_voice(directory, source, target, version):
    voice = directory / 'codex-resources/voice'
    runtime_files(voice.resolve(), target, public_release=True)
    manifest = json.loads((voice / 'manifest.json').read_text())
    if (manifest['buildCommit'] != source or manifest['appVersion'] != version
            or manifest['voiceTarget'] != target or manifest['appTarget'] != target):
        raise RuntimeError('Voice package build identity mismatch')
    expected = {'bin/codex'} | {str(p.relative_to(directory)) for p in voice.rglob('*')
                              if p.is_file() and p.name != 'manifest.json'}
    if set(manifest['sha256']) != expected:
        raise RuntimeError('Voice manifest must cover the whole voice bundle and CLI')
    for relative, sha in manifest['sha256'].items():
        if not (directory / relative).resolve().is_relative_to(directory.resolve()) or digest(directory / relative) != sha:
            raise RuntimeError('Voice package digest mismatch: ' + relative)
    if output([voice / 'bin/codex-voice-host', '--build-commit']) != source:
        raise RuntimeError('Packaged voice helper build identity mismatch')


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output', required=True, type=Path)
    p.add_argument('--source-commit', default='HEAD')
    p.add_argument('--target', default='x86_64-unknown-linux-gnu', choices=SUPPORTED_TARGETS)
    p.add_argument('--target-dir', type=Path, default=Path(os.environ.get('CARGO_TARGET_DIR', REPO / 'codex-rs/target')))
    p.add_argument('--work-dir', type=Path)
    p.add_argument('--base-package', type=Path, help='Reuse a verified whole package of the same runtime source, preserving all binaries')
    p.add_argument('--voice-inputs', type=Path, help='Same-source build_voice.py output; otherwise build it in the isolated work directory')
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
        verify_voice(dest, manifest['runtimeSourceCommit'], manifest['target'], manifest['workspaceVersion'])
        manifest['qualification'] = json.loads(a.record_qualification.read_text())
        manifest['completeness'] = result
        manifest['qualificationCommit'] = output(['git', 'rev-parse', 'HEAD'])
        manifest['packagingScriptsSha256'] = packaging_hashes()
        shutil.copy2(REPO / 'docs/fork-mailbox.md', dest / 'INTERFACE.md')
        operator_assets(dest)
        seal(dest, manifest)
        if manifest['qualification'].get('status') == 'passed':
            print(ready_line(dest, manifest), flush=True)
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
    env.update(CARGO_BUILD_JOBS='8')
    target_dir = a.target_dir.resolve()
    base_stage = work / 'base-package'
    base_manifest = None
    if a.base_package:
        base = a.base_package.resolve(strict=True)
        base_manifest = json.loads((base / 'MANIFEST.json').read_text())
        run(['sha256sum', '-c', 'SHA256SUMS'], cwd=base)
        if (base_manifest['runtimeSourceCommit'] != source or base_manifest['rustSourceTree'] != tree
                or base_manifest['target'] != a.target or base_manifest['workspaceVersion'] != read_workspace_version()
                or base_manifest['files'] != inventory(base)):
            raise RuntimeError('Base package differs from its manifest or selected source/target')
        # The old package intentionally fails the new shipped gate. Validate its
        # full canonical base before adding voice; only the final union can pass.
        for relative in contract['upstreamFiles'] + ['bin/codex-factory-bridge']:
            if not (base / relative).is_file():
                raise RuntimeError('Base package missing canonical input: ' + relative)
        env['CODEX_BWRAP_SHA256'] = digest(base / 'codex-resources/bwrap')
        if env['CODEX_BWRAP_SHA256'] != base_manifest['bwrapEmbeddedSha256']:
            raise RuntimeError('Base bwrap digest mismatch')
        shutil.copytree(base, base_stage)
        (base_stage / 'MANIFEST.json').unlink(); (base_stage / 'SHA256SUMS').unlink()
    else:
        env.update(resolve_codex_v8_cargo_env(TARGET_SPECS[a.target]))
        native = target_dir / a.target / 'release'
        cargo_base = ['cargo', 'build', '--locked', '--release', '--target', a.target, '--target-dir', target_dir]
        # This order follows upstream's Linux Cargo release action. Never strip bwrap after hashing.
        # Keep bundle/job revision stamps out of bwrap; preserve cache reuse for the same component source.
        bwrap_env = {k: v for k, v in env.items() if k not in ('CODEX_FACTORY_REVISION', 'STABLE_GIT_COMMIT', 'CODEX_BWRAP_SHA256')}
        run([*cargo_base, '-p', 'codex-bwrap', '--bin', 'bwrap'], cwd=REPO / 'codex-rs', env=bwrap_env)
        bwrap = work / 'bwrap'; shutil.copy2(native / 'bwrap', bwrap)
        run(['strip', '--strip-debug', '--strip-unneeded', bwrap])
        env['CODEX_BWRAP_SHA256'] = digest(bwrap)
        if env['CODEX_BWRAP_SHA256'] != REQUIRED_BWRAP:
            raise RuntimeError('STOP: Part R bwrap digest changed: ' + env['CODEX_BWRAP_SHA256'])
        env.update(CODEX_FACTORY_REVISION=source, STABLE_GIT_COMMIT=source)
        metadata = json.loads(output(['cargo', 'metadata', '--locked', '--no-deps', '--format-version', '1'], cwd=REPO / 'codex-rs', env=env))
        owners = {t['name']: pkg['name'] for pkg in metadata['packages'] for t in pkg['targets'] if 'bin' in t['kind']}
        binaries = [*contract['binaries'], 'codex-factory-bridge']
        args = [*cargo_base, '--features', 'codex-install-context/factory']
        for package in sorted({owners[b] for b in binaries}):
            args.extend(['-p', package])
        for binary in binaries:
            args.extend(['--bin', binary])
        run(args, cwd=REPO / 'codex-rs', env=env)
        stage = base_stage
        # Same canonical builder called by build-codex-package-archive.sh, preserving prebuilt bytes.
        run([sys.executable, REPO / 'scripts/build_codex_package.py', '--target', a.target, '--variant', 'codex',
             '--package-dir', stage, '--package-version', read_workspace_version(), '--cargo-profile', 'release',
             '--entrypoint-bin', native / 'codex', '--code-mode-host-bin', native / 'codex-code-mode-host',
             '--exec-server-bin', native / 'codex-exec-server', '--bwrap-bin', bwrap, '--strip', 'none'], env=env)
        for binary in binaries:
            if not (stage / 'bin' / binary).exists():
                shutil.copy2(native / binary, stage / 'bin' / binary)
    voice_inputs = a.voice_inputs.resolve() if a.voice_inputs else work / 'voice-inputs'
    if not a.voice_inputs:
        run([sys.executable, REPO / 'scripts/factory/build_voice.py', '--work-dir', voice_inputs,
             '--source-commit', source, '--target', a.target], env=env)
    voice_build = json.loads((voice_inputs / 'build.json').read_text())
    if (voice_build['sourceCommit'] != source or voice_build['target'] != a.target
            or digest(voice_inputs / 'codex-voice-host') != voice_build['helperSha256']):
        raise RuntimeError('Voice build inputs do not match source/target/helper digest')
    stage = work / 'package'
    assemble(base_stage, voice_inputs / 'codex-voice-host', a.target, source, stage,
             runtime=voice_inputs / 'release-runtime', release_version=read_workspace_version())
    (stage / 'codex').symlink_to('bin/codex')
    verify_voice(stage, source, a.target, read_workspace_version())
    if base_manifest:
        for relative in contract['upstreamFiles'] + ['bin/codex-factory-bridge']:
            if digest(stage / relative) != base_manifest['files'][relative]['sha256']:
                raise RuntimeError('Existing runtime file changed during voice assembly: ' + relative)
    shutil.copy2(REPO / 'docs/fork-mailbox.md', stage / 'INTERFACE.md')
    operator_assets(stage)
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
                    packagingCommit=output(['git', 'rev-parse', 'HEAD']), packagingScriptsSha256=packaging_hashes(),
                    target=a.target, profile='release',
                    workspaceVersion=read_workspace_version(), factoryIdentity=factory, voiceBuild=voice_build,
                    basePackage=str(a.base_package) if a.base_package else None,
                    baseRuntimeByteIdentity=({p: digest(stage / p) == item['sha256'] for p, item in base_manifest['files'].items()
                                             if p.startswith(('bin/', 'codex-path/')) or p == 'codex-resources/bwrap'}
                                            if base_manifest else None),
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
