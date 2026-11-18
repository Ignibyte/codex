# Fork changelog

- 2026-11-18 — Plan 003 stabilizes stamped-build snapshots and corrects the standalone executor version and Kitty file-reference tests.

- 2026-11-18 — Plan 003 normalizes package permissions and checks readability for installed seats.

- 2026-11-18 — Plan 003 merges upstream Codex 0.169.0 and builds the complete 1117a package. Workspace failures and remaining Ops proofs are recorded in the plan.

- 2026-11-12: Plan 002 corrects the shipped-package gate and adds the upstream voice bundle and root launcher in 1112d, preserving the 1112c CLI/bwrap bytes and sandbox precondition.

- 2026-11-12: Plan 002 adds manifest-pinned sandbox preflight and plain enrollment refusal for restricted Ubuntu, documents relocation/Ops preconditions, and prints bwrap digests in package READY lines without rebuilding 1112c.

- 2026-11-12: Plan 002 adds the tested, separate-UID Forge/dev-3 operator runbook and setup helper to the whole package, with restricted SSH admission, explicit enrollment and a no-model restart/replay proof.

- 2026-11-12: Plan 002 complete-package fix uses upstream's full Linux package layout plus the bridge, adds a mandatory package completeness gate, and requires packaged tool execution proof.

- 2026-11-12: Plan 002 release follow-up stamps `0.167.2+ignibyte.1112b` for current model discovery, prepares the separate `dist/1112b` release, and documents an isolated fork `CODEX_HOME`.

- 2026-11-12: Plan 002 imports upstream c0cc9fc820 (74 commits), preserves the factory mailbox and roles, builds the x86_64 GNU release pair, and documents the foreman interface; all seven mailbox qualifiers pass, with 14 workspace failures freshly reproduced on pinned upstream and recorded in the plan.

- 2026-11-10: Plan 001 merges upstream 627b579219 (791 commits since the common base), retaining restored factory integration, persistent agents, roles, cross-account messaging and the fork workflow; regenerate the lockfile and protocol schemas with Cargo/upstream tooling. Local mailbox qualification: 22 PASS, one pre-existing delivery-fixture failure and 19 explicit skips, recorded in the plan.

- 2026-11-09: reset to the 10-27 factory state (cf021-brief-run-a); added WORKFLOW.md and AGENTS.md.

## Known red on rust-v0.169.0 on this box

- `transport::remote_control::tests::remote_control_waits_for_account_id_before_enrolling` — Enrollment request missed the 100 ms deadline after auth reload.
- `suite::v2::mcp_server_status::mcp_server_status_list_tools_and_auth_only_skips_slow_inventory_calls` — Tools/auth-only MCP inventory exceeded its 500 ms response deadline.
- `suite::v2::web_search::model_lab_search_provider::critical_discovery_error` — Two scoped model-lab tools/list calls instead of one.
- `suite::v2::web_search::model_lab_search_provider::failed_invocation_never_replays_on_native` — Two scoped model-lab tools/list calls instead of one.
- `suite::v2::web_search::model_lab_search_provider::host_disables_literature_only` — Two scoped model-lab tools/list calls instead of one.
- `suite::v2::web_search::model_lab_search_provider::inherit_connector_version` — Two scoped model-lab tools/list calls instead of one.
- `suite::v2::web_search::model_lab_search_provider::matching_query_filter_cannot_authorize_cached_navigation` — Two scoped model-lab tools/list calls instead of one.
- `suite::v2::web_search::model_lab_search_provider::work_hosted_only` — Two scoped model-lab tools/list calls instead of one.
- `suite::v2::web_search::model_lab_search_provider::xatu_control` — Two scoped model-lab tools/list calls instead of one.
- `suite::v2::web_search::model_lab_search_provider::xatu_literature_treatment` — Two scoped model-lab tools/list calls instead of one.
- `suite::v2::turn_start::turn_start_enforces_managed_network_sandbox` — Managed-network proxy probe returned EAGAIN after the sandbox child started.
- `tests::policy_resolution_retries_after_auth_refresh` — Policy remained absent after auth refresh.
- `tests::world_state_stops_after_rejected_refresh` — Mock settings endpoint received one matching request instead of two.
