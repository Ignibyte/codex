# Plan 004 — Part R: renewals (APPROVED TO BUILD, 14:31 CST)

**Goal:** Run hub/local seats unattended before dev-3 admission. Owner `1fcc1441` selects T subject to its gate. R build approved by /tmp/fork-brief-004-review.txt; T waits for R installed/proven and its launcher gate; S/003 stay held. UID 983/client GID 982/preflight are owner-reported; init/keygen/ssh-config/units stay held. Commands are proposed.

**Smallest version:** Keep connect-host/remote-entry for offline compatibility only; dev-3→Forge port 22 stays CLOSED. No outbound dev-3 leg/Forge forced entry. Keep credential format. Fleet-bridge `init --defer-remote` saves seat/host/key UUIDs, omits admission/pilot bootstrap; locals work independently. Fleet-bridge `admit-remote --config-root <cfg>` later uses saved UUIDs, verifies existing admissions; no re-init/deletion. Default init stays compatible; partial init stops.

Add service-owner `renew-host --state <service> --host <uuid> --key-id <current> --ttl 86400`: registry lock, boot/revocation/current-key checks, audit, clock-rollback floor; extend without shortening/key-slot consumption. No auto-admission/un-revocation; lapse restoration requires the approved allowlist. Renew at 12h. Lapse denies later frames; admitted work can finish; commits/receipts persist subject to message TTL. Key rotation stays separate.

**Rotation/publication:** Root oneshot runs `setup-forge-box.py renew --config /etc/fleet-manager-bridge-renew.json`, singleton lock; drops to fleet-bridge for signing/configured ai for publication. Rotate enrolled seats every 30min, TTL 3600s/overlap ≤60s. Rotation ID/private pending record makes prepare→registry commit→publish→confirm resumable; resolve it before another rotation. Recovery rechecks boot/grant/revocation; never publish uncommitted/expired candidates. Fsync/atomic rename preserves ai:0600. Remote publisher: separate inventory-pinned Forge→dev-3 key, fixed ai command, encrypted stdin, bounded/idempotent seat-bound install; no sudo/shell/forwarding. Confirm activation/delivery; retry identical candidates after lost acknowledgments. Tokens/nonces never enter argv/logs.

**Mandatory Ops setup:** Root:0644 `fleet-manager-bridge-renew.service/.timer`: root oneshot, UMask=0077, NoNewPrivileges=yes, SETUID/SETGID bounding/ambient capabilities for the UID drop, explicit writable roots; OnBootSec=30s/OnUnitActiveSec=5min. Root:0600 `/etc/fleet-manager-bridge-renew.json` fixes package/state/seat/incarnation/UID/destinations/publisher/policies; root:0700 `/var/lib/fleet-manager-bridge-renew` holds the singleton broker lock; the signing UID owns the private per-seat rotation journal and deterministic candidate temporary file. Validate owners/paths/no symlink escape; skip deferred/unenrolled pilot. Forge Ops provisions root:0600 publisher key/pin; dev-3 Ops adds its forced public-key line to ai's 0700/0600 SSH directory/file, no sudo. SETUP.md retains sandbox preflight/executor labels.

```sh
# Forge — Ops/root, after approved R package installation; PKG is its verified root
python3 "$PKG/setup-forge-box.py" render-renewal --config-root /etc/fleet-manager-bridge --output /var/lib/fleet-manager-bridge-renew/staged
install -o root -g root -m 0600 /var/lib/fleet-manager-bridge-renew/staged/allowlist.json /etc/fleet-manager-bridge-renew.json
install -o root -g root -m 0644 /var/lib/fleet-manager-bridge-renew/staged/fleet-manager-bridge-renew.{service,timer} /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now fleet-manager-bridge-renew.timer
```

Failures alert/nonzero and retain pending state; no blind rotation, privilege fallback or fabricated receipt. Each client frame reloads credentials: no endpoint restart; expired/stale tokens fail, model work is not automatically cancelled. Restore/reconcile original IDs; bounded exhausted outboxes need eligible explicit replay, never repeated Forge actions. Revocation/reboot fencing overrides the timer.

**Files:** Let F=`codex-rs/factory/src`, S=`scripts/factory`. R changes F/{main.rs,service.rs}; S/{setup-forge-box.py,test-setup-forge-box.py,package.py,check_package.py,test_check_package.py,qualify_packaged_tools.py}; adds S/qualify_renewals.py and S/systemd/fleet-manager-bridge-renew.{service,timer}.in; updates docs/{factory-setup.md,fork-mailbox.md}, CHANGELOG-FORK.md and this plan. Package/gate include the fork helper/unit assets. Preserve old command behavior; no T mode in R.

