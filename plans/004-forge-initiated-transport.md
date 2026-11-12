# Plan 004 — Part R: renewals (APPROVED TO BUILD, 14:31 CST)

**Goal:** Run hub/local seats unattended before dev-3 admission. Owner `1fcc1441` selects T subject to its gate. R build approved by /tmp/fork-brief-004-review.txt; T waits for R installed/proven and its launcher gate; S is approved after the pilot works (owner 3f56caab); S code and 003 stay held. UID 983/client GID 982/preflight are owner-reported; init/keygen/ssh-config/units stay held. Commands are proposed.

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

# Plan 004 — Part T: Forge-initiated transport (APPROVED TO BUILD, 16:23 CST)

**Goal/gate:** Owner `1fcc1441` chooses T: R/1112e then T/1112f, isolating signer recovery from transport changes. Dev-3→Forge port 22 stays CLOSED. The pilot uses an explicitly throwaway ai-enrolled root; final accounts and one durable init wait for S. R is owner-reported installed/proven. GO is /tmp/fork-brief-T-go.txt; no fleet-host action or merge is authorized.

**Smallest version:** Add Forge connect-source/dev-3 source-entry in F/{main.rs,network.rs}. Forge fleet-bridge owns a dedicated 0600 key/dev-3 inventory pin and dials fleet-bridge@dev-3 on the same open port 22. **Withdraw the ai→sudo sketch:** SSH_* presence is caller-controlled (current network.rs:386); it is not proof of SSH authentication. Dev-3 Ops instead root-controls the service principal's accepted key/forced command and protocol-only login launcher; no ai-owned key lookup, ai→fleet-bridge sudo, password, arbitrary shell, PTY, forwarding or config/agent/proxy fallback. Ordinary ai access remains separate.

### Why this is not a workaround

Keep separate service UIDs, actual SO_PEERCRED/allowed UID/process-incarnation checks, pinned peer identity and per-request host/key/lease/revocation checks in service::remote_frame. Same IDs/receipts/idempotency; no forwarding, nested reverse SSH or fabricated context. **Gate:** prove every check; any weakening means STOP/owner ruling. Ops must explicitly approve replacing literal nologin with the root-owned protocol-only launcher (not a general shell). If nologin or ai login is mandatory, STOP; do not improvise a bypass. The seven launcher conditions remain mandatory offline proof gates; owner GO authorizes their implementation, not a weakened check.

**Launcher contract — seven conditions, T build approved after R proof:**
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

**Migration/Ops:** configure-transport validates setup/registry/generation and an existing R allowlist when supplied in the root input; preserves IDs/grants/sequences/leases/MAC key/credentials/ledgers. Atomically stage protected paths/pins/units/SSH policy; reject mixed activation. Drain old connector; rollback configuration only. Re-render R allowlist paths, keep publisher separate. Ops installs Forge fleet-manager-bridge-source.service (fleet-bridge, requires hub, reconnect/backoff); dev-3 sshd directly starts source-entry as UID 983 through the restricted launcher. Root-owned authorized-key policy accepts only the dedicated transport key/command; ai cannot override it. Remove obsolete Forge transport entries/dev-3 outbound unit/key/pin; preserve operator/publisher access. No transport sudo grant remains. Verify owned processes before cleanup; no hand edits/re-init/key rotation.

```sh
# Forge — Ops/root, after T plan approval and whole-package installation
python3 "$PKG/setup-forge-box.py" configure-transport --config-root /etc/fleet-manager-bridge --direction forge-initiated --input "$OPS_INPUT" --output "$STAGE"
# Both hosts — Ops/root: install reviewed host-specific STAGE generation
# Forge — Ops/root, after dev-3 entry/pin verification
systemctl daemon-reload
systemctl enable --now fleet-manager-bridge-source.service
```

**Failures/proof:** Connector/proxy locks reject duplicates before socket mutation. Dev-3 reboot retains ledgers/invalidates presence; lock/owner/type/dead-incarnation proof precedes socket cleanup. Forge reboot preserves fencing/quarantine/reconciliation; connector/timer cannot bypass it. Prove fake SSH_* and ai-key/sudo/launcher injection denied; loopback SSH/UID/pin/host/lease/revocation, forbidden shell/forwarding, commit/reply cuts, duplicates/reboots/migration/rollback/R renewal. Repeat offline tools/voice/seven mailbox/R timer-crash proofs; no host contact.

