# Plan 001: upstream sync

## Goal
Bring the Codex fork current with upstream while preserving the owner's persistent agents, roles and cross-account communication.

## Smallest version
Fetched 2026-11-10: fork `e00ce2881`, upstream/main `627b579219d492b5445d6d433d3110eb53e507dc`; common base `970111f6cb62cc02fe36a82ea7d71b37a2889c97`. Upstream adds **791 commits**; the fork has **2 unique commits**. Upstream changes 3,378 paths; a `git merge-tree --write-tree --name-only --no-messages` preview changes 3,377 fork paths and finds exactly four conflicts below, without changing the checkout/index.

After approval, on `sync/upstream-1110`, **MERGE** that pinned upstream/main commit with `git merge --no-ff --no-commit`; never rebase or force-push. Preserve the fork's `WORKFLOW.md`, `AGENTS.md`, `CHANGELOG-FORK.md` and entire `plans/` tree; only append our changelog/proof. Reconcile CLI/server conflicts with the restored factory integration intact, regenerate the lockfile and protocol export from merged sources, and use upstream's toolchain. Stop for review if reconciliation needs new behavior. Review before the final `--no-ff` merge to main or any main push.

## Will not build
No new features, Forge mechanisms, migration, installation, release, service changes, fleet-spec-manager edits or manager-pane actions. No implementation/build/test execution in this planning step.

## Files
- Import the upstream tracked-file delta identified by `git diff --name-only 970111f6c 627b57921`, except the four protected fork paths above; retain fork-only files.
- Conflicts: `codex-rs/Cargo.lock`; `codex-rs/app-server-protocol/schema/precomputed/app-server-exports-experimental.json.zst`; `codex-rs/app-server/src/message_processor.rs`; `codex-rs/cli/src/main.rs`.
- Generated protocol fixtures/SDK outputs from `just write-app-server-schema`, only as required by merged protocol types; `CHANGELOG-FORK.md` and this plan for documentation/proof. No unrelated edits.

## Proof
Use upstream `docs/install.md`/`justfile`: Linux default features, two build jobs/test threads, no `--all-features` or cross-platform matrix. Workspace tests cover **all 119 directly changed Rust packages**, including `codex-core`, `codex-cli`, `codex-protocol`, `codex-app-server`, `codex-app-server-protocol`, `codex-app-server-daemon`, `codex-tui`, `codex-agent-identity`, `codex-agent-message-board-client` and `codex-message-board`; also cover retained `codex-factory`, `codex-install-context` and `codex-agent-roles`. Existing platform cfgs apply; record platform skips and any failures honestly. Run sequentially after approval:

```sh
(cd codex-rs && CARGO_BUILD_JOBS=2 cargo build --locked -p codex-cli -p codex-app-server -p codex-factory)
CARGO_BUILD_JOBS=2 just test --workspace --test-threads=2
(cd codex-rs && CARGO_BUILD_JOBS=2 cargo test --locked --workspace --doc)
just test-github-scripts
git diff --check
git merge-base --is-ancestor 627b579219d492b5445d6d433d3110eb53e507dc HEAD
git diff --exit-code e00ce2881 -- WORKFLOW.md AGENTS.md
```

Record outputs here; verify the fork-only tree, roles/message-board tests and generated-schema checks before review. Build failures block completion.

## Approval and additions
Reviewer (Claude owner seat): APPROVED 2026-11-10 11:46 CST.

Owner brief `/tmp/fsm-brief-fork-go.txt`: reconcile all four conflicts so upstream behavior lands and the restored persistent-agent, role and cross-account-message features remain. Regenerate Cargo.lock with Cargo from merged manifests, never hand-edit it. Regenerate the compressed protocol export with `just write-app-server-schema`, never hand-merge it.