**Proof:** full-package-proof builds matching fault binaries/eight jobs; runs seven mailbox lanes, setup/renewal and actual shell/write/MCP with local model fixtures (offline driver, no owner login/backend). Private NVMe /tmp,/run,/etc; verify units/run accelerated timer in isolated systemd. Cover repeated renewal/no key-slot growth, lapse/clock rollback/revocation, deferred admission, concurrent brokers, invalid paths/UIDs/keys, prepare/commit/rename/ack deaths, outage past overlap/TTL, duplicate-free recovery. Record output here; not yet run.

```sh
# z690 — fork seat, only after R approval; TASK is a fresh isolated NVMe root
mkdir -p "$TASK/tmp"
export RUSTUP_TOOLCHAIN=1.95.0 CARGO_BUILD_JOBS=8 TMPDIR="$TASK/tmp"
(cd codex-rs && CARGO_BUILD_JOBS=8 cargo test --locked -p codex-factory -- --test-threads=8)
python3 scripts/factory/package.py --output dist/1112e --work-dir "$TASK/package"
python3 scripts/factory/qualify_renewals.py --package dist/1112e --work-dir "$TASK/proof" --full-package-proof
```

Whole 1112e includes voice/launcher. Require bwrap `bd2ed101e19111f5c06ea4bb16ace19ca10693a56b343265b156937d4434f023`; matching CLI/bridge/voice source, all binary hashes. Ops installs root-owned `/opt/fleet-codex/<40-hex-commit>-1112e`/release AppArmor profile. R supports locals; admit/deploy dev-3 only with T. No step needs dev-3→Forge SSH. R then T remain separate complete packages: isolate renewal recovery from transport changes and qualify each; pay a full build/proof twice (~23min measured baseline each, 25–35min allowance plus new tests/implementation). No host contact/deployment, policy edits, Forge workflow, tickets/backlogs/evidence folders or 003 code. Owner reviews before merge.

---

# Plan 004 — Part T: Forge-initiated transport (owner-selected; text review pending)

**Goal/gate:** Owner `1fcc1441` chooses T: R/1112e then T/1112f, isolating signer recovery from transport changes. Dev-3→Forge port 22 stays CLOSED. Keep init/state; no fresh roots/deletion. Commands await review.

**Smallest version:** Add Forge connect-source/dev-3 source-entry in F/{main.rs,network.rs}. Forge fleet-bridge owns a dedicated 0600 key/dev-3 inventory pin and dials fleet-bridge@dev-3 on the same open port 22. **Withdraw the ai→sudo sketch:** SSH_* presence is caller-controlled (current network.rs:386); it is not proof of SSH authentication. Dev-3 Ops instead root-controls the service principal's accepted key/forced command and protocol-only login launcher; no ai-owned key lookup, ai→fleet-bridge sudo, password, arbitrary shell, PTY, forwarding or config/agent/proxy fallback. Ordinary ai access remains separate.

### Why this is not a workaround

Keep separate service UIDs, actual SO_PEERCRED/allowed UID/process-incarnation checks, pinned peer identity and per-request host/key/lease/revocation checks in service::remote_frame. Same IDs/receipts/idempotency; no forwarding, nested reverse SSH or fabricated context. **Gate:** prove every check; any weakening means STOP/owner ruling. Ops must explicitly approve replacing literal nologin with the root-owned protocol-only launcher (not a general shell). If nologin or ai login is mandatory, STOP; do not improvise a bypass. Gate not yet passed.

