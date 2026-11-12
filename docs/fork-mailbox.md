# Fork mailbox and the 2026-11-12 upstream sync

Plan 002 imports upstream `c0cc9fc820f62e4dbdde68214dd6d792d6c8a7ee` while retaining the factory mailbox, app-server integration, CLI additions, and agent roles.

The complete release is prepared under `dist/1112e/`. Install the **whole folder**: `codex-package.json`, `bin/`, `codex-resources/`, `codex-path/`, manifest, checksums, interface, top-level `codex -> bin/codex` link, and the entire `codex-resources/voice/` bundle (helper, native libraries/plugins, manifests and licenses). The target is `x86_64-unknown-linux-gnu` (glibc), requiring glibc 2.39 or newer and the OpenSSL 3.0 symbol ABI. The CLI also needs `libbz2.so.1.0`. Check the manifest's complete library/version requirements against the destination host before installation. This work does not install or restart anything. Frozen 1112/1112b lack tool hosts, and 1112c lacks the shipped voice bundle/root launcher. They are incomplete packages: their successful no-tool turns did not qualify shell execution.

For bot integration, read `dist/1112e/INTERFACE.md`, which cites the implementation for each call and rule. Address messages to an enrolled seat UUID; mailbox conversation IDs and Codex thread IDs serve different purposes. A sender persists one message UUID and immutable payload for retries. Receipts distinguish local acceptance, service acceptance, delivery, consumption, and completion.

Bridge delivery alone does not start a Codex turn. On the recipient app-server, call `thread/factoryMessage` with the existing `threadId`, `project`, and `messageId`. `queue_only` stays queued; `wake_when_idle` starts an idle session or returns `deferred`. Inspect the reason: Plan mode, server draining, pending trigger input, and a busy thread can prevent admission. Retry when that condition clears, using the same IDs. Repeat that call to reconcile a started turn and publish its consumption/completion receipts from durable history. An uncertain result requires reconciliation rather than a new message UUID.

Verify the artifact checksums from their directory:

```sh
cd dist/1112e
sha256sum -c SHA256SUMS
```

Qualification results and any blocking failures are recorded in `plans/002-upstream-sync-1112.md`. Final main merge and push require the reviewer's approval after validation.

The release-version follow-up is packaged separately in `dist/1112b/`; `dist/1112/` stays frozen. Its workspace version is `0.167.2+ignibyte.1112b`: the base follows upstream's stable release tag, while build metadata identifies the fork. Model discovery and its cache use the numeric `0.167.2` version. For future builds, stamp `[workspace.package].version` before building and regenerate the lockfile with `cargo update --workspace`; a development `0.0.0` build can receive an older model catalog. Check both `--version` and the manifest's full source revision before installation.

Create every future release through `python3 scripts/factory/package.py --output dist/<release> --work-dir <fresh-isolated-directory>`. It derives the Linux binaries from upstream's release action, uses the canonical builder and upstream voice release assembly, adds the installed root launcher/bridge, embeds the stripped bwrap digest, and refuses an incomplete package. `scripts/factory/upstream-shipped-linux.json` records the official 0.167.2 archive's verified inventory plus upstream's installer link. The gate requires that shipped inventory **and** the derived layout; it compares path coverage, not stock binary bytes. The voice helper and runtime must match the CLI source commit. The fork version extension accepts SemVer build metadata while keeping the upstream source/target/hash checks. For a correction from the same runtime source, `--base-package dist/1112c --source-commit 6cb7fd718329da87bfd1dcf4c10536cf82b1c367` verifies/copies the whole base package and preserves its binaries; `--voice-inputs <build_voice.py output>` can reuse verified same-source voice inputs. Otherwise the isolated build runs upstream's Bazel release targets and stage/seal steps. No system tool installation is required; the pinned official Bazel binary and caches stay in the work directory. The independent gate is `python3 scripts/factory/check_package.py dist/<release>`. An upstream archive assembly change requires reviewing its complete file contract before updating the gate's reviewed digest. Keep the root metadata and sibling resource/path directories when installing; copying only executables breaks the package contract. The runnable paths are `bin/codex`, `bin/codex-app-server`, and `bin/codex-factory-bridge` within that root. Packaged tool turns and all mailbox qualifiers must pass before a package is ready.

