# Fork changelog

- 2026-11-12: Plan 004 R adds deferred remote admission, same-key host renewals and crash-resumable seat credential publication through a root-owned allowlist/timer in complete package 1112e.

- 2026-11-12: Plan 002 corrects the shipped-package gate and adds the upstream voice bundle and root launcher in 1112d, preserving the 1112c CLI/bwrap bytes and sandbox precondition.

- 2026-11-12: Plan 002 adds manifest-pinned sandbox preflight and plain enrollment refusal for restricted Ubuntu, documents relocation/Ops preconditions, and prints bwrap digests in package READY lines without rebuilding 1112c.

- 2026-11-12: Plan 002 adds the tested, separate-UID Forge/dev-3 operator runbook and setup helper to the whole package, with restricted SSH admission, explicit enrollment and a no-model restart/replay proof.

- 2026-11-12: Plan 002 complete-package fix uses upstream's full Linux package layout plus the bridge, adds a mandatory package completeness gate, and requires packaged tool execution proof.

- 2026-11-12: Plan 002 release follow-up stamps `0.167.2+ignibyte.1112b` for current model discovery, prepares the separate `dist/1112b` release, and documents an isolated fork `CODEX_HOME`.

- 2026-11-12: Plan 002 imports upstream c0cc9fc820 (74 commits), preserves the factory mailbox and roles, builds the x86_64 GNU release pair, and documents the foreman interface; all seven mailbox qualifiers pass, with 14 workspace failures freshly reproduced on pinned upstream and recorded in the plan.

- 2026-11-10: Plan 001 merges upstream 627b579219 (791 commits since the common base), retaining restored factory integration, persistent agents, roles, cross-account messaging and the fork workflow; regenerate the lockfile and protocol schemas with Cargo/upstream tooling. Local mailbox qualification: 22 PASS, one pre-existing delivery-fixture failure and 19 explicit skips, recorded in the plan.

- 2026-11-09: reset to the 10-27 factory state (cf021-brief-run-a); added WORKFLOW.md and AGENTS.md.