**Files:** F/{main.rs,network.rs}; codex-rs/{Cargo.toml,Cargo.lock} for the explicitly approved 1112f version stamp; S/{setup-forge-box.py,test-setup-forge-box.py,package.py,check_package.py,test_check_package.py}; add S/{fleet-bridge-ssh-entry.py,qualify_forge_transport.py}, S/systemd/fleet-manager-bridge-source.service.in; update docs/{factory-setup.md,fork-mailbox.md}, CHANGELOG-FORK.md, this plan. Launcher accepts only fixed protocol invocation/config, never shell syntax.

```sh
# z690 — fork seat, after text approval; fresh NVMe TASK
mkdir -p "$TASK/tmp"
export RUSTUP_TOOLCHAIN=1.95.0 CARGO_BUILD_JOBS=8 TMPDIR="$TASK/tmp"
(cd codex-rs && cargo test --locked -p codex-factory -- --test-threads=8)
python3 scripts/factory/package.py --output dist/1112f --work-dir "$TASK/package"
python3 scripts/factory/qualify_forge_transport.py --package dist/1112f --work-dir "$TASK/proof" --full-package-proof
```

Whole root-owned `/opt/fleet-codex/<40-hex-commit>-1112f`, release AppArmor profile and same-source voice/root launcher; report all binary hashes. The explicit 1112f workspace version produces bwrap `0ce4fa5b9da214841641f12032afa6cda3d93b45f321705fa8bde5fb2c270b74`; Ops must approve that new digest/path before deployment. The unchanged-bwrap condition recorded for R/1112e remains satisfied there; it is not silently represented as the 1112f digest. ~23min measured build/proof baseline each (1019.56+135.416+129.34+40.96+20.44s); new tests/implementation unmeasured. Production Forge→dev-3 routing, UID egress/pins/sudo/AppArmor/two kernels/live jobs remain owner/Ops proof. No deployment, new Forge workflow, tickets/evidence folders, automatic cutover or 003 code; owner reviews before merge.

---

# Plan 004 — Part S: private content and arrival signal (APPROVED AFTER PILOT; no S code now)

**Decision/package:** I recommend S after R/T in a separate whole `dist/1112g`, not silently added to 1112f. Both features can share g, but change endpoint identities, payload/wait framing and clients. R/T remain useful independently; neither claims endpoint-only secrecy or automatic inbox delivery. Owner 3f56caab approves S only after the pilot works. Provision endpoint accounts before enrollment; the account map and exact client/file inputs must be settled before implementation. No S code or live-host inspection occurred.

**Ops account decision — owner 3f56caab, text only:** Until S is live the mailbox carries notices only, never a secret. Account names below are proposed; Ops assigns free, stable numeric UIDs/GIDs through ucs-ops before any durable enrollment. Private bases have root-owned, non-endpoint-writable parents; each endpoint owns its base/subdirectories 0700 and secret files 0600.

| Endpoint / host | Account | Private base B / owned assets | Socket group | R allowlist |
| --- | --- | --- | --- | --- |
| spec-manager / Forge | fleet-spec-manager | /var/lib/fleet-seats/spec-manager; B/codex, B/credentials/mailbox.json, B/keys/mailbox.key (S), B/native, private controller socket/processes | fleet-bridge-clients | role=spec-manager; actor.uid/gid/groups for this account; matching policy.grant.agentUid; per-role credential/state paths; local publication |
| foreman-bot / Forge | fleet-foreman-bot | /var/lib/fleet-seats/foreman-bot; same owned asset layout | fleet-bridge-clients | role=foreman-bot; this account's actor/grant UID and private paths; local publication |
| pilot-seat / dev-3 | fleet-pilot-seat | /var/lib/fleet-seats/pilot-seat; same owned asset layout | fleet-bridge-clients on dev-3 | role=pilot-seat; this account's actor/grant UID and private paths; SSH publisher user=fleet-pilot-seat; matching root-owned receiver policy |

The common fleet-bridge service UID, registry/MAC key and T transport key remain separate. Root retains R's allowlist/package pins and publisher SSH private key; endpoint S encryption keys are different and do not exist in R. Each account needs its own rendered Codex/MCP/hook identity and private control socket. Shared ai must not retain read/control/run-as access to endpoints; unrestricted root/sudo would defeat this boundary. Root/Ops and chosen model providers remain trusted readers.

