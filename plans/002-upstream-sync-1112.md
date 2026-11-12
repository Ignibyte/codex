# Plan 002: upstream sync, 2026-11-12

**Status:** Approved by the owner-seat reviewer, 2026-11-12; approval line below.

**Goal:** Bring in the latest upstream Codex changes while preserving persistent agents, roles, and authenticated cross-account mailbox communication.

**Smallest version:** Merge pinned `upstream/main` `c0cc9fc820f62e4dbdde68214dd6d792d6c8a7ee` into fork main `7d1a35a7b98ad50d7fdd9215f406b430914b11b9`: 74 upstream commits, 831 changed paths since base `627b579219d492b5445d6d433d3110eb53e507dc`. After plan approval, create `sync/upstream-1112` from main (the brief's explicit branch-name override), then `git merge --no-ff --no-commit c0cc9fc820f62e4dbdde68214dd6d792d6c8a7ee`. Preserve factory/bridge, app-server hooks, CLI additions, roles, and fork-only files. Reconcile both preview conflicts; regenerate protocol exports with `just write-app-server-schema --experimental` and `just write-app-server-schema`, never hand-merge compressed exports. Commit in small steps; finish validation and documentation before requesting final review.

**Will not build:** Features, subsystems, Forge workflow, tickets, backlogs, evidence folders, deployment, installation, or service changes. No other repo, real login, remote-host action, or secret output. Temporary qualification fixtures are disposable; this plan is the proof record. Stop and ask the owner when blocked.

**Files:** Import exactly the upstream tracked-file delta enumerated by `git diff --name-only 627b579219d492b5445d6d433d3110eb53e507dc c0cc9fc820f62e4dbdde68214dd6d792d6c8a7ee`; preserve `AGENTS.md`, `WORKFLOW.md`, and existing plans. Preview conflicts: `codex-rs/core/src/client.rs` and `codex-rs/app-server-protocol/schema/precomputed/app-server-exports-experimental.json.zst`. Regenerate `codex-rs/Cargo.lock` only if needed, using Cargo. Permit schema-generator outputs required by merged types. Add/update only this plan, `CHANGELOG-FORK.md` (dated plan-002 entry), and `docs/fork-mailbox.md` beyond that import. Deliver ignored artifacts `dist/1112/codex`, `codex-factory-bridge`, `MANIFEST.json`, `SHA256SUMS`, and `INTERFACE.md`. Any further source file requires an amended plan and approval.

**Proof:** Run after approval, using eight build jobs and upstream's toolchain. Workspace tests cover every touched crate and the retained factory/roles integration; failures block completion, with no automatic carryover of plan-001 exceptions.

```sh
CARGO_BUILD_JOBS=8 just test --workspace --test-threads=8
(cd codex-rs && CARGO_BUILD_JOBS=8 cargo test --locked --workspace --doc)
just test-github-scripts
git diff --check
git merge-base --is-ancestor c0cc9fc820f62e4dbdde68214dd6d792d6c8a7ee HEAD
git diff --exit-code 7d1a35a7b9 -- AGENTS.md WORKFLOW.md
```

Build the release pair with `CARGO_BUILD_JOBS=8 CODEX_FACTORY_REVISION="$(git rev-parse HEAD)" cargo build --locked --release --target x86_64-unknown-linux-gnu --target-dir target/1112-release -p codex-cli -p codex-factory --features codex-install-context/factory --bin codex --bin codex-factory-bridge` from `codex-rs`; copy both binaries to `dist/1112/`. This is a **glibc** target: record actual required GLIBC versions, architecture, toolchain, binary versions, factory identity, full source commit, and SHA-256 digests in the manifest; do not assume Ubuntu ABI compatibility. Build a separate test pair with the same command, replacing `--release` with `--profile dev-small`, target directory with `target/1112-faults`, and features with `codex-install-context/factory,codex-app-server/factory-fixture-faults,codex-factory/fixture-faults`.

With `fork_dist=/srv/stacks/codex/dist/1112`, `fork_fault=/srv/stacks/codex/codex-rs/target/1112-faults/x86_64-unknown-linux-gnu/dev-small`, and `fork_tmp=$(mktemp -d)`, run:

```sh
for lane in contract credentials recovery; do
  python3 scripts/factory/qualify_${lane}.py --binary "$fork_dist/codex-factory-bridge" --evidence "$fork_tmp/$lane" || exit 1
done
python3 scripts/factory/qualify_native.py --binary "$fork_dist/codex-factory-bridge" --cli "$fork_dist/codex" --evidence "$fork_tmp/native"
python3 scripts/factory/qualify_durability.py --binary "$fork_fault/codex-factory-bridge" --evidence "$fork_tmp/durability"
(cd dist/1112 && sha256sum -c SHA256SUMS)
```

These exercise bridge serve, two authenticated endpoints, send/receive/ack, receipt authorities, session wake/defer, retries, and real process-death recovery without loss or duplication. Record command outputs and tested commit under **Proof result**. Derive `INTERFACE.md` from code with `file:line` citations: exact send/receive/ack/recovery calls, receipt/idempotency rules, endpoint/thread addressing, and session wake behavior; reflect user-facing instructions in `docs/fork-mailbox.md`.

**Final gate:** Present the validated head, artifact manifest, and `git diff main...HEAD --stat` for review. Only after the reviewer's **APPROVED**, merge with `--no-ff` into main, push `origin main`, delete the sync branch per workflow, and print `FORK 002 DONE <main sha>`. Stop here for plan review first.

Reviewer (Claude owner seat): APPROVED 2026-11-12 08:12 CST, in the owner's place (owner row 9b02c08b). One change: use CARGO_BUILD_JOBS=8 and --test-threads=8, not 2. z690 has 24 cores and the owner wants this soon; keep it to 8 so his workstation stays usable. Make sure step 5 of the brief (dist/1112/INTERFACE.md, the bridge calls, receipts and idempotency, addressing and wake, with file:line) is produced. Then go.


## Proof result — stopped for owner decision, 2026-11-12

Code head: `f74eee01268b40954afd6c133937a788187ab93e`, branch `sync/upstream-1112`; main remains `7d1a35a7b98ad50d7fdd9215f406b430914b11b9`. The two preview conflicts were the only conflicts. Retained the fork's outgoing legacy mailbox-ID translation followed by upstream's allocation-aware ID check; regenerated the experimental protocol export from merged sources. Cargo merged the lockfile without manual editing. Scope check found no unapproved paths; all 100 fork-only files from the original main remain byte-identical.

```text
$ CARGO_BUILD_JOBS=8 just write-app-server-schema --experimental
Finished test profile in 57.25s
schema_fixtures_tests::write_schema_fixtures_from_env ... ok
1 passed; 0 failed (4.73s); exit 0
$ CARGO_BUILD_JOBS=8 just write-app-server-schema
Finished test profile in 11.15s
schema_fixtures_tests::write_schema_fixtures_from_env ... ok
1 passed; 0 failed (4.78s)
SDK: 1 file reformatted; Done.; exit 0
$ just test-github-scripts
Ran 76 tests in 0.792s
OK; exit 0
$ git diff --check
(no output; exit 0)
$ git merge-base --is-ancestor c0cc9fc820f62e4dbdde68214dd6d792d6c8a7ee HEAD
(no output; exit 0)
$ git diff --exit-code 7d1a35a7b9 -- AGENTS.md WORKFLOW.md
(no output; exit 0)
```

Workspace build/test used upstream Rust 1.95.0, `CARGO_BUILD_JOBS=8`, `INSTA_UPDATE=no`, and upstream's checksum-verified Codex V8 archive/binding pair from `scripts/codex_package/v8.py::resolve_codex_v8_cargo_env`. `TMPDIR` was already `/mnt/fast/tmp`. Release/fault target directories were prepared as aliases to the existing `/mnt/fast/target` cache; their release and dev-small profiles remain distinct. No release or fault build was started.

```text
$ CARGO_BUILD_JOBS=8 just test --workspace --test-threads=8
Finished test profile in 9m 11s
Starting 24701 tests across 329 binaries (54 tests skipped)
Error: failed to copy executable fixture /mnt/fast/target/debug/codex to
/tmp/<private fixture>/packages/standalone/releases/0.0.0-x86_64-unknown-linux-musl/bin/codex:
cp: error writing ...: Disk quota exceeded
Cancelling due to interrupt: 4 tests still running
Summary [107.596s] 2025/24701 tests run:
2012 passed (2 slow, 1 flaky), 13 failed, 54 skipped
22676/24701 tests were not run due to interrupt
exit 100; total elapsed 663.81s including compilation
```

All 13 final failures occurred while copying the CLI fixture into hardcoded `/tmp`, before their intended checks:

```text
codex-cli::app_server_daemon bootstrap_updater_is_ready_in_deep_codex_home
codex-cli::app_server_daemon bootstrap_reports_updater_bind_failure
codex-cli::app_server_daemon explicit_restart_applies_feature_overrides_and_preserves_saved_settings
codex-cli::app_server_daemon managed_daemon_restarts_from_deleted_working_directory
codex-cli::app_server_daemon managed_identity_survives_locale_and_timezone_changes
codex-cli::app_server_daemon managed_starts_ensure_one_updater_and_recover_a_missing_one
codex-cli::app_server_daemon manual_update_rejects_an_unowned_installation
codex-cli::app_server_daemon package_ownership_check_does_not_start_an_updater
codex-cli::app_server_daemon restart_applies_saved_updater_preference
codex-cli::app_server_daemon managed_start_keeps_updater_on_marker_mismatch_but_stops_it_for_pin
codex-cli::app_server_daemon unmanaged_app_server_does_not_launch_updater
codex-cli::app_server_daemon managed_start_succeeds_when_updater_record_is_invalid
codex-cli::app_server_daemon start_applies_feature_overrides_only_when_launching
```

The immediate source is `codex-rs/cli/tests/app_server_daemon/support.rs:25`, which uses `tempdir_in("/tmp")`; the helper at lines 53–54 cannot hard-link from the NVMe filesystem and falls back to copying. This is the same failure category recorded in plan 001, but no current-upstream comparison or waiver is claimed. Sent SIGINT only to this run's verified nextest PID and waited for its summary/exit. The approved plan carries no inherited exceptions; per WORKFLOW.md, stop here and ask the owner how to resolve the host quota blocker.

`dist/1112/INTERFACE.md` is written from the merged source with file:line citations for transport, send/receive/ack/status/recovery, receipts, idempotency, addressing, and explicit session wake/reconciliation. Its source locations were checked. `docs/fork-mailbox.md` is drafted. No doc tests, release builds, mailbox qualifications, binary manifest/checksums, changelog completion entry, main merge, or push has been performed. None is claimed as passing.

Additional review fact: the imported upstream delta contains trailing whitespace in two TUI snapshots (`offline_delete_after_wheel.snap:8` and `owned_startup_layout.snap:17`); both complete blobs match pinned upstream. The planned ordinary `git diff --check` passes. No snapshot bytes were changed and no exception was assumed.


## Owner-approved recovery, 2026-11-12

Owner seat approved rerunning the failed/unrun cases, then the full planned proof, release build, and mailbox qualification. Use `/srv/stacks/codex-tmp-1112` on NVMe for TMPDIR and disposable fixtures; leave the host `/tmp` unchanged, delete nothing outside that directory, and remove the directory when done. The approved private mount namespace maps only the test process's `/tmp` to a subdirectory there, because the failed helpers explicitly ignore TMPDIR. A fresh HOME/XDG configuration and isolated Git configuration keep owner settings out of fixture expectations. Keep eight build jobs and eight test threads. Recover the exact passed-case set from the original JUnit report rather than guessing which tests ran. No source change or failure waiver is authorized by this recovery approval.
