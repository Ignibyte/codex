# Plan 003 — upstream sync, 2026-11-17

**Status:** Awaiting owner approval.

**Goal:** Ship a complete Codex fork that offers and runs GPT-6.2 Sol while preserving persistent agents, roles, and authenticated cross-account messaging.

**Smallest version:** From fork main `32f47da69f46569d01d91ad2c412366f0c64bf07`, branch `plan/003-upstream-sync-1117` and merge `rust-v0.169.0^{commit}` (`d32063f96c2dff1dc96af839b3952abb6acfeda4`) with `--no-ff --no-commit` after approval. This is the newest stable tag in the fetched refs. Its merge base is `3a89580f08c985e99b9cc99df53c67fb8482ef93`, and its delta is two commits across seven paths. Use `0.169.0+ignibyte.1117a`: `models-manager/src/lib.rs:24` sends only Cargo major/minor/patch through the manager and the API's `client_version` query, so the service sees `0.169.0`. The bundled catalog lacks GPT-6.2 Sol. Backend eligibility at 0.169.0 is unverified and must be established by the built CLI before considering raw main or a different pin. Retain the Plan 002 factory, mailbox, roles, persistent-agent and bridge behavior, regenerate lockfile and schemas, then build the full 1112d-style GNU package with same-source upstream voice assembly and the root launcher through `package.py`.

**Will not build:** New features, Forge workflow, tickets, backlogs, or evidence folders. Ops handles installation and deployment. Local commits only. Keep work under the checkout and /tmp, with no sudo, SSH, service changes, other repositories, or owner credentials.

**Files:** The seven paths from `git diff --name-only 3a89580f08 d32063f96c` (workspace Cargo.toml, core config schema, session step context, compact_remote/gcp/scenarios_incremental_tools tests, features lib), `codex-rs/Cargo.lock`, and generated app-server schema paths only as emitted by the existing recipes. Refresh `scripts/factory/{upstream-shipped-linux.json,check_package.py,test_check_package.py}` for the matching official inventory and gate. Update this plan, `docs/{fork-mailbox.md,factory-setup.md}`, and `CHANGELOG-FORK.md`. Package output is `dist/1117a`. Any further path requires owner approval.

**Proof:** Export `CARGO_BUILD_JOBS=8 RUST_TEST_THREADS=8 RUSTUP_TOOLCHAIN=1.95.0`. Prefix every cargo, build wrapper and test invocation below with `nice -n 10`. Use isolated temporary fixtures and verify toolchain and release inputs.

- Run `cargo update --workspace --offline` from codex-rs, then `just write-config-schema`, `just write-app-server-schema --experimental` and `just write-app-server-schema` from the root. Run `just test --workspace --test-threads=8`, `cargo test --locked --workspace --doc -- --test-threads=8` from codex-rs, and `just test-github-scripts`. Rerun exact failed cases on an unchanged archive of the pin under matching conditions and record both results. Run `git diff --check`, `git merge-base --is-ancestor d32063f96c HEAD` and review `git diff main...HEAD --stat`.
- Run `python3 scripts/factory/package.py --output dist/1117a --source-commit HEAD --work-dir "$scratch/package"` and `python3 scripts/factory/check_package.py dist/1117a`. Run `python3 -m unittest discover -s scripts/factory -p test_check_package.py` and the Plan 002 upstream package and voice assembly regression tests. Exercise the relocated voice helper through Hello, InitializeRuntime and Close. Check `dist/1117a/codex --version` and factory identity. Seal proofs through `package.py --output dist/1117a --record-qualification "$scratch/qualification.json"`; from dist/1117a run `sha256sum -c SHA256SUMS`. Print and compare the full `sha256sum codex-resources/bwrap` digest, `MANIFEST.json`'s `bwrapEmbeddedSha256`, and that exact string found in `bin/codex`.
- Build the Plan 002 dev-small fault pair with `cargo build --locked --profile dev-small --target x86_64-unknown-linux-gnu --target-dir "$scratch/fault" -p codex-cli -p codex-factory --features codex-install-context/factory,codex-app-server/factory-fixture-faults,codex-factory/fixture-faults --bin codex --bin codex-factory-bridge` and matching revision/bwrap stamps. Run `python3 scripts/factory/qualify_<lane>.py --binary <bridge> --evidence <temporary fixture output>` for contract, credentials, recovery, native, lifecycle, durability and reconciliation. Use packaged inputs for the first five, fault inputs for the last two, and the matching `--cli <codex>` for native, lifecycle and reconciliation.
- Check `dist/1117a/codex debug models --bundled` and use the fresh remote catalog via `dist/1117a/codex debug models` once service access is authorized. Capture the exact listed ID and prove a completed turn on it. Run `python3 scripts/factory/qualify_packaged_tools.py --package dist/1117a --work-dir "$scratch/tools"` with owner-arranged qualification access and verify actual read-only shell, workspace-write and local MCP effects. Ask and stop at the login boundary if required.

