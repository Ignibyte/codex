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
PASS: all 99 fork-added files absent upstream retained byte-for-byte
PASS: AGENTS.md and WORKFLOW.md unchanged from e00ce2881; existing plans retained
PASS: no unresolved merge entries
```

Pre-existing whitespace: `git diff --cached --check` and pristine upstream's `git diff --check 970111f6cb62cc02fe36a82ea7d71b37a2889c97 HEAD` both return 2 with the same **33 path/line/message diagnostics across eight files**. All eight blobs equal upstream: the skills-toggle pasted-search and resume-picker TUI snapshots; `patches/llvm_mingw_import_warnings.patch`, `patches/notify_windows_idle.patch`, `patches/rules_rs_bindgen_v0_0_4.patch`, `patches/rules_rust_declared_msvc_linker.patch`, `third_party/v8/abseil_lifetime_capture.patch`, `third_party/v8/v8_windows_snapshot_wine.patch`. Preserve upstream patch/snapshot bytes; this is an approved pre-existing exception, not a fork regression. Ordinary unstaged `git diff --check` passes.

Workspace tests and remaining checks: pending; no main merge or push until they finish under the owner's failure-comparison rule.
