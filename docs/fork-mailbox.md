# Fork mailbox and the 2026-11-12 upstream sync

Plan 002 imports upstream `c0cc9fc820f62e4dbdde68214dd6d792d6c8a7ee` while retaining the factory mailbox, app-server integration, CLI additions, and agent roles.

The release artifacts are prepared under `dist/1112/`: `codex`, `codex-factory-bridge`, `MANIFEST.json`, `SHA256SUMS`, and `INTERFACE.md`. The target is `x86_64-unknown-linux-gnu` (glibc), requiring glibc 2.39 or newer and the OpenSSL 3.0 symbol ABI. The CLI also needs `libbz2.so.1.0`. Check the manifest's complete library/version requirements against the destination Ubuntu host before installation. This sync does not install or restart anything.

For bot integration, read `dist/1112/INTERFACE.md`, which cites the implementation for each call and rule. Address messages to an enrolled seat UUID; mailbox conversation IDs and Codex thread IDs serve different purposes. A sender persists one message UUID and immutable payload for retries. Receipts distinguish local acceptance, service acceptance, delivery, consumption, and completion.

Bridge delivery alone does not start a Codex turn. On the recipient app-server, call `thread/factoryMessage` with the existing `threadId`, `project`, and `messageId`. `queue_only` stays queued; `wake_when_idle` starts an idle session or returns `deferred`. Inspect the reason: Plan mode, server draining, pending trigger input, and a busy thread can prevent admission. Retry when that condition clears, using the same IDs. Repeat that call to reconcile a started turn and publish its consumption/completion receipts from durable history. An uncertain result requires reconciliation rather than a new message UUID.

Verify the artifact checksums from their directory:

```sh
cd dist/1112
sha256sum -c SHA256SUMS
```

Qualification results and any blocking failures are recorded in `plans/002-upstream-sync-1112.md`. Final main merge and push require the reviewer's approval after validation.

The release-version follow-up is packaged separately in `dist/1112b/`; `dist/1112/` stays frozen. Its workspace version is `0.167.2+ignibyte.1112b`: the base follows upstream's stable release tag, while build metadata identifies the fork. Model discovery and its cache use the numeric `0.167.2` version. For future builds, stamp `[workspace.package].version` before building and regenerate the lockfile with `cargo update --workspace`; a development `0.0.0` build can receive an older model catalog. Check both `--version` and the manifest's full source revision before installation.

Run the fork with a dedicated `CODEX_HOME`, including the future Forge spec agent. For example, set `CODEX_HOME=/home/ai/.local/share/fleet-codex/home` in its launcher/service and create that directory with mode 0700. Use the normal Codex login flow for this home. Keep stock's default `~/.codex` separate: the real version fixes catalog eligibility, but sharing a home still shares model caches, config, credentials, and session state. Never put credential contents in a runbook, manifest, or log.
