# Plan 002: upstream sync, 2026-11-12

**Status:** Upstream sync merged and pushed as `163ad7adcf2145656c8d0bb13191731993f7047c`. The owner approved the release-version follow-up merge in `/tmp/fork-brief-uid.txt` (2026-11-12 10:58 CST). The 14 upstream-owned results remain listed as failures, not waived.

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

## Urgent release-version follow-up, 2026-11-12

**Authorization/status:** The owner's `/tmp/fork-brief-version.txt` explicitly authorizes this small plan-002 follow-up before plan 003, without a new plan. Implementation is authorized; a new owner **APPROVED** is required before its `--no-ff` main merge. Branch: `plan/002-release-version`, from main `163ad7adcf2145656c8d0bb13191731993f7047c`.

**Goal / smallest version:** Replace the development version `0.0.0` with `0.167.2+ignibyte.1112b`, rebuild the release pair into `dist/1112b`, and prove GPT-6.1 Terra access. Upstream's `rust-v0.167.2` tag (2026-11-11 20:03:57 UTC; commit `ce21072c90d32c40af7a55b60b2fb35c84a43a6a`) predates pinned upstream `c0cc9fc820` (2026-11-12 05:21:30 UTC). Its release commit changes only the workspace version. `.github/workflows/rust-release.yml:34` requires the tag and Cargo version to agree, and line 610 explains refreshing workspace versions in the lockfile with `cargo update --workspace`. Use this stable release base, with fork build metadata. `models-manager/src/lib.rs:24` sends only major/minor/patch to model discovery/cache, so that path reports `0.167.2` while `--version` identifies the fork. The live proof will check backend acceptance.

**Files:** Only `codex-rs/Cargo.toml`, Cargo-regenerated `codex-rs/Cargo.lock`, this plan, `docs/fork-mailbox.md`, and one additional plan-002 line in `CHANGELOG-FORK.md`. Ignored artifacts: `dist/1112b/{codex,codex-factory-bridge,MANIFEST.json,SHA256SUMS,INTERFACE.md}`. Leave all bytes and metadata in `dist/1112` unchanged. No runtime source, deployment, new subsystem, full workspace rerun, or plan-003 work. The 14 recorded upstream-owned failures remain failures.

**Proof:** Use Rust 1.95.0, eight build jobs, the same GNU target and factory feature as the original release. Build both release executables with `CODEX_FACTORY_REVISION=<source commit>` and `cargo build --locked --release --target x86_64-unknown-linux-gnu --target-dir target/1112b-release -p codex-cli -p codex-factory --features codex-install-context/factory --bin codex --bin codex-factory-bridge`. Build the separate fault pair as before, using `--profile dev-small`, `target/1112b-faults`, and the two fixture-fault features. Run all seven existing mailbox qualifiers listed above against the corresponding new binaries, both `--version` commands, factory-info, artifact checksums, and `git diff --check`. Per the owner's explicit override, do not repeat workspace tests. Use isolated NVMe temporary storage, a private `/tmp` mount for existing fixtures, and delete only the task's temporary directory after recording results.

For the live proof, use the available normal z690 ChatGPT credential record through a private, mode-0700 `CODEX_HOME`; never print or alter the owner's credentials/config/cache. Run one tiny `codex exec --sandbox read-only --skip-git-repo-check -c 'model="gpt-6.1-terra"' -c 'model_reasoning_effort="high"'` turn from a disposable directory, asking for a fixed reply and no tools. Record completion and the fetched numeric cache version/model availability without credential or account output. If the login is unusable, report the exact limitation for the owner's Forge proof. Document a dedicated fork `CODEX_HOME` for plan 003 so stock and fork caches/settings/sessions are separate.

**Final gate:** Record actual results and the tested source commit here; present the artifact path and branch for review, then print `FORK VERSION FIX READY /srv/stacks/codex/dist/1112b`. Merge only after a new owner **APPROVED**. Read the setup brief in full after the version fix, before resuming plan 003.

### Release-version proof result

Tested source: **`6cb7fd718329da87bfd1dcf4c10536cf82b1c367`**. Both builds used eight jobs, Rust/Cargo 1.95.0, the same checksum-verified V8 archive/bindings and GNU target as the original sync. `cargo update --workspace --offline` stamped 175 workspace packages; a parsed comparison confirmed all external package records are unchanged. The highest stable remote `rust-v*` tag is `rust-v0.167.2`; newer 0.168/0.169 tags are prereleases. Build metadata remains visible in the CLI, and the live backend accepts it.