**Launcher contract — 14:32 conditions, T only (not built in R):**
1. T starts only after owner confirms R installed/proven; all seven conditions remain a review gate.
2. Root owns /etc/ssh/fleet-bridge.keys, mode 0644; exactly one restrict/from="10.200.68.55"/forced-command key line, no home key lookup.
3. Match User fleet-bridge: PermitTTY no; AllowTcpForwarding no; X11Forwarding no; AllowAgentForwarding no; PermitTunnel no; AuthenticationMethods publickey.
4. Both fleet-bridge accounts remain password-locked and have no sudo rule.
5. Root owns the generated 0755 launcher and every package ancestor; launcher takes ZERO application arguments.
6. It validates SSH_ORIGINAL_COMMAND equals exactly codex-factory-v1; missing/other values exit nonzero before opening state/socket.
7. It rejects SSH_TTY, malformed context and unexpected argv; refusal emits exactly one syslog line containing ids/reason only, never command text/token/body.
8. It execs ONLY the pinned root-owned package's bin/codex-factory-bridge source-entry with generated fixed state/host/key/config arguments.
9. The root-controlled protocol login dispatcher handles only sshd's fixed -c launcher invocation, then execs the zero-argument launcher; no general shell/eval/user-selected executable.
10. Actual sshd/PAM/locked-account compatibility is proved offline; if that dispatch cannot satisfy the zero-argument/no-shell condition, STOP T for owner ruling.
11. Any other remote command fails; a PTY request is refused; TCP/X11/agent/tunnel forwarding requests are refused, independently of application credentials.
12. Only existing Forge→dev-3 port 22 is used; no firewall change or dev-3→Forge connection.
13. Setup helper generates whole Match/key/dispatcher/launcher/unit files into a staging folder; SETUP.md displays them whole with actors/owners/modes.
14. Ops installs the reviewed generation through its ucs-ops PR; no hand typing, unprivileged install or mutation by the launcher.
15. One future Forge Ops command: python3 "$PKG/setup-forge-box.py" verify-launcher --config "$OPS_TRANSPORT_CONFIG".
16. Expected: AUTHENTICATED_PROTOCOL PASS; SHELL REFUSED; PTY REFUSED; FORWARD REFUSED; exit 0 only if all four are proved; network timeout is not refusal proof.

**(a) Standing connection:** Yes: long-lived bidirectional SSH; systemd supervises/restarts with backoff; ServerAliveInterval=15/ServerAliveCountMax=3 detects dead peers. Idle costs processes/memory/sockets/keepalive packets, ZERO model calls/tokens, including reconnects. Work admission starts model turns separately. Drops retain commits/outboxes; reconcile original IDs/payloads/sequences. Serialize bounded 2.5s requests; separate idle wait. Timeout/truncation/mismatch closes the channel; reap owned children only. S adds arrival signaling without blocking requests.

**(b) Local conversation:** Once enrolled/bound, spec manager and foreman bot use `/var/lib/fleet-manager-bridge/run/bridge.sock`, each with its seat credential; no local SSH. Receipts: sender local_accepted → hub service_accepted → recipient delivered → native consumed → terminal completed. Delivery is not a Forge seal. Forge owns projects/cards/specifications/seals/rulings/workflow decisions; receipts describe communication/turns only. Existing Python manager integration is not assumed.

**Migration/Ops:** configure-transport validates setup/registry/R allowlist/generation; preserves IDs/grants/sequences/leases/MAC key/credentials/ledgers. Atomically stage protected paths/pins/units/SSH policy; reject mixed activation. Drain old connector; rollback configuration only. Re-render R allowlist paths, keep publisher separate. Ops installs Forge fleet-manager-bridge-source.service (fleet-bridge, requires hub, reconnect/backoff); dev-3 sshd directly starts source-entry as UID 983 through the restricted launcher. Root-owned authorized-key policy accepts only the dedicated transport key/command; ai cannot override it. Remove obsolete Forge transport entries/dev-3 outbound unit/key/pin; preserve operator/publisher access. No transport sudo grant remains. Verify owned processes before cleanup; no hand edits/re-init/key rotation.

```sh
# Forge — Ops/root, after T plan approval and whole-package installation
python3 "$PKG/setup-forge-box.py" configure-transport --config-root /etc/fleet-manager-bridge --direction forge-initiated --input "$OPS_INPUT" --output "$STAGE"
# Both hosts — Ops/root: install reviewed host-specific STAGE generation
# Forge — Ops/root, after dev-3 entry/pin verification
systemctl daemon-reload
systemctl enable --now fleet-manager-bridge-source.service
```

**Failures/proof:** Connector/proxy locks reject duplicates before socket mutation. Dev-3 reboot retains ledgers/invalidates presence; lock/owner/type/dead-incarnation proof precedes socket cleanup. Forge reboot preserves fencing/quarantine/reconciliation; connector/timer cannot bypass it. Prove fake SSH_* and ai-key/sudo/launcher injection denied; loopback SSH/UID/pin/host/lease/revocation, forbidden shell/forwarding, commit/reply cuts, duplicates/reboots/migration/rollback/R renewal. Repeat offline tools/voice/seven mailbox/R timer-crash proofs; no host contact.

