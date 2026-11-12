#!/usr/bin/env python3
"""Fail closed against upstream's current Linux package contract."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import sys
import tempfile

REPO = Path(__file__).resolve().parents[2]
os.environ['CODEX_REPO_ROOT'] = str(REPO)
sys.path.insert(0, str(REPO / 'scripts'))
from codex_package.cli import create_parser
from codex_package.layout import build_package_dir, validate_package_dir
from codex_package.targets import PACKAGE_VARIANTS, TARGET_SPECS, PackageInputs

ACTION = Path('.github/actions/build-codex-packages/action.yml')
ARCHIVE = Path('.github/scripts/build-codex-package-archive.sh')
# The archive wrapper also has conditional assembly (for example voice bundles).
# An unreviewed change there must not bypass the canonical-layout inventory below.
REVIEWED_ARCHIVE_SHA256 = '2abcc24b8cad163983abfaeedc0d2ece3fc1c27391526a47803c86e41d9e9386'
SUPPORTED_TARGETS = ('x86_64-unknown-linux-gnu',)


def upstream_contract(target='x86_64-unknown-linux-gnu', repo=REPO):
    if target not in SUPPORTED_TARGETS:
        raise RuntimeError('Unreviewed target: review its full upstream resources, including conditional voice bundles, before packaging')
    spec = TARGET_SPECS[target]
    if not spec.is_linux:
        raise RuntimeError('This fork package gate currently supports Linux targets only')
    action = (repo / ACTION).read_text()
    matches = re.findall(r'^\s*BASE_BINARIES:\s*[\'"]([^\'"\n]+)[\'"]\s*$', action, re.M)
    if len(matches) != 1:
        raise RuntimeError('Cannot unambiguously read upstream BASE_BINARIES; review upstream changes')
    binaries = shlex.split(matches[0])
    if not binaries or any(not re.fullmatch(r'[a-z0-9-]+', b) for b in binaries):
        raise RuntimeError('Unrecognized upstream binary list')
    archive = (repo / ARCHIVE).read_text()
    flags = set(re.findall(r'--[a-z0-9-]+-bin\b', archive))
    known_flags = set(create_parser()._option_string_actions)
    if flags - known_flags:
        raise RuntimeError('Unknown upstream archive resource flags: ' + ', '.join(sorted(flags - known_flags)))
    if hashlib.sha256((repo / ARCHIVE).read_bytes()).hexdigest() != REVIEWED_ARCHIVE_SHA256:
        raise RuntimeError('Upstream archive assembly changed; review the complete Linux contract and update its reviewed digest')
    if '--exec-server-bin' not in flags or '--code-mode-host-bin' not in flags:
        raise RuntimeError('Upstream archive helper contract changed; review required')
    variants = [v for v in PACKAGE_VARIANTS.values() if v.cargo_bin in binaries]
    if not variants:
        raise RuntimeError('No upstream package variants matched BASE_BINARIES')
    # Let upstream itself materialize the required Linux layout. No fork file list.
    with tempfile.TemporaryDirectory(prefix='fork-package-contract-') as tmp:
        root = Path(tmp)
        dummy = root / 'executable'; dummy.write_bytes(b'contract fixture\n'); dummy.chmod(0o755)
        inputs = PackageInputs(entrypoint_bin=dummy, code_mode_host_bin=dummy,
                               exec_server_bin=dummy, rg_bin=dummy, bwrap_bin=dummy,
                               codex_command_runner_bin=None, codex_windows_sandbox_setup_bin=None)
        files = set()
        for variant in variants:
            layout = root / variant.name; layout.mkdir()
            build_package_dir(layout, '0.0.0', variant, spec, inputs)
            validate_package_dir(layout, variant, spec)
            files.update(str(p.relative_to(layout)) for p in layout.rglob('*') if p.is_file())
    files.update('bin/' + b for b in binaries)
    return dict(target=target, binaries=binaries, upstreamFiles=sorted(files),
                upstreamSources={str(p): hashlib.sha256((repo / p).read_bytes()).hexdigest()
                                 for p in (ACTION, ARCHIVE, Path('scripts/codex_package/layout.py'))})


def check(directory, target='x86_64-unknown-linux-gnu', repo=REPO):
    contract = upstream_contract(target, repo=repo)
    required = [*contract['upstreamFiles'], 'bin/codex-factory-bridge']
    missing = [p for p in required if not (directory / p).is_file()]
    nonexec = [p for p in required if p != 'codex-package.json' and (directory / p).is_file()
               and not os.access(directory / p, os.X_OK)]
    errors = []
    external = [p for p in required if (directory / p).exists()
                and not (directory / p).resolve().is_relative_to(directory.resolve())]
    if external:
        errors.append('Required files resolve outside the package: ' + ', '.join(external))
    if not missing:
        try:
            validate_package_dir(directory, PACKAGE_VARIANTS['codex'], TARGET_SPECS[target])
        except Exception as exc:
            errors.append(str(exc))
    return dict(passed=not (missing or nonexec or errors), directory=str(directory),
                missing=missing, notExecutable=nonexec, errors=errors, **contract)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('directory', type=Path, nargs='?')
    p.add_argument('--target', default='x86_64-unknown-linux-gnu', choices=SUPPORTED_TARGETS)
    p.add_argument('--contract', action='store_true')
    a = p.parse_args()
    try:
        result = upstream_contract(a.target) if a.contract else check(a.directory.resolve(), a.target)
    except Exception as exc:
        print(json.dumps({'passed': False, 'error': str(exc)}, indent=2))
        return 1
    print(json.dumps(result, indent=2))
    return 0 if a.contract or result['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