```text
$ cargo build --locked --release --target x86_64-unknown-linux-gnu --target-dir target/1112b-release -p codex-cli -p codex-factory --features codex-install-context/factory --bin codex --bin codex-factory-bridge
Finished release profile [optimized + debuginfo] in 13m 18s
exit 0; 798.28s
$ # Matching fault build: --profile dev-small, target/1112b-faults,
$ # features codex-install-context/factory,codex-app-server/factory-fixture-faults,codex-factory/fixture-faults; CARGO_INCREMENTAL=0
Finished dev-small profile [unoptimized] in 2m 28s
exit 0; 148.39s
$ dist/1112b/codex --version
codex-cli 0.167.2+ignibyte.1112b
$ dist/1112b/codex-factory-bridge --version
codex-factory 0.167.2+ignibyte.1112b
```

Factory-info reports `ignibyte-codex-factory`, revision `6cb7fd718329da87bfd1dcf4c10536cf82b1c367`, version `0.167.2+ignibyte.1112b`, and `publicUpdatesAllowed=false`. The new manifest records actual ELF dependencies, source/tree identities, versions, hashes, qualification and live-proof results. Both binaries still require GLIBC_2.39 and OPENSSL_3.0.0; the CLI also requires libbz2.so.1.0. No installation or remote-host proof is claimed.

All seven existing qualifier commands were rerun as specified above, with production paths replaced by `dist/1112b` and fault paths by `target/1112b-faults/x86_64-unknown-linux-gnu/dev-small`. A private mount namespace bound `/srv/stacks/codex-version-tmp-1112b/mount-tmp` at its own `/tmp`, with TMPDIR=/tmp and isolated HOME/XDG/Git settings. Fixture UIDs remained separated. Results:

| Qualifier | Variant | Output / exit / elapsed |
| --- | --- | --- |
| contract | production | passed=true; 64 checks; 0; 0.21s |
| credentials | production | passed=true; 7 checks; 0; 5.96s |
| recovery | production | passed=true; 9 checks; 0; 20.30s |
| native | production | passed=true; 5 checks; 0; 2.15s |
| lifecycle | production | passed=true; 4 checks; 0; 2.24s |
| durability | fault-injection | passed=true; 7 checks; 0; 1.69s |
| reconciliation | fault-injection | passed=true; 5 crash lanes; 0; 10.62s |

All 96 counted checks and all five reconciliation lanes pass. Per the owner's explicit instruction, no full workspace/doc/GitHub suite rerun was performed for this version-only follow-up. The original 14 upstream-owned workspace failures remain **failures**, not waived.

The z690 live proof used the available normal ChatGPT credential record through a private mode-0700 CODEX_HOME and mode-0600 credential copy, with no owner config copied. The command used `CODEX_HOME=<private home>` and:

```sh
/srv/stacks/codex/dist/1112b/codex exec --sandbox read-only --skip-git-repo-check --ephemeral --ignore-user-config --json -c 'model="gpt-6.1-terra"' -c 'model_reasoning_effort="high"' -c 'cli_auth_credentials_store="file"' 'Reply with exactly FORK_VERSION_OK. Do not use any tools or access any files.'
```

Output: `turn.completed`; final reply `FORK_VERSION_OK`; **exit 0; 3.77s; zero tool calls**. The fetched private cache has `client_version="0.167.2"` and contains both `gpt-6.1-terra` and `gpt-6.1-sol`. Before/after hashes of the owner's auth.json, config.toml and models_cache.json are unchanged. One nonfatal diagnostic reported that `codex-code-mode-host` is absent from the requested two-binary package; the read-only model turn completed successfully. This proof does not qualify code-mode tool execution. Carry that packaging fact into subsequent setup/spec planning; no extra helper or runtime change is included here.

SHA-256: `codex` = `96cde4cac686d8f297fa6ebdc43fc2610a1a535ed1f63497d94e2a09e8da62e1`; `codex-factory-bridge` = `04eb9ac93dd83a9b51a1e414bd50db53d56cd7050ff2cdfb4624f2cff17ecc64`.