**Files:** F/{main.rs,network.rs}; S/{setup-forge-box.py,test-setup-forge-box.py,package.py,check_package.py,test_check_package.py}; add S/{fleet-bridge-ssh-entry.py,qualify_forge_transport.py}, S/systemd/fleet-manager-bridge-source.service.in; update docs/{factory-setup.md,fork-mailbox.md}, CHANGELOG-FORK.md, this plan. Launcher accepts only fixed protocol invocation/config, never shell syntax.

```sh
# z690 — fork seat, after text approval; fresh NVMe TASK
mkdir -p "$TASK/tmp"
export RUSTUP_TOOLCHAIN=1.95.0 CARGO_BUILD_JOBS=8 TMPDIR="$TASK/tmp"
(cd codex-rs && cargo test --locked -p codex-factory -- --test-threads=8)
python3 scripts/factory/package.py --output dist/1112f --work-dir "$TASK/package"
python3 scripts/factory/qualify_forge_transport.py --package dist/1112f --work-dir "$TASK/proof" --full-package-proof
```

Whole root-owned `/opt/fleet-codex/<40-hex-commit>-1112f`, release AppArmor profile, exact R bwrap/voice/launcher; report all binary hashes. ~23min measured build/proof baseline each (1019.56+135.416+129.34+40.96+20.44s); new tests/implementation unmeasured. Production Forge→dev-3 routing, UID egress/pins/sudo/AppArmor/two kernels/live jobs remain owner/Ops proof. No deployment, new Forge workflow, tickets/evidence folders, automatic cutover or 003 code; owner reviews before merge.

---

# Plan 004 — Part S: private content and arrival signal (proposal; separate review)

**Decision/package:** I recommend S after R/T in a separate whole `dist/1112g`, not silently added to 1112f. Both features can share g, but change endpoint identities, payload/wait framing and clients. R/T remain useful independently; neither claims endpoint-only secrecy or automatic inbox delivery. S needs owner agreement on OS-user isolation and client integration before implementation. No live-host inspection occurred.

**(c) Who can read today — observed source, main `32f47da69f`:**

| Place | Actual content/readers |
| --- | --- |
| Local Unix socket | Plaintext inside kernel IPC: communicating endpoint and fleet-bridge processes; root can inspect. Other users cannot simply tap it. |
| Between hosts | SSH encrypts the wire against ordinary network observers; endpoint/proxy/SSH processes and host roots handle plaintext. This is not endpoint-to-endpoint content encryption. |
| Hub registry/backups | Plaintext envelopes; fleet-bridge and root can read. Its normal audit/recording projection contains IDs/outcomes, not body text. |
| Sender outbox, recipient native ledger/session history | Plaintext; owning OS user and root. **All three endpoints and other agents currently share ai:** those processes can read ai-owned files/keys/credentials and impersonate a seat. 0700/0600 does not separate them. |
| stdout/journals/logs | Outbox inspection, thread/factoryMessage responses and native history can output bodies; remote-entry stdout is a protocol pipe that can contain them. Capturing/teeing/tracing those streams persists plaintext. Readers include root, the owning account and configured journal/log/forwarder readers. Live logging ACLs/retention were not inspected. |

Source: F/service.rs:129,1784,2115; F/client.rs:70,223; F/main.rs:769; app-server request_processors/factory.rs:409,487; F/recording.rs:69. Inbox/status/service-inspect are metadata/receipts, not blanket body dumps. A model provider sees text submitted in an admitted model turn; Forge sees content explicitly placed in its cards/comments/artifacts. Neither reader disappears through transport encryption.

**Smallest real security improvement:** We can encrypt bodies before sender outbox enqueue, store ciphertext at the hub, and decrypt inside the recipient. Use vetted authenticated public-key envelopes, endpoint-held private keys, independently Ops-pinned public keys and binding to sender/recipient/project/message ID/expiry. Retain a sender-readable encrypted copy; retries reuse exact ciphertext. Explicitly version encrypted payloads; reject tampering/unknown keys/plaintext fallback. Routing/receipts remain visible; body validation happens before model admission. Move service-originated body production to an endpoint too; the hub must never hold its private key or already know a supposedly private body. Decrypted histories stay endpoint-private; diagnostics contain IDs/errors, not bodies. Encryption uses code, no model calls/tokens.

| Choice | Gain / limit / cost |
| --- | --- |
| Current SSH + permissions | Protects network traffic/other UIDs; hub and all ai agents remain readers. |
| Encryption with shared ai | Hides new hub bodies from fleet-bridge; another ai process can steal endpoint keys/read decrypted history. **Does not meet the owner's agent-isolation goal.** |
| Separate endpoint UIDs + encryption | Recommended against other agents: private homes/keys/credentials/RPC/controllers under root-owned parents, no shared-ai sudo/control access or transcript copies. Ops provisions users; R's allowlist gains per-role UIDs. Root, compromised endpoints and chosen model providers remain readers. |