Re-run every failing test on pristine upstream `627b5792` in a scratch worktree. A matching upstream failure is pre-existing: record it and proceed; a fork-only failure blocks and must be fixed. If the full workspace run exceeds two hours, report elapsed time and partial results. Once validation is complete, merge this branch into main with `--no-ff`, push origin main and **keep this branch**. No fleet-spec-manager or manager-pane changes.

## Proof result

Environment: z690/Linux, upstream Rust 1.95.0, two Cargo jobs, existing `/mnt/fast/target` cache. Pristine comparison worktree: `/srv/stacks/codex-upstream-1110`, detached at full pinned upstream SHA; clean before tests.

Conflict resolution: retain FactoryInfo plus upstream's agent-command-center help; take upstream's time-provider expression while retaining every factory RPC handler. Seed Cargo.lock from upstream, then regenerate through `cargo update --workspace` (offline first lacked the pinned sqlx checkout; online succeeded). Cargo added only the factory package and its required dependency edges; no hand-edited lockfile. Regenerate experimental exports and stable schemas/SDK with the upstream commands, never hand-merge the binary.

```text
$ cargo update --workspace
Locking 1 package to latest compatible version
Adding codex-factory v0.0.0 (/srv/stacks/codex/codex-rs/factory)
(exit 0)
$ CARGO_BUILD_JOBS=2 just write-app-server-schema --experimental
Finished `test` profile [unoptimized + debuginfo] target(s) in 4m 07s
test schema_fixtures_tests::write_schema_fixtures_from_env ... ok
test result: ok. 1 passed; 0 failed; 0 ignored; 0 measured; 324 filtered out; finished in 4.71s
(exit 0)
$ CARGO_BUILD_JOBS=2 just write-app-server-schema
Finished `test` profile [unoptimized + debuginfo] target(s) in 13.14s
test schema_fixtures_tests::write_schema_fixtures_from_env ... ok
test result: ok. 1 passed; 0 failed; 0 ignored; 0 measured; 324 filtered out; finished in 4.73s
SDK artifacts: 1 file reformatted; Done. (generator uint-format warnings only)
(exit 0)
$ cd codex-rs && CARGO_BUILD_JOBS=2 cargo build --locked -p codex-cli -p codex-app-server -p codex-factory
Finished `dev` profile [unoptimized + debuginfo] target(s) in 6m 37s
real 397.49
user 701.49
sys 70.06
(exit 0)
PASS: all 99 fork-added files absent upstream retained; 98 byte-identical, CHANGELOG-FORK.md has only the planned append
PASS: AGENTS.md and WORKFLOW.md unchanged from e00ce2881; existing plans retained
PASS: no unresolved merge entries
```

Pre-existing whitespace: `git diff --cached --check` and pristine upstream's `git diff --check 970111f6cb62cc02fe36a82ea7d71b37a2889c97 HEAD` both return 2 with the same **33 path/line/message diagnostics across eight files**. All eight blobs equal upstream: the skills-toggle pasted-search and resume-picker TUI snapshots; `patches/llvm_mingw_import_warnings.patch`, `patches/notify_windows_idle.patch`, `patches/rules_rs_bindgen_v0_0_4.patch`, `patches/rules_rust_declared_msvc_linker.patch`, `third_party/v8/abseil_lifetime_capture.patch`, `third_party/v8/v8_windows_snapshot_wine.patch`. Preserve upstream patch/snapshot bytes; this is an approved pre-existing exception, not a fork regression. Ordinary unstaged `git diff --check` passes.

Validation completed on code at `c625dba485` (merge `ea606e1692` plus the approved qualification-proof addition). No further implementation changes were needed. Main has not been changed during validation.

The first full-workspace attempt could not download the unpublished denoland V8 152.2.0 sandbox binding (HTTP 404); pristine upstream failed on the same URL. Use upstream's `scripts/codex_package/v8.py::resolve_codex_v8_cargo_env` to fetch its checksum-verified Codex V8 archive/binding pair, then set the returned `RUSTY_V8_ARCHIVE` and `RUSTY_V8_SRC_BINDING_PATH` for both trees. This fixed the build environment without a source or dependency-pin change.