```text
$ cd dist/1112b && sha256sum -c SHA256SUMS
codex: OK
codex-factory-bridge: OK
INTERFACE.md: OK
MANIFEST.json: OK
exit 0
$ git diff --exit-code 6cb7fd7183 -- codex-rs
(no output; exit 0)
$ git diff --check
(no output; exit 0)
```

Frozen `dist/1112` verification PASS: all five file hashes, sizes, modes and modification times are unchanged. The follow-up diff names only its five allowed tracked files. Documentation updates do not change the tested Rust tree. No main merge or push of this follow-up has occurred; final owner **APPROVED** remains required.

Cleanup PASS: removed `/srv/stacks/codex-version-tmp-1112b`, including its private credential copy and fixture output, after recording results. Zero task namespace processes remained. Deleted nothing outside that directory; the host `/tmp` remains its original tmpfs.

### Subsequent setup brief — blocked on the requested UID layout

Read `/tmp/fork-brief-setup.txt`, `/tmp/fsm-bridge-protocol.md`, and `/tmp/fsm-bridge.routes.json.example` in full. The setup correction places spec-manager and foreman-bot on Forge and pilot-seat on dev-3, with all sessions and the requested bridge/proxy under `ai`. Source inspection and two disposable probes against the new production bridge establish a concrete conflict with that requested layout:

```text
$ codex-factory-bridge bootstrap --state <private-temp>/service --grant <private-temp>/grant.json --output <private-temp>/bootstrap
# grant.agentUid equals the bridge operator's effective UID
exit 1: Error: agent_service_uid_boundary
$ codex-factory-bridge connect-host --state <private-temp>/proxy --socket <private-temp>/remote.sock --config <private-temp>/proxy.json
# allowedAgentUids contains the proxy's own effective UID
exit 1: Error: network_agent_uid_invalid
```

`codex-rs/factory/src/service.rs:836` requires a local seat UID different from the service UID; the remote bootstrap also requires the remote seat UID to differ from its admitted proxy UID at line 863. `codex-rs/factory/src/network.rs:142` rejects allowed endpoint UIDs equal to the proxy UID. These are intentional existing boundaries, not missing shell arguments. In addition, `codex-rs/app-server/src/request_processors/factory.rs:65` requires service-owned binding files: a distinct-UID endpoint cannot read a mode-0600 service-owned file. Existing native fixtures use mode 0640 with a readable group, while credentials remain 0600.

Neither probe created a registry, service key, credential, listener, or network connection. Removed only its disposable `/srv/stacks/codex-setup-probe-*` directory. No live host was contacted. Per AGENTS.md's instruction to stop and ask when stuck, setup implementation and plan 003 are paused for an owner decision: use the existing separate-UID service/proxy contract (including appropriate binding readability), or explicitly scope/approve a runtime change for trusted shared-UID operation. The requested tested setup runbook is not yet complete, and no setup-ready marker is claimed. The completed version-fix artifact and its proof remain ready for the separately required merge approval.

### Owner approval and UID decision, 2026-11-12 10:58 CST

The owner explicitly **APPROVED** the version fix in `/tmp/fork-brief-uid.txt`: merge with `--no-ff`, push main, and print `FORK VERSION MERGED <main sha>`. `dist/1112b` is the fleet installation build. Rechecked its four SHA256SUMS entries and the unchanged tested Rust tree before merge; only the five named tracked files differ from main. No test failure is reclassified or waived.

The same brief resolves the setup blocker: retain the existing protocol, with a dedicated `fleet-bridge` service/proxy UID on each host and `ai` endpoint UIDs. Ops creates users/groups through ucs-ops; document system units with `User=fleet-bridge` and service-owned 0640 bindings where endpoint readability is needed. No runtime change is authorized. Setup will continue after the version merge, before plan 003.

## Urgent complete-package fix — owner-authorized 2026-11-12

**Goal / authorization:** `/tmp/fork-brief-hosts.txt`, including its 11:09 addendum and owner row `beef7305`, explicitly orders the whole upstream Codex program plus fork code, mechanically enforced, ahead of setup and plan 003. The prior no-tool smoke proved model access only; it did not qualify a usable fleet build. Branch `plan/002-complete-package` starts at main `413612082ee930c005ca805452d52c202f5debbb`.

