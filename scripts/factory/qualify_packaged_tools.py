#!/usr/bin/env python3
"""Prove packaged tools using an isolated home; optional local Responses fixture."""
import argparse
import hashlib
import http.server
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import time
import threading
import uuid

from check_package import check


class LocalModel:
    """Return a code-mode call, then finish only after receiving its tool output."""
    def __init__(self, code):
        self.requests = []
        owner = self
        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args): pass
            def do_POST(self):
                request = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                index = len(owner.requests); owner.requests.append(request)
                self.send_response(200)
                self.send_header('Content-Type', 'text/event-stream')
                self.send_header('Connection', 'close'); self.end_headers()
                def emit(event):
                    self.wfile.write(('event: ' + event['type'] + '\ndata: ' + json.dumps(event) + '\n\n').encode())
                    self.wfile.flush()
                emit(dict(type='response.created', response=dict(id='offline-' + str(index))))
                item = (dict(type='custom_tool_call', call_id='packaged-tool', name='exec', input=code)
                        if index == 0 else dict(type='message', role='assistant', id='done',
                        content=[dict(type='output_text', text='Offline package tool proof complete.')]))
                emit(dict(type='response.output_item.done', item=item))
                emit(dict(type='response.completed', response=dict(id='offline-' + str(index),
                     usage=dict(input_tokens=0, output_tokens=0, total_tokens=0))))
        self.server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True); self.thread.start()
    def close(self):
        self.server.shutdown(); self.server.server_close(); self.thread.join()


def fingerprints(home):
    return {name: hashlib.sha256((home / name).read_bytes()).hexdigest()
            for name in ('auth.json', 'config.toml', 'models_cache.json') if (home / name).is_file()}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--package', required=True, type=Path)
    p.add_argument('--work-dir', required=True, type=Path)
    p.add_argument('--offline', action='store_true', help='Use only an owned loopback model fixture; no login read')
    p.add_argument('--cases', nargs='+', choices=('shell_readonly', 'file_workspace_write', 'mcp_readonly'),
                   default=('shell_readonly', 'file_workspace_write', 'mcp_readonly'))
    a = p.parse_args(); package = a.package.resolve(); root = a.work_dir.resolve()
    if root.exists() or not check(package)['passed']:
        raise RuntimeError('Fresh proof directory and complete package required')
    root.mkdir(mode=0o700); os.umask(0o077)
    normal = Path(os.environ.get('CODEX_HOME', str(Path.home() / '.codex')))
    original = {} if a.offline else fingerprints(normal)
    (root / 'owner-fingerprints-before.json').write_text(json.dumps(original))
    home = root / 'codex-home'; home.mkdir(mode=0o700)
    if not a.offline:
        auth = json.loads((normal / 'auth.json').read_text())
        if auth.get('auth_mode') != 'chatgpt' or not isinstance(auth.get('tokens'), dict):
            raise RuntimeError('Usable normal ChatGPT file login required for this proof')
        shutil.copyfile(normal / 'auth.json', home / 'auth.json'); (home / 'auth.json').chmod(0o600)
    env = {k: v for k, v in os.environ.items() if not k.startswith(('CODEX_', 'OPENAI_'))}
    env.update(CODEX_HOME=str(home), HOME=str(root / 'home'), TMPDIR=str(root / 'tmp'),
               NO_PROXY='127.0.0.1,localhost', no_proxy='127.0.0.1,localhost',
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
        model = None
        if a.offline:
            if name == 'mcp_readonly':
                code = ('const t = ALL_TOOLS.find(t => t.name.includes("fork_probe") && t.name.includes("probe_note")); '
                        'if (!t) throw new Error("missing_fixture_mcp"); text(await tools[t.name]({}));')
            else:
                shell = ('cat note.txt; uname -n' if name == 'shell_readonly' else
                         "printf '%s\\n' " + write_nonce + ' > tool-write.txt; cat tool-write.txt')
                code = 'text(await tools.exec_command(' + json.dumps(dict(cmd=shell, max_output_tokens=1000)) + '));'
            model = LocalModel(code)
            for setting in ['model_provider="fixture"', 'features.api_key_model_discovery=false',
                            'features.plugins=false', 'features.code_mode_only=true', 'features.code_mode_host_grpc=true',
                            'model_providers.fixture.name="Offline local qualification"',
                            f'model_providers.fixture.base_url="http://127.0.0.1:{model.server.server_port}/v1"',
                            'model_providers.fixture.wire_api="responses"',
                            'model_providers.fixture.supports_websockets=false',
                            'model_providers.fixture.request_max_retries=0', 'model_providers.fixture.stream_max_retries=0']:
                extra.extend(['-c', setting])
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
            finally:
                if model: model.close()
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
        if model:
            expected = dict(shell_readonly=read_nonce, file_workspace_write=write_nonce, mcp_readonly=mcp_nonce)[name]
            returned = len(model.requests) == 2 and expected in json.dumps(model.requests[1].get('input', []))
            result.update(localModelRequests=len(model.requests), toolOutputReturnedToModel=returned,
                          externalModelRequests=0, codeModeHostRequired=True)
            result['passed'] = result['passed'] and returned
            (root / (name + '-model-requests.json')).write_text(json.dumps(model.requests, indent=2) + '\n')
        results[name] = result
        (root / 'results.json').write_text(json.dumps(results, indent=2) + '\n')
        print(json.dumps({name: {k: v for k, v in result.items() if k != 'command'}}, sort_keys=True), flush=True)
        if not result['passed']:
            return 1
    results['ownerFilesUnchanged'] = True if a.offline else original == fingerprints(normal)
    results['ownerFilesRead'] = not a.offline
    launch_checks = {}
    for relative, flag in [('bin/codex-app-server', '--help'), ('bin/codex-code-mode-host', '--help'),
                           ('bin/codex-exec-server', '--help'), ('codex-path/rg', '--version'),
                           ('codex-resources/bwrap', '--version')]:
        r = subprocess.run([str(package / relative), flag], env=env, stdin=subprocess.DEVNULL,
                           capture_output=True, text=True, timeout=10)
        launch_checks[relative] = {'exit': r.returncode, 'passed': r.returncode == 0}
    results['packagedProgramLaunches'] = launch_checks
    results['package'] = str(package)
    results['authMode'] = 'none; local offline fixture' if a.offline else 'chatgpt'
    results['separateCodexHome'] = True
    (root / 'results.json').write_text(json.dumps(results, indent=2) + '\n')
    return 0 if all(x['passed'] for x in launch_checks.values()) else 1


if __name__ == '__main__':
    raise SystemExit(main())