Run the fork with a dedicated `CODEX_HOME`, including the future Forge spec agent. For example, set `CODEX_HOME=/home/ai/.local/share/fleet-codex/home` in its launcher/service and create that directory with mode 0700. Use the normal Codex login flow for this home. Keep stock's default `~/.codex` separate: the real version fixes catalog eligibility, but sharing a home still shares model caches, config, credentials, and session state. Never put credential contents in a runbook, manifest, or log.

For the owner-operated two-host setup, read the package's `SETUP.md` (tracked source: `docs/factory-setup.md`). Ops provisions a separate fleet-bridge UID on Forge and dev-3 and installs the whole package under `/opt/fleet-codex/453f5ee1190ded9792155d142ef751dcc3e13963-1112e`; ai continues to own endpoint jobs, credentials and native ledgers. The setup helper never starts services. Its local SSH fixture proves the setup protocol with separate UIDs, while real-host installation and a live native cutover remain owner actions.

Each Ubuntu endpoint also needs Ops' AppArmor/user-namespace permission for the fork's actual packaged bwrap path and digest; stock's permission does not carry over. Run `python3 <package>/setup-forge-box.py preflight --bin-dir <package>/bin` as the endpoint user before setup. Enrollment repeats this no-model namespace check and refuses on failure. The helper prints the path and manifest digest and never changes policy/ownership or substitutes full-access execution. The package stays relocatable; regenerate absolute operator bindings/units/SSH configuration and Ops' path permission when moving it. Every later package READY line includes the full bwrap digest.


# Source-cited mailbox interface

# Factory mailbox interface for the foreman bot

Source: fork `f74eee01268b40954afd6c133937a788187ab93e`, containing upstream `c0cc9fc820f62e4dbdde68214dd6d792d6c8a7ee`. Build and qualification status belongs in `MANIFEST.json` and plan 002; these are interface facts from the source.

## Endpoint, identity, and transport

Start the existing bridge as its service UID:

```sh
codex-factory-bridge serve --state /absolute/service-state --socket /absolute/service-dir/bridge.sock
```

The registry must already be initialized. The socket directory belongs to the service UID and cannot be group/world writable; an existing socket path causes `socket_path_exists`. A ready JSON line reports the PID, UID, and socket. Each connection accepts one newline-terminated JSON frame, at most 65,536 bytes, with three-second I/O timeouts. Replies are `{"ok":true,...}` or `{"ok":false,"error":"reason"}`. Sources: `codex-rs/factory/src/main.rs:411`, `codex-rs/factory/src/service.rs:2438`, `codex-rs/factory/src/service.rs:2455`, `codex-rs/factory/src/service.rs:2489`, `codex-rs/factory/src/protocol.rs:8`.

An endpoint is an enrolled **seat UUID** with a host UUID, incarnation UUID, UID, project/recipient scopes, and granted operations. A message's `recipient` is the seat UUID. Its `conversationId` groups mailbox messages; it is separate from a Codex `threadId`. `messageId` is the immutable UUID used for deduplication and receipts. Sources: `codex-rs/factory/src/protocol.rs:63`, `codex-rs/factory/src/protocol.rs:101`, `codex-rs/factory/src/protocol.rs:164`, `codex-rs/app-server-protocol/src/protocol/v2/thread.rs:2375`.

The existing remote transport presents the same local Unix interface: `codex-factory-bridge connect-host --state "$proxy_state" --socket "$proxy_socket" --config "$transport_config"` opens bounded SSH channels using the configured transport. It requires the existing admitted host, key, and seat configuration. This sync performs no remote enrollment or connection. Sources: `codex-rs/factory/src/main.rs:225`, `codex-rs/factory/src/network.rs:431`.

Use `codex_factory::client::exchange(socket, service_uid, credential_path, &request, frame)` when embedding Rust. It verifies the socket directory and kernel peer UID, reads the private credential file, and adds `protocol:1` and `auth:{seat,credential,request}` internally. Do not log credentials or construct shell arguments containing their contents. Other implementations must reproduce that wire shape and peer verification. Requests contain `protocol`, `sender`, `host`, `incarnation`, `sequence`, `project`, `recipient`, and `operation`; the nested fields use camelCase where applicable. Sources: `codex-rs/factory/src/client.rs:179`, `codex-rs/factory/src/client.rs:200`, `codex-rs/factory/src/service.rs:149`, `codex-rs/factory/src/protocol.rs:99`.

