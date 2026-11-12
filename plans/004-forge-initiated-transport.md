# Plan 004 — Forge-initiated transport (design; approval pending)

**Goal:** Preserve mailbox authority/facts over Forge→dev-3 SSH. Includes the 13:28 additions. No code/host contact/init execution; new verbs/timers are **proposed, unavailable in 1112d**.

1. **Transport:** Configuration alone cannot reverse `network.rs:298/386`. Unix forwarding loses UID/host attestation; nested SSH retains Forge's entry. Add connect-source/source-entry: fleet-bridge→ai@dev-3, dedicated key/pinned host/fixed command, no shell/PTY/forwarding. Dev-3 retains SO_PEERCRED/presence checks; existing frames/remote_frame preserve per-request host/key/lease checks. Serialize, bound frames/2.5s requests, separate idle wait, close uncertain channels. No wire-schema change/fabricated SSH_*.

2. **Ops/files:** Move transport authorized_keys/sudo rule to dev-3; remove Forge counterparts/dev-3 outbound unit/key/pin. Keep separate UIDs/service-owned 0600 keys/pins. Forge gains connector/renewal units; separate pinned forced ai publisher receives credentials on stdin. Files: `codex-rs/factory/src/{main.rs,network.rs,service.rs}`, `scripts/factory/{setup-forge-box.py,test-setup-forge-box.py,qualify_forge_transport.py}`, `docs/{factory-setup.md,fork-mailbox.md}`, CHANGELOG-FORK.md, this plan. Setup generates Ops-installed units/config/publisher fragments.

3. **Cost/proof:** Whole `dist/1112e`, root-owned `/opt/fleet-codex/<commit>-1112e`, per-release AppArmor. Bridge/CLI/app-server/matching-commit voice change; measure remaining binary deltas. Preserve rg/bwrap `bd2ed101e191…`. Measured builds 1019.56+135.416+129.34s; mailbox/tools 40.96+20.44s (~23min); allow 25–35min plus implementation/new tests. Prove transport cuts/reboots/duplicates, migration/renewals, crate/package/tool/mailbox checks. Real routing/sudo/AppArmor/reboots need Ops qualification.

4. **Failures:** Drops preserve commits/outbox; reconcile original IDs/payloads. Dev-3 reboot preserves ledgers; require lock/dead-incarnation proof before socket cleanup. Forge reboot retains authority fencing/quarantine/reconciliation, no replay/re-init. Connector/proxy locks reject duplicate sessions; timeout closes channel, preventing reply mispairing.

5. **Local split:** Technically YES; 1112d pre-admits dev-3 for 24h. Strict deferral needs proposed init --defer-remote. Current mechanics below assume SETUP.md accounts/directories/real-path hub unit and inventory DEV3_AI_UID. Skip successful init; partial init stops. Owner's init hold remains; days-long operation needs item 8.

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

6. **Keep init:** Successful state/config survives. Proposed validated configure-transport preserves all hub/host/seat/incarnation/current-key IDs, grants/sequences, registry/service key/outboxes/ledgers; atomically regenerates direction/address/port/pins/units. No fresh roots/deletion/cleanup script. Partial/inconsistent init stops. Deferred admission uses saved UUIDs; keep pilot unenrolled/routes disabled.

7. **Host lease:** No adequate existing command: rotate-host needs file edits and exhausts 32 used-key slots. Add service-owner renew-host: locked/audited same-key extension ≤86400s, boot/revocation checks; rotation remains separate. Proposed Ops/root command: `python3 "$PKG/setup-forge-box.py" renew --config /etc/fleet-manager-bridge-renew.json`. Mandatory root-owned `fleet-manager-bridge-renew.service/.timer` runs at boot/every five minutes, renews host at 12h, drops to fleet-bridge for authority operations; no hand edits. Lapse rejects new remote admissions (`host_credential_inactive`); previously admitted work may finish. Committed mail/receipts remain, subject to message TTL; reconcile lost replies, never un-revoke/replay.

8. **Seat credentials:** Same Ops/root broker rotates enrolled seats every 30min using existing rotate (3600s TTL/60s overlap); endpoints never self-renew. Root-owned allowlist fixes IDs/destinations. Durably stage; publish ai-owned 0600 files using fsync/atomic rename locally/forced remote publisher. Confirm activation/delivery; recover pending publication before rotating again; never log/pass secrets as arguments. Failed publication alerts/fails closed. `client.rs:223` reloads each frame: no restart; expiry gives credential_expired, stale rotated token credential_denied. Model work is not automatically cancelled; mailbox calls fail and bounded retries may exhaust. Restore credentials/reconcile original IDs/receipts, explicitly replay eligible exhausted outbox entries; never repeat Forge actions. Working timers and publication-crash/overlap/outage tests are mandatory setup proof.

Plan 003 remains unbuilt. **FORK TRANSPORT NOTE READY.**