Record all results against the tested source here. Print `ASK: …` and stop for unresolved fork-hook conflicts, fork-only proof failures, or network needed beyond crates/upstream fetch, including the matching official release inventory or release build inputs if unavailable locally. After all proof passes, print `PLAN 003 BUILT <sha>` with the three digest values and exact model ID, then await separate merge approval.

Reviewer (Claude owner seat): APPROVED 2026-11-17 12:53 CST, in the owner's place (owner row d7328b3d: "update the latest codex on our fork and deploy it out"). The pin is right: rust-v0.169.0 is the newest released upstream state and the fork already carries upstream to c0cc9fc820, so the delta is two commits. Two additions: (1) record in the plan that the installed build 1112d (0.167.2+ignibyte.1112b) already shows "GPT-6.2-Sol default" in a fresh session on this box, so the model is offered at 0.167.2 as well: the model proof is on 1117a regardless; (2) state in the plan how many commits upstream/main (04cfdee311) is ahead of this pin and that they are NOT in this release: the owner decides separately whether he wants unreleased main.

The owner reports that installed 1112d (`0.167.2+ignibyte.1112b`) already displays “GPT-6.2-Sol default” in a fresh session on Forge. That observation establishes an offer at client version `0.167.2` on that session. The model ID and execution proof still belong to 1117a.

`git rev-list --left-right --count d32063f96c...04cfdee311` reports `2 333`. The histories diverge at `3a89580f08`, with two release-only commits and 333 upstream/main-only commits. The 333 count includes commits already in fork main through `c0cc9fc820`. The 297 commits in `c0cc9fc820..04cfdee311` are excluded from 1117a and require a separate owner decision.

Build paused on the approved branch during the merge. The sole conflict is the expected workspace version in `codex-rs/Cargo.toml`. The official 0.169.0 Linux release archive and published checksum are needed for the matching inventory, and Rust 1.95.0 is absent from the local Rustup inventory. Both require staged inputs or owner authorization under the brief's network limits.

### Release inputs and partial proof