## Exact bridge calls

The following frame bodies are passed to `exchange`; it adds the common authentication fields. `message_id` is deliberately snake_case in wire frames, whereas envelope and receipt fields use camelCase.

| Purpose | Frame body | Request operation / recipient |
| --- | --- | --- |
| Synchronize | `{"method":"sync"}` | `observe` / own seat |
| Discover seats | `{"method":"peers"}` | `observe` / own seat |
| List own inbox | `{"method":"inbox"}` | `observe` / own seat |
| Send | `{"method":"send","envelope":ENVELOPE}` | `queue` / envelope recipient |
| Receive | `{"method":"receive","message_id":MESSAGE_UUID}` | `observe` / own recipient seat |
| Acknowledge | `{"method":"ack","receipt":RECEIPT}` | `acknowledge` / own recipient seat |
| Read receipts | `{"method":"status","message_id":MESSAGE_UUID}` | `observe` / message's recipient |

`sync` returns `lastSequence`, `grant` and the optional `credentialCurrent` flag without advancing the sequence. A valid overlap token can have `credentialCurrent:false`; credential publication requires true. Serialize calls for each seat; ordinary authenticated requests require exactly `lastSequence+1`. Successful receive, ack, discovery, and status calls advance the sequence. Sources: `codex-rs/factory/src/protocol.rs:113`, `codex-rs/factory/src/service.rs:2064`, `codex-rs/factory/src/service.rs:2087`, `codex-rs/factory/src/service.rs:2109`, `codex-rs/factory/src/service.rs:2152`, `codex-rs/factory/src/service.rs:2260`, `codex-rs/factory/src/service.rs:2353`.

An `ENVELOPE` has `protocol:1`, `messageId`, `conversationId`, optional `assignmentId` and `replyTo`, `sender`, `incarnation`, `recipient`, `project`, `kind`, `mode`, `createdAt`, `expiresAt`, `text`, and `artifactRefs`. Times are Unix seconds; creation may be at most 60 seconds ahead of the service clock, and expiry must be in the future and within 3,600 seconds of creation. Text is nonempty and at most 16,384 bytes; at most eight artifact references. Modes are `queue_only` and `wake_when_idle`. Kinds are `note`, `assignment`, `clarification`, `reply`, `result`, and `cancellation_request`. Sources: `codex-rs/factory/src/protocol.rs:144`, `codex-rs/factory/src/protocol.rs:164`, `codex-rs/factory/src/protocol.rs:183`.

Send returns `state` and `receipts` (a new send also returns `receipt`; an exact duplicate returns `duplicate:true`). Receive returns `envelope` and `receipts`; it changes `service_accepted` to `delivered` once. Repeated receive is allowed while delivered, but receive after consumption/completion is refused; use status or native reconciliation. Sources: `codex-rs/factory/src/service.rs:2260`, `codex-rs/factory/src/service.rs:2353`.

## Receipts and idempotency

| Receipt state | Writer | Meaning |
| --- | --- | --- |
| `local_accepted` | `outbox` | Sender persisted local bytes. |
| `service_accepted` | `service` | Bridge committed the envelope. |
| `delivered` | `inbox` | Recipient retrieved the envelope. |
| `consumed` | `runtime` | Correlated native turn consumed it. |
| `completed` | `worker` | Correlated turn completed successfully. |

`RECEIPT` is `{protocol:1,messageId,state,writer,turnId}`. Only the recipient with an explicit `acknowledge` grant can submit `consumed` or `completed`; both require a nonempty `turnId`. The order is delivered → consumed → completed, with the same turn ID. Repeating an existing receipt state with the same turn ID is idempotent; a different turn ID yields `receipt_turn_conflict`. The raw bridge validates the permitted caller, writer label, order, and turn IDs; the native wrapper additionally verifies durable turn history. Earlier receipts do not establish completion. Sources: `codex-rs/factory/src/protocol.rs:228`, `codex-rs/factory/src/protocol.rs:262`, `codex-rs/factory/src/service.rs:2152`, `codex-rs/app-server/src/request_processors/factory_reconcile.rs:137`.