**Parameterisation required before durable enrollment:** R's bridge/authentication/renewal engine already supports per-seat UIDs and render-renewal reads each binding's agentUid. However, shipped 1112e init accepts one --agent-uid for both local seats and gives them one shared credential parent. Extend the setup inputs to per-role UIDs, credential/native paths and publisher login, and have Ops set each CODEX_HOME/workspace/environment; render all grants/bindings/bootstrap deliveries/routes/allowlist from one role map. The renewal template must generate ReadWritePaths for the local per-role credential directories; its shipped shared-directory exception does not cover /var/lib/fleet-seats. Keep ProtectSystem/ProtectHome and peer/file checks, rather than widening access to all homes. The restricted pilot publisher SSH login also needs an Ops-approved executable forced-command route, not bare nologin. Owner GO 16:23 supersedes the earlier suggestion to put this parameterisation in T: it belongs to S/1112g. T keeps the common service UID and an ai-enrolled throwaway pilot; it only renders the existing R template with pilot path/unit substitutions. Final per-role UIDs/paths, template changes for final accounts and durable enrollment wait for S. No S encryption/wait implementation is included in T.

**Direct install answer:** With the shipped 1112e setup helper, converting already-enrolled ai seats to new UIDs requires a fresh initialized setup/state root for the supported helper workflow; editing the allowlist and re-enrolling the same UUID is unsupported. Bootstrap/enroll refuse any existing UUID even after revoke, and rotation preserves its grant. Therefore use only a separate throwaway R proof under ai, or wait for final account parameters before durable init/enrollment. Never delete a durable root. At the low-level protocol an existing registry can accept entirely new seat UUIDs, but replacing the recipient graph (potentially the pilot too), bindings, ledgers and pending work needs a reviewed migration that 1112e does not provide. A fresh root is not inherent to T transport or S cryptography; no simple same-seat UID migration is claimed available now.

**Version / release:** Owner's latest instruction starts release-labelled versions at 1112f: 0.167.2+ignibyte.1112f, keeping model-catalog numeric version 0.167.2. Received 1112e is frozen. T and future package planning must retain factory update-OFF and review the additional managed start/attach stock-provenance guard proposed in /tmp/fork-updater-answer.txt; no runtime change is made by these answers.

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

**Review state:** Owner chose T subject to its no-weakened-check gate; this update is plan text only. S has conditional owner approval (3f56caab): build only after the pilot works. Account/provisioning text below is permitted now; S code remains held. Plan 003 stays held pending the owner's ordering decision; this text does not place it after S. **FORK PLAN 004 WRITTEN.**

## R execution authorization

Owner review 14:31 CST approves R now, not merge/deployment. Root renewal executes pinned package code/config only; failures must be nonzero with one ids-only log line. T is approved in principle but waits until R is installed/proven and launcher conditions pass; S was not approved at that 14:31 review. The later 3f56caab decision approves S only after the pilot works. Seat-launch question was answered in ten lines before any R code/build. No Forge/dev-3 contact is authorized. Stop at FORK PACKAGE READY dist/1112e with every hash and the unchanged full bwrap digest.

## R proof result — 2026-11-12

All R proofs PASS on code head `f760cedab810e1dc9b5128d1ba0a3d599dc4ebf9` (`plan/004-renewals`). Runtime/voice source `453f5ee1190ded9792155d142ef751dcc3e13963`; Rust tree `b16fc65d8703e89bd7976c0b40732125156adc59` equals HEAD. Main remains `32f47da69f46569d01d91ad2c412366f0c64bf07`; no merge, deployment or fleet-host contact.

The first unqualified package was retained only inside the task directory, then rebuilt after fixing candidate-file writes: a deterministic private temporary file now fsyncs before atomic rename, and a partial temporary is rebuilt from the same committed nonce. Legacy rotation keeps relative output support. The generated root unit bounds/retains SETUID/SETGID for the approved UID drop under NoNewPrivileges; real child UIDs have zero effective/permitted/ambient capabilities. Remote stderr is never projected into the root log, including a peer deliberately echoing its bearer. The root allowlist is the only runtime configuration; setup/bindings are read only during explicit rendering.

Proof uses NVMe TMPDIR/private /tmp, a private volatile /run required by nspawn, task-owned /etc inside isolated systemd, separate service/endpoint UIDs, and no host bus. The legacy setup fixture has its own mount namespace so its test sudoers cannot affect later lanes. All failed fixture iterations were corrected before the complete planned proof below. No production sandbox or authentication check was waived.