**Smallest version:** One `scripts/factory/package.py` entry point builds upstream's current Linux package contract and adds the factory bridge. A standalone `check_package.py` reads `.github/actions/build-codex-packages/action.yml`, `.github/scripts/build-codex-package-archive.sh` and the upstream canonical layout builder; missing or unknown requirements fail closed. Reuse that builder for layout, not an independently maintained copy. For the selected `x86_64-unknown-linux-gnu` target, the upstream file union is `codex-package.json`, `bin/codex`, `bin/codex-app-server`, `bin/codex-code-mode-host`, `bin/codex-exec-server`, `codex-resources/bwrap`, and `codex-path/rg`. Add `bin/codex-factory-bridge`, interface/manifest/checksums. No GNU voice resources are selected by the upstream archive script (its voice assembly is restricted to Darwin, Linux musl and Windows). Changes in upstream requirements must turn the check red rather than silently omit a file.

Build from the unchanged Rust source tree at `6cb7fd718329da87bfd1dcf4c10536cf82b1c367`, including `CODEX_FACTORY_REVISION` set to that commit. Preserve the existing CLI bytes if compatible with the upstream release steps; otherwise record its new hash and version. Follow Linux's build-bwrap/strip/hash-before-runtime-build step, so the bundled bwrap digest is embedded. Use eight jobs and the verified V8 pair. Publish only a fresh `dist/1112c` through the package script; freeze both 1112 and 1112b. Do not change Rust, accounts, live hosts, services, setup artifacts or plan 003 during this prerequisite.

**Files:** Add `scripts/factory/package.py`, `scripts/factory/check_package.py`, `scripts/factory/test_check_package.py`, and `scripts/factory/qualify_packaged_tools.py`; update this plan, `docs/fork-mailbox.md`, `WORKFLOW.md` with the permanent packaging rule, and one CHANGELOG-FORK.md line. Ignored output is the complete `dist/1112c` package. No other tracked files.

**Proof:** Show the standalone completeness check FAIL on frozen dist/1112b and PASS on the new package; run its focused regression tests and relevant existing upstream package-builder tests. From the installed package path, use the owner's exact no-daemon/no-approval/ignore-config/ignore-rules/strict-config command line with GPT-6.1 Terra high for (a) an actual shell read under read-only, (b) an actual file write under workspace-write, and (c) a local stdio MCP tool if available. Use private CODEX_HOME/auth/config and isolated NVMe directories, with actual tool-event/output and filesystem assertions; do not count a no-tool response as a pass. Rerun all seven existing mailbox qualifiers, with production binaries from the new package and the matching separate fault pair. Record every result and any limitation in MANIFEST.json and this plan, including complete upstream file presence, ELF requirements, source identities, hashes and CLI version. Never print real credentials. The 14 original upstream-owned workspace failures stay failures; no full workspace rerun is requested. Finish with `FORK HOSTS READY <folder> <codex sha256 first 12> <code-mode-host sha256 first 12>`, then resume setup and plan 003.

### Complete-package proof result

Runtime source remains **`6cb7fd718329da87bfd1dcf4c10536cf82b1c367`**, with no Rust edits. The package build used eight jobs, Rust/Cargo 1.95.0, factory install context, STABLE_GIT_COMMIT/CODEX_FACTORY_REVISION set to that source, and the same verified V8 pair. The package entry point is the only dist creator; it invokes upstream's canonical builder and adds the full action binary union plus the bridge. Linux bwrap was built, stripped, hashed, and embedded before building the runtimes, following upstream's Cargo release action.

```text
$ python3 scripts/factory/package.py --output dist/1112c --source-commit 6cb7fd718329da87bfd1dcf4c10536cf82b1c367 --target x86_64-unknown-linux-gnu --target-dir /mnt/fast/target --work-dir <isolated fresh directory>
Complete package created: /srv/stacks/codex/dist/1112c
exit 0; 1019.56s
$ python3 scripts/factory/check_package.py dist/1112b
passed=false; missing canonical bin/codex, bin/codex-app-server,
bin/codex-code-mode-host, bin/codex-exec-server, codex-package.json,
codex-path/rg, codex-resources/bwrap, bin/codex-factory-bridge
exit 1 (expected negative control; flat two-binary layout is incomplete)
$ python3 scripts/factory/check_package.py dist/1112c
passed=true; missing=[]; notExecutable=[]; errors=[]; exit 0
$ python3 -B -m unittest discover -s scripts/factory -p test_check_package.py -v
Ran 6 tests; OK
$ CODEX_REPO_ROOT=/srv/stacks/codex python3 -B -m unittest discover -s scripts/codex_package -p 'test_*.py'
Ran 27 tests; OK
```

