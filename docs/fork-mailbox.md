# Fork mailbox and the 2026-11-12 upstream sync

Plan 002 imports upstream `c0cc9fc820f62e4dbdde68214dd6d792d6c8a7ee` while retaining the factory mailbox, app-server integration, CLI additions, and agent roles.

The release artifacts are prepared under `dist/1112/`: `codex`, `codex-factory-bridge`, `MANIFEST.json`, `SHA256SUMS`, and `INTERFACE.md`. The target is `x86_64-unknown-linux-gnu` (glibc). Check the manifest's required GLIBC versions and libraries against the destination Ubuntu host before installation. This sync does not install or restart anything.

For bot integration, read `dist/1112/INTERFACE.md`, which cites the implementation for each call and rule. Address messages to an enrolled seat UUID; mailbox conversation IDs and Codex thread IDs serve different purposes. A sender persists one message UUID and immutable payload for retries. Receipts distinguish local acceptance, service acceptance, delivery, consumption, and completion.

Bridge delivery alone does not start a Codex turn. On the recipient app-server, call `thread/factoryMessage` with the existing `threadId`, `project`, and `messageId`. `queue_only` stays queued; `wake_when_idle` starts an idle session or returns `deferred`. Retry deferred delivery using the same IDs. Repeat that call to reconcile a started turn and publish its consumption/completion receipts from durable history. An uncertain result requires reconciliation rather than a new message UUID.

Verify the artifact checksums from their directory:

```sh
cd dist/1112
sha256sum -c SHA256SUMS
```

Qualification results and any blocking failures are recorded in `plans/002-upstream-sync-1112.md`. Final main merge and push require the reviewer's approval after validation.
