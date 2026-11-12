# Plan 004 — Forge-initiated transport (design only; approval pending)

**Goal:** Carry the mailbox over Forge→dev-3 SSH, preserving authority/durable facts. Owner briefs: 2026-11-12 13:24/13:26 CST; package merged at `32f47da69f46569d01d91ad2c412366f0c64bf07`. Design only; no host contact.

1. **Smallest change:** Configuration alone cannot reverse `network.rs:298/386`: dev-3 spawns SSH per request. Unix forwarding loses endpoint UID/host attestation; nested reverse SSH retains Forge's entry/key/sudo rule. Add Forge `connect-source` and dev-3 `source-entry` in `codex-rs/factory/src/{main.rs,network.rs}`. Forge fleet-bridge dials **ai@dev-3** with a dedicated key, inventory-pinned host identity, fixed command, no shell/PTY/forwarding. Forced entry becomes fleet-bridge; SO_PEERCRED/allowed UID and presence-process checks stay on dev-3. Existing Forwarded/Returned frames travel over stdio; Forge binds host/key UUIDs and calls unchanged `service::remote_frame`, rechecking the lease each request. Serialize one request; bound frames/waits, retain 2.5s request deadlines, separate idle wait, close on uncertainty. No schema/receipt/storage change or fabricated SSH_* context.

2. **Ops/files:** Keep separate service UIDs, protected roots and ai credentials. Forge gains a fleet-bridge connector unit requiring the hub, reconnect/backoff, service-owned 0600 key/pin under /etc. Move the transport authorized_keys/exact ai→fleet-bridge sudo rule to **dev-3**, pinned to entry/path/host/key; remove Forge's counterparts and dev-3's outbound proxy unit/key/Forge pin. Keep operator keys. Credentials use files/encrypted stdin, never argv/logs. Also change `scripts/factory/{setup-forge-box.py,test-setup-forge-box.py}`, add `scripts/factory/qualify_forge_transport.py`, update `docs/{factory-setup.md,fork-mailbox.md}`, CHANGELOG-FORK.md and this plan.

3. **Cost/proof:** New whole package via package.py: `dist/1112e`, root-owned `/opt/fleet-codex/<40-hex-commit>-1112e`, new per-release AppArmor profile. Bridge, CLI/app-server and matching-commit voice helper change; rebuild all binaries, measure code-mode-host/exec-server deltas. Preserve rg and exact bwrap `bd2ed101e191…`; abort on drift. Measured: primary build 1019.56s, voice 135.416s, faults 129.34s, mailbox 40.96s, tools 20.44s (~23min). Estimate **25–35min**, plus implementation/cold-fetch/unmeasured unit/new-fixture time. Proof: factory tests, completeness/voice/tools/seven mailbox lanes, separate-UID SSH cuts before/after commit, reboot fencing and duplicate sessions. Actual routing/service-UID egress, host sudo/AppArmor and physical reboots require owner/Ops qualification.

4. **Failures:** SSH drop closes the channel; outbox/commits survive, reconcile identical message IDs/payloads with existing sequence rules. Dev-3 reboot preserves ledgers, invalidates process identities; remove sockets only after lock/dead-incarnation proof. Forge reboot retains `lifecycle.rs::prepare_locked` fencing: revoked authority, quarantined pending, active work requiring explicit reconciliation; no automatic replay/re-init. Two sessions: per-host connector and dev-3 proxy-state locks reject the second before socket mutation. A timeout kills the channel, preventing late-reply mispairing. No new durable queue or exactly-once network claim.

5. **Today: local seats YES; literal deferred host admission NO.** `initialize` (`setup-forge-box.py:174`) pre-admits dev-3 for 24h; enrollment/transport can wait without re-init. Propose `init --defer-remote`, later admit-host with saved UUIDs, for strict deferral. Ops/root commands below assume SETUP.md accounts/directories/hub unit with this real PKG and inventory DEV3_AI_UID. Skip init if already successful; partial init means stop. Leave remote SSH rules/unit uninstalled.

```sh
set -euo pipefail
PKG=/opt/fleet-codex/6cb7fd718329da87bfd1dcf4c10536cf82b1c367-1112d
STATE=/var/lib/fleet-manager-bridge; CFG=/etc/fleet-manager-bridge
: "${DEV3_AI_UID:?set from Ops inventory}"
sudo -u ai python3 "$PKG/setup-forge-box.py" preflight --bin-dir "$PKG/bin"
sudo -u fleet-bridge python3 "$PKG/setup-forge-box.py" init --bin-dir "$PKG/bin" \
  --state-root "$STATE" --config-root "$CFG" --run-root "$STATE/run" \
  --remote-state-root "$STATE" --remote-config-root "$CFG" --remote-run-root "$STATE/run" \
  --agent-uid "$(id -u ai)" --remote-agent-uid "$DEV3_AI_UID" \
  --remote-service-uid "$(id -u fleet-bridge)" --ssh-address 10.200.68.55 \
  --ssh-port 22 --ssh-user ai --service-user fleet-bridge
systemctl daemon-reload
systemctl enable --now fleet-manager-bridge.service
for ROLE in spec-manager foreman-bot; do
  FORK_TOKEN=$(sudo -u fleet-bridge python3 "$PKG/setup-forge-box.py" bootstrap --config-root "$CFG" --role "$ROLE" | python3 -c 'import json,sys; print(json.load(sys.stdin)["bootstrapFile"])')
  install -o ai -g ai -m 0600 "$FORK_TOKEN" "$CFG/credentials/$ROLE.bootstrap"
  sudo -u ai python3 "$PKG/setup-forge-box.py" enroll --bin-dir "$PKG/bin" --binding "$CFG/$ROLE.binding.json" --bootstrap "$CFG/credentials/$ROLE.bootstrap"
done
```

Keep pilot unenrolled/routes disabled and preserve IDs/state. Later use bootstrap-remote; expired host lease needs rotate-host with a fresh UUID/coordinated config updates, never re-init/re-admit/revoke. Local credentials need hourly renewal. Plan 003's four additions are acknowledged; build waits. **FORK TRANSPORT NOTE READY.**