The next full attempt reached an upstream TUI test compile error. Both merged source and pristine upstream give:

```text
$ CARGO_BUILD_JOBS=2 just test --workspace --test-threads=2
error[E0061]: this method takes 2 arguments but 1 argument was supplied
  tui/src/chatwidget/tests/status_and_layout.rs:246
  chat.open_feedback_consent(FeedbackCategory::SafetyCheck);
  missing bool can_save (method at tui/src/chatwidget.rs:1067)
(exit 101; 471.03 s including build)
$ # pristine upstream, same verified V8 environment
$ CARGO_BUILD_JOBS=2 just test -p codex-tui --lib --test-threads=2
same error[E0061], same call and method
(exit 101; 350.77 s)
```

The test and implementation blobs equal upstream. Under the approved pre-existing-failure rule, retain upstream's bytes and run the rest of the workspace with this test package excluded. The production TUI was built successfully. Use `INSTA_UPDATE=no`, `NEXTEST_STATUS_LEVEL=fail`, `NEXTEST_FINAL_STATUS_LEVEL=fail`, two Cargo jobs and two test threads for both test trees.

```text
$ CARGO_BUILD_JOBS=2 just test --workspace --exclude codex-tui --test-threads=2
Starting 18459 tests across 322 binaries (46 tests skipped)
Summary [3286.279s] 18459 tests run: 18414 passed (17 slow), 45 failed, 46 skipped
ELAPSED_SECONDS 3815.54; EXIT 100
$ # pristine upstream: just test, selecting all 45 exact package/test pairs listed below
Starting 45 tests across 87 binaries (10208 tests skipped)
Summary [155.668s] 45 tests run: 0 passed, 45 failed, 10208 skipped
ELAPSED_SECONDS 845.28; EXIT 100
PASS: identical 45 failing cases; all 45 normalized failure signatures match
$ cd codex-rs && CARGO_BUILD_JOBS=2 cargo test --locked --workspace --doc
170 doc-test targets: 2 passed, 0 failed, 3 ignored
ELAPSED_SECONDS 641.79; EXIT 0
$ just test-github-scripts
Ran 76 tests in 0.633s
OK
(exit 0)
$ git diff --check
(no output; exit 0)
$ git merge-base --is-ancestor 627b579219d492b5445d6d433d3110eb53e507dc HEAD
(no output; exit 0)
$ git diff --exit-code e00ce2881 -- WORKFLOW.md AGENTS.md
(no output; exit 0)
```

The longest full-workspace attempt took 63 min 36 s including build; it did not cross the two-hour reporting threshold. The 46 skips are nextest's default ignored cases; non-Linux code remains cfg-excluded. The 10,208 skips in the comparison are unselected cases, not additional successful tests. No snapshot was accepted/updated and no owner home/config/quota was changed.

All 45 failures are pre-existing on this host at pinned upstream, with matching assertion/error signatures (temporary paths, PIDs and timings normalized):

- 2 app-server and 6 core snapshots plus 2 skills-extension assertions load the owner's real `diagnose-crash`/`omarchy` user skills into otherwise isolated expected catalogues.
- 18 CLI daemon/package cases copy a roughly 1.1 GB debug CLI into helpers' hardcoded `/tmp`; both trees return `Disk quota exceeded (os error 122)`. `TMPDIR` already points to NVMe, but these helpers explicitly use `/tmp`.
- 3 core network-approval cases: wiremock expected 2 requests, received 1.
- 4 exec-server shell-snapshot cases: two `xxxx` versus `xx` captures, one profile-capture read-only refusal, one websocket recovery timeout after 45 s.
- Git-utils: the host's diff-prefix configuration produces `c/`, `w/`, `1/`, `2/` instead of expected `a/`, `b/`.
- 2 Linux sandbox cases: missing bwrap under the test's restricted PATH; redirected synthetic-mount tmp is read-only.
- PTY invalid-first-candidate lookup unexpectedly starts a child; V8's typed-array ABI check and sandbox-feature assertion fail; 2 voice jitter-buffer cases report unavailable and 2 voice transport cases have no acceptable remote candidate.

