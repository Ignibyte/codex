"""Owned Linux daemon and deterministic model fixtures for factory qualification."""

import base64
import hashlib
import http.server
import json
import os
from pathlib import Path
import pty
import fcntl
import re
import select
import shutil
import signal
import socket
import struct
import subprocess
import tempfile
import threading
import time
import termios


def sha256(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


class Tui:
    def __init__(self, daemon, thread):
        self.capture = daemon.evidence / "tui.raw"
        self.data = b""
        self.pid, self.fd = pty.fork()
        if self.pid == 0:
            os.chdir(daemon.home / "project")
            os.execve(daemon.cli, [str(daemon.cli), "resume", thread, "--no-alt-screen"], daemon.env)
        self.identity = incarnation(self.pid)
        fcntl.ioctl(self.fd, termios.TIOCSWINSZ, struct.pack("HHHH", 45, 160, 0, 0))
        self.exited = False

    def pump(self, timeout=0.1):
        if select.select([self.fd], [], [], timeout)[0]:
            try:
                data = os.read(self.fd, 65536)
            except OSError:
                return
            self.data += data
            self.capture.write_bytes(self.data)
            if b"\x1b[6n" in data:
                os.write(self.fd, b"\x1b[1;1R")

    def wait_text(self, text):
        deadline = time.monotonic() + 25
        while text.encode() not in self.data:
            self.pump()
            assert time.monotonic() < deadline, self.data[-3000:]

    def send(self, data):
        os.write(self.fd, data)

    def wait_exit(self):
        deadline = time.monotonic() + 15
        while True:
            self.pump()
            pid, status = os.waitpid(self.pid, os.WNOHANG)
            if pid:
                self.exited = True
                return os.waitstatus_to_exitcode(status)
            assert time.monotonic() < deadline, self.data[-3000:]

    def close(self):
        if not self.exited and incarnation(self.pid) == self.identity:
            os.kill(self.pid, signal.SIGTERM)
            os.waitpid(self.pid, 0)
        os.close(self.fd)
        plain = re.sub(rb"\x1b\[[0-?]*[ -/]*[@-~]", b"", self.data)
        self.capture.with_suffix(".txt").write_bytes(plain)


def incarnation(pid):
    try:
        fields = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
        if fields[0] == "Z":
            return None
        return [Path("/proc/sys/kernel/random/boot_id").read_text().strip(), fields[19]]
    except FileNotFoundError:
        return None


class Rpc:
    """Small synchronous RFC6455 client over the daemon's private Unix socket."""

    def __init__(self, path, transcript):
        self.sock = socket.socket(socket.AF_UNIX)
        self.sock.settimeout(20)
        self.sock.connect(str(path))
        self.transcript = transcript
        self.serial = 0
        self.events = []
        key = base64.b64encode(os.urandom(16)).decode()
        request = (f"GET / HTTP/1.1\r\nHost: localhost\r\nUpgrade: websocket\r\n"
                   f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\n"
                   "Sec-WebSocket-Version: 13\r\n\r\n")
        self.sock.sendall(request.encode())
        header = b""
        while not header.endswith(b"\r\n\r\n"):
            header += self.read(1)
        accept = base64.b64encode(hashlib.sha1(
            (key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode()).digest())
        assert header.startswith(b"HTTP/1.1 101") and accept in header, header
        self.call("initialize", {"clientInfo": {"name": "factory_fixture", "version": "1"},
                                 "capabilities": {"experimentalApi": True}})
        self.send({"method": "initialized"})

    def read(self, count):
        data = b""
        while len(data) < count:
            chunk = self.sock.recv(count - len(data))
            if not chunk:
                raise EOFError("daemon disconnected")
            data += chunk
        return data

    def frame(self, payload, opcode=1):
        mask = os.urandom(4)
        size = len(payload)
        header = bytes([0x80 | opcode])
        header += bytes([0x80 | size]) if size < 126 else (
            b"\xfe" + struct.pack("!H", size) if size < 65536 else
            b"\xff" + struct.pack("!Q", size))
        self.sock.sendall(header + mask + bytes(v ^ mask[i % 4] for i, v in enumerate(payload)))

    def send(self, value):
        self.transcript.append({"direction": "request", "value": value})
        self.frame(json.dumps(value).encode())

    def recv(self):
        fragments = b""
        while True:
            first, second = self.read(2)
            size = second & 127
            if size == 126:
                size = struct.unpack("!H", self.read(2))[0]
            elif size == 127:
                size = struct.unpack("!Q", self.read(8))[0]
            assert size <= 32 * 1024 * 1024
            mask = self.read(4) if second & 128 else None
            data = self.read(size)
            if mask:
                data = bytes(v ^ mask[i % 4] for i, v in enumerate(data))
            opcode = first & 15
            if opcode == 8:
                raise EOFError("daemon closed websocket")
            if opcode == 9:
                self.frame(data, 10)
                continue
            if opcode == 10:
                continue
            fragments += data
            if first & 128:
                value = json.loads(fragments)
                self.transcript.append({"direction": "response", "value": value})
                return value

    def call(self, method, params):
        self.serial += 1
        request_id = self.serial
        self.send({"id": request_id, "method": method, "params": params})
        while True:
            value = self.recv()
            if value.get("id") == request_id and ("result" in value or "error" in value):
                if "error" in value:
                    raise RuntimeError(json.dumps(value["error"]))
                return value["result"]
            self.events.append(value)

    def close(self):
        self.sock.close()


class Model:
    def __init__(self):
        self.requests = []
        self.outputs = {}
        self.gates = []
        self.lock = threading.Lock()
        owner = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                body = self.rfile.read(int(self.headers["Content-Length"]))
                with owner.lock:
                    index = len(owner.requests)
                    owner.requests.append({"path": self.path, "body": json.loads(body)})
                    gate = threading.Event()
                    owner.gates.append(gate)
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Connection", "close")
                self.end_headers()
                def emit(event):
                    self.wfile.write(("event: " + event["type"] + "\ndata: " +
                                      json.dumps(event) + "\n\n").encode())
                    self.wfile.flush()
                try:
                    emit({"type": "response.created", "response": {"id": f"fixture-{index}"}})
                    if not gate.wait(90):
                        return
                    emit({"type": "response.output_item.done", "item": owner.outputs.get(index, {
                        "type": "message", "role": "assistant", "id": f"msg-{index}",
                        "content": [{"type": "output_text", "text": f"fixture-result-{index}"}]})})
                    emit({"type": "response.completed", "response": {"id": f"fixture-{index}",
                         "usage": {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}}})
                except (BrokenPipeError, ConnectionResetError):
                    pass

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def wait_request(self, index):
        deadline = time.monotonic() + 25
        while len(self.gates) <= index:
            assert time.monotonic() < deadline, "fixture model was not contacted"
            time.sleep(0.05)

    def release(self, index):
        self.gates[index].set()

    def close(self):
        for gate in self.gates:
            gate.set()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()


class Daemon:
    def __init__(self, package, evidence, model):
        self.evidence = Path(evidence)
        self.package = Path(package).resolve()
        self.cli = self.package / "bin/codex"
        self.home = Path(tempfile.mkdtemp(prefix="cf2-", dir=f"/run/user/{os.getuid()}"))
        self.home.chmod(0o700)
        self.transcript = []
        self.commands = []
        self.processes = {}
        self.connections = []
        self.tuis = []
        self.env = {"PATH": os.environ["PATH"], "HOME": str(self.home),
                    "CODEX_HOME": str(self.home), "TERM": "xterm-256color",
                    "NO_PROXY": "127.0.0.1,localhost"}
        (self.home / "project").mkdir()
        (self.home / "config.toml").write_text(f'''model = "mock-model"
model_provider = "fixture"
approval_policy = "never"
sandbox_mode = "read-only"
cli_auth_credentials_store = "file"
[features]
plugins = false
api_key_model_discovery = false
[analytics]
enabled = false
[model_providers.fixture]
name = "Bounded local fixture"
base_url = "http://127.0.0.1:{model.server.server_port}/v1"
wire_api = "responses"
request_max_retries = 0
stream_max_retries = 0
supports_websockets = false
[projects."{self.home / 'project'}"]
trust_level = "trusted"
''')
        state = self.home / "app-server-daemon"
        state.mkdir(mode=0o700)
        (state / "settings.json").write_text(json.dumps({"shutdownGraceSeconds": 1,
              "remoteControlEnabled": False, "updater": {"autoUpdateEnabled": False}}))

    def command(self, *args):
        command = [str(self.cli), *args]
        result = subprocess.run(command, env=self.env, cwd=self.home / "project",
                                capture_output=True, text=True, timeout=35)
        self.commands.append({"command": command, "exit_code": result.returncode,
                              "stdout": result.stdout, "stderr": result.stderr})
        assert result.returncode == 0, self.commands[-1]
        return json.loads(result.stdout)

    def lifecycle(self, action):
        result = self.command("app-server", "daemon", action)
        if result.get("pid"):
            pid = result["pid"]
            self.processes[pid] = incarnation(pid)
        return result

    def connect(self):
        path = self.lifecycle("version")["socketPath"]
        rpc = Rpc(path, self.transcript)
        self.connections.append(rpc)
        return rpc

    def pid(self):
        record = json.loads((self.home / "app-server-daemon/daemon.pid").read_text())
        return record["pid"]

    def kill(self, pid):
        assert self.processes[pid] and incarnation(pid) == self.processes[pid]
        os.kill(pid, signal.SIGKILL)
        deadline = time.monotonic() + 10
        while incarnation(pid) == self.processes[pid]:
            assert time.monotonic() < deadline
            time.sleep(0.05)

    def close(self):
        for tui in self.tuis:
            tui.close()
        for rpc in self.connections:
            rpc.close()
        try:
            self.lifecycle("stop")
        finally:
            for pid, identity in self.processes.items():
                if identity and incarnation(pid) == identity:
                    self.kill(pid)
            for path in (self.home / "app-server-daemon").glob("*.log"):
                shutil.copyfile(path, self.evidence / path.name)
            (self.evidence / "rpc.json").write_text(json.dumps(self.transcript, indent=2) + "\n")
            (self.evidence / "commands.json").write_text(json.dumps(self.commands, indent=2) + "\n")
            (self.evidence / "processes.json").write_text(json.dumps(self.processes, indent=2) + "\n")
            shutil.rmtree(self.home)
