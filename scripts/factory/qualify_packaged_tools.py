#!/usr/bin/env python3
"""Live ChatGPT proof of tools from a complete package, using an isolated home."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import time
import uuid

from check_package import check


def fingerprints(home):
    return {name: hashlib.sha256((home / name).read_bytes()).hexdigest()
            for name in ('auth.json', 'config.toml', 'models_cache.json') if (home / name).is_file()}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--package', required=True, type=Path)
    p.add_argument('--work-dir', required=True, type=Path)
    p.add_argument('--cases', nargs='+', choices=('shell_readonly', 'file_workspace_write', 'mcp_readonly'),
                   default=('shell_readonly', 'file_workspace_write', 'mcp_readonly'))
    a = p.parse_args(); package = a.package.resolve(); root = a.work_dir.resolve()
    if root.exists() or not check(package)['passed']:
        raise RuntimeError('Fresh proof directory and complete package required')
    root.mkdir(mode=0o700); os.umask(0o077)
    normal = Path(os.environ.get('CODEX_HOME', str(Path.home() / '.codex')))
    original = fingerprints(normal)
    (root / 'owner-fingerprints-before.json').write_text(json.dumps(original))
    auth = json.loads((normal / 'auth.json').read_text())
    if auth.get('auth_mode') != 'chatgpt' or not isinstance(auth.get('tokens'), dict):
        raise RuntimeError('Usable normal ChatGPT file login required for this proof')
    home = root / 'codex-home'; home.mkdir(mode=0o700)
    shutil.copyfile(normal / 'auth.json', home / 'auth.json'); (home / 'auth.json').chmod(0o600)
    env = {k: v for k, v in os.environ.items() if not k.startswith(('CODEX_', 'OPENAI_'))}
    env.update(CODEX_HOME=str(home), HOME=str(root / 'home'), TMPDIR=str(root / 'tmp'),
               XDG_CONFIG_HOME=str(root / 'xdg'), XDG_CACHE_HOME=str(root / 'cache'),
               XDG_DATA_HOME=str(root / 'data'), XDG_STATE_HOME=str(root / 'state'))
    for name in ('home', 'tmp', 'xdg', 'cache', 'data', 'state'):
        (root / name).mkdir(mode=0o700)
    readonly, writable, mcpdir = [root / n for n in ('readonly', 'workspace', 'mcp')]
    for d in (readonly, writable, mcpdir): d.mkdir(mode=0o700)
    read_nonce, write_nonce, mcp_nonce = ['FORK_TOOL_' + uuid.uuid4().hex for _ in range(3)]
    (readonly / 'note.txt').write_text(read_nonce + '\n')
    server = root / 'probe_mcp.py'; calls = root / 'mcp-calls.jsonl'
    server.write_text('''import json,sys,pathlib
calls=pathlib.Path(CALLS)
for line in sys.stdin:
 try:request=json.loads(line)
 except json.JSONDecodeError:continue
 if 'id' not in request:continue
 method=request.get('method');params=request.get('params',{})
 if method=='initialize':result={'protocolVersion':params.get('protocolVersion','2024-11-05'),'capabilities':{'tools':{}},'serverInfo':{'name':'fork-proof','version':'1'}}
 elif method=='tools/list':result={'tools':[{'name':'probe_note','description':'Return the local package qualification nonce.','inputSchema':{'type':'object','properties':{},'additionalProperties':False},'annotations':{'readOnlyHint':True,'destructiveHint':False,'openWorldHint':False}}]}
 elif method=='tools/call':
  assert params.get('name')=='probe_note'
  with calls.open('a') as f:f.write(json.dumps({'tool':'probe_note','nonce':NONCE})+'\\n')
  result={'content':[{'type':'text','text':NONCE}],'isError':False}
 elif method=='ping':result={}
 else:
  print(json.dumps({'jsonrpc':'2.0','id':request['id'],'error':{'code':-32601,'message':'Method not found'}}),flush=True);continue
 print(json.dumps({'jsonrpc':'2.0','id':request['id'],'result':result}),flush=True)
'''.replace('CALLS', repr(str(calls))).replace('NONCE', repr(mcp_nonce)))
    cases = [
        ('shell_readonly', 'read-only', readonly,
         'Use the shell tool to run exactly `cat note.txt; uname -n`. Report the actual output.', []),
        ('file_workspace_write', 'workspace-write', writable,
         f'Use the shell tool to write exactly {write_nonce} followed by one newline to tool-write.txt, then read it back. Do not change any other files.', []),
        ('mcp_readonly', 'read-only', mcpdir,
         'Call the MCP server fork_probe tool probe_note once and report its returned text. Use the MCP tool, not a shell substitute.',
         ['-c', 'mcp_servers.fork_probe.command=' + json.dumps(sys.executable),
          '-c', 'mcp_servers.fork_probe.args=' + json.dumps([str(server)])]),
    ]
    results = {}
    for name, sandbox, cwd, prompt, extra in cases:
        if name not in a.cases:
            continue
        cmd = [str(package / 'bin/codex'), '--no-daemon', '--ask-for-approval', 'never', 'exec',
               '--ignore-user-config', '--ignore-rules', '--strict-config', '--sandbox', sandbox,
               '-c', 'model="gpt-6.1-terra"', '-c', 'model_reasoning_effort="high"',
               '--cd', str(cwd), '--skip-git-repo-check', '--json', *extra, '--', prompt]
        start = time.monotonic()
        with (root / (name + '.jsonl')).open('w') as out, (root / (name + '.stderr')).open('w') as err:
            try:
                child = subprocess.run(cmd, env=env, stdin=subprocess.DEVNULL, stdout=out, stderr=err, timeout=180)
                code = child.returncode
            except subprocess.TimeoutExpired:
                code = 124
        records = []
        for line in (root / (name + '.jsonl')).read_text().splitlines():
            try: records.append(json.loads(line))
            except json.JSONDecodeError: pass
        items = [r['item'] for r in records if r.get('type') == 'item.completed']
        commands = [i for i in items if i.get('type') == 'command_execution']
        mcp_events = [i for i in items if i.get('type') == 'mcp_tool_call']
        completed = any(r.get('type') == 'turn.completed' for r in records)
        successful_commands = [i for i in commands if i.get('exit_code') == 0 and i.get('status') == 'completed']
        if name == 'shell_readonly':
            actual = '\n'.join(i.get('aggregated_output', '') for i in successful_commands)
            observed = read_nonce in actual and socket.gethostname() in actual
        elif name == 'file_workspace_write':
            file = writable / 'tool-write.txt'
            observed = bool(successful_commands) and file.is_file() and file.read_text() == write_nonce + '\n'
        else:
            server_calls = [json.loads(line) for line in calls.read_text().splitlines()] if calls.exists() else []
            observed = any(c == {'tool': 'probe_note', 'nonce': mcp_nonce} for c in server_calls)
        missing_host = any('host executable was not found' in i.get('message', '') for i in items)
        result = dict(passed=code == 0 and completed and observed and not missing_host, exit=code,
                      seconds=round(time.monotonic() - start, 2), model='gpt-6.1-terra', reasoningEffort='high',
                      sandbox=sandbox, turnCompleted=completed, commandExecutions=len(commands),
                      successfulCommands=len(successful_commands), mcpEvents=len(mcp_events),
                      effectVerified=observed, missingHostDiagnostic=missing_host, command=cmd)
        results[name] = result
        (root / 'results.json').write_text(json.dumps(results, indent=2) + '\n')
        print(json.dumps({name: {k: v for k, v in result.items() if k != 'command'}}, sort_keys=True), flush=True)
        if not result['passed']:
            return 1
    results['ownerFilesUnchanged'] = original == fingerprints(normal)
    launch_checks = {}
    for relative, flag in [('bin/codex-app-server', '--help'), ('bin/codex-code-mode-host', '--help'),
                           ('bin/codex-exec-server', '--help'), ('codex-path/rg', '--version'),
                           ('codex-resources/bwrap', '--version')]:
        r = subprocess.run([str(package / relative), flag], env=env, stdin=subprocess.DEVNULL,
                           capture_output=True, text=True, timeout=10)
        launch_checks[relative] = {'exit': r.returncode, 'passed': r.returncode == 0}
    results['packagedProgramLaunches'] = launch_checks
    results['package'] = str(package)
    results['authMode'] = 'chatgpt'
    results['separateCodexHome'] = True
    (root / 'results.json').write_text(json.dumps(results, indent=2) + '\n')
    return 0 if all(x['passed'] for x in launch_checks.values()) else 1


if __name__ == '__main__':
    raise SystemExit(main())