Each exact case below was re-run on pristine upstream with `-p <package>` and `-E '(package(=<package>) and test(=<test>))'`, OR-combined into one invocation. Every line is FAIL on both trees:

```text
codex-app-server suite::v2::thread_sample::sample_preserves_context_and_keeps_source_unchanged::threadhistorymode_legacy_turnstatus_completed_false_expects
codex-app-server suite::v2::thread_sample::sample_preserves_context_and_keeps_source_unchanged::threadhistorymode_legacy_turnstatus_completed_true_expects
codex-cli bootstrap_reports_updater_bind_failure
codex-cli bootstrap_updater_is_ready_in_deep_codex_home
codex-cli explicit_restart_applies_feature_overrides_and_preserves_saved_settings
codex-cli managed_daemon_restarts_from_deleted_working_directory
codex-cli managed_start_keeps_updater_on_marker_mismatch_but_stops_it_for_pin
codex-cli managed_identity_survives_locale_and_timezone_changes
codex-cli managed_start_succeeds_when_updater_record_is_invalid
codex-cli managed_starts_ensure_one_updater_and_recover_a_missing_one
codex-cli package_ownership_check_does_not_start_an_updater
codex-cli manual_update_rejects_an_unowned_installation
codex-cli restart_applies_saved_updater_preference
codex-cli start_applies_feature_overrides_only_when_launching
codex-cli wall_clock_shift_keeps_server_and_updater_managed
codex-cli unmanaged_app_server_does_not_launch_updater
codex-cli packaged_daemon_bootstrap_seeds_local_package
codex-cli packaged_daemon_explicit_replacement_migrates_running_legacy
codex-cli packaged_daemon_restart_seeds_local_package
codex-cli packaged_daemon_start_and_explicit_replacement
codex-core suite::network_approval::user_network_approval_once_session_and_denial_semantics::canonical_requirements
codex-core suite::network_approval::user_network_approval_once_session_and_denial_semantics::legacy_requirements
codex-core suite::network_approval::user_network_approval_once_session_and_denial_semantics::proxy_requirements
codex-core suite::scenarios::astra_kickoff_with_skills_plugins_and_remote_compaction
codex-core suite::scenarios::astra_plugin_policy_limits_visible_skills::descriptions
codex-core suite::scenarios::astra_plugin_policy_limits_visible_skills::omit_descriptions
codex-core suite::scenarios::astra_refreshes_plugin_tools_and_skills_in_an_existing_thread::simplified_selection_hints_off
codex-core suite::scenarios::astra_refreshes_plugin_tools_and_skills_in_an_existing_thread::simplified_selection_hints_on
codex-core suite::scenarios::skill_catalog_dedup::skill_catalog_deltas_survive_resume_and_rebuild_after_compaction
codex-exec-server shell_snapshot_v2_capture_failure_falls_back_and_retries::remote_pipe_recovery
codex-exec-server shell_snapshot_v2_capture_failure_falls_back_and_retries::remote_tty_recovery
codex-exec-server shell_snapshot_v2_filters_profile_exports_and_stays_in_memory::remote_sandbox
codex-exec-server shell_snapshot::shell_snapshot_concurrent_replays_keep_independent_readers::blocked_descriptor_path
codex-git-utils info::diff_tests::tracked_and_untracked_patches_preserve_order_and_apply
codex-linux-sandbox suite::sandbox::root_metadata_tests::root_metadata_symlinks_cannot_reopen_approved_denials
codex-linux-sandbox suite::sandbox::sandbox_keeps_parent_repo_discovery_while_blocking_child_metadata
codex-skills-extension host_service::tests::snapshot_for_config_merges_extension_host_and_legacy_plugin_roots
codex-skills-extension host_service::tests::snapshot_for_config_preserves_host_precedence_for_symlinked_plugin_root
codex-utils-pty child_command::linux_tests::path_search_stops_at_invalid_candidates
codex-v8-poc tests::reads_typed_array_contents_with_the_compiled_binding_size
codex-v8-poc tests::sandbox_feature_matches_linked_v8
codex-voice-host devices::playout::tests::jitter_buffer_preserves_packets_delivered_in_a_burst
codex-voice-host devices::playout::tests::real_decoder_renders_current_rtp_and_rejects_pre_epoch_arrivals
codex-voice-host transport::tests::negotiates_real_ordered_channel_over_udp_and_tcp
codex-voice-host transport::tests::negotiates_after_early_packet_loss_and_slow_tcp_connect
```