The gate derives the current file inventory from upstream, detects a newly added action binary automatically, rejects each individually missing file and external-helper symlinks, and fails closed on unreviewed archive assembly changes. Its archive digest must be reviewed again after an upstream change. Other targets, including the musl voice-bundle branch, are refused until their whole resource contract is reviewed; they cannot silently pass the GNU gate.

| Every upstream GNU Linux package file | Present in 1112c |
| --- | --- |
| codex-package.json | yes |
| bin/codex | yes |
| bin/codex-app-server | yes |
| bin/codex-code-mode-host | yes |
| bin/codex-exec-server | yes |
| codex-resources/bwrap | yes |
| codex-path/rg | yes |
| Fork addition: bin/codex-factory-bridge | yes |

No upstream file in this target's package contract is omitted. The root also contains INTERFACE.md, MANIFEST.json and SHA256SUMS. The source-cited interface now lives in tracked docs/fork-mailbox.md so future packaging does not depend on an ignored historical dist. The CLI hash **changed**, because this release follows the Linux bwrap-digest/source-stamping build. Its version remains **`codex-cli 0.167.2+ignibyte.1112b`** from the same source; model discovery remains numeric 0.167.2. Factory revision is the full source above and public updates remain disabled.

- `bin/codex`: 1632394040 bytes; SHA-256 `87b000eb4f2b2e3be3944cbced938b3d6ad5b1fe9945946e26e6ec041ca36458`.
- `bin/codex-code-mode-host`: 206347296 bytes; SHA-256 `1b3567c030899cf44333b335686e3b7c5dbd7db8af2bbe5f715ddd98db35a0fa`.
- `codex-resources/bwrap`: stripped/embedded SHA-256 `bd2ed101e19111f5c06ea4bb16ace19ca10693a56b343265b156937d4434f023`.

The manifest inventories every file's bytes/hash and every ELF's library/symbol requirements. All five first-party binaries require GLIBC_2.39; bwrap requires GLIBC_2.38; the pinned rg has no dynamic glibc requirement. Destination ABI checks remain the owner's installation step.

The packaged live proofs used the available z690 ChatGPT login through private mode-0700 CODEX_HOME directories and mode-0600 credential copies. The common command prefix was exactly the owner's:

```text
dist/1112c/bin/codex --no-daemon --ask-for-approval never exec --ignore-user-config --ignore-rules --strict-config --sandbox <mode> -c model="gpt-6.1-terra" -c model_reasoning_effort="high" --cd <isolated directory> --skip-git-repo-check --json -- <prompt>
```

stdin was closed. No binaries in target/ were used for these turns. The qualification driver is `scripts/factory/qualify_packaged_tools.py --package dist/1112c --work-dir <fresh private directory>`.

| Actual tool proof | Verified result |
| --- | --- |
| read-only shell: `cat note.txt; uname -n` | PASS: one completed command, exit 0; unpredictable file nonce and actual hostname in tool output; turn completed; 6.78s |
| workspace-write shell: write/read tool-write.txt | PASS: one completed command, exit 0; actual file bytes match the requested nonce/newline; turn completed; 7.08s |
| read-only local stdio MCP probe_note | PASS: one MCP event and independently recorded server call/nonce; turn completed; exit 0; 6.97s |

The first MCP attempt was correctly refused by the unchanged `never` approval policy because the fixture omitted readOnlyHint. Fixed the local fixture's truthful read-only/nondestructive/closed-world annotations and reran **only that failed case** (`--cases mcp_readonly`); no approval policy was weakened. Both the initial failed attempt and successful retry are recorded in the manifest. The final proof's owner auth/config/cache hashes are unchanged. No missing-host diagnostic remains. Packaged app-server, code-mode-host, exec-server `--help`, rg `--version`, and bwrap `--version` all exit 0.

The matching fault pair was rebuilt from the same source with the same embedded bwrap digest/source stamps, eight jobs, dev-small, CARGO_INCREMENTAL=0, and the two fixture-fault features: exit 0, 129.34s. The seven existing mailbox programs were then rerun with production inputs from dist/1112c/bin and the separate fault pair:

| Qualifier | Result / elapsed |
| --- | --- |
| contract | PASS; 64 checks; exit 0; 0.18s |
| credentials | PASS; 7 checks; exit 0; 4.97s |
| recovery | PASS; 9 checks; exit 0; 18.55s |
| native | PASS; 5 checks; exit 0; 2.14s |
| lifecycle | PASS; 4 checks; exit 0; 2.26s |
| durability | PASS; 7 checks; exit 0; 1.69s |
| reconciliation | PASS; 5 actual native crash lanes; exit 0; 10.61s |

All 96 counted checks and five crash lanes pass. The original 14 upstream-owned workspace failures remain failures, not waived; no full workspace suite rerun is claimed. No Forge/dev-3/live service was touched. Qualification is recorded through the package entry point's `--record-qualification` mode, which rechecks file hashes/completeness and seals the manifest/checksums.

Final checksum proof PASS: INTERFACE.md, MANIFEST.json, all five bin/ executables, codex-package.json, codex-path/rg and codex-resources/bwrap are OK. Both dist/1112 and dist/1112b retain every captured file, hash, size, mode and mtime (including the two staged setup helpers that already existed in 1112b when the host brief froze it). Scope PASS: exactly eight named tracked files; Rust tree equals the tested source; git diff --check passes. Removed only `/srv/stacks/codex-hosts-tmp-1112c`, including private credential copies and fixture output, with zero remaining mailbox namespace processes. The host /tmp remains its original tmpfs. Package/proof source is `20e6f97c5f`; subsequent changes in this commit only record these results.

## Resume the authorized separate-UID setup delivery

The setup/UID briefs authorize implementation; the hosts brief changes its installation target to the **whole dist/1112c folder** and freezes 1112b. Preserve its two already-staged helper files byte-for-byte. Continue on the current plan-002 branch from the completed host fix. Add canonical `scripts/factory/setup-forge-box.py`, `scripts/factory/test-setup-forge-box.py`, and `docs/factory-setup.md`; update package.py to include these fork operator assets as root setup-forge-box.py, test-setup-forge-box.py and SETUP.md, through the same packaging entry point. Update this plan, docs/fork-mailbox.md and one setup changelog line. No Rust, protocol or upstream-file change.

The helper stages only the existing protocol and never starts services or creates users. Ops provisions fleet-bridge (nologin/no sudo) on both hosts, ai endpoints and a client group. Use protected /var/lib and /etc roots; the existing proxy rejects an ai-owned home as a private-key/config parent. Reserve the same numerical service UID on both hosts for the supplied routes schema's single service_uid. Only the four specified cross-seat edges are granted; self scope remains necessary for existing sync/inbox calls. Generate key-specific restricted SSH admission through ai's existing sshd login and an exact Ops sudoers command to execute remote-entry as fleet-bridge; the service account has no sudo permission. Bindings are service-owned 0640, endpoint credentials/ledgers ai-owned 0600/0700. Real thread IDs are supplied explicitly, routes stay disabled pending owner smoke/cutover, and a stock running session cannot gain fork hooks through an environment change.

Proof: rerun the complete setup fixture with production 1112c/bin binaries, distinct service/endpoint UIDs, private /tmp, /run and sudoers mounts, actual loopback OpenSSH and the generated forced sudo command. Prove three enrollments, all four edges/receipts, both forbidden cross-seat directions, same-state serve restart/replay with exactly four records and no duplicated delivery, routes filled from supplied UUIDs, modes and repeated-init refusal. Record output in the tracked runbook and this plan; seal those assets and setup results through package.py, with original host proofs preserved and program hashes unchanged. No real host/service/account action. Then write plan 003 and stop for review as originally ordered.

### Setup proof result

Canonical setup, fixture and runbook source: `0228883c1b`. The package contains setup-forge-box.py, test-setup-forge-box.py and SETUP.md copied only through package.py; all match their tracked sources byte-for-byte. The helper's explicit init/bootstrap/enroll/routes/proof steps never start a service or create an account. Actual native thread IDs remain an operator input; routes stay disabled, and the real stock pilot needs an owner-controlled fork restart.

