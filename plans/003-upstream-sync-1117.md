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

## Ops qualification follow-up — approved

Goal — make the complete fork package readable to installed seats while preserving authenticated mailbox qualification. Normalize package directories and executable entry points to 0755 and regular data files to 0644 after copying assets, leaving the relative launcher symlink intact. Check permission bits independently of the builder UID and reject unreadable package files. Preserve runtime bytes, manifests and checksums in the existing 1117a mode-only repair. No install, service changes or Rust changes.

Files — `scripts/factory/package.py`, `scripts/factory/check_package.py`, `scripts/factory/test_check_package.py`, this plan, and `CHANGELOG-FORK.md`. Proof — run `nice -n 10 python3 -B -m unittest discover -s scripts/factory -p test_check_package.py` including a restrictive-umask regression, `nice -n 10 python3 -B scripts/factory/check_package.py dist/1117a`, and `sha256sum -c SHA256SUMS` from the package. Build the already approved fault pair after workspace completion using the wrapper and existing manifest bwrap stamp. Ops' four reported passes are credentials (7), recovery (9), native (5), and lifecycle (4).

Reviewer (Claude owner seat): APPROVED 04:05 CST 11-18, the package-mode follow-up as written (0755 directories and executables, 0644 data files, the launcher symlink kept, a gate check that refuses a file other users cannot read, a restrictive-umask regression test, runtime bytes and checksums unchanged). No Rust change, no install.

Mode repair result — changed the 13 specified installed-package files from 0600 to 0644. Both the independent gate and all 52 SHA256SUMS entries passed after chmod. The revised gate also passed against 1117a. Ten package-gate tests passed, including world-readability rejection and recovery from umask 077. The 1117a manifest and checksum bytes were preserved.

There are seven mailbox qualifiers in total. The unprivileged seventh is the builder's contract lane (64 checks passed). Ops owns six separate-UID lanes and ran the first four from a byte-identical readable copy of `scripts/factory`. `/tmp/fork003-build/OPS-QUALIFIERS.md` now records that requirement and the split.

Fault pair passed at one job in `forge-build-fork003-20261118T040812.scope` (59m 34s, 6,508 MiB peak). Both executables are under `/tmp/fork003-build/fault/x86_64-unknown-linux-gnu/dev-small`. CLI factory-info matches the packaged source revision and disables public updates. The embedded CLI bwrap stamp matches the production package. Fault CLI SHA-256 `6224d36aa923603a6ed5de0cf5fbc443a0720915113ccf7965c7cf7b02f09ba8`. Fault bridge SHA-256 `b33f6e9fcb252ed20fe9bf57a965b62e1fcd7289201ebe79c6d272be7ad657cc`. Ops owns the remaining durability and reconciliation runs. The unchanged production release profile uses `debug = "line-tables-only"` as declared in Cargo.toml.

## Workspace triage — 2026-11-18

**STOP. Two test defects and 93 failures or timeouts with an unverified cause block qualification.**

- **D, 12569 — `standalone_initializes_reports_version_and_runs_helpers`.** The test expects the stamped Cargo version in a source-build RPC response. `BuildInfo::resolve` explicitly returns `0.0.0` without a package manifest. The log also says the sandbox subcase was unavailable, but execution advanced to the version assertion.
- **D, 22793 — `kitty_local_file_pet_image_uses_file_reference_without_inline_payload`.** The test bans `cG5n` anywhere in the escape sequence, even inside the legitimate encoded path. For the configured TMPDIR and tempfile name length, Base64 of `/tmp/fork003tmp/.tmpABCDEF/frame.png` ends in `cG5n`. The file-reference-mode assertion preceding the failure passed.
- **Unplaced (93).** Filesystem helper SIGABRT without stderr, sandbox command exits 134, workspace writes followed by ENOENT, multi-environment copy JSON decoding, git-attribution refresh/mock mismatch, duplicate model-lab search discovery, permission grant and approved-command assertions, remote-control auth wake and RPC deadlines, missing shell capture, tool-metadata timeout, and restricted-file MCP mock misses. Exact ordinals and evidence are listed first in the table.

The D findings concern test fixtures. Both test files match the pinned upstream tree at the merge. This analysis establishes no shipped-runtime regression from those two cases. The release remains blocked. The SDK/profile and package proofs earlier in the plan do not waive any workspace outcome.