## Additional approved proof — mailbox qualification

Owner brief `/tmp/fsm-brief-qualify.txt`, 2026-11-10 12:58 CST: AFTER workspace tests and before the main merge, run `scripts/factory/qualify_delivery.py`; run the scripted non-interactive `qualify_two_logins.py` mode if available without a real second login, otherwise record that requirement and skip sign-in. Run every other existing `qualify_*.py` that needs no live login, no network beyond localhost and no live Forge write. List each as PASS, FAIL or SKIPPED with its reason. Use isolated temporary fixtures required by these existing tests and keep the permanent record in this plan.

Qualification failures compare against pristine **fork main `e00ce2881`**, not upstream: matching baseline failures are pre-existing and do not block; merge-only failures must be fixed. Retain the original upstream-baseline rule for workspace tests. Merge/push only after both sets of checks; keep the sync branch. Finish with `FORK QUALIFIED <sha>`.

### Qualification result

**22 PASS, 1 FAIL (pre-existing), 19 SKIPPED; all 42 scripts accounted for.** All 42 qualification sources equal pristine fork main `e00ce2881`. The eligible checks used synthetic credentials, private UID-separated fixtures and localhost/Unix sockets; no real login, remote host or Forge write was used. Temporary fixture outputs are not a new permanent evidence tree; this plan is the record.

Build the actual factory package with the existing builder, which enables `codex-install-context/factory`. Its compiled `factory-info` reports storage 11, bridge 7, native 2 and `publicUpdatesAllowed=false`. Build the pristine fork-main package separately, from unchanged source and its original Rust 1.99.1; merged builds use upstream Rust 1.95.0. Neither package is installed.

```sh
python3 -u scripts/factory/package_control.py build --package /mnt/fast/tmp/codex-qual-1110/merged-package --evidence /mnt/fast/tmp/codex-qual-1110/merged-build --target /mnt/fast/target --revision sync-1110-c625dba485
# exit 0; 819.73 s including queued builds
# same command in pristine e00ce2881, substituting fork-main-package, fork-main-build and fork-main-e00ce2881:
# exit 0; 769.78 s including queued builds
```

Package manifest SHA-256: merged `54a4460d264359e01b15ddf927a769a0500502befaef8daa104626cd3390aef5`; fork main `4d83817248e5c4e72d48a219f82ea5075bc2d9027af96db2436b2d49c11a95b9`.

Standalone checks use the merged debug bridge and packaged Codex. Also re-run the 64 contract checks and all 5 native mailbox journeys with **both binaries from the verified merged package**: PASS. These prove authenticated peer discovery; queue-only delivery without waking; correctly attributed wake/reply with stable IDs; busy deferral/idempotency; impersonation, unknown-seat and approval-override refusals. Packaged sessions and package rollback/history checks passed too.

For fault lanes build a separate temporary binary pair, with the existing test-only feature; no production binary or service is changed:

```sh
cd codex-rs
CARGO_BUILD_JOBS=2 CARGO_INCREMENTAL=0 CODEX_FACTORY_REVISION=sync-1110-c625dba485-faults cargo build --locked --offline --target x86_64-unknown-linux-gnu --profile dev-small -p codex-cli -p codex-factory --features codex-install-context/factory,codex-app-server/factory-fixture-faults,codex-factory/fixture-faults --bin codex --bin codex-factory-bridge
# exit 0; 453.02 s including queued builds
```

Each executable qualifier is invoked as `python3 scripts/factory/qualify_<name>.py --evidence <private temporary output>`, with its required arguments:

- `contract`, `credentials`, `enrollment`, `recovery`: `--binary <merged debug bridge>`.
- `native`, `native_controls`, `cancellation`, `lifecycle`, `live_driver`, `routes`, `observation`, `rate_limit`, `pilot_gateway`, `support_schedule`, `recording`, `network`: `--binary <merged debug bridge> --cli <merged-package/bin/codex>`.
- `durability`, `lifecycle_faults`, `support_faults`: `--binary <merged fault bridge>`; `reconciliation` also gets `--cli <merged fault Codex>`.
- `sessions`: `--package <merged-package>`; `packages`: `--package-a <fork-main-package> --package-b <merged-package>`.
- `delivery`: `--package <merged-package> --old-package <fork-main-package> --expected-storage-epoch 11`; repeat in pristine fork main with its own package. There is no genuine earlier-epoch package, so epoch-crossing/archive stages remain unproved after the earlier inventory failure.
- Additional packaged-bridge checks: `contract` and `native` substitute `<merged-package/bin/codex-factory-bridge>`.