```text
$ sudo -n unshare --mount --propagation private python3 <isolated NVMe directory>/entry.py
$ # entry.py provides private /tmp and /run and executes:
$ python3 -B dist/1112c/test-setup-forge-box.py --root /tmp/setup-proof-1112c --dist /srv/stacks/codex/dist/1112c
Setup fixture exit: 0
passed=true; seats=3; directedEdges=4
durableMessagesAfterRestart=4; receiptsPerMessage=2
serviceUid=65534; endpointUid=65532; modelsStarted=0
transport="actual loopback OpenSSH; generated sudo forced command; one kernel"
realFleetHostsTested=false
```

All seven setup checks PASS: init stages three seats with no listeners and repeated init refuses without registry mutation; the generated restricted SSH/sudo entry runs a nologin service account; three real enrollments include the remote pilot via connect-host; every allowed direction sends/receives with service/inbox receipts; both ungranted direct spec/pilot directions fail; same-state serve restart plus exact-UUID resend/re-receive retains four records and one delivery receipt each; disabled routes have all supplied UUIDs and correct binding/credential owners/modes. Full actual stdout is pasted in SETUP.md as requested. The fixture's root SSH frontend and one kernel do not prove production ai sudoers admission, systemd units, physical-host routing/firewall/host pins or the live native cutover. No live host/service/account was changed.

The owner runbook lists the per-host ucs-ops account/group request, every directory/file owner/group/mode, exact system units and commands by actor, the generated forced SSH/sudoers lines, local/remote enrollment, explicit message/restart proof, native binding environment, thread/factoryMessage wake/defer/reconciliation, current stock-session limitation and finite lease maintenance. A dedicated proxy UID is required on dev-3. Matching numerical fleet-bridge UIDs are required by the supplied routes schema. Service state/config use /var/lib and /etc because the existing proxy rejects an ai-owned parent. The package gate's six regressions still pass after adding operator assets; no Rust file changed. Original host qualification is preserved in MANIFEST.json together with setup's PASS. Program hashes remain codex `87b000eb4f2b…`, code-mode-host `1b3567c03089…`; all package checksums are reverified after sealing.

Cleanup PASS: removed only the two task-owned setup scratch directories `/srv/stacks/codex-setup-tmp-1112b` and `/srv/stacks/codex-setup-tmp-1112c`, including synthetic keys/state/logs. Neither fixture namespace retained a process; the host /tmp remains tmpfs. The final package preserves both older dist folders; only 1112c gained the requested operator assets.

## Owner-authorized Ubuntu sandbox precondition — no rebuild

`/tmp/fork-brief-apparmor.txt` (read in full) reports 1112c received with valid checksums on Forge. Ubuntu 24.04 with `kernel.apparmor_restrict_unprivileged_userns=1` rejects its sandbox with `bwrap: loopback: Failed RTM_NEWADDR: Operation not permitted`; stock's path/xattr-specific AppArmor permission does not cover the fork's different path/file. Danger-full-access succeeding does not qualify sandboxed operation. Ops is fixing the root-owned bwrap/xattr/AppArmor policy through ucs-ops; this task must not change those settings or ownership, rebuild any binary, or merge before owner review.

**Scope / files:** On `plan/002-apparmor-precondition`, update `scripts/factory/setup-forge-box.py`, `scripts/factory/test-setup-forge-box.py`, `scripts/factory/package.py`, `docs/factory-setup.md`, `docs/fork-mailbox.md`, `WORKFLOW.md`, `CHANGELOG-FORK.md`, and this plan. Refresh only 1112c's operator assets, manifest and checksums through package.py. Preserve every ELF and both older dist folders. Plan 003 remains unimplemented on its separate planning branch.

**Smallest version / proof:** Add a preflight subcommand and enforce it at endpoint enrollment/proof steps under the actual endpoint UID. Read the bwrap digest from package-root/MANIFEST.json, verify the file, print its relocated path/digest, and run a fixed no-model read-only user/pid/network-namespace probe through that packaged bwrap. A failure must plainly name the Ops precondition and refuse; no full-access fallback or policy/ownership mutation. Service-only staging runs as fleet-bridge, so the runbook requires a separate ai preflight on every host and enrollment repeats it as ai. Extend the owned separate-UID SSH fixture with real successful preflight, a simulated bwrap denial that prevents enrollment before any credential/network action, and a relocated package-root check. Record z690's positive result without claiming Ubuntu/AppArmor policy qualification on the real hosts. Re-strip two copies of the existing cached bwrap without rebuilding and compare its published digest; retain Cargo cache reuse/strip-before-hash and never post-strip. Make package READY output include the full manifest bwrap digest. Audit runtime paths for relocation, document configurable absolute operator paths and the separate application-journal/home examples, recheck checksums/scope, then ask the owner to review the 1112c commits before any main merge.

