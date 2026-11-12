# Fork changelog

- 2026-11-12: Plan 002 imports upstream c0cc9fc820 (74 commits), preserves the factory mailbox and roles, builds the x86_64 GNU release pair, and documents the foreman interface; all seven mailbox qualifiers pass, with 14 workspace failures freshly reproduced on pinned upstream and recorded in the plan.

- 2026-11-10: Plan 001 merges upstream 627b579219 (791 commits since the common base), retaining restored factory integration, persistent agents, roles, cross-account messaging and the fork workflow; regenerate the lockfile and protocol schemas with Cargo/upstream tooling. Local mailbox qualification: 22 PASS, one pre-existing delivery-fixture failure and 19 explicit skips, recorded in the plan.

- 2026-11-09: reset to the 10-27 factory state (cf021-brief-run-a); added WORKFLOW.md and AGENTS.md.