Separate OS users are the real local boundary; encryption alone cannot supply it. If ai keeps unrestricted sudo/root access, splitting UIDs still does not isolate those agents. Nothing here hides decrypted content from root controlling an endpoint host. Choosing UIDs before enrollment avoids later grant migration; otherwise require reviewed re-enrollment/reconciliation, never hand-edit grants. Old plaintext ledgers/backups are not retroactively protected or silently purged. Costs include key provisioning/pins/rotation/recovery, ciphertext CPU/size and coordinated protocol/client migration; measure latency/RSS/size. Missing keys fail closed. No mandatory isolation check may be waived to ship S.

**(d) Who checks inbox today:**

| Endpoint | Caller/cadence established by this repo/setup |
| --- | --- |
| spec-manager on Forge | No installed mailbox watcher or polling schedule. Python manager's Forge-card loop is not demonstrated mailbox integration. |
| foreman-bot on Forge | No installed mailbox watcher or polling schedule. |
| pilot-seat on dev-3 | No installed mailbox watcher or polling schedule. |

APIs/tests call inbox explicitly; delivery alone does not start a session. Owner reports init/units still held. External live controllers/cadences are unverified; we must not invent an interval. Existing Python/stock sessions need an explicit adapter/bound app-server, not a claim of retroactive wake support.

**Non-polling proposal:** One model-free watcher per seat (spec-manager/foreman-bot on Forge, pilot-seat on dev-3) blocks on an authenticated own-seat `wait(afterRevision)` over bridge.sock/remote.sock. A durable per-seat revision advances with committed mail. Register/check the revision without a lost-wake race; after waking, reconcile inbox once and call the configured thread/factoryMessage adapter. Notification is not delivery/consumption/completion. Keep queue_only inert; retain deferred IDs and retry on idle/state-change events or explicit resume. Park without registry/sequence locks or exhausting ordinary request workers. Cross-process writers require a protected-directory commit watch plus locked durable-revision check, not an in-memory-only notifier. Restart/overflow/reconnect performs catch-up from durable state; no recurring inbox scan. Recheck auth on return/revocation and credential/host-lease expiry; rotation re-registers with current credentials. Waiting/reconnect costs no model call/token.

Across T's standing SSH, S adds **explicit versioned, bounded request IDs and notification/wait channels**; ordinary commands keep their deadlines. An indefinitely blocked wait must not occupy T's single request lane. Authenticated metadata-only signals travel over the same encrypted link; drops resume the saved cursor, and duplicate signals reconcile the same message IDs. Fail closed on mismatched versions; no nested SSH/socket-forwarding/timeout bypass.

**Scope/proof/cost:** Expected S paths: F/{protocol.rs,service.rs,client.rs,network.rs,main.rs}, new F/{arrival.rs,message_crypto.rs}, manager/foreman producers; app-server request_processors/{factory.rs,factory_reconcile.rs} and protocol bindings; S/{setup-forge-box.py,package.py,check_package.py}, new watcher/qualification/unit assets; docs/CHANGELOG/this plan. Exact files/adapters precede S code. Offline proofs: unauthorized UID cannot read keys/body or authenticate; hub/log scans show no new plaintext; crypto tamper/replay/key-rotation/recovery; cursor/lost-wake/reboot/revoke/expiry; waits cannot starve sends/acks; three client adapters and zero idle model requests; full R/T/package proofs. No crypto/performance proof claimed. Allow an additional whole build (~23min prior baseline), unmeasured security/protocol/client tests and Ops identity work; not a 25–35min total promise. Historical retention/live clients need owner decisions.

**Review state:** Owner chose T subject to its no-weakened-check gate; this update is plan text only. S is a proposal, not an approved implementation. Stop here for owner review. **FORK PLAN 004 WRITTEN.**

## R execution authorization

Owner review 14:31 CST approves R now, not merge/deployment. Root renewal executes pinned package code/config only; failures must be nonzero with one ids-only log line. T is approved in principle but waits until R is installed/proven and launcher conditions pass; S is not approved. Seat-launch question was answered in ten lines before any R code/build. No Forge/dev-3 contact is authorized. Stop at FORK PACKAGE READY dist/1112e with every hash and the unchanged full bwrap digest.
