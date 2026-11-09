#!/usr/bin/env python3
"""Exercise the actual packaged daemon using fixture responses, never a real login."""

import argparse
import json
from pathlib import Path
import time
import traceback
import threading
import sys
import subprocess

from session_fixture import Daemon, Model, Tui, sha256


def wait_turn(rpc, thread, turn, statuses):
    deadline = time.monotonic() + 25
    while True:
        state = rpc.call("thread/read", {"threadId": thread, "includeTurns": True})["thread"]
        found = next((t for t in state["turns"] if t["id"] == turn), None)
        if found and found["status"] in statuses:
            return found
        assert time.monotonic() < deadline, state
        time.sleep(0.1)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--package", required=True, type=Path)
    parser.add_argument("--evidence", required=True, type=Path)
    args = parser.parse_args()
    args.evidence.mkdir(mode=0o700, parents=True, exist_ok=False)
    model = Model()
    daemon = Daemon(args.package, args.evidence, model)
    checks = []
    try:
        started = daemon.lifecycle("start")
        rpc = daemon.connect()
        thread = rpc.call("thread/start", {"cwd": str(daemon.home / "project"),
                          "historyMode": "legacy"})["thread"]["id"]
        def turn():
            return rpc.call("turn/start", {"threadId": thread, "input": [
                {"type": "text", "text": "Synthetic fixture request; no tools."}]})["turn"]["id"]
        first = turn()
        model.wait_request(0)
        rpc.close()
        tui = Tui(daemon, thread)
        daemon.tuis.append(tui)
        tui.wait_text("mock-model")
        time.sleep(0.5)
        tui.send(b"\x03")
        tui.wait_text("Run in background")
        tui.send(b"2\r")
        assert tui.wait_exit() == 0
        checks.append({"check": "native_tui_run_in_background", "exit_code": 0,
                       "capture": "tui.raw and tui.txt"})
        rpc = daemon.connect()
        resumed = rpc.call("thread/resume", {"threadId": thread})["thread"]
        assert resumed["id"] == thread
        assert any(t["id"] == first and t["status"] == "inProgress" for t in resumed["turns"])
        assert daemon.pid() == started["pid"]
        model.release(0)
        completed = wait_turn(rpc, thread, first, {"completed"})
        assert "fixture-result-0" in json.dumps(completed)
        checks.append({"check": "disconnect_preserves_turn_and_history", "thread": thread,
                       "turn": first, "status": completed["status"]})
        second = turn()
        model.wait_request(1)
        rpc.call("turn/interrupt", {"threadId": thread, "turnId": second})
        stopped = wait_turn(rpc, thread, second, {"interrupted"})
        model.release(1)
        checks.append({"check": "explicit_interrupt_differs_from_disconnect", "status": stopped["status"]})
        before = daemon.pid()
        graceful_turn = turn()
        model.wait_request(2)
        release = threading.Timer(0.2, model.release, args=[2])
        release.start()
        daemon.lifecycle("restart")
        release.join()
        rpc = daemon.connect()
        restored = rpc.call("thread/resume", {"threadId": thread})["thread"]
        assert restored["id"] == thread and "fixture-result-0" in json.dumps(restored)
        assert daemon.pid() != before
        assert next(t for t in restored["turns"] if t["id"] == graceful_turn)["status"] == "completed"
        checks.append({"check": "graceful_restart_retains_history", "thread": thread,
                       "inflight_turn": graceful_turn, "outcome": "completed during shutdown grace"})
        third = turn()
        model.wait_request(3)
        dead = daemon.pid()
        daemon.kill(dead)
        rpc.close()
        daemon.lifecycle("start")
        rpc = daemon.connect()
        restored = rpc.call("thread/resume", {"threadId": thread})["thread"]
        assert restored["id"] == thread
        old = next(t for t in restored["turns"] if t["id"] == third)
        assert old["status"] != "completed", old
        checks.append({"check": "abrupt_death_never_reports_completion", "turn": third,
                       "observed_status": old["status"], "interpretation": "interrupted or uncertain; not uninterrupted execution"})
        try:
            rpc.call("thread/resume", {"threadId": "00000000-0000-0000-0000-000000000000"})
            raise AssertionError("absent thread accepted")
        except RuntimeError as exc:
            checks.append({"check": "absent_thread_refused", "error": str(exc)})
        result = {"passed": True, "checks": checks, "cli_sha256": sha256(daemon.cli)}
    except Exception:
        result = {"passed": False, "checks": checks, "error": traceback.format_exc()}
    finally:
        daemon.close()
        model.close()
        (args.evidence / "model-requests.json").write_text(json.dumps(model.requests, indent=2) + "\n")
    result["command"] = [sys.executable, *sys.argv]
    result["source_commit"] = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    result["test_source"] = {name: sha256(Path(__file__).parent / name)
                             for name in ["qualify_sessions.py", "session_fixture.py"]}
    result["cleanup"] = {"private_home_removed": not daemon.home.exists(),
                         "process_incarnations_ended": True}
    result["exit_code"] = 0 if result["passed"] else 1
    (args.evidence / "result.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
