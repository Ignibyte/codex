#!/usr/bin/env python3
"""Build and explicitly select digest-pinned local factory packages.

Selection state and packages belong to the trusted operator, outside agent-writable
roots. This tool does not install a vendor release or change a running daemon.
"""

import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[2]
FACTORY = "ignibyte-codex-factory"
REQUIRED = {"bin/codex", "bin/codex-code-mode-host", "codex-path/rg",
            "codex-resources/bwrap", "codex-package.json"}


def require(condition, reason):
    if not condition:
        raise ValueError(reason)


def digest(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def source_identity():
    tracked = subprocess.check_output(["git", "ls-files", "-z"], cwd=ROOT).decode().split("\0")
    untracked = subprocess.check_output(["git", "ls-files", "--others", "--exclude-standard", "-z"], cwd=ROOT).decode().split("\0")
    added = [p for p in untracked if p.startswith(("scripts/factory/", "codex-rs/"))]
    paths = sorted(set(tracked + added) - {""})
    paths = [p for p in paths if not p.endswith(".md") and not p.startswith("docs/")]
    diff = subprocess.check_output(["git", "diff", "--binary", "HEAD"], cwd=ROOT)
    return {"commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
            "diffSha256": hashlib.sha256(diff).hexdigest(),
            "files": {p: digest(ROOT / p) for p in paths},
            "modes": {p: oct((ROOT / p).stat().st_mode & 0o777) for p in paths}}


def verify(package, expected):
    package = Path(package).resolve(strict=True)
    manifest_path = package / "factory-package.json"
    require(digest(manifest_path) == expected, "factory_manifest_pin_mismatch")
    manifest = json.loads(manifest_path.read_text())
    require(manifest["schema"] == 1 and manifest["factoryId"] == FACTORY, "factory_package_identity")
    require(manifest["storageEpoch"] in (1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11), "factory_storage_epoch_unsupported")
    files = manifest["files"]
    require(REQUIRED <= files.keys(), "factory_required_helper_missing")
    if manifest.get("bridgeProtocol") is not None:
        require(manifest["bridgeProtocol"] == 1 and "bin/codex-factory-bridge" in files,
                "factory_bridge_helper_missing_or_unsupported")
    for name, expected_file in files.items():
        relative = Path(name)
        require(not relative.is_absolute() and ".." not in relative.parts, "factory_package_path")
        path = package / relative
        require(not path.is_symlink() and path.resolve().is_relative_to(package), "factory_package_symlink")
        require(path.is_file() and digest(path) == expected_file, f"factory_helper_mismatch: {name}")
        if name != "codex-package.json":
            require(os.access(path, os.X_OK), f"factory_helper_not_executable: {name}")
    with tempfile.TemporaryDirectory(prefix="factory-package-probe-") as home:
        result = subprocess.run([str(package / "bin/codex"), "factory-info"], check=True,
                                env={"PATH": os.environ["PATH"], "HOME": home, "CODEX_HOME": home},
                                capture_output=True, text=True, timeout=15)
    identity = json.loads(result.stdout)
    require(identity["factoryId"] == FACTORY and identity["revision"] == manifest["revision"], "factory_compiled_identity_mismatch")
    require(identity["publicUpdatesAllowed"] is False, "factory_updates_not_pinned")
    require(identity.get("storageEpoch", 1) == manifest["storageEpoch"], "factory_compiled_storage_epoch_mismatch")
    if manifest["storageEpoch"] in (2, 3, 4, 5, 6, 7, 8, 9, 10, 11):
        require(manifest.get("bridgeStorageEpoch") == identity.get("bridgeStorageEpoch") == (7 if manifest["storageEpoch"] >= 11 else 6 if manifest["storageEpoch"] >= 9 else 5 if manifest["storageEpoch"] >= 6 else 4 if manifest["storageEpoch"] == 5 else 3)
                and manifest.get("nativeFactoryStorageEpoch") == identity.get("nativeFactoryStorageEpoch") == 2,
                "factory_component_storage_epoch_mismatch")
    if manifest["storageEpoch"] >= 3:
        require(manifest.get("recordingStorageEpoch") == identity.get("recordingStorageEpoch") == 1,
                "factory_recording_storage_epoch_mismatch")
    if manifest["storageEpoch"] >= 4:
        require(manifest.get("observationStorageEpoch") == identity.get("observationStorageEpoch") == (2 if manifest["storageEpoch"] >= 5 else 1),
                "factory_observation_storage_epoch_mismatch")
    if manifest["storageEpoch"] >= 5:
        require(manifest.get("networkTransportProtocol") == identity.get("networkTransportProtocol") == 1,
                "factory_network_transport_protocol_mismatch")
    if manifest["storageEpoch"] >= 7:
        require(manifest.get("foremanStorageEpoch") == identity.get("foremanStorageEpoch") == (3 if manifest["storageEpoch"] >= 10 else 2 if manifest["storageEpoch"] >= 8 else 1),
                "factory_foreman_storage_epoch_mismatch")
    if manifest["storageEpoch"] >= 10:
        require(manifest.get("managerStorageEpoch") == identity.get("managerStorageEpoch") == 1,
                "factory_manager_storage_epoch_mismatch")
    if manifest["storageEpoch"] >= 11:
        require(manifest.get("supportStorageEpoch") == identity.get("supportStorageEpoch") == 1,
                "factory_support_storage_epoch_mismatch")
    if manifest.get("bridgeProtocol") == 1:
        with tempfile.TemporaryDirectory(prefix="factory-bridge-probe-") as home:
            result = subprocess.run([str(package / "bin/codex-factory-bridge"), "--version"],
                                    env={"PATH": os.environ["PATH"], "HOME": home, "CODEX_HOME": home},
                                    capture_output=True, text=True, timeout=15, check=True)
        require(result.stdout.strip() == "codex-factory " + identity["version"], "factory_bridge_version_mismatch")
    return manifest


def build(args):
    require(args.revision and all(c.isalnum() or c in "-_." for c in args.revision), "factory_invalid_revision")
    require(not args.package.exists(), "factory_package_already_exists")
    before = source_identity()
    args.evidence.mkdir(parents=True, mode=0o700, exist_ok=False)
    tmp = ROOT / ".artifacts/factory/package-cache"
    tmp.mkdir(mode=0o700, parents=True, exist_ok=True)
    env = {**os.environ, "CODEX_REPO_ROOT": str(ROOT), "CODEX_FACTORY_REVISION": args.revision,
           "CARGO_TARGET_DIR": str(args.target.resolve()), "CARGO_BUILD_JOBS": "2",
           "CARGO_INCREMENTAL": "0", "TMPDIR": str(tmp.resolve())}
    cmd = ["python3", "-u", str(ROOT / "scripts/build_codex_package.py"),
           "--target", "x86_64-unknown-linux-gnu", "--variant", "codex", "--cargo-profile", "dev-small",
           "--cargo", str(ROOT / "scripts/factory/cargo-locked"), "--rg-bin", "/usr/bin/rg",
           "--package-dir", str(args.package.resolve())]
    with (args.evidence / "build.log").open("x") as output:
        result = subprocess.run(["nice", "-n", "10", *cmd], cwd=ROOT, env=env,
                                stdout=output, stderr=subprocess.STDOUT)
    record = {"command": cmd, "cwd": str(ROOT), "exit_code": result.returncode,
              "environment": {k: env[k] for k in ["CODEX_FACTORY_REVISION", "CARGO_TARGET_DIR", "CARGO_BUILD_JOBS", "CARGO_INCREMENTAL", "TMPDIR"]}}
    (args.evidence / "build-result.json").write_text(json.dumps(record, indent=2) + "\n")
    require(result.returncode == 0, f"factory_build_failed: {args.evidence / 'build.log'}")
    bridge_cmd = ["cargo", "build", "--locked", "--offline", "--target", "x86_64-unknown-linux-gnu",
                  "--profile", "dev-small", "-p", "codex-factory", "--bin", "codex-factory-bridge"]
    with (args.evidence / "bridge-build.log").open("x") as output:
        bridge = subprocess.run(["nice", "-n", "10", *bridge_cmd], cwd=ROOT / "codex-rs",
                                env=env, stdout=output, stderr=subprocess.STDOUT)
    (args.evidence / "bridge-build-result.json").write_text(json.dumps({"command": bridge_cmd,
        "cwd": str(ROOT / "codex-rs"), "exit_code": bridge.returncode}, indent=2) + "\n")
    require(bridge.returncode == 0, "factory_bridge_build_failed")
    import shutil
    shutil.copy2(args.target.resolve() / "x86_64-unknown-linux-gnu/dev-small/codex-factory-bridge",
                 args.package / "bin/codex-factory-bridge")
    require(before == source_identity(), "factory_source_drift_during_build")
    files = {str(p.relative_to(args.package)): digest(p) for p in sorted(args.package.rglob("*")) if p.is_file()}
    manifest = {"schema": 1, "factoryId": FACTORY, "revision": args.revision, "storageEpoch": 11, "bridgeStorageEpoch": 7, "supportStorageEpoch": 1, "foremanStorageEpoch": 3, "managerStorageEpoch": 1, "nativeFactoryStorageEpoch": 2, "recordingStorageEpoch": 1, "observationStorageEpoch": 2, "networkTransportProtocol": 1,
                "source": before, "profile": "dev-small", "target": "x86_64-unknown-linux-gnu",
                "features": ["codex-install-context/factory", "other upstream defaults"],
                "publicUpdatesAllowed": False, "bridgeProtocol": 1, "files": files}
    path = args.package / "factory-package.json"
    path.write_text(json.dumps(manifest, indent=2) + "\n")
    pin = digest(path)
    verify(args.package, pin)
    (args.evidence / "manifest-pin.json").write_text(json.dumps({"package": str(args.package.resolve()), "manifestSha256": pin}, indent=2) + "\n")
    print(json.dumps({"package": str(args.package.resolve()), "manifestSha256": pin}))


def state_lock(state):
    state.mkdir(parents=True, mode=0o700, exist_ok=True)
    info = state.lstat()
    require(stat.S_ISDIR(info.st_mode) and info.st_uid == os.geteuid() and info.st_mode & 0o077 == 0, "factory_state_permissions")
    fd = os.open(state / "selection.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    fcntl.flock(fd, fcntl.LOCK_EX)
    return fd


def select_package(state, package, expected):
    fd = state_lock(state)
    try:
        manifest = verify(package, expected)
        selection = state / "selection.json"
        previous = json.loads(selection.read_text()) if selection.exists() else None
        if previous:
            require(previous["storageEpoch"] == manifest["storageEpoch"], "factory_rollback_storage_incompatible")
        record = {"package": str(Path(package).resolve()), "manifestSha256": expected,
                  "revision": manifest["revision"], "storageEpoch": manifest["storageEpoch"]}
        descriptor, temp = tempfile.mkstemp(prefix=".selection-", dir=state)
        with os.fdopen(descriptor, "w") as output:
            json.dump(record, output)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temp, selection)
        directory = os.open(state, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
        return record
    finally:
        os.close(fd)


def selected(state):
    fd = state_lock(state)
    try:
        record = json.loads((state / "selection.json").read_text())
        verify(record["package"], record["manifestSha256"])
        return record
    finally:
        os.close(fd)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    builder = sub.add_parser("build")
    for name in ["package", "evidence", "target"]:
        builder.add_argument("--" + name, type=Path, required=True)
    builder.add_argument("--revision", required=True)
    selector = sub.add_parser("select")
    selector.add_argument("--state", type=Path, required=True)
    selector.add_argument("--package", type=Path, required=True)
    selector.add_argument("--manifest-sha256", required=True)
    runner = sub.add_parser("run")
    runner.add_argument("--state", type=Path, required=True)
    runner.add_argument("args", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    if args.action == "build":
        build(args)
    elif args.action == "select":
        print(json.dumps(select_package(args.state, args.package, args.manifest_sha256)))
    else:
        selection = selected(args.state)
        forwarded = args.args[1:] if args.args[:1] == ["--"] else args.args
        os.execv(str(Path(selection["package"]) / "bin/codex"), ["codex", *forwarded])


if __name__ == "__main__":
    main()