The bridge fingerprints the whole envelope under `messageId`. An authenticated exact repeat returns the existing state and receipts; changed content under that UUID yields `message_conflict`. Only exact previously committed sends receive the replay-sequence exception; new sends still require the next sequence. The native `factory/send` wrapper retains the original timestamps on retries and requires matching payload and expiry duration. Sources: `codex-rs/factory/src/service.rs:2260`, `codex-rs/app-server/src/request_processors/factory.rs:320`.

## Durable send and recovery commands

These are real CLI subcommands; `send`, `receive`, and `ack` above are wire methods, not standalone CLI subcommands.

```sh
codex-factory-bridge enqueue --state "$outbox" --grant "$grant_file" --envelope "$envelope_file"
codex-factory-bridge relay --state "$outbox" --grant "$grant_file" --socket "$socket" --service-uid "$service_uid" --credential "$credential_file" --message-id "$message_id"
codex-factory-bridge retry --state "$outbox" --grant "$grant_file" --socket "$socket" --service-uid "$service_uid" --credential "$credential_file" --message-id "$message_id"
codex-factory-bridge outbox --state "$outbox"
codex-factory-bridge replay --state "$outbox" --message-id "$message_id"
codex-factory-bridge recovery-status --state "$service_state"
codex-factory-bridge backup --state "$service_state" --output "$archive_file"
codex-factory-bridge restore --input "$archive_file" --destination "$fresh_state"
```

Enqueue persists `local_accepted` before returning. Relay synchronizes and resends the saved envelope. Attempts are persisted before I/O, capped at three, with delays of 1/2/4 seconds; retry waits for the saved deadlines. Exhaustion retains the entry as `retry_exhausted`. After inspection, explicit replay resets that disposition under the **same ID and payload**; expired entries, more than 16 explicit replays, and replays crossing later accepted work are refused. FIFO is per sender/recipient/conversation. Sources: `codex-rs/factory/src/main.rs:284`, `codex-rs/factory/src/client.rs:112`, `codex-rs/factory/src/client.rs:263`, `codex-rs/factory/src/client.rs:294`, `codex-rs/factory/src/client.rs:475`.

After a lost bridge response or same-boot restart, retry the saved message rather than minting a new UUID. Registry writes use a private temporary file, rename, and directory fsync before the response. Restart `serve` with the same state; if a socket remains, verify the old owned process has stopped before removing its stale socket. A host reboot is different: startup fences old grants, quarantines pending messages, and flags delivered/consumed work for reconciliation. Sources: `codex-rs/factory/src/service.rs:523`, `codex-rs/factory/src/service.rs:2350`, `codex-rs/factory/src/service.rs:2504`, `codex-rs/factory/src/lifecycle.rs:64`, `codex-rs/factory/src/lifecycle.rs:97`.

Backup/restore are operator calls, run with access to the service-owned private state. Backup contains historical facts rather than credentials or signing keys. Restore requires an absent destination beneath a private operator-owned parent; it creates a fresh host and key, revokes historical seat grants, quarantines pending work, and retains terminal receipts as history. It does not replay archived work or restore enrollment authority. Sources: `codex-rs/factory/src/main.rs:135`, `codex-rs/factory/src/lifecycle.rs:129`, `codex-rs/factory/src/lifecycle.rs:180`, `codex-rs/factory/src/lifecycle.rs:184`, `codex-rs/factory/src/lifecycle.rs:243`, `codex-rs/factory/src/lifecycle.rs:288`.

## Addressing a Codex session and waking it

Launch the recipient's `codex app-server --stdio` with protected `CODEX_FACTORY_BINDING` and `CODEX_FACTORY_SERVICE_UID`. The binding contains `serviceUid`, `socket`, `credential` (a path), `state` (the private native ledger), and `grant`. Its file must belong to the service UID and be protected from group/world writes. Initialize JSON-RPC with `capabilities:{experimentalApi:true}`, then send the `initialized` notification. Sources: `codex-rs/app-server/src/request_processors/factory.rs:37`, `codex-rs/app-server/src/request_processors/factory.rs:53`, `codex-rs/app-server-protocol/src/protocol/common.rs:927`, `scripts/factory/native_fixture.py:89`, `scripts/factory/native_owner.py:9`.