```text
$ RUSTUP_TOOLCHAIN=1.95.0 CARGO_BUILD_JOBS=8 CARGO_TARGET_DIR=/mnt/fast/target TMPDIR=$TASK/tmp cargo test --locked --manifest-path codex-rs/Cargo.toml -p codex-factory -- --test-threads=8
test result: ok. 7 passed; 0 failed; 0 ignored; 0 measured; 0 filtered out
(binary target: 0 tests, PASS)
$ TMPDIR=$TASK/tmp python3 -m unittest discover -s scripts/factory -p test_check_package.py
Ran 9 tests
OK
$ rustfmt --check --edition 2024 --config skip_children=true codex-rs/factory/src/main.rs codex-rs/factory/src/service.rs
exit 0
$ RUSTUP_TOOLCHAIN=1.95.0 CARGO_BUILD_JOBS=8 CARGO_TARGET_DIR=/mnt/fast/target TMPDIR=$TASK/tmp python3 scripts/factory/package.py --output dist/1112e --work-dir $TASK/package-2
    Finished `release` profile [optimized + debuginfo] target(s) in 0.25s
    Finished `release` profile [optimized + debuginfo] target(s) in 13m 27s
Complete package created: /srv/stacks/codex/dist/1112e
$ # package.py --record-qualification refreshed/sealed the final operator assets before proof; status remained pending.
$ RUSTUP_TOOLCHAIN=1.95.0 CARGO_BUILD_JOBS=8 CARGO_TARGET_DIR=/mnt/fast/target TMPDIR=$TASK/tmp python3 scripts/factory/qualify_renewals.py --package dist/1112e --work-dir $TASK/full-proof-complete --full-package-proof
{"status": "passed", "mailboxLanes": 7, "renewals": true, "setup": true, "packagedTools": true, "externalModelRequests": 0, "externalFleetHostContacts": 0, "bwrapSha256": "bd2ed101e19111f5c06ea4bb16ace19ca10693a56b343265b156937d4434f023"}
```

Same-source fault pair uses dev-small, GNU target, eight jobs, the pinned checksum-verified V8 pair, and factory/app-server fixture-fault features; production binaries contain no fixture faults. All seven lanes ran against the appropriate new production/fault pair:

| Proof | Result | Seconds |
| --- | --- | ---: |
| Renewals / isolated generated systemd timer | 21 PASS | 20.2 |
| Legacy setup / actual loopback OpenSSH | 3 seats, 4 directed edges, restart/replay PASS | 4.17 |
| contract | 64 PASS | 0.21 |
| credentials | 7 PASS | 5.63 |
| recovery | 9 PASS | 17.35 |
| native | 5 PASS | 2.07 |
| lifecycle | 4 PASS | 1.67 |
| durability | 7 PASS | 1.52 |
| reconciliation | 5 lanes PASS | 10.38 |
| Packaged shell / workspace write / MCP | 3 PASS | 2.12 |

Renewal checks include deferred local conversation and two timer rotations per local seat with zero remote hosts; same-key lapse restoration/40 renewals with one key slot; wrong key/clock floor/no shortening (crate tests); immutable grants/sequence/receipts; singleton/UID/code/mode/TTL rejection, relative destinations and symlinked allowlist refusal, wrong pinned SSH host-key refusal; five real process-death boundaries (prepare, registry commit, candidate rename, output, confirmation); stale publisher rejection inside overlap; real pinned SSH publication/lost ack/outage/secret-echo refusal; outage beyond TTL; later explicit admission with reserved IDs; host/seat revocation; and reboot fencing. A failed actual unit reports ExecMainStatus=1/Result=exit-code and exactly one ids-only failure line.

Each packaged tool case completed through the required packaged code-mode host, returned the verified real effect to the local model fixture, and made exactly two local model requests. No owner login/config/cache was read; external model requests = 0. Full upstream shipped 45-path union, voice source/runtime, root launcher and fork unit assets pass completeness/checksums. All hashes are in dist/1112e/MANIFEST.json and SHA256SUMS. Bwrap `bd2ed101e19111f5c06ea4bb16ace19ca10693a56b343265b156937d4434f023` is unchanged. Version remains 0.167.2+ignibyte.1112b; actual ABI requirements remain in the manifest.

The 14 recorded upstream-owned workspace failures remain failures in plan 002, not waived; no full workspace rerun was requested for R. Real Forge/dev-3 install, AppArmor path approval, current live backend and R operation on those hosts remain owner/Ops review. T runtime, its launcher/SSH-policy changes, S and plan 003 were not built. Stop at FORK PACKAGE READY dist/1112e for install review.

