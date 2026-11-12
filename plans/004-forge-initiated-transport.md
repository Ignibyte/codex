# Plan 004 — Part R: renewals (one page; approval pending)

**Goal:** Run the hub/two local seats unattended before dev-3 admission. Owner brief 004 authorizes planning only: R awaits text approval, T a separate order, 003 waits. Owner reports UID 983/client GID 982 and passing preflight; init/keygen/ssh-config/units stay held. New commands below are proposed.

**Smallest version:** Keep connect-host/remote-entry and credential wire format. Fleet-bridge `init --defer-remote` saves seat/host/key UUIDs, omits admission/pilot bootstrap; local enrollments/hub work independently. Fleet-bridge `admit-remote --config-root <cfg>` later admits saved UUIDs, verifies existing admissions; no re-init/deletion. Default init stays compatible; partial init stops.

Add service-owner `renew-host --state <service> --host <uuid> --key-id <current> --ttl 86400`: registry lock, boot/revocation/current-key checks, audit, clock-rollback floor; extend without shortening/key-slot consumption. No auto-admission/un-revocation; lapse restoration requires the approved allowlist. Renew at 12h. Lapse denies later frames; admitted work can finish; commits/receipts persist subject to message TTL. Key rotation stays separate.

**Rotation/publication:** Root oneshot runs `setup-forge-box.py renew --config /etc/fleet-manager-bridge-renew.json`, singleton lock; drops to fleet-bridge for signing/configured ai for publication. Rotate enrolled seats every 30min, TTL 3600s/overlap ≤60s. Rotation ID/private pending record makes prepare→registry commit→publish→confirm resumable; resolve it before another rotation. Recovery rechecks boot/grant/revocation; never publish uncommitted/expired candidates. Fsync/atomic rename preserves ai:0600. Remote publisher: separate inventory-pinned Forge→dev-3 key, fixed ai command, encrypted stdin, bounded/idempotent seat-bound install; no sudo/shell/forwarding. Confirm activation/delivery; retry identical candidates after lost acknowledgments. Tokens/nonces never enter argv/logs.

**Mandatory Ops setup:** Root:0644 `fleet-manager-bridge-renew.service/.timer`: root oneshot, UMask=0077, NoNewPrivileges=yes, explicit writable roots; OnBootSec=30s/OnUnitActiveSec=5min. Root:0600 `/etc/fleet-manager-bridge-renew.json` fixes package/state/seat/incarnation/UID/destinations/publisher/policies; root:0700 `/var/lib/fleet-manager-bridge-renew` holds pending publication. Validate owners/paths/no symlink escape; skip deferred/unenrolled pilot. Forge Ops provisions root:0600 publisher key/pin; dev-3 Ops adds its forced public-key line to ai's 0700/0600 SSH directory/file, no sudo. SETUP.md retains sandbox preflight/executor labels.

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

Whole 1112e includes voice/launcher. Require bwrap `bd2ed101e19111f5c06ea4bb16ace19ca10693a56b343265b156937d4434f023`; matching CLI/bridge/voice source, all binary hashes. Ops installs root-owned `/opt/fleet-codex/<40-hex-commit>-1112e`/release AppArmor profile. R supports locals; existing remote route still needs owner opening. Build/proof allowance: T's measured ~23min baseline, 25–35min plus new tests/implementation. No host contact/deployment, policy edits, Forge workflow, tickets/backlogs/evidence folders or 003 code. Owner reviews before merge.

---

# Plan 004 — Part T: Forge-initiated transport (one page; owner order required)

**Goal/gate:** Add optional Forge-initiated transport after reviewed R, preserving renewal/publication. Build only on the owner's direction choice; if dev-3→Forge SSH opens, keep R/current mode and omit T. Existing init/state survives; no fresh roots/deletion. Commands are proposed.

**Smallest version:** Add Forge connect-source/dev-3 source-entry in F/{main.rs,network.rs}; keep connect-host as an explicit alternative. Forge fleet-bridge owns a dedicated 0600 client key/inventory-sourced dev-3 pin and dials ai@dev-3. Fixed codex-factory-v1, private address, strict host checking; no user-config/agent/proxy fallback, arbitrary shell, PTY or forwarding. Dev-3's exact forced command/sudo rule changes ai to fleet-bridge; service remains nologin/no sudo. Require genuine SSH context, never fabricated SSH_*.

Dev-3 owns remote.sock; verifies SO_PEERCRED/allowed ai UID and reported process incarnation. Existing Forwarded frames travel upstream; Forge validates host/key IDs and calls unchanged service::remote_frame per request, returning existing Returned frames. Never forward the hub Unix socket: that loses endpoint UID/host attestation. No message/credential/receipt schema change, cached authority or durable transport queue. Serialize one request; bound queue/frames/2.5s deadlines including lock waits; separate idle wait/keepalive. Timeout/drop/truncation/mismatch closes channel before another request; reap only owned children. Recheck lease/revocation on established sessions.

