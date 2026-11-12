# Plan 002: upstream sync, 2026-11-12

**Status:** Final owner-seat review APPROVED; merge/push authorized. The 14 upstream-owned results remain listed as failures, not waived.

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


### Isolated recovery result

The host `/tmp` remains its original tmpfs. Each test command runs in a private mount namespace with `/srv/stacks/codex-tmp-1112/mount-tmp` bound at its own `/tmp`. TMPDIR is `/tmp` **inside that namespace**, so both environment-aware and hardcoded helpers use the isolated NVMe directory. HOME/XDG directories are also isolated there; Cargo/Rustup caches and the verified V8 inputs are retained.

The original JUnit report identified exactly 2,012 passing cases. A generated exact-case nextest filter excluded those cases, selecting only the 13 failures plus 22,676 unrun cases. The first recovery completed 263 cases (262 passed, one migration-path failure) before a controlled stop: the migration decoder rejects punctuated temporary ancestors. Its correction was solely the short namespace-local TMPDIR alias; no source was changed. The second recovery excluded all 2,274 accumulated passing cases and ran the remaining 22,427 cases. It completed in 1,171.11s including startup: **22,413 passed, 14 failed**. Across the original run and both recoveries, all 24,701 non-ignored cases are now accounted for: 24,687 passed and 14 failed. All 13 original quota cases and the migration-path case pass. There are no quota failures in either isolated recovery.

The remaining failures are three core network-approval mock-count assertions, four exec-server shell-snapshot cases, one PTY path-search case, two V8 ABI/sandbox cases, and four voice cases. No waiver is assumed. The full planned workspace proof was then started from `2551c9e97060d90ddf11101de0d06d965387f0a1` in the corrected environment. A plain source archive of pinned upstream was prepared inside the same temporary directory for an exact-case comparison; no other checkout/repository or host service was changed.


## Final proof and artifacts — review required

Fork proof, build, and mailbox commands below used runtime source `2551c9e97060d90ddf11101de0d06d965387f0a1` (Rust source tree `2358c8d378d209061800c4bb7be101f584be3b30`), upstream Rust 1.95.0, eight Cargo jobs, and eight nextest threads. No runtime source changed after the upstream merge; subsequent branch changes are plan/documentation only. No fixture login, live Forge write, remote host action, or host `/tmp` change was used.

### Full planned proof

```text
$ CARGO_BUILD_JOBS=8 just test --workspace --test-threads=8
Finished test profile in 0.68s
Summary [1279.229s] 24701 tests run:
24687 passed (17 slow, 1 flaky), 14 failed, 54 skipped
exit 100; 1281.47s including startup
$ cd codex-rs && CARGO_BUILD_JOBS=8 cargo test --locked --workspace --doc
171 doc-test targets: 2 passed, 0 failed, 3 ignored
exit 0; 33.73s
$ just test-github-scripts
Ran 76 tests in 0.563s
OK; exit 0
$ git diff --check
(no output; exit 0)
$ git merge-base --is-ancestor c0cc9fc820f62e4dbdde68214dd6d792d6c8a7ee HEAD
(no output; exit 0)
$ git diff --exit-code 7d1a35a7b9 -- AGENTS.md WORKFLOW.md
(no output; exit 0)
```

The 54 skips are nextest's configured ignored cases; non-Linux cfgs remain excluded. No snapshot was accepted. The full failed-case set exactly matches the isolated recovery's 14 cases. This is a completed full run, not the earlier interrupted run.

### Fresh comparison against pinned upstream

An unchanged `git archive` of `c0cc9fc820f62e4dbdde68214dd6d792d6c8a7ee` inside the approved temporary directory supplied the comparison tree. Eight key source/lockfile blobs were checked against that commit, including every affected test source; the comparison used `--locked`, the same toolchain, V8 pair, private TMPDIR, HOME/XDG, and Git configuration. Run only these exact 14 failed binary/test pairs, OR-combined with nextest `binary_id(=...) and test(...)` predicates:

```text
$ CARGO_BUILD_JOBS=8 just test --locked -p codex-core -p codex-exec-server -p codex-utils-pty -p codex-v8-poc -p codex-voice-host --test-threads=8 -E <the exact 14 binary/test pairs in the table>
Finished test profile in 4m 38s
Starting 14 tests across 5 binaries
Summary [90.389s] 14 tests run: 0 passed, 14 failed, 3318 skipped
exit 100; 369.37s including build
PASS: identical 14 failed cases; 14/14 normalized failure signatures match
```

The comparison skips are unselected cases, not passes. Signature comparison checks exit type, primary failure location, and meaningful assertion/error content; it normalizes source/temporary paths, PIDs and ephemeral ports, and omits volatile PTY child-handle details. It does not compare full synthetic request dumps.