`/tmp/fork003-build/big-workspace-runtime.log:35098` records 24,701 run, 24,534 passed, 166 failed, one timed out, 54 skipped, and 2,160.869 seconds. Scope peak was 8,540 MiB. The table uses Nextest ordinals and one-based log line numbers. Its 167 unique ordinals exactly match the terminal failure and timeout recap. Retries and recovered flaky tests are excluded. No new compile or test was started for this analysis.

Plan 002 reports 24,687 passes and 14 failures across 24,701 executed cases on the desktop, after recovering from the quota and temp-path issues. Both current V8 cases are exact matches from its enumerated failure list and reproduced on its pinned upstream. The other 12 historical failures are different cases. A case from this table would have passed Plan 002's desktop sweep if selected there, but the plan does not enumerate each pass or establish that the earlier upstream pin selected every current case. The Plan 002 workspace run had the development stamp and its later version-only follow-up did not rerun the workspace.

| Class | Count | Nextest ordinals | Diagnostic and evidence | Condition to pass or evidence still needed | Plan 002 desktop |
| --- | ---: | --- | --- | --- | --- |
| D | 1 | 12569 | L19217–19225: standalone initialize returns `0.0.0` but the test compares with `CARGO_PKG_VERSION`. The producer uses `BuildInfo::version()`, whose explicit source-build fallback is `0.0.0` without a package manifest. The stamped Cargo version exposes an incorrect test expectation. | Source-build and installed-package expectations need separate assertions. | Outside Plan 002’s recorded 14 failures. Exact pass and environment are not recorded individually. |
| D | 1 | 22793 | L31355–31356: Kitty file-reference assertion rejects `cG5n` anywhere in output. File mode base64-encodes the canonical path, so a Base64-aligned `png` suffix itself produces the forbidden text. The prior `t=f` assertion succeeded. This is a path-dependent test assertion defect. | Decode the referenced path and assert the transfer mode and payload. | Outside Plan 002’s recorded 14 failures. Exact pass and environment are not recorded individually. |
| Unplaced | 5 | 1485, 2469, 3476, 7961, 10893 | L1576: auth change fails to wake remote control before retry delay. L3773: MCP inventory deadline. L6919: managed network probe deadline. L15253: no shell capture file before timeout. L11657–11664: tool-metadata message-budget case hits 60.021s limit with no diagnostic. | Trace the awaited wake, RPC, file creation or pure test work separately. The timeouts do not establish common causation. | Outside Plan 002’s recorded 14 failures. Exact pass and environment are not recorded individually. |
| Unplaced | 4 | 2915, 2919, 2925, 8955 | L4553–4563: second approval after the next turn starts. L4787–4797 and L4853–4863: grant count/scope assertion. L12903–12908: approved command result reports an unexpected boolean. These assert runtime behavior, with no direct evidence of an environmental trigger. | Compare grants and command results against pinned upstream. | Outside Plan 002’s recorded 14 failures. Exact pass and environment are not recorded individually. |
| Unplaced | 8 | 3454, 3456, 3458, 3459, 3464, 3472, 3473, 3474 | L5881–5886 and L6455–6460: 2 scoped `tools/list` calls where the fixture expects 1. The assertion counts calls matching the model-supported-tools metadata. No stamp-only snapshot or connection failure. | Compare connector discovery calls against the pinned upstream and trace the duplicate request. | Outside Plan 002’s recorded 14 failures. Exact pass and environment are not recorded individually. |
| Unplaced | 59 | 8011, 8012, 8013, 8087, 8761, 9576, 12223, 12230, 12231, 12232, 12254, 12280, 12338, 12348, 12349, 12350, 12351, 12364, 12365, 12367, 12368, 12369, 12370, 12371, 12372, 12373, 12374, 12375, 12376, 12377, 12381, 12382, 12383, 12384, 12385, 12388, 12391, 12394, 12397, 12414, 12420, 12423, 12426, 12430, 12431, 12432, 12433, 12436, 12437, 12439, 12445, 12451, 12453, 12454, 12455, 12465, 12468, 12470, 12471 | L11777, L16863, L17373: filesystem sandbox helper aborts with SIGABRT and empty stderr. The symlink assertions see RPC internal errors instead of the expected denial, and positive reads/writes fail too. This establishes the failing process, but cannot distinguish a missing bwrap, abort inside bwrap, namespace/Landlock initialization or a helper defect. | Retain helper stderr and distinguish package lookup from sandbox entry in the approved reproduction. | Outside Plan 002’s recorded 14 failures. Exact pass and environment are not recorded individually. |
| Unplaced | 7 | 8366, 9598, 9934, 10284, 10285, 11156, 12229 | L12237–12240, L13767–13772, L14150–14156, L14578–14585, L15679–15680, L16886–16897: shell exits 134 with empty or missing output. Workspace-root denial gets abort instead of denial. The root cause of the abort is absent. | Correlate with helper stderr. | Outside Plan 002’s recorded 14 failures. Exact pass and environment are not recorded individually. |
| Unplaced | 1 | 9582 | L13708 onward: restricted file MCP retry gets zero calls for mocks that expected one. Companion file test shows the sandbox helper abort, but this case records only the mock mismatch. | Capture the earlier file/MCP failure before mock verification. | Outside Plan 002’s recorded 14 failures. Exact pass and environment are not recorded individually. |
| Unplaced | 4 | 11123, 11190, 11191, 11202 | L15630, L15730, L15746, L15772: ENOENT while reading expected workspace outputs. A preceding helper failure is plausible, but the messages do not identify the missing item or why it was missing. | Capture write/patch results before reading the outputs. | Outside Plan 002’s recorded 14 failures. Exact pass and environment are not recorded individually. |
| Unplaced | 2 | 12953, 12980 | L19679–19681, L19722–19736: attribution policy absent after refresh and loopback settings mock sees 1 request instead of 2. A loopback request reached the mock. No evidence of an external network failure or a TMPDIR path failure. | Expose the refresh result and mock settings call sequence. | Outside Plan 002’s recorded 14 failures. Exact pass and environment are not recorded individually. |
| Unplaced | 3 | 14992, 15054, 15089 | L21937, L22034, L22082–22086: JSON `expected value at line 1 column 1`. The last case wraps it in a partial-copy error. No producer stderr or offending bytes are shown. | Trace the copy subprocess and capture its stderr and first response. | Outside Plan 002’s recorded 14 failures. Exact pass and environment are not recorded individually. |
| A | 24 | 1901, 1902, 1903, 1905, 1906, 1907, 1927, 1928, 1930, 1931, 1989, 2107, 2109, 2110, 2913, 2916, 3175, 3187, 3694, 3695, 9571, 13956, 13957, 13958 | L2140, L3223, L20816: launcher explicitly reports no system bwrap on PATH and no adjacent bundled resource. Includes all three failing Linux sandbox cases and all three failing executor-skills cases. | Expose a working bwrap to source test binaries on PATH and verify sandbox launch with the host namespace policy. | Outside Plan 002’s recorded 14 failures. Exact pass and environment are not recorded individually. |
| A | 2 | 1904, 1929 | L2342–2352, L2805: output cap limits stderr to the panic prefix `\nthre` and stdout is empty. Both are in the same command/exec fixture as the explicit missing-bwrap failures and report the missing-bwrap warning. These two are inferred from the matching panic prefix and sibling evidence. | Use the same working bwrap precondition as the full-stderr cases. | Outside Plan 002’s recorded 14 failures. Exact pass and environment are not recorded individually. |
| A | 1 | 11046 | L15447–15454: user shell prints `1` for `CODEX_SANDBOX_NETWORK_DISABLED` where the test expects `not-set`. The variable is present in the outer session and the runner has no unset. Historical inheritance is inferred from the value and script. | Launch the proof with the ambient network-sandbox marker cleared and let the tested runner set its own markers. | Outside Plan 002’s recorded 14 failures. Exact pass and environment are not recorded individually. |
| A | 4 | 21670, 21675, 21676, 21681 | L29742–29747, L29808–29817, L29836–29839, L29875–29880: color SGR is stripped and the emulator sees Default instead of RGB. The terminal writer emits colors through Crossterm and the outer session has `NO_COLOR`. The exact original environment was not captured. | Clear inherited color disabling and force color output for these deterministic escape-sequence checks. | Outside Plan 002’s recorded 14 failures. Exact pass and environment are not recorded individually. |
| B | 2 | 3533, 3535 | L7066–7067 and L7104–7105: `-Codex Doctor v0.0.0` / `+Codex Doctor v0.169.0+ignibyte.1117a`. | Use deterministic version fixtures for the Doctor snapshots. | Outside Plan 002’s recorded 14 failures. Exact pass and environment are not recorded individually. |
| B | 11 | 18215, 18218, 18392, 18401, 18621, 18665, 18666, 18682, 18687, 18817, 18899 | L25341–25349: `-Use this CLI package v0.0.0 from …` / `+Use this CLI package v0.169.0+ignibyte.1117a from …`, with wrap and padding. L26234–26235: `->_ OpenAI Codex (v0.0.0)` / `+>_ OpenAI Codex (v0.169.0+ignibyte.1117a)`. Reconnect service warnings also change only their CLI version. | Pin the rendered version in the fixture before layout, or use reviewed stamped snapshots. | Outside Plan 002’s recorded 14 failures. Exact pass and environment are not recorded individually. |
| B | 4 | 21987, 22031, 22033, 22035 | L30263–30270: `-Update available! 0.0.0 -> 9.9.9` / `+Update available! 0.169.0+ignibyte.1117a -> 9.9.9` with box width adjustment. L30464–30465 changes the header version only. | Pin the rendered version before layout or use reviewed stamped snapshots. | Outside Plan 002’s recorded 14 failures. Exact pass and environment are not recorded individually. |
| B | 1 | 23020 | L31642–31643: same masked `v<VERSION>` on both sides, with only trailing spaces different. Version is normalized after rendering, leaving the longer header’s padding difference. | Pin the version before layout so its padding is stable. | Outside Plan 002’s recorded 14 failures. Exact pass and environment are not recorded individually. |
| B | 19 | 23091, 23092, 23093, 23094, 23095, 23098, 23099, 23100, 23103, 23106, 23107, 23108, 23109, 23110, 23111, 23112, 23115, 23130, 23158 | L31946–31947: `-OpenAI Codex (v0.0.0)` / `+OpenAI Codex (v0.169.0+ignibyte.1117a)`. The narrow kana fixture wraps the version onto its own line at L32928–32930. | Pin the version before layout or use reviewed stamped snapshots. | Outside Plan 002’s recorded 14 failures. Exact pass and environment are not recorded individually. |
| B | 2 | 23922, 23930 | L34205–34206: `-Update available · 0.0.0 → 9.9.9` / `+Update available · 0.169.0+ignibyte.1117a → 9.9.9`. L34154–34162 shows the same substitution wrapping and pushing the release-notes continuation out of the short viewport. | Pin the version before layout or use reviewed stamped snapshots. | Outside Plan 002’s recorded 14 failures. Exact pass and environment are not recorded individually. |
| C | 1 | 24456 | L34818–34825: compares linked V8 sandbox capability (`true`) with Rust `cfg!(feature = "sandbox")` (`false`). | Use a V8 artifact/configuration with sandbox mode matching the Rust feature. Plan 002 records the exact same failure on the desktop and its pinned upstream. | Same failure on desktop and pinned upstream |
| C | 1 | 24457 | L34843–34849 and `v8-poc/src/lib.rs:165–183`: for byte length 0, native `view.has_buffer()` is false but expected true from the binding’s compile-time `TYPED_ARRAY_MAX_SIZE_IN_HEAP` predicate. This is the placement assertion before reading bytes, with a stale size-zero binding identified by the fixture comment. | Use an ABI-matched native V8 artifact and Rust bindings generated with the same typed-array layout and constants. Plan 002 records the exact same failure on the desktop and its pinned upstream. | Same failure on desktop and pinned upstream |

**Totals** — A 31, B 39, C 2, D 2, unplaced 93. Class A includes seven environment attributions inferred from truncated child output or the still-visible outer environment, with the supporting observations in the table. Class B is entirely version formatting, including version-induced layout. Class C is a native-artifact or binding configuration mismatch. The log provides no missing-symbol or backtrace-dependent failure attributable to `CARGO_PROFILE_DEV_DEBUG=0 CARGO_PROFILE_TEST_DEBUG=0`. The normal panic hint about `RUST_BACKTRACE` is not evidence of a debug-info dependency. Neither Landlock nor user-namespace policy is independently diagnosed in this log.