**Migration:** Root-only configure-transport validates setup/registry/R allowlist/generation; rejects partial/inconsistent state. Preserve hub/host/seat/incarnation/current-key IDs, grants/sequences/leases, MAC key, credentials/outboxes/ledgers. Stage direction/address/port/pin/package paths/unit/sudoers/key fragments; atomic writes/generation checks prevent mixed activation. Stop/drain old connector before enabling new. No manual JSON/re-init/key rotation/authority expansion. Re-render R allowlist paths; publisher key/timer stay separate. Failed activation restores configuration only; changed identity blocks rollback.

**Ops delta:** Retain UIDs/groups/modes/hub/renewal units. Forge gains fleet-manager-bridge-source.service (fleet-bridge, requires/after hub, reconnect/backoff). Move only transport forced-key/exact sudoers entry to dev-3, pinned to package path/saved host/key UUIDs. Preserve unrelated operator/publisher keys. Remove Forge's obsolete transport entries; disable dev-3 outbound unit/key/Forge pin after owned-process checks. sshd starts source-entry, without another permanent dev-3 proxy unit. Ops reviews/installs protected host-specific fragments; fleet-bridge cannot install policy.

```sh
# Forge — Ops/root, after explicit T order and whole-package installation
python3 "$PKG/setup-forge-box.py" configure-transport --config-root /etc/fleet-manager-bridge --direction forge-initiated --input "$OPS_INPUT" --output "$STAGE"
# Both hosts — Ops/root: install reviewed host-specific STAGE generation
# Forge — Ops/root, after dev-3 entry/pin installation and verification
systemctl daemon-reload
systemctl enable --now fleet-manager-bridge-source.service
```

**Failures:** Drop/lost reply retains acceptance/commits; reconcile original IDs/payloads/sequences, never infer completion from delivery. Dev-3 reboot retains ledgers, invalidates process reports; require exclusive lock/owner/type/dead-incarnation checks before socket removal. Forge reboot retains boot authority fencing/quarantine and explicit reconciliation; connector/timer cannot bypass it. Per-host connector/proxy-state locks reject duplicate sessions before socket mutation. Timeout prevents late-reply mispairing; busy/deferred/uncertain work remains explicit. Credential failure follows R; SSH is not a perpetual lease.

**Files:** T changes F/{main.rs,network.rs}; S/{setup-forge-box.py,test-setup-forge-box.py,package.py,check_package.py,test_check_package.py}; adds S/qualify_forge_transport.py and S/systemd/fleet-manager-bridge-source.service.in; updates docs/{factory-setup.md,fork-mailbox.md}, CHANGELOG-FORK.md, this plan. Reuse R broker/publisher/tests; preserve current-mode compatibility. Other files require plan amendment.

**Proof:** Real loopback SSH/separate UIDs; wrong pin/key/UID/host and forbidden shell/forwarding; isolated publisher/transport keys; both transport modes; live-channel lease/revocation; concurrency, each failure above, migration/repeated migration/partial activation/rollback. Verify unchanged IDs/receipts and continued R renewal. Full-package-proof repeats offline tool/voice/seven mailbox/R timer-crash proofs. Reboot fences use disposable fixtures; no host contact/reboot.

```sh
# z690 — fork seat, only after explicit T order; fresh NVMe TASK
mkdir -p "$TASK/tmp"
export RUSTUP_TOOLCHAIN=1.95.0 CARGO_BUILD_JOBS=8 TMPDIR="$TASK/tmp"
(cd codex-rs && CARGO_BUILD_JOBS=8 cargo test --locked -p codex-factory -- --test-threads=8)
python3 scripts/factory/package.py --output dist/1112f --work-dir "$TASK/package"
python3 scripts/factory/qualify_forge_transport.py --package dist/1112f --work-dir "$TASK/proof" --full-package-proof
```

T is smaller in code, not a partial package: rebuild/stamp affected bridge/CLI/app-server/voice, report all helper hashes, retain R's exact bwrap/whole bundle. Ops: root-owned /opt/fleet-codex/<40-hex-commit>-1112f and release AppArmor profile. Measured baseline ~23min (1019.56+135.416+129.34+40.96+20.44s); allow 25–35min plus new tests/implementation, no clean-cache promise. Production routes/service-UID egress/pins/sudo/AppArmor/two kernels/live jobs remain owner/Ops proof. No deployment, Forge subsystem, tickets/backlogs/evidence folders, automatic cutover or 003 code. Record proof here; owner reviews before merge.