| Binary / exact test | Failure on both trees | Comparison |
| --- | --- | --- |
| `codex-core::all` / `suite::network_approval::user_network_approval_once_session_and_denial_semantics::canonical_requirements` | Mock expects 2 matching requests; receives 1. | MATCH |
| `codex-core::all` / `suite::network_approval::user_network_approval_once_session_and_denial_semantics::legacy_requirements` | Mock expects 2 matching requests; receives 1. | MATCH |
| `codex-core::all` / `suite::network_approval::user_network_approval_once_session_and_denial_semantics::proxy_requirements` | Mock expects 2 matching requests; receives 1. | MATCH |
| `codex-exec-server::exec_process` / `shell_snapshot::shell_snapshot_concurrent_replays_keep_independent_readers::blocked_descriptor_path` | Websocket recovery times out after 45 seconds. | MATCH |
| `codex-exec-server::exec_process` / `shell_snapshot_v2_capture_failure_falls_back_and_retries::remote_pipe_recovery` | Shell snapshot differs: `xxxx` versus `xx`. | MATCH |
| `codex-exec-server::exec_process` / `shell_snapshot_v2_capture_failure_falls_back_and_retries::remote_tty_recovery` | Shell snapshot differs: `xxxx` versus `xx`. | MATCH |
| `codex-exec-server::exec_process` / `shell_snapshot_v2_filters_profile_exports_and_stays_in_memory::remote_sandbox` | Profile/sandbox stdout and exit-status snapshot differ. | MATCH |
| `codex-utils-pty` / `child_command::linux_tests::path_search_stops_at_invalid_candidates` | Invalid first PATH candidate starts a child instead of stopping lookup. | MATCH |
| `codex-v8-poc` / `tests::reads_typed_array_contents_with_the_compiled_binding_size` | Typed-array byte-length assertion reports 0. | MATCH |
| `codex-v8-poc` / `tests::sandbox_feature_matches_linked_v8` | Linked V8 sandbox is true; prototype crate feature is false. | MATCH |
| `codex-voice-host::bin/codex-voice-host` / `devices::playout::tests::jitter_buffer_preserves_packets_delivered_in_a_burst` | Jitter buffer unavailable. | MATCH |
| `codex-voice-host::bin/codex-voice-host` / `devices::playout::tests::real_decoder_renders_current_rtp_and_rejects_pre_epoch_arrivals` | Jitter buffer unavailable. | MATCH |
| `codex-voice-host::bin/codex-voice-host` / `transport::tests::negotiates_after_early_packet_loss_and_slow_tcp_connect` | Remote-candidate assertion fails. | MATCH |
| `codex-voice-host::bin/codex-voice-host` / `transport::tests::negotiates_real_ordered_channel_over_udp_and_tcp` | Remote-candidate assertion fails. | MATCH |

**These 14 failures are freshly reproduced on current pinned upstream; none is a new, observed fork-only failure. They remain failures. No plan-001 exception or new waiver is assumed.** WORKFLOW.md requires passing touched-crate tests, and the approved plan requires final review before main changes. The subsequent owner-seat approval below authorizes merge/push while retaining all 14 results as upstream failures, not waived results.

### Release build and mailbox qualification

```text
$ cd codex-rs
$ CARGO_BUILD_JOBS=8 CODEX_FACTORY_REVISION=2551c9e97060d90ddf11101de0d06d965387f0a1 cargo build --locked --release --target x86_64-unknown-linux-gnu --target-dir target/1112-release -p codex-cli -p codex-factory --features codex-install-context/factory --bin codex --bin codex-factory-bridge
Finished release profile [optimized + debuginfo] in 14m 58s
exit 0; 898.59s
$ # Same packages/bins/target/revision; --profile dev-small, target/1112-faults;
$ # features codex-install-context/factory,codex-app-server/factory-fixture-faults,codex-factory/fixture-faults; CARGO_INCREMENTAL=0
Finished dev-small profile [unoptimized] in 2m 30s
exit 0; 150.33s
```

Artifacts are `/srv/stacks/codex/dist/1112/codex` and `codex-factory-bridge`, plus `MANIFEST.json`, `SHA256SUMS`, and the source-cited `INTERFACE.md`. Both production executables target **x86_64-unknown-linux-gnu**, require **GLIBC_2.39**, and import **OPENSSL_3.0.0** symbols. The CLI additionally needs `libbz2.so.1.0`; the manifest lists all required library/version names. No Ubuntu-host compatibility test or installation is claimed. The production build has the factory install-context feature and no fault features; factory-info revision equals the full source commit above and `publicUpdatesAllowed=false`.

- `codex`: `codex-cli 0.0.0`, 1631283872 bytes, SHA-256 `2b6ef470061662b04689a31c5d87158bcb385a0b4d28d23882c5af27ba176a8d`.
- `codex-factory-bridge`: `codex-factory 0.0.0`, 47380888 bytes, SHA-256 `599a1bb17a7b78bd0e20cae1cc6cbdd5f148db57107b1aafda0eac339203fb08`.