### Sandbox-precondition proof result

Helper/builder source `0203d3503f`, runbook proof update `ad883b7eb9`. No Cargo build, binary rebuild, policy, xattr, sysctl, production ownership, live service or real-host operation was performed. All seven packaged runtime executable/resource SHA-256 digests match the before snapshot; codex remains `87b000eb4f2b…`, code-mode-host `1b3567c03089…`, and bwrap remains **`bd2ed101e19111f5c06ea4bb16ace19ca10693a56b343265b156937d4434f023`**. Two independent stripped staging copies of the existing same-source Cargo bwrap each reproduced that full digest; this is cached-artifact stability, not a claim of clean-build reproducibility across toolchains/paths.

```text
$ # No-model probe as endpoint UID, through the packaged bwrap:
$ python3 <package>/setup-forge-box.py preflight --bin-dir <package>/bin
sandbox.passed=true; uid=65532 in the separate-UID fixture
bwrapSha256=bd2ed101e19111f5c06ea4bb16ace19ca10693a56b343265b156937d4434f023
$ sudo -n unshare --mount --propagation private python3 <isolated>/entry.py
Setup/sandbox fixture exit: 0
passed=true; checks=9; seats=3; directedEdges=4; durableMessagesAfterRestart=4
receiptsPerMessage=2; serviceUid=65534; endpointUid=65532; modelsStarted=0
realFleetHostsTested=false
$ TMPDIR=<isolated> python3 -B -m unittest discover -s scripts/factory -p test_check_package.py -v
Ran 6 tests; OK
```

The nine-check fixture adds successful endpoint preflight/relocated resource lookup and a simulated `RTM_NEWADDR: Operation not permitted` that emits a plain path/digest/ucs-ops refusal before reading enrollment inputs. It retains the seven real local SSH/enrollment/route/restart checks. Full actual output is pasted in SETUP.md. A separate reflink copy of the **whole folder** at a new prefix passed its packaged preflight as uid 1000, printing the relocated bwrap path and original full manifest digest; relocated CLI/bridge versions also passed. Runtime/setup source contains no fixed `/home/ai/.local/share/fleet-codex` package prefix. Only the client journal defaults use `/home/ai/fleet-spec-project`; CODEX_HOME examples and generated absolute bindings/units/SSH paths are explicitly operator configuration to update on relocation.

z690's `/proc/sys/kernel/apparmor_restrict_unprivileged_userns` is absent and unchanged. Actual restricted-Ubuntu approval remains Ops' work and must be verified by the owner on each endpoint host after the ucs-ops change; the injected denial is not an actual AppArmor policy test. The helper does not modify policy, ownership or xattrs and offers no danger-full-access fallback. The runbook makes root-owned bwrap plus its root-only xattr/profile a precondition on restricted Ubuntu and requires the probe as ai, not the bridge service UID.

Only operator text assets, manifest and checksums were refreshed through package.py. All source/packaged helper/doc bytes match; all SHA256SUMS entries pass. A first digest-verification command had the wrong working directory; its corrected absolute-path rerun passed. The packager now emits the required digest mechanically:

```text
FORK HOSTS READY /srv/stacks/codex/dist/1112c 87b000eb4f2b 1b3567c03089 BWRAP bd2ed101e19111f5c06ea4bb16ace19ca10693a56b343265b156937d4434f023
```

No main merge is authorized yet. The owner explicitly requested review of 1112c commits `00d4dab308`, `20e6f97c5f`, `1145eeb133` and their setup/precondition follow-ups before any main merge. The 14 earlier upstream-owned workspace failures remain failures, not waived; Rust source is unchanged from `6cb7fd7183`.

Final scope is exactly the eight named follow-up files. Removed only `/srv/stacks/codex-apparmor-tmp-1112c`, including the relocated reflink package and synthetic fixture keys/state; zero fixture namespace processes remained. The host /tmp remains tmpfs. No main merge or push to main occurred in this follow-up.