Implementation citations in INTERFACE.md were mapped to this qualified Rust tree, including the new credentialCurrent sync flag. The final package seal records this proof; the temporary NVMe task tree is removed after sealing. Final source review and install approval remain with the owner.

## T execution decisions — owner GO 16:23 CST

1112e is installed/proven per owner; received bytes stay frozen. Build only T/1112f, version 0.167.2+ignibyte.1112f, whole upstream package/voice/root launcher plus the fork. Start this T branch from main and fast-forward the unmerged R dependency; main is untouched. Preserve the compile-time factory updater gate; no additional provenance guard or updater code in T. Ops sets updater.autoUpdateEnabled=false and drains stock backend/updater at cutover. The stronger stock start/attach refusal is a separately reviewed S item.

The pilot alone uses ai and roots named pilot (state, config, run, renewal runtime, units and staged SSH generation). SETUP supplies exact init, activation and owned-root removal commands; its root is never reused as final. Final per-endpoint account/UID/path parameterisation and the final once-only init belong to S/1112g, after the pilot works. Do not add those parameters or S crypto/wait code to T. The helper may render existing renewal templates with pilot path/unit substitutions so the approved throwaway paths work without broadening writable roots. Until S is live the mailbox carries notices only, never a secret.

No host contact, deployment, merge, 003 work, account provisioning, firewall/policy edits or message dispatch is authorized. Generated artifacts are reviewed whole and installed by Ops through ucs-ops. Stop at FORK PACKAGE READY dist/1112f with all hashes and the full bwrap digest.

## T implementation and development proof — 2026-11-12

Runtime source is frozen at `c2f29c9f13bdd414736fc986284cb49672829c58`; operator/qualification implementation at `863900fa6e`. Whole 1112f is being built by package.py from that Rust tree, with version 0.167.2+ignibyte.1112f and no additional updater/provenance code. Main stays `32f47da69f46569d01d91ad2c412366f0c64bf07`. No fleet-host contact or deployment occurred.

```text
$ RUSTUP_TOOLCHAIN=1.95.0 CARGO_BUILD_JOBS=8 CARGO_TARGET_DIR=/mnt/fast/target TMPDIR=$TASK/tmp cargo test --locked --manifest-path codex-rs/Cargo.toml -p codex-factory -- --test-threads=8
test result: ok. 12 passed; 0 failed; 0 ignored; 0 measured; 0 filtered out
(binary target: 0 tests, PASS)
$ TMPDIR=$TASK/tmp python3 -m unittest discover -s scripts/factory -p test_check_package.py
Ran 9 tests
OK
$ rustfmt --check --edition 2024 --config skip_children=true codex-rs/factory/src/main.rs codex-rs/factory/src/network.rs
exit 0
$ # Python compile and runbook shell syntax checks
PASS; runbook host commands were parsed, never executed
```

The development T proof passes 20 groups against the current production/fault bridge pair. It runs real OpenSSH/PAM in an owned private systemd/network fixture, with the actual kernel peer UID, a password-locked fleet-bridge account, root-owned one-line key/Match/dispatcher/zero-argument launcher, and no service sudo. It proves the four-line Ops check (including explicit SSH refusals, never a timeout), exactly one ids-only wrong-command syslog line, three enrollments/four directed edges/two forbidden edges, idle/no registry polling, duplicate owners, lease lapse/restoration, bearer reload/R publication, both process deaths/owned stale cleanup, wrong pins/UIDs/fake SSH context/key injection/root code/config generations, nine bad replies, four wrong authenticated greetings, three commit/caller cuts, real systemd restart/backoff, current-key/revocation/boot fences, and pilot R path/allowlist/unit integration without identity/registry migration. Fixture failures were corrected before this pass; an earlier fixture pointed at an old explicit-target debug output, so its crash proof was discarded and rerun against the correctly hashed current binaries.

The public socket is atomically renamed into place only after its inode/device/process ownership record is durable. Crashes before publication cannot leave an unproven public socket; live or unknown sockets are never deleted to make startup pass. Legacy connect-host/remote-entry remains solely for compatibility fixtures; the shipped setup requires Forge-initiated transport.

Full release/package proof and final hashes follow below. The 14 recorded upstream-owned workspace failures remain failures in plan 002, not waived. No unrelated workspace test rerun, S code, automatic watcher, encryption, final-account parameterisation, stock provenance guard, merge or plan 003 work is claimed.
