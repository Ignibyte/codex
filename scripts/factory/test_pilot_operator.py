#!/usr/bin/env python3
"""Operator-only pilot guards; no system mutation or external host calls."""
import argparse
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import types
import unittest
from unittest.mock import patch

SPEC=importlib.util.spec_from_file_location('pilot_setup',Path(__file__).with_name('setup-forge-box.py'))
setup=importlib.util.module_from_spec(SPEC);SPEC.loader.exec_module(setup)


class PilotOperatorTests(unittest.TestCase):
    def test_saved_pilot_refuses_legacy_before_missing_key_or_any_write(self):
        with tempfile.TemporaryDirectory(prefix='pilot-refusal-') as tmp:
            cfg=Path(tmp);path=cfg/'setup.json'
            path.write_text(json.dumps(dict(serviceUid=os.geteuid(),pilot=True)))
            before={p.name:p.read_bytes() for p in cfg.iterdir()}
            with self.assertRaisesRegex(RuntimeError,'pilot_legacy_ssh_refused'):
                setup.operator(argparse.Namespace(command='ssh-config',config_root=str(cfg),client_public_key='/missing/key'))
            self.assertEqual(before,{p.name:p.read_bytes() for p in cfg.iterdir()})

    def account(self,*,shell='/usr/sbin/nologin',uid=1001,status='L',sudo_code=1,extra_stderr='',allowed=False):
        account=types.SimpleNamespace(pw_uid=uid,pw_gid=1002,pw_shell=shell)
        password=subprocess.CompletedProcess([],0,stdout='fleet-bridge '+status+' 2026-11-12 0 99999 7 -1\n',stderr='')
        listing='User fleet-bridge may run the following commands on fixture:\n    (root) NOPASSWD: /usr/bin/id\n' if allowed else 'User fleet-bridge is not allowed to run sudo on fixture.\n'
        sudo=subprocess.CompletedProcess([],sudo_code,stdout=listing,stderr=extra_stderr)
        with patch.object(setup.pwd,'getpwnam',return_value=account),patch.object(setup,'root_query',side_effect=[password,sudo]),contextlib.redirect_stdout(io.StringIO()):
            return setup.account_check(dict(serviceUid=1001,serviceGid=1002,forgeLoginShell='/usr/sbin/nologin'),'forge')

    def test_denied_listing_and_locked_correct_account_pass(self):
        self.assertEqual(self.account()['uid'],1001)
        self.assertEqual(self.account(sudo_code=0)['uid'],1001)

    def test_any_allowed_sudo_command_refuses_even_if_true_would_not_be_allowed(self):
        with self.assertRaisesRegex(RuntimeError,'account_sudo_not_denied'):self.account(sudo_code=0,allowed=True)

    def test_wrong_shell_refuses(self):
        with self.assertRaisesRegex(RuntimeError,'account_shell_denied'):self.account(shell='/bin/bash')

    def test_unlocked_account_refuses(self):
        with self.assertRaisesRegex(RuntimeError,'account_password_not_locked'):self.account(status='P')

    def test_root_uid_refuses(self):
        with self.assertRaisesRegex(RuntimeError,'account_uid_denied'):self.account(uid=0)

    def test_query_error_with_denial_text_still_refuses(self):
        with self.assertRaisesRegex(RuntimeError,'account_sudo_not_denied'):self.account(extra_stderr='sudo: LDAP lookup failed\n')
        with self.assertRaisesRegex(RuntimeError,'account_sudo_not_denied'):self.account(extra_stderr='/etc/sudoers:1: syntax error\n')

    def test_generation_change_invalidates_root_report(self):
        with tempfile.TemporaryDirectory(prefix='pilot-report-') as tmp:
            stage=Path(tmp);(stage/'generation.json').write_text('{}')
            value=dict(host='forge',generation='one',stageSha256=setup.file_sha(stage/'generation.json'),verifiedAt=int(setup.time.time()),bootId=Path('/proc/sys/kernel/random/boot_id').read_text().strip())
            report=stage/'report.json';report.write_text(json.dumps(value))
            with patch.object(setup,'rooted',side_effect=lambda p,**kw:Path(p)):
                setup.fresh_report(report,'forge',dict(generation='one'),stage)
                (stage/'generation.json').write_text('{"changed":true}')
                with self.assertRaisesRegex(RuntimeError,'host_report_generation_denied'):
                    setup.fresh_report(report,'forge',dict(generation='one'),stage)


if __name__=='__main__':unittest.main()
