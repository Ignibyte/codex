#!/usr/bin/env python3
"""Regression guard for omitted upstream files and future upstream list changes."""
import json
from pathlib import Path
import shutil
import tempfile
import unittest

import check_package as gate
from codex_package.layout import build_package_dir
from codex_package.targets import PACKAGE_VARIANTS, TARGET_SPECS, PackageInputs


class CompletePackageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='fork-package-test-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.source = self.root / 'executable'; self.source.write_text('fixture'); self.source.chmod(0o755)
        self.package = self.root / 'package'; self.package.mkdir()
        inputs = PackageInputs(self.source, self.source, self.source, self.source, self.source, None, None)
        build_package_dir(self.package, '0.167.2+fixture', PACKAGE_VARIANTS['codex'],
                          TARGET_SPECS['x86_64-unknown-linux-gnu'], inputs)
        for binary in gate.upstream_contract()['binaries'] + ['codex-factory-bridge']:
            if not (self.package / 'bin' / binary).exists():
                shutil.copy2(self.source, self.package / 'bin' / binary)
        for relative in gate.upstream_contract()['shippedFiles'] + gate.FORK_FILES:
            path = self.package / relative
            if relative == 'codex':
                path.symlink_to('bin/codex')
            elif not path.exists():
                path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(self.source, path)

    def test_each_required_file_is_a_release_blocker(self):
        self.assertTrue(gate.check(self.package)['passed'])
        required = set(gate.upstream_contract()['upstreamFiles'] + gate.upstream_contract()['shippedFiles']
                       + gate.FORK_FILES)
        for relative in sorted(required):
            file = self.package / relative
            content, mode = file.read_bytes(), file.stat().st_mode
            file.unlink()
            result = gate.check(self.package)
            self.assertFalse(result['passed'], relative)
            self.assertIn(relative, result['missing'])
            if relative == 'codex':
                file.symlink_to('bin/codex')
            else:
                file.write_bytes(content); file.chmod(mode)
        self.assertTrue(gate.check(self.package)['passed'])

    def fixture_repo(self):
        repo = self.root / 'upstream'
        for relative in (gate.ACTION, gate.ARCHIVE, gate.SHIPPED,
                         Path('scripts/install/install.sh'), Path('scripts/codex_package/layout.py')):
            (repo / relative).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(gate.REPO / relative, repo / relative)
        return repo

    def test_launcher_must_be_the_installed_relative_link(self):
        launcher = self.package / 'codex'
        launcher.unlink(); shutil.copy2(self.source, launcher)
        self.assertFalse(gate.check(self.package)['passed'])
        launcher.unlink(); launcher.symlink_to('bin/codex-exec-server')
        self.assertFalse(gate.check(self.package)['passed'])

    def test_licenses_are_required_but_need_not_be_executable(self):
        license = self.package / 'codex-resources/voice/licenses/LGPL-2.1.txt'
        license.chmod(0o644)
        self.assertTrue(gate.check(self.package)['passed'])
        license.unlink()
        self.assertIn(str(license.relative_to(self.package)), gate.check(self.package)['missing'])

    def test_release_version_change_requires_a_new_shipped_inventory(self):
        repo = self.fixture_repo(); path = repo / gate.SHIPPED
        record = json.loads(path.read_text()); record['releaseVersion'] = '0.167.1'
        path.write_text(json.dumps(record))
        with self.assertRaisesRegex(RuntimeError, 'Shipped release inventory'):
            gate.check(self.package, repo=repo)

    def test_new_upstream_binary_is_required_without_a_fork_list_update(self):
        repo = self.fixture_repo(); action = repo / gate.ACTION
        action.write_text(action.read_text().replace('BASE_BINARIES: "', 'BASE_BINARIES: "codex-future-helper ', 1))
        result = gate.check(self.package, repo=repo)
        self.assertFalse(result['passed'])
        self.assertIn('bin/codex-future-helper', result['missing'])
        shutil.copy2(self.source, self.package / 'bin/codex-future-helper')
        self.assertTrue(gate.check(self.package, repo=repo)['passed'])

    def test_external_helper_symlink_cannot_stand_in_for_a_packaged_file(self):
        helper = self.package / 'bin/codex-code-mode-host'
        helper.unlink(); helper.symlink_to(self.source)
        result = gate.check(self.package)
        self.assertFalse(result['passed'])
        self.assertIn('outside the package', result['errors'][0])

    def test_unknown_archive_resource_fails_closed(self):
        repo = self.fixture_repo(); archive = repo / gate.ARCHIVE
        archive.write_text(archive.read_text() + '\n# --new-upstream-resource-bin\n')
        with self.assertRaisesRegex(RuntimeError, 'Unknown upstream archive resource'):
            gate.check(self.package, repo=repo)

    def test_new_conditional_archive_bundle_requires_review(self):
        repo = self.fixture_repo(); archive = repo / gate.ARCHIVE
        archive.write_text(archive.read_text().replace('*-unknown-linux-musl', '*-unknown-linux-gnu'))
        with self.assertRaisesRegex(RuntimeError, 'Upstream archive assembly changed'):
            gate.check(self.package, repo=repo)

    def test_unreviewed_voice_package_target_cannot_be_declared_complete(self):
        with self.assertRaisesRegex(RuntimeError, 'Unreviewed target'):
            gate.check(self.package, target='x86_64-unknown-linux-musl')


if __name__ == '__main__':
    unittest.main()