Each qualifier ran as `python3 scripts/factory/qualify_<lane>.py --binary <variant>/codex-factory-bridge --evidence <isolated output>`, adding `--cli <same variant>/codex` for native, lifecycle, and reconciliation. Production variants use the actual dist binaries; fault variants use the separate dev-small pair. Two existing checks beyond the initial five strengthen the requested mailbox proof: lifecycle verifies actual native completion/recording and bridge restart; reconciliation verifies five native admission/receipt crash boundaries. No qualification script or runtime source was edited.

| Existing qualifier | Binary variant | Result |
| --- | --- | --- |
| `contract` | production | PASS: 64 checks; exit 0; 0.21s |
| `credentials` | production | PASS: 7 checks; exit 0; 5.73s |
| `recovery` | production | PASS: 9 checks; exit 0; 18.16s |
| `native` | production | PASS: 5 checks; exit 0; 2.32s |
| `lifecycle` | production | PASS: 4 checks; exit 0; 2.23s |
| `durability` | fault-injection | PASS: 7 checks; exit 0; 1.71s |
| `reconciliation` | fault-injection | PASS: 5 native crash lanes; exit 0; 10.68s |

The seven programs pass, including all 96 counted contract/credential/recovery/native/lifecycle/durability checks and all five additional native crash/reconciliation lanes. Authenticated endpoints use separate fixture UIDs. These exercise receipts through completed, replay/idempotency, actual process deaths before commit/ack, no reinjection after native restart, queue-only delivery, idle wake/busy defer, terminal history, and fenced restore. Tests use synthetic credentials/model responses; no real account login or second-physical-host claim.

`INTERFACE.md` covers exact send/receive/ack/status/retry/replay/backup/restore calls, receipt writers/order/idempotency, endpoint versus thread addressing, explicit wake admission/defer reasons, and native history reconciliation. All 71 file:line citation locations were checked. The manifest includes full source/upstream commits, compiler/Cargo/binary versions, ABI requirements, binary hashes, and truthful validation results. SHA256SUMS covers both production binaries, the interface, and the manifest.

### Final scope and cleanup

`git diff main...HEAD --stat` (including final documentation): **834 files changed, 28457 insertions(+), 6326 deletions(-)**. Scope check PASS: all 834 paths are in the pinned upstream delta or the three named documentation/changelog files; no unexpected untracked files. Original fork-only preservation PASS: 99 byte-identical files plus the one planned changelog entry.

Only the pinned upstream import plus `plans/002-upstream-sync-1112.md`, `docs/fork-mailbox.md`, and `CHANGELOG-FORK.md` may differ from original main. AGENTS.md, WORKFLOW.md, existing plans and all fork-only runtime sources remain unchanged. The changelog's single planned append is the only alteration to an original fork-only file. Dist and build artifacts remain ignored. Runtime `codex-rs` tree identity is checked against the tested source commit after documentation.

Removed `/srv/stacks/codex-tmp-1112` after recording all results. Verified zero remaining processes in task-owned temporary mount namespaces. Cleanup was limited to that directory; the host `/tmp` remains its original tmpfs and the release artifacts remain in dist/1112.

```text
$ cd dist/1112 && sha256sum -c SHA256SUMS
codex: OK
codex-factory-bridge: OK
INTERFACE.md: OK
MANIFEST.json: OK
exit 0
$ git diff --exit-code 2551c9e970 -- codex-rs
(no output; exit 0)
```

At final proof capture, main was `7d1a35a7b98ad50d7fdd9215f406b430914b11b9` and no main merge or push had occurred. The reviewer subsequently approved the reviewed artifacts, file scope, and merge as recorded below. The final `FORK 002 DONE <main sha>` marker follows successful --no-ff main merge and origin/main push.


## Final owner-seat approval, 2026-11-12

Owner seat: **APPROVED**, in the owner's place (rows `9b02c08b`, `a9921e09`). All 14 failures reproduce on pinned upstream `c0cc9fc820`; none is in the fork's factory code. They stay listed as failures in this plan, **not waived**. The owner seat explicitly directed merging `sync/upstream-1112` into main with `--no-ff` and pushing `origin main`.

`dist/1112` is frozen at the reviewed bytes and metadata by explicit owner instruction. Its pre-approval manifest remains unchanged; this plan records the later approval. Before merge, origin/main still equals original main `7d1a35a7b98ad50d7fdd9215f406b430914b11b9`, runtime source still matches tested `2551c9e970`, and AGENTS.md/WORKFLOW.md remain unchanged. The only change after the reviewed head `c472499d3e` is this approval record and status update. No tests are reclassified as passed.