The recipient app-server exposes these JSON-RPC calls; their response payload is `result.data`:

```json
{"jsonrpc":"2.0","id":1,"method":"factory/peers","params":{"project":"PROJECT"}}
{"jsonrpc":"2.0","id":2,"method":"factory/inbox","params":{"project":"PROJECT"}}
{"jsonrpc":"2.0","id":3,"method":"factory/send","params":{"messageId":"MESSAGE_UUID","conversationId":"CONVERSATION_UUID","recipient":"RECIPIENT_SEAT_UUID","project":"PROJECT","kind":"note","mode":"wake_when_idle","text":"Message text.","replyTo":null,"expiresInSeconds":3600}}
{"jsonrpc":"2.0","id":4,"method":"thread/factoryMessage","params":{"threadId":"CODEX_THREAD_ID","project":"PROJECT","messageId":"MESSAGE_UUID"}}
{"jsonrpc":"2.0","id":5,"method":"factory/status","params":{"project":"PROJECT","messageId":"MESSAGE_UUID"}}
```

Discover/create/resume the recipient's thread through the normal thread APIs, then target its app-server with `thread/factoryMessage`. Sending to the bridge **does not itself wake a session**. `queue_only` is staged without starting a turn. `wake_when_idle` uses `start_turn_if_idle`: it returns `started` with a turn ID, or `deferred` when it cannot submit; retry the same message/thread after idle. The ledger refuses binding the same message to another thread. Sources: `codex-rs/app-server-protocol/src/protocol/v2/thread.rs:2350`, `codex-rs/app-server/src/request_processors/factory.rs:347`, `codex-rs/app-server/src/request_processors/factory.rs:409`, `codex-rs/app-server/src/request_processors/factory.rs:461`, `codex-rs/app-server/src/request_processors/factory.rs:501`.

Inspect the returned `reason` before retrying a deferred message. Core can decline with `NotIdle`, `PlanMode`, `ServerDraining`, or `PendingTriggerTurn`; an idle thread in Plan mode still cannot admit this automatic start. Retry when the stated condition clears, preserving the same IDs. The factory RPC directly requests an automatic start; the generic `wake_on_agent_messages` feature does not replace the recipient-side bridge RPC. Sources: `codex-rs/core/src/session/turn_input.rs:74`, `codex-rs/core/src/session/turn_input.rs:287`, `codex-rs/core/src/session/turn_input.rs:540`, `codex-rs/core/src/session/turn_input.rs:576`, `codex-rs/core/src/session/turn_input.rs:874`, `codex-rs/app-server/src/request_processors/factory.rs:507`.

Repeat `thread/factoryMessage` with the same IDs to reconcile a started turn, including after restart. It checks durable history for exactly one matching authored agent message and correlated turn, then emits consumed/completed receipts as appropriate. It does not blindly start a second turn after uncertain admission. Failed/interrupted or ambiguous history does not produce a successful completed receipt. Sender-side `factory/status` only reads receipts for a message in that sender's outbox; it does not drive recipient reconciliation. Sources: `codex-rs/app-server/src/request_processors/factory.rs:200`, `codex-rs/app-server/src/request_processors/factory.rs:430`, `codex-rs/app-server/src/request_processors/factory.rs:497`, `codex-rs/app-server/src/request_processors/factory_reconcile.rs:87`, `codex-rs/app-server/src/request_processors/factory_reconcile.rs:185`, `codex-rs/app-server/src/request_processors/factory_reconcile.rs:229`.

Plan 004 R adds deferred host admission and unattended renewals in 1112e. Initialize with `init --defer-remote` for the two local Forge seats; the root-owned allowlist/timer skips the absent remote host and unenrolled pilot. Later `admit-remote` uses the reserved IDs without resetting state. Same-key `renew-host` extends the approved host lease without key rotation. Seat rotations have a durable operation ID and resume prepare/commit/publication/confirmation after crashes; publication is atomic under the endpoint UID and rejects stale candidates. Credentials keep the same wire format, per-frame reload, expiry, revocation and reboot fencing. See SETUP.md for root-owned code/config pins and the nonzero, one-line redacted failure contract. R implements no Forge-initiated transport or private-body/wake controller; T and S keep their own gates.
