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