Verified the official Linux musl archive against the matching entry in the [published checksum file](https://github.com/openai/codex/releases/download/rust-v0.169.0/codex-package_SHA256SUMS). [Archive URL](https://github.com/openai/codex/releases/download/rust-v0.169.0/codex-package-x86_64-unknown-linux-musl.tar.gz). SHA-256 `3b8ed091149cc63a59046b9f094bcfa21cdb4dda15ede441ce369bcee4a71ba0`. The 44 archive file paths plus the pinned installer's launcher match the prior 45-path set. Refreshed the inventory and fixture. `nice -n 10 python3 -B -m unittest discover -s scripts/factory -p test_check_package.py` passed all nine tests.

Rustup staged Rust 1.95.0 (`59807616e`) with homes under `/tmp/fork003-build`. The workspace version conflict is resolved in the working tree to `0.169.0+ignibyte.1117a`. The offline lockfile refresh stopped at the uncached Git-pinned crossterm crate (`openai-oss-forks/crossterm@ed1cdab3`). The Plan 002 build also requires the verified GNU Rusty V8 archive/binding and Bazel 9.2.0 with its pinned native voice inputs, which are outside the newly approved fetches. The upstream Linux musl archive serves as inventory proof and cannot provide the same-source GNU voice runtime. The merge remains in progress.

Merge completed locally at `a24cbd078500328a6a5891ea5a370292176e1949` with the approved upstream pin as second parent. Cargo refreshed the workspace versions and all 1,310 external lockfile records compare unchanged. The GNU Rusty V8 artifacts passed the repository's checksum verification, and staged Bazel 9.2.0 matches the pinned digest. `just test-github-scripts` exited zero. At the next owner checkpoint, schema generation was in flight. The normal schema recipe requires `uv` for SDK generation and no local executable was found. Six of the seven mailbox qualifiers instantiate `Fleet`, whose production fixture setup calls `sudo -n` for separate UID ownership and process launch. The brief disallows sudo, so those qualifiers require an owner-run fixture or explicit local fixture authorization.

Owner directed Ops to run credentials, recovery, native, lifecycle, durability and reconciliation. All six are pending Ops. The exact commands, UIDs, expected results and fixture cleanup are recorded in `/tmp/fork003-build/OPS-QUALIFIERS.md`. The unprivileged contract qualifier remains builder-owned.

Verified [official uv 0.13.9 for x86_64 Linux](https://github.com/astral-sh/uv/releases/download/0.13.9/uv-x86_64-unknown-linux-gnu.tar.gz) against its published SHA-256 `a7fbaff2b9a271d85c1bdb540941090fd1ce011797b4abeb48e4dcd39c2471d5`. The uv executable, cache and locked SDK environment are staged under `/tmp/fork003-build`.

Config schema and both app-server schema generators passed. The first standard generator invocation passed its Rust schema test and then stopped because its shell predated the staged uv. The rerun with the isolated locked SDK environment passed SDK generation as well. Generated files match the committed source. GitHub scripts passed all 76 tests, canonical package tests passed all 27, and voice assembly tests passed all 17. The pinned ripgrep 15.2.0 cache archive passed SHA-256 verification against the checkout's DotSlash manifest (`33e15bcf1624b25cdd2a55813a47a2f95dbe126268203e76aa6a585d1e7b149c`).

The first workspace compilation detected missing native build inputs before tests ran. Verified and extracted Ubuntu Noble `libcap-dev 1:2.66-5ubuntu2.4` and `libasound2-dev 1.2.11-1ubuntu0.3` against local authenticated apt metadata into /tmp with owner approval. Relocated their pkg-config paths and used the matching host runtime SONAMEs; no package was installed. The Cargo voice test inputs are staged using the upstream native SDK recipe.

The owner assigned the signed-in catalog, GPT-6.2 Sol turn and live packaged shell/write/MCP proof to Ops. Builder checks the bundled catalog offline. Doc tests passed across 171 targets (2 passed, 0 failed, 3 ignored). The release voice recipe completed 6,336 Bazel actions and sealed same-source GNU voice inputs. The staged Cargo voice SDK verified GLib 2.88.3 and GStreamer 1.28.6 before the workspace retry.

The workspace retry with staged native inputs was interrupted before tests ran. Running it alongside the release build saturated the shared Forge build slice's 8 GiB memory-high threshold and both compilers spent most CPU time in reclaim. Interrupted only the verified workspace Cargo PID and will rerun the workspace proof serially. Forge's build limits were left in place.

The serial 8-job canonical release build produced stripped bwrap, the factory bridge and the code-mode host. Forge's per-scope 8 GiB limit killed the release Cargo scope before the remaining binaries linked. The user journal records `Failed with result 'oom-kill'` for scope `forge-build-20261117T141721-188753.scope`. No dist folder was published. Requested permission to retry with two Cargo jobs while keeping the release profile and eight test threads.

Owner authorized two Cargo build jobs with eight test threads. The release retry uses the same target, profile, source, features, and stripped bwrap stamp in the existing release cache. The canonical packager remains the entry point for assembling the package.

The two-job release retry also hit the same Forge OOM limit, late in app-server/TUI compilation (`forge-build-20261117T150839-636296.scope`). Retrying serially at one Cargo job within the approved ceiling, with the release profile, input bytes and eight test threads unchanged.

The one-job release reached final `codex` compilation but also hit the 8 GiB scope limit (`forge-build-20261117T154024-923049.scope`, journal result `oom-kill`). The canonical packager did not run on this retry, and `dist/1117a` is absent. The release now needs an owner decision between a larger approved build scope and reduced debug information. The two-job workspace proof with eight test threads is running separately.

Owner selected an Ops-provided larger approved release-build scope to preserve the original release profile. The builder will leave Forge's current limits in place. Continue the unprivileged workspace proof separately while the release scope is pending.

The pre-package release bridge passed all 64 unprivileged contract checks. The contract will be repeated against the actual packaged bridge after assembly.

The workspace compile with staged GLib/GStreamer reached `opusic-sys` and then found no host CMake. Reused the already fetched Bazel-pinned CMake 3.31.8 executable from the voice toolchain and restarted the workspace command with the same two Cargo jobs and eight test threads. No additional network or system installation was needed.

With the staged CMake, the two-job workspace build passed the Opus dependency and reached voice-host/core compilation. The build scope then spent minutes at 8 GiB with approximately 256 KiB of file cache and over 25 million file refaults, with neither crate completing. Interrupted only that workspace Cargo PID and restarted with one Cargo job and eight test threads. The same test selection and compiler profile remain in use.

## STATUS — paused by owner, 2026-11-17 18:58 CST

**Done.** Upstream merge `a24cbd078500328a6a5891ea5a370292176e1949` is local on `plan/003-upstream-sync-1117`. Config and both app-server schemas and the SDK generated successfully with a clean source diff. Doc tests passed (171 targets, 2 passed, 3 ignored), GitHub scripts passed (76), package-gate regressions passed (9), canonical Python package tests passed (27), voice assembly tests passed (17), and the pre-package bridge contract passed all 64 checks. The same-source GNU release voice build passed 6,336 Bazel actions. Voice helper SHA-256 is `f9ab3b5b0bac014f8f2dbeee1ce3a45009ab7632f0913678ef6ec62ce2495752`. The sealed voice runtime receipt SHA-256 is `b70c95a335e2d97a6162c26a86dd1840a1f1d67022a23c4d035536b650571b78`.

Verified release archive, uv and ripgrep digests are above. Other staged input SHA-256 values:

- Bazel 9.2.0 GNU x86_64 asset `7668a95db1250f12c40407251e4e203b4ec8bf39bc495d2f485b2d8c99048694`.
- GNU Rusty V8 152.2.0 archive `b3b490f2aa809eda7f6ac727f851fb00ce8355be72827577cfaac28c7137dbcd`, bindings `3e4d5fecdbe0f98a6017e69d7ca82519a77fb032c5eb5e4cdbecdd3e2ec519d5`, pinned checksum manifest `1ec730635970ab1c0e8f4764ea113ad29d69d7f8d4c2ae69f3d766434a759350`.
- Ubuntu `libcap-dev_2.66-5ubuntu2.4_amd64.deb` `07f2462867569a2119a2ad0f1593232663f2d1612b791c230d22a8d73a15abee`, `libasound2-dev_1.2.11-1ubuntu0.3_amd64.deb` `b83795b1ba0e40cfa8363359fd17a06260838d8ae0f315531771a62f7c7e215a`.
- Rustup authenticated the Rust 1.95.0 components. The locally staged channel manifest SHA-256 is `46c1f8d1bcef24174217545ece8c22eb395a42e3534f618736c17a759a31e255`. Python SDK dependencies came from the checked-in frozen uv lock. All 1,310 external Cargo lock records stayed unchanged.

**Pending.** The unchanged release hit Forge's 8 GiB ceiling at eight, two and one Cargo jobs. The owner reported four OOM kills. The retained user journal confirms OOM termination of all three release scopes named above. The one-job workspace scope's journal does not confirm an additional OOM, and Nextest reports only that the compile subprocess exited. Highest spot measurements recorded for the eight- and two-job release scopes were 8,587,563,008 and 8,588,095,488 bytes of `memory.current`. The one-job release was sampled at 7,987,105,792 bytes of `memory.current` before its final CLI compile was killed. The two-job workspace retry was sampled at 8,547,885,056 bytes of anonymous memory before interruption. These samples are lower bounds, and the ended scopes no longer exposed cgroup peak counters. The workspace build consumed about 56 GiB of target data and exceeded the owner's disk allowance on the shared Forge volume before any test cases ran. Comparison against the upstream pin remains pending for any failures once tests run.

At the stop order, the host process check found no Cargo process with the approved checkout's `codex-rs` working directory, so no signal was sent. Deleted only `/tmp/fork003-build/target`. `df -h /` then showed `/dev/sda2 492G 372G 115G 77% /`. Retained the release cache, voice inputs, Cargo and Rustup homes, native SDK, logs, and package cache. No further Cargo build or test was started. No 1117a package, packaged contract or voice handshake, SHA256SUMS, three packaged bwrap readings, exact model slug, fault pair, or packaged tool proof exists. The six separate-UID and signed-in proofs remain assigned to Ops. The draft stripped bwrap SHA-256 is `600bc13837f72386cf53f9e2e5681424ca9aced69c71b34da6eef6bd96153345`. This draft hash is from scratch and may differ after rebuilding.

**Release debug option.** The owner authorized an environment-only `CARGO_PROFILE_RELEASE_DEBUG=0` retry before issuing the stop. It was never started. It would disable Cargo's release debuginfo for Rust compilation units, losing source-level DWARF for postmortems. The fork packager strips bwrap and deliberately uses `--strip none` on the other release binaries. Upstream's separate release symbol-archive job expects symbols for its sidecar. The fork packager has no symbol-sidecar deliverable and the gate checks the shipped paths and executability. MANIFEST records ELF ABI symbol versions, which are distinct from debug information. Preserve the gate and record the debug override and scope telemetry in the plan and package qualification notes if used.

### Resume commands for a provisioned build machine

From the checkout of the paused branch, stage the retained inputs under the same scratch path or update the paths in the staged environment scripts. Use a machine with enough disk for the complete workspace target and release artifacts. Run the workspace and release builds serially. Keep Rust 1.95.0 and eight test threads.

```bash
set -euo pipefail
scratch=/tmp/fork003-build
source "$scratch/build-env.sh"
src=a24cbd078500328a6a5891ea5a370292176e1949
target=x86_64-unknown-linux-gnu
git diff --exit-code "$src" -- codex-rs

# Full workspace proof with the retained, receipt-checked native SDK.
(
  source "$scratch/voice-test-env.sh"
  export CARGO_BUILD_JOBS=1 TMPDIR=/tmp/fork003tmp HOME="$scratch/test-home"
  export XDG_CONFIG_HOME="$HOME/config" XDG_CACHE_HOME="$HOME/cache"
  export XDG_DATA_HOME="$HOME/data" XDG_STATE_HOME="$HOME/state"
  export GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_NOSYSTEM=1
  export CMAKE="$scratch/voice/bazel-root/87511efd1e72e0112d297cee009aba75/external/rules_foreign_cc++tools+cmake-3.31.8-linux-x86_64/bin/cmake"
  export PATH="$(dirname "$CMAKE"):$PATH"
  unset CODEX_HOME OPENAI_API_KEY OPENAI_ADMIN_KEY
  mkdir -p "$TMPDIR" "$XDG_CONFIG_HOME" "$XDG_CACHE_HOME" "$XDG_DATA_HOME" "$XDG_STATE_HOME"
  nice -n 10 just test --workspace --test-threads=8
)
# For failures, repeat the exact binary/test pairs on a git archive of
# d32063f96c2dff1dc96af839b3952abb6acfeda4 using the same proof environment.

# Preserve the stock profile on a provisioned scope. If the owner confirms the
# reduced-debug retry for this machine, export only CARGO_PROFILE_RELEASE_DEBUG=0.
export CARGO_BUILD_JOBS=1
release="$scratch/release"
work=$(mktemp -d "$scratch/resume.XXXXXXXX")
(
  cd codex-rs
  nice -n 10 cargo build --locked --release --target "$target" --target-dir "$release" -p codex-bwrap --bin bwrap
  cp "$release/$target/release/bwrap" "$work/bwrap"
  strip --strip-debug --strip-unneeded "$work/bwrap"
  export CODEX_BWRAP_SHA256="$(sha256sum "$work/bwrap" | cut -d ' ' -f 1)"
  export CODEX_FACTORY_REVISION="$src" STABLE_GIT_COMMIT="$src"
  nice -n 10 cargo build --locked --release --target "$target" --target-dir "$release" \
    --features codex-install-context/factory \
    -p codex-app-server -p codex-cli -p codex-code-mode-host -p codex-exec-server-cli -p codex-factory \
    --bin codex --bin codex-app-server --bin codex-code-mode-host --bin codex-exec-server --bin codex-factory-bridge
  nice -n 10 cargo build --locked --profile dev-small --target "$target" --target-dir "$scratch/fault" \
    -p codex-cli -p codex-factory \
    --features codex-install-context/factory,codex-app-server/factory-fixture-faults,codex-factory/fixture-faults \
    --bin codex --bin codex-factory-bridge
)
# Capture the actual scope memory.peak and memory.events while each scope exists.
nice -n 10 python3 -B scripts/factory/package.py --output dist/1117a \
  --source-commit "$src" --target "$target" --target-dir "$release" \
  --work-dir "$work/package" --voice-inputs "$scratch/voice"
nice -n 10 python3 -B scripts/factory/check_package.py dist/1117a
(cd dist/1117a && nice -n 10 sha256sum -c SHA256SUMS)
cp -a --reflink=auto dist/1117a "$work/relocated-1117a"
nice -n 10 timeout 40s python3 -B "$scratch/prove-package.py" dist/1117a "$work/relocated-1117a"
mkdir "$work/model-home"
env -i PATH=/usr/bin:/bin HOME="$work/model-home" CODEX_HOME="$work/model-home" \
  nice -n 10 dist/1117a/codex debug models --bundled > "$work/bundled-models.json"
nice -n 10 python3 -B scripts/factory/qualify_contract.py \
  --binary dist/1117a/bin/codex-factory-bridge --evidence "$work/contract"
```

The retained `prove-package.py` prints the bwrap resource, MANIFEST and embedded CLI digests and requires all three to match, then runs the relocated voice handshake. Use `OPS-QUALIFIERS.md` for the six separate-UID commands and signed-in catalog, execution and tool proofs. After recording the actual results and build debug profile, seal them with `nice -n 10 python3 -B scripts/factory/package.py --output dist/1117a --record-qualification "$work/qualification.json"` and recheck SHA256SUMS. Document the release and changelog before separate merge review.

## Package facts — 2026-11-18

- Canonical package `/home/ai/codex-fork-build/dist/1117a`, Ops release folder `a24cbd078500328a6a5891ea5a370292176e1949-1117a`, CLI `0.169.0+ignibyte.1117a`.
- Independent completeness gate passed with empty missing, nonexecutable and error lists. All 52 SHA256SUMS entries passed.
- The packaged bwrap file, `MANIFEST.json` field `bwrapEmbeddedSha256`, and exact embedded CLI string all read `600bc13837f72386cf53f9e2e5681424ca9aced69c71b34da6eef6bd96153345`.
- Bundled IDs from the packaged CLI are `gpt-6-astra`, `gpt-6.1-sol`, `gpt-6.1-terra`, `gpt-6-sol`, `gpt-6-luna`, `gpt-5.6-sol`, `gpt-5.6-terra`, `gpt-5.6-luna`, `gpt-daybreak-blue-latest`, `gpt-daybreak-red-latest`, `gpt-5.5`, `codex-auto-review`. Signed-in GPT-6.2 Sol discovery and execution remain with Ops.
- Packaged bridge contract passed 64 checks. A relocated whole-package copy passed Hello, InitializeRuntime and Close. The initial voice probe used a different registry path and was correctly refused. The corrected probe used the required `GST_REGISTRY=/dev/null`.

Ops scope `forge-build-fork003-20261117T193146.scope` passed at one Cargo job with the unchanged optimized + debuginfo release profile. Peak was 10,885 MiB and Cargo elapsed time was 286m 11s. Canonical packaging passed in `forge-build-fork003-20261118T001935.scope` with a 4,343 MiB peak. No reduced-debug release override was used.

The owner directed the workspace proof to use one Cargo job and eight test threads with `CARGO_PROFILE_DEV_DEBUG=0 CARGO_PROFILE_TEST_DEBUG=0`. Those overrides apply only to the workspace proof. The 50 GiB disk floor remains active. Six separate-UID qualifiers and live signed-in tool and model proof remain pending Ops.

## Workspace proof result — 2026-11-18

After the package facts commit, removed only `/tmp/fork003-build/release` and `/tmp/fork003-build/big-package-work` as directed. Free space after removal was 114 GiB. The installed-package candidate in `dist/1117a` remains at the verified bytes.

`nice -n 10 just test --workspace --test-threads=8` ran through Ops' wrapper at one Cargo job with `CARGO_PROFILE_DEV_DEBUG=0 CARGO_PROFILE_TEST_DEBUG=0` and the receipt-checked staged voice SDK. The full compile finished in 166m 41s with a 37 GiB target. Scope `forge-build-fork003-20261118T003330.scope` peaked at 10,392 MiB and exited 104 during discovery, before any cases ran. The compiled voice-host test executable needed `libgstapp-1.0.so.0`. Verified the test list independently with `LD_LIBRARY_PATH=/tmp/fork003-build/voice-cargo/runtime/lib`, then reran the workspace command with that staged runtime on its loader path. No source, release profile or test selection changed.

The runtime pass completed in 2,160.869 seconds in `forge-build-fork003-20261118T032239.scope` with an 8,540 MiB peak and exit 100. Nextest ran 24,701 cases across 329 binaries. There were 24,534 passes (24 slow, two flaky), 166 failures, one timeout and 54 skipped. The failure counts are exec-server 54, TUI 42, app-server 33, core 21, CLI 4, Linux sandbox 3, multi-environment copy-file 3, git attribution 2, V8 proof-of-concept 2, app-server transport 1 and exec-server CLI 1. The exact cases and diagnostics remain in `/tmp/fork003-build/big-workspace-runtime.log`. The watchdog did not stop either scope. Free space ended at 71 GiB.

The workspace suite is red. Comparison of these exact failures and timeout on the pinned upstream source remains pending. There is no basis yet to attribute all failures to upstream or to waive them. The production package passed its independent completeness, checksum, offline catalog, relocated voice handshake and contract proofs. The fault pair, six separate-UID qualifiers, signed-in GPT-6.2 Sol discovery and execution, and live packaged shell/write/MCP proofs remain pending. The packaged docs still use the historical 1112d setup examples. The tracked release notes give Ops the 1117a folder name and digest without resealing the package after the handoff.