| Script | Result | Proof / reason |
| --- | --- | --- |
| `qualify_cancellation.py` | PASS | 4 checks; exit 0. |
| `qualify_contract.py` | PASS | 64 checks; exit 0. |
| `qualify_credentials.py` | PASS | 7 checks; exit 0. |
| `qualify_default_fences.py` | SKIPPED | Genuine pre-CF010 native-ledger CLI is unavailable; e00ce2881 already has the current epoch. |
| `qualify_delivery.py` | FAIL, pre-existing | 2 checks passed, then missing cf-001/inventory.json; same failure and same 2 checks on pristine e00ce2881. |
| `qualify_durability.py` | PASS | 7 checks; exit 0. |
| `qualify_enrollment.py` | PASS | 39 checks; exit 0. |
| `qualify_foreman.py` | SKIPPED | Requires real Forge fixture writes; excluded by the no-Forge-write instruction. |
| `qualify_foreman_recovery.py` | SKIPPED | Requires real Forge fixture writes; excluded by the no-Forge-write instruction. |
| `qualify_legacy_message.py` | SKIPPED | Genuine pre-prefix-fix CLI is unavailable; e00ce2881 already has the prefix fix. |
| `qualify_lifecycle.py` | PASS | 4 checks; exit 0. |
| `qualify_lifecycle_faults.py` | PASS | 2 crash/recovery lanes; exit 0. |
| `qualify_live_driver.py` | PASS | 2 checks; exit 0. |
| `qualify_managed_native.py` | SKIPPED | Requires real Forge fixture writes; excluded by the no-Forge-write instruction. |
| `qualify_manager.py` | SKIPPED | Requires real Forge fixture writes; excluded by the no-Forge-write instruction. |
| `qualify_manager_dispatch.py` | SKIPPED | Requires real Forge fixture writes; excluded by the no-Forge-write instruction. |
| `qualify_manager_responsiveness.py` | SKIPPED | Requires real Forge fixture writes; excluded by the no-Forge-write instruction. |
| `qualify_native.py` | PASS | 5 checks; exit 0. |
| `qualify_native_controls.py` | PASS | 2 checks; exit 0. |
| `qualify_network.py` | PASS | 20 checks; exit 0. Owned loopback SSH only; no second-host claim. |
| `qualify_network_gate.py` | SKIPPED | Requires real Forge fixture writes; excluded by the no-Forge-write instruction. |
| `qualify_next_five.py` | SKIPPED | Aggregate requires historical canary packages and pre-epoch CLI unavailable after restoration; eligible standalone lanes are run separately. |
| `qualify_observation.py` | PASS | 10 checks; exit 0. |
| `qualify_observation_gate.py` | SKIPPED | Requires real Forge fixture writes; excluded by the no-Forge-write instruction. |
| `qualify_packages.py` | PASS | 16 checks; exit 0. Genuine pristine-main/merged packages; shared history and rollback. |
| `qualify_pilot_gateway.py` | PASS | 4 checks; exit 0. |
| `qualify_rate_limit.py` | PASS | 3 checks; exit 0. |
| `qualify_reconciliation.py` | PASS | 5 crash/recovery lanes; exit 0. |
| `qualify_recording.py` | PASS | 10 checks; exit 0. Synthetic HTTP contract server only; no Forge instance. |
| `qualify_recording_forge.py` | SKIPPED | Requires real Forge fixture writes; excluded by the no-Forge-write instruction. |
| `qualify_recording_gate.py` | SKIPPED | Requires real Forge fixture writes; excluded by the no-Forge-write instruction. |
| `qualify_recovery.py` | PASS | 9 checks; exit 0. |
| `qualify_remote_network.py` | SKIPPED | Requires a second kernel/remote SSH host and non-loopback route. |
| `qualify_routes.py` | PASS | 6 checks; exit 0. Optional historical-binary downgrade lane unavailable. |
| `qualify_scale.py` | SKIPPED | Requires real Forge fixture writes; excluded by the no-Forge-write instruction. |
| `qualify_service_vm.py` | SKIPPED | Requires a prepared VM, guest service/reboot journey and non-loopback 10.0.2.2 model route. |
| `qualify_sessions.py` | PASS | 6 checks; exit 0. |
| `qualify_supervision.py` | SKIPPED | Requires real Forge fixture writes; excluded by the no-Forge-write instruction. |
| `qualify_support.py` | SKIPPED | Requires a genuine pre-support bridge for its downgrade refusal; unavailable in the restored tree/cache. |
| `qualify_support_faults.py` | PASS | 6 checks; exit 0. |
| `qualify_support_schedule.py` | PASS | 7 checks; exit 0. |
| `qualify_two_logins.py` | SKIPPED | No credential-free non-interactive mode: identity() reads two real ChatGPT profiles before --live is tested; a distinct second account/subject is mandatory. No sign-in attempted. |

`qualify_two_logins.py` has no credential-free scripted mode: before testing `--live`, `main()` reads both real profile identities and requires distinct ChatGPT account and subject IDs. No account was signed in and no credentials were read/copied.

Exact terminal delivery failures (both at line 23, before the historical stock-check/epoch-crossing/archive stages):

```text
merged checks: complete_package_with_compiled_component_epochs, complete_source_content_modes_and_diff_fresh
FileNotFoundError: [Errno 2] No such file or directory: '/srv/stacks/codex/.artifacts/factory/cf-001/inventory.json'
exit 1
pristine e00ce2881 checks: complete_package_with_compiled_component_epochs, complete_source_content_modes_and_diff_fresh
FileNotFoundError: [Errno 2] No such file or directory: '/srv/stacks/codex-fork-main-1110/.artifacts/factory/cf-001/inventory.json'
exit 1
```

There is no merge-only qualification failure. The mandatory delivery script was run and its unavailable historical fixture was demonstrated on pristine fork main. The skipped legacy, two-account, remote/VM and Forge-dependent checks remain unproved.

Build/test logs were temporary `/tmp/codex-sync-1110-*.log` files; source/package pins, outputs, exceptions and limits are recorded here. Remove only the owned temporary caches/fixtures and clean comparison worktrees after recording. Final merge follows the owner's `--no-ff`, push-origin-main and keep-sync-branch instructions.
