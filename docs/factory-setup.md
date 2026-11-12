# Forge bridge pilot — Part T, complete package 1112f

Until S is live the mailbox carries notices only, never a secret.

This is the owner/Ops installation runbook. The fork seat builds and qualifies offline and changes no fleet host. Owner reports 1112e installed and proven; spec-manager remains on 1112d until one drained cutover to 1112f. Install the whole folder, including bin/, codex-path/, codex-resources/voice/, codex-package.json, the root codex -> bin/codex launcher, MANIFEST.json, SHA256SUMS, operator scripts and systemd/. Version: 0.167.2+ignibyte.1112f. The manifest records source, ABI requirements and every hash.

The pilot uses **throwaway roots enrolled under ai**. Never reuse any pilot root as the final root. Part S/1112g supplies the per-role UID/path parameters and final accounts fleet-spec-manager, fleet-foreman-bot and fleet-pilot-seat; the final root is initialized once then. There is no supported same-seat UID rebind through an allowlist edit. Preserve any existing durable setup. Plan 003 and S implementation remain held.

## 1. Ops prerequisites and actors

All host changes below enter a reviewed ucs-ops PR. The helper stages files and starts no service. Ops installs generated files verbatim; do not hand type a key, dispatcher, launcher, SSH policy or unit.

| Identity/path | Owner, mode and rule |
| --- | --- |
| fleet-bridge on both hosts | same reserved nonroot numeric UID; primary group fleet-bridge; password locked; **no sudo rule** |
| fleet-bridge-clients | ai and fleet-bridge only; permits socket use, never writing service/config roots |
| Forge fleet-bridge login | /usr/sbin/nologin; no inbound transport key |
| dev-3 fleet-bridge login | generated /etc/ssh/fleet-bridge-pilot-login, root:root 0755; protocol dispatcher, no general shell |
| /opt/fleet-codex and all package ancestors | root:root, no group/world write, no symlink ancestors |
| package dirs/executables | root:root 0755; ordinary resources/docs 0644; preserve packaged relative symlinks |
| STATE, STATE/run | fleet-bridge:fleet-bridge-clients 2710 |
| CFG | fleet-bridge:fleet-bridge-clients 2750 |
| CFG/grants,operator,transport; STATE/service,pending,connector or source | fleet-bridge:fleet-bridge-clients 0700; private files 0600 |
| CFG/*.binding.json, setup.json, routes*.json | fleet-bridge:fleet-bridge-clients 0640 |
| CFG/credentials | ai:ai 0700; each bootstrap/credential 0600 |
| STATE/native | fleet-bridge:fleet-bridge-clients 0710 |
| STATE/native/ROLE | ai:ai 0700 |
| run/bridge.sock or remote.sock | service creates 0660 with clients group |
| /etc/ssh/fleet-bridge-pilot.keys | root:root 0644; exactly one restricted, source-bound forced-command key |
| generated SSH JSON/policy | root:root 0644; wrappers root:root 0755 |
| renewal allowlist and publisher private key/pin | root:root 0600; root-owned non-writable ancestors |
| /var/lib/fleet-manager-bridge-pilot-renew | root:root 0700 |
| /var/lib/fleet-codex-pilot-stage | root:root 0700; generated transport generation |

Ops assigns free UIDs/GIDs; fixture numbers 65534/65532 are not production assignments. Use fresh ai logins or a fresh tmux server after adding clients membership; an existing tmux server retains its old groups. A fleet-bridge home is never an AuthorizedKeysFile. Keep ordinary ai login and the separate R publisher distinct from the service login.

Only the existing **Forge 10.200.68.55 -> dev-3 port 22** route is used. Dev-3 -> Forge port 22 stays closed. No reverse SSH, socket forwarding, firewall change, service sudo or password unlock is permitted. Ops obtains dev-3's ed25519 host public key from trusted inventory, not an unauthenticated ssh-keyscan.

```sh
# Forge — operator shell; use the received manifest's runtimeSourceCommit
PKG=/opt/fleet-codex/c2f29c9f13bdd414736fc986284cb49672829c58-1112f
BIN=$PKG/bin
STATE=/var/lib/fleet-manager-bridge-pilot
CFG=/etc/fleet-manager-bridge-pilot
STAGE=/var/lib/fleet-codex-pilot-stage
```

```sh
# dev-3 — operator shell
PKG=/opt/fleet-codex/c2f29c9f13bdd414736fc986284cb49672829c58-1112f
BIN=$PKG/bin
STATE=/var/lib/fleet-manager-bridge-remote-pilot
CFG=/etc/fleet-manager-bridge-remote-pilot
STAGE=/var/lib/fleet-codex-pilot-stage
```

The final package path is printed in the READY answer. SOURCE is the absolute received **whole package folder**. On each host Ops installs it once, then provisions only that host's fresh pilot roots:

```sh
# Both hosts — Ops/root; variables above are host-specific
(cd "$SOURCE" && sha256sum -c SHA256SUMS)
install -d -o root -g root -m 0755 /opt/fleet-codex
test ! -e "$PKG"
cp -a -- "$SOURCE" "$PKG"
chown -R root:root "$PKG"
chmod 0755 "$PKG" "$PKG/bin" "$PKG/codex-path" "$PKG/codex-resources"
test ! -e "$STATE" && test ! -e "$CFG"
install -d -o fleet-bridge -g fleet-bridge-clients -m 2710 "$STATE" "$STATE/run"
install -d -o fleet-bridge -g fleet-bridge-clients -m 2750 "$CFG"
install -d -o fleet-bridge -g fleet-bridge-clients -m 0710 "$STATE/native"
install -d -o ai -g ai -m 0700 "$CFG/credentials"
```

```sh
# Forge — Ops/root
install -d -o ai -g ai -m 0700 "$STATE/native/spec-manager" "$STATE/native/foreman-bot"
```

```sh
# dev-3 — Ops/root
install -d -o ai -g ai -m 0700 "$STATE/native/pilot-seat"
```

### Sandbox precondition

1112f's version change produces bwrap digest **0ce4fa5b9da214841641f12032afa6cda3d93b45f321705fa8bde5fb2c270b74**. This differs from received 1112e. Ops must approve the new digest and exact installed path through its codex-cli AppArmor policy before use. The fork seat changes no AppArmor, sysctl, xattr or host ownership.

```sh
# Both hosts — ai, same execution context as endpoint jobs; no model call
python3 "$PKG/setup-forge-box.py" preflight --bin-dir "$BIN"
```

This verifies the manifest hash and runs that exact packaged bwrap with a read-only root and new user/PID/network namespaces, including loopback setup. Enrollment and endpoint proof commands repeat it before credential/network actions. Service-only staging is not endpoint proof. If it fails, send Ops its path, digest and error; stop setup. Never substitute danger-full-access. After Ops fixes the policy, repeat this probe and real packaged shell/write/MCP turns on each actual host.

Packages are relocatable; generated units, bindings, SSH generation, R allowlist and Ops sandbox permissions contain absolute paths and must be regenerated for a move. Root code pins must match the whole package. The source build strips a staging bwrap before hashing and never strips packaged bytes afterward; different source/version/toolchain builds need not be byte identical.

## 2. Exact throwaway pilot initialization

Ops supplies AI_UID (Forge ai), DEV3_AI_UID (dev-3 ai), FLEET_UID (common service UID) from inventory. This is the only init, on Forge; no dev-3 init. The remote run directory is inside its throwaway state root.

```sh
# Forge — fleet-bridge, after the fresh roots and endpoint preflight
python3 "$PKG/setup-forge-box.py" init --pilot --defer-remote --bin-dir "$BIN" \
  --state-root /var/lib/fleet-manager-bridge-pilot \
  --config-root /etc/fleet-manager-bridge-pilot \
  --run-root /var/lib/fleet-manager-bridge-pilot/run \
  --remote-state-root /var/lib/fleet-manager-bridge-remote-pilot \
  --remote-config-root /etc/fleet-manager-bridge-remote-pilot \
  --remote-run-root /var/lib/fleet-manager-bridge-remote-pilot/run \
  --agent-uid "$AI_UID" --remote-agent-uid "$DEV3_AI_UID" \
  --remote-service-uid "$FLEET_UID"
python3 "$PKG/setup-forge-box.py" source-keygen --config-root /etc/fleet-manager-bridge-pilot
```

Init reserves hub/host/key/seat/incarnation UUIDs, bindings, grants and disabled routes, but admits no remote host and starts nothing. A partial/repeated init refuses reuse: stop for owner review. Token expiry is recovered through bootstrap or R renewal, never by re-init. The Forge private transport key is CFG/transport/forge-ed25519, service-owned 0600. Copy only its public .pub file for Ops review. Never copy the registry/MAC key off Forge.

## 3. Generate the complete transport installation

Forge Ops/root supplies a root:0600 input JSON, with no bearer/private-key contents:

```json
{
  "forgeAddress": "10.200.68.55",
  "sourceAddress": "<inventory dev-3 private IPv4>",
  "port": 22,
  "clientPublicKey": "<actual Forge forge-ed25519.pub line>",
  "sourceHostKey": "<trusted inventory dev-3 ed25519 public key>"
}
```

For an existing pilot R configuration add renewalConfig with its root:0600 allowlist path. The helper validates that generation with its pinned package helper and checks service state, host/key/seat UUIDs and every binding. Never add it for a different root. Fresh pilot setup has no previous R generation. Staging preserves registry, keys, admissions, grants, sequences, credentials, outboxes and receipts. It refuses inconsistent bindings and non-pilot paths. Output must be absent beneath a root-owned parent; generation.json seals its public file hashes and modes.

```sh
# Forge — Ops/root; OPS_INPUT is the reviewed root:0600 JSON
python3 -I "$PKG/setup-forge-box.py" configure-transport \
  --direction forge-initiated --config-root /etc/fleet-manager-bridge-pilot \
  --input "$OPS_INPUT" --output /var/lib/fleet-codex-pilot-stage
# Forge — Ops/root: display EVERY generated file whole for the ucs-ops PR
for FILE in /var/lib/fleet-codex-pilot-stage/*; do
  printf '\nFILE %s\n' "$FILE"
  cat -- "$FILE"
done
```

This display includes source.json, source_known_hosts, setup.json, entry.json, launch.json, generation.json, the whole key line, Match policy, both wrappers and both units. These contain public identities, paths, pins and hashes, no bearer or private key. Review the actual generation, not placeholder examples. Transfer the whole reviewed staging folder to dev-3 through Ops' established channel. Neither setup nor this fork seat installs it remotely.

The generated key file is exactly ONE line, with the actual public key substituted by the helper:

```text
restrict,from="10.200.68.55",command="/etc/ssh/fleet-bridge-pilot-launcher" ssh-ed25519 <actual-public-key> fleet-forge-source
```

The generated Match policy, shown whole:

```sshconfig
Match User fleet-bridge
    AuthorizedKeysFile /etc/ssh/fleet-bridge-pilot.keys
    AuthorizedKeysCommand none
    PubkeyAcceptedAlgorithms ssh-ed25519
    PubkeyAuthentication yes
    AuthenticationMethods publickey
    PasswordAuthentication no
    KbdInteractiveAuthentication no
    PermitTTY no
    AllowTcpForwarding no
    X11Forwarding no
    AllowAgentForwarding no
    PermitTunnel no
    DisableForwarding yes
    PermitUserRC no
```

DisableForwarding also blocks stream-local forwarding. No user/home key lookup or certificate key algorithm is accepted by this Match. Ops' global sshd policy keeps PermitUserEnvironment no; that directive is not legal inside Match. Place the reviewed fragment in the host's existing include chain, terminate its scope with Match all before unrelated policy, and verify sshd -t and sshd -T -C user=fleet-bridge,host=dev-3,addr=10.200.68.55. The effective options must match this block; a preceding conflicting Match is a blocker.

The generated dispatcher and zero-argument launcher are each three lines; PACKAGE below represents the helper's actual absolute root-owned package path, not a user-supplied runtime input:

```python
#!/usr/bin/python3 -I
import runpy
runpy.run_path('PACKAGE/fleet-bridge-ssh-entry.py')["dispatch"]('/etc/ssh/fleet-bridge-pilot-launch.json')
```

```python
#!/usr/bin/python3 -I
import runpy
runpy.run_path('PACKAGE/fleet-bridge-ssh-entry.py')["launch"]('/etc/ssh/fleet-bridge-pilot-launch.json')
```

The package's complete fleet-bridge-ssh-entry.py is displayed in section 8. The dispatcher accepts only sshd's exact -c forced-launcher invocation. It execs the launcher with zero application arguments. Root:0644 launch.json pins package, entry config, both wrappers, UUIDs, service UID and SHA-256 values; every ancestor is root-owned/non-writable. The launcher requires exactly codex-factory-v1, no SSH_TTY and the fixed authenticated route, then execs only the pinned package bridge source-entry with fixed config. It scrubs the environment. Direct ai calls with forged SSH_* fail the actual service-UID check. Refusal exits 1 and emits exactly one authpriv syslog line containing only UUIDs and a fixed reason:

```text
entry_refused host=<uuid> key=<uuid> generation=<uuid> code=command
```

No raw command, bearer, payload or SSH peer error is logged. Password remains locked; neither service account gets sudo. Dev-3 Ops changes the account's shell to the generated root-owned dispatcher through its reviewed account task; literal nologin cannot execute an SSH forced command. If that protocol-only shell cannot meet host policy, stop for the owner; do not add sudo or open a shell.

The generated Forge source unit, shown whole with renderer tokens:

```ini
[Unit]
Description=Forge-initiated fleet bridge source connection
Requires=@PREFIX@.service
After=@PREFIX@.service network-online.target

[Service]
Type=simple
User=fleet-bridge
Group=fleet-bridge
SupplementaryGroups=fleet-bridge-clients
UMask=0077
ExecStart=@PACKAGE@/bin/codex-factory-bridge connect-source --state @STATE@/connector --config @CONFIG@/transport/source.json
Restart=on-failure
RestartSec=5
TimeoutStopSec=10
KillMode=control-group
NoNewPrivileges=yes
ProtectHome=yes
ProtectSystem=strict
ReadWritePaths=@STATE@

[Install]
WantedBy=multi-user.target
```

The generated hub unit, shown whole with renderer tokens:

```ini
[Unit]
Description=Throwaway pilot fleet bridge hub
After=network.target
[Service]
Type=simple
User=fleet-bridge
Group=fleet-bridge
SupplementaryGroups=fleet-bridge-clients
UMask=0077
ExecStart=PACKAGE/bin/codex-factory-bridge serve --state STATE/service --socket RUN/bridge.sock
Restart=no
KillMode=control-group
TimeoutStopSec=10
NoNewPrivileges=yes
ProtectHome=yes
ProtectSystem=strict
ReadWritePaths=STATE
[Install]
WantedBy=multi-user.target
```

Ops installs only its host's reviewed generation:

```sh
# dev-3 — Ops/root; STAGE is the received complete reviewed staging folder
install -o root -g root -m 0644 "$STAGE/entry.json" /etc/ssh/fleet-bridge-pilot-entry.json
install -o root -g root -m 0644 "$STAGE/launch.json" /etc/ssh/fleet-bridge-pilot-launch.json
install -o root -g root -m 0644 "$STAGE/fleet-bridge-pilot.keys" /etc/ssh/fleet-bridge-pilot.keys
install -o root -g root -m 0644 "$STAGE/fleet-bridge-pilot-match.conf" /etc/ssh/fleet-bridge-pilot-match.conf
install -o root -g root -m 0755 "$STAGE/fleet-bridge-pilot-login" /etc/ssh/fleet-bridge-pilot-login
install -o root -g root -m 0755 "$STAGE/fleet-bridge-pilot-launcher" /etc/ssh/fleet-bridge-pilot-launcher
# Ops' PR connects this Match fragment to sshd's include chain and installs
# the account dispatcher/locked-password/no-sudo policy, then validates/reloads sshd.
```

```sh
# Forge — Ops/root
install -o fleet-bridge -g fleet-bridge-clients -m 0600 "$STAGE/source.json" "$CFG/transport/source.json"
install -o fleet-bridge -g fleet-bridge-clients -m 0600 "$STAGE/source_known_hosts" "$CFG/transport/source_known_hosts"
install -o fleet-bridge -g fleet-bridge-clients -m 0640 "$STAGE/setup.json" "$CFG/setup.json"
install -o root -g root -m 0644 "$STAGE/fleet-manager-bridge-pilot.service" /etc/systemd/system/
install -o root -g root -m 0644 "$STAGE/fleet-manager-bridge-pilot-source.service" /etc/systemd/system/
systemd-analyze verify /etc/systemd/system/fleet-manager-bridge-pilot.service /etc/systemd/system/fleet-manager-bridge-pilot-source.service
systemctl daemon-reload
systemctl enable --now fleet-manager-bridge-pilot.service
```

Ops also transfers the generated Forge CFG/pilot-seat.binding.json to dev-3's CFG/pilot-seat.binding.json, service:clients 0640. Do not transfer other seats' credentials or service state. The next section installs R publication before pilot enrollment.

## 4. Enroll locals, install R, then admit the pilot

For ROLE=spec-manager and ROLE=foreman-bot, respectively:

```sh
# Forge — Ops/root; no token is printed or placed in argv
BOOTSTRAP=$(sudo -u fleet-bridge -- python3 "$PKG/setup-forge-box.py" bootstrap \
  --config-root /etc/fleet-manager-bridge-pilot --role "$ROLE" | python3 -c 'import json,sys; print(json.load(sys.stdin)["bootstrapFile"])')
install -o ai -g ai -m 0600 "$BOOTSTRAP" "/etc/fleet-manager-bridge-pilot/credentials/$ROLE.bootstrap"
```

```sh
# Forge — ai, successful sandbox preflight and fresh groups
python3 "$PKG/setup-forge-box.py" enroll --bin-dir "$BIN" \
  --binding "/etc/fleet-manager-bridge-pilot/$ROLE.binding.json" \
  --bootstrap "/etc/fleet-manager-bridge-pilot/credentials/$ROLE.bootstrap"
```

The bootstrap lifetime is 300 seconds. An expired unused token is replaced with bootstrap; an enrolled credential uses R renewal. Local seats use bridge.sock without SSH. Delivery does not start a model turn; INTERFACE.md describes thread/factoryMessage, queue_only and deferred admission. T installs no inbox watcher.

R's **separate** remote credential publisher uses a different root-owned Forge key and pinned known_hosts, target ai@dev-3:22, fixed codex-factory-publish-v1 forced command and encrypted stdin. It does not use the fleet-bridge transport login. Ops provides root:0600 PUBLISHER_CONFIG with kind=ssh, address, port=22, user=ai, identityFile, knownHosts and hostKeyAlias, plus its root-owned public-key file PUBLISHER_PUBLIC_KEY. Use the existing R publisher setup/inventory; do not invent a second inbound route. ai's ordinary executable login permits its forced command; service nologin/dispatcher rules do not apply to ai. No ai or service sudo grant is added.

```sh
# Forge — Ops/root; output must be fresh, runtime root is root:0700
python3 -I "$PKG/setup-forge-box.py" render-renewal \
  --config-root /etc/fleet-manager-bridge-pilot \
  --runtime-root /var/lib/fleet-manager-bridge-pilot-renew \
  --unit-prefix fleet-manager-bridge-pilot \
  --publisher-config "$PUBLISHER_CONFIG" --publisher-public-key "$PUBLISHER_PUBLIC_KEY" \
  --output /var/lib/fleet-manager-bridge-pilot-renew/staged
for FILE in /var/lib/fleet-manager-bridge-pilot-renew/staged/*; do
  printf '\nFILE %s\n' "$FILE"
  cat -- "$FILE"
done
# Forge — Ops/root, through the reviewed Ops PR
install -o root -g root -m 0600 /var/lib/fleet-manager-bridge-pilot-renew/staged/allowlist.json /etc/fleet-manager-bridge-pilot-renew.json
install -o root -g root -m 0644 /var/lib/fleet-manager-bridge-pilot-renew/staged/fleet-manager-bridge-pilot-renew.service /etc/systemd/system/
install -o root -g root -m 0644 /var/lib/fleet-manager-bridge-pilot-renew/staged/fleet-manager-bridge-pilot-renew.timer /etc/systemd/system/
systemd-analyze verify /etc/systemd/system/fleet-manager-bridge-pilot-renew.service /etc/systemd/system/fleet-manager-bridge-pilot-renew.timer
```

Dev-3 Ops installs the complete generated pilot-publisher.json at /etc/fleet-manager-bridge-pilot-publisher.json, root:root 0644, and merges the generated publisher-authorized-keys.fragment verbatim into ai's existing 0700/0600 SSH files through its Ops PR. Preserve unrelated approved operator keys. This distinct publisher key/policy is not fleet-bridge's root-owned one-line AuthorizedKeysFile.

The R root oneshot uses only the root-owned allowlist and pinned package helper/bridge. Its service/state/seat/publication policies do not reload service-owned setup at runtime. It drops to the service UID for signing and endpoint UID for atomic publication. NoNewPrivileges remains on; only CAP_SETUID/CAP_SETGID are bounded/retained for the UID drop, then child UIDs have zero capabilities. Every five minutes it renews the current host key at 12h (maximum 86400s lease) and rotates seats at 30min (3600s TTL, overlap <=60s). The pilot paths/unit prefix select /etc/fleet-manager-bridge-pilot-renew.json and only pilot writable roots.

Rotation operation IDs, private candidate journal, fsync/atomic rename, current-token sync and confirmation make crashes/lost acknowledgments resumable. Never clear pending state or hand-edit registry/credentials. Expiry, host/seat revocation and reboot fences override the timer; it never admits a missing host. A failure is nonzero and one ids-only renewal_failed line; inspect the pilot renewal unit/journal and repair the reviewed config before retrying the same operation.

```sh
# Forge — fleet-bridge; explicit pilot admission, preserving reserved IDs
python3 "$PKG/setup-forge-box.py" admit-remote --config-root /etc/fleet-manager-bridge-pilot
```

```sh
# Forge — Ops/root, after dev-3 generation and publisher are installed/verified
systemctl daemon-reload
systemctl enable --now fleet-manager-bridge-pilot-source.service
# Forge — Ops/root; ONE qualification command, timeout is a FAILURE
python3 -I "$PKG/setup-forge-box.py" verify-launcher \
  --config /etc/fleet-manager-bridge-pilot/transport/source.json
```

Expected output, exactly these four lines and exit 0:

```text
AUTHENTICATED_PROTOCOL PASS
SHELL REFUSED
PTY REFUSED
FORWARD REFUSED
```

The check authenticates the pinned generation/service/host/key/hub and current boot; an existing owner returns an authenticated busy greeting without changing its socket. It tests an actual wrong command, a forced PTY request and remote TCP forwarding. Authentication failure or timeout cannot count as shell refusal; PTY/forward refusal require OpenSSH's explicit refusal diagnostics. The check uses only Forge -> dev-3:22 and creates no model calls. Failure blocks activation; do not weaken policy.

Forge fleet-bridge now obtains pilot-seat's bootstrap using bootstrap --config-root /etc/fleet-manager-bridge-pilot --role pilot-seat. Ops transfers that one file privately, installing it ai:ai 0600 at /etc/fleet-manager-bridge-remote-pilot/credentials/pilot-seat.bootstrap on dev-3 before its 300-second expiry. Do not print or log its contents.

```sh
# dev-3 — ai; successful packaged sandbox preflight
python3 "$PKG/setup-forge-box.py" enroll --bin-dir "$BIN" \
  --binding /etc/fleet-manager-bridge-remote-pilot/pilot-seat.binding.json \
  --bootstrap /etc/fleet-manager-bridge-remote-pilot/credentials/pilot-seat.bootstrap
```

```sh
# Forge — Ops/root; after actual publisher delivery is proved
systemctl enable --now fleet-manager-bridge-pilot-renew.timer
```

The standing source has one owned SSH child, no inbox polling/model calls while idle, keepalive 15s/count 3, serialized bounded 2.5s requests and systemd restart backoff 5s. It attests each Unix caller's actual SO_PEERCRED UID. Forge rechecks host/key/lease/revocation/boot and bearer on every frame. Drops retain the original message UUID/payload/sequence and receipts for reconciliation, never repeated Forge actions. Duplicate instances fail before replacing a live socket. Restart removes only a socket matching a durable service-owned inode/device/path and dead boot/PID/start-time proof; unknown paths fail closed. The ownership record is durable before public socket publication. Reboot still requires the existing explicit service recovery; the connector/timer cannot approve it.

## 5. Drained fork cutover and updater setting

The factory compile-time updater gate stays OFF. No additional updater/provenance runtime code is included in T. At each drained cutover Ops **merges** this fragment into that CODEX_HOME/app-server-daemon/settings.json, retaining other settings:

```json
{"updater":{"autoUpdateEnabled":false}}
```

Ops stops both the inventory's stock backend and its **separate updater** before launching the fork, and removes stock after the cutover per owner a9921e09. Stopping only a TUI is insufficient. Ops resolves the actual service/process names from inventory and confirms the drained process tree; this runbook does not guess those names. Fleet services use absolute fork paths and --no-daemon. A fail-closed refusal to start/attach to stock is a separately reviewed S/1112g item, not a T guarantee.

Use a distinct CODEX_HOME with 0700 mode, authenticate it normally and copy only reviewed config/MCP/custom command inputs. Do not share/symlink stock caches, session databases or daemon state. Leave code-mode/executor overrides unset. An in-place session resume requires a drained, backed-up compatible home; no automatic live migration is provided here.

```sh
# dev seat — ai; owner's interactive-seat preference, never a preflight fallback
FORK_HOME=/home/ai/.local/share/fleet-codex/dev-seat
env -u CODEX_FACTORY_BINDING -u CODEX_FACTORY_SERVICE_UID -u CODEX_EXEC_SERVER_URL \
  CODEX_HOME="$FORK_HOME" "$PKG/bin/codex" --no-daemon -m gpt-6.1-sol \
  -c 'model_reasoning_effort="high"' --ask-for-approval never --sandbox danger-full-access
```

For a supervised backend use the absolute fork app-server with a new private Unix socket and this fork home's settings; attach explicitly to that socket. Ops verifies /proc/PID/exe for the TUI, backend and code-mode-host from outside the tree. Never infer backend provenance from the TUI binary alone. Until S, all ai endpoints can read each other's ai-owned state; SSH protects transport but does not give endpoint-only content secrecy.

## 6. Exact pilot removal after the pilot

Owner/Ops first drains all three pilot endpoint jobs and reconciles pending work. The following is **only** for these fresh throwaway roots, never a durable root. Perform through the Ops PR after the pilot, with reviewed generation.json and installed-file hashes. Keep all received packages and ordinary CODEX_HOMEs/project directories. Stop the timer before transport, then hub:

```sh
# Forge — Ops/root, after endpoint jobs are drained
systemctl disable --now fleet-manager-bridge-pilot-renew.timer
systemctl stop fleet-manager-bridge-pilot-renew.service
systemctl disable --now fleet-manager-bridge-pilot-source.service fleet-manager-bridge-pilot.service
```

Verify their control groups contain no processes and the matching dev-3 source entry/SSH session has exited. Do not kill a reused PID or delete an unproven live socket. Ops removes the pilot publisher key line by its recorded fingerprint, preserving unrelated ai keys; removes the installed root publisher policy only after its hash matches the reviewed generation; and removes pilot unit files through its configuration management. Before dropping the SSH Match, Ops makes fleet-bridge inaccessible (password still locked, no sudo, shell restored to /usr/sbin/nologin, no permitted service key lookup) and validates/reloads sshd. Keep the restricted Match/key lookup until that account policy is in force. Remove only the reviewed pilot key/wrappers/config/include, never unrelated SSH or publisher policy.

These are the exact disposable mailbox roots, with fixed names, checked by Ops/root before removal. --one-file-system prevents descending into another filesystem; symlink roots are rejected. No path is derived from untrusted setup.json during cleanup.

```sh
# Forge — Ops/root; services/jobs confirmed stopped above
for DIR in /var/lib/fleet-manager-bridge-pilot /etc/fleet-manager-bridge-pilot; do
  test ! -L "$DIR" && test -d "$DIR" || exit 1
  test "$(stat -c %u "$DIR")" = "$(id -u fleet-bridge)" || exit 1
done
rm -rf --one-file-system -- /var/lib/fleet-manager-bridge-pilot /etc/fleet-manager-bridge-pilot
# Forge — Ops/root; these two staging/runtime roots must still be root-owned
for DIR in /var/lib/fleet-manager-bridge-pilot-renew /var/lib/fleet-codex-pilot-stage; do
  test ! -L "$DIR" && test -d "$DIR" || exit 1
  test "$(stat -c %u "$DIR")" = 0 || exit 1
done
rm -rf --one-file-system -- /var/lib/fleet-manager-bridge-pilot-renew /var/lib/fleet-codex-pilot-stage
```

```sh
# dev-3 — Ops/root; matching source entry and endpoint confirmed stopped
for DIR in /var/lib/fleet-manager-bridge-remote-pilot /etc/fleet-manager-bridge-remote-pilot; do
  test ! -L "$DIR" && test -d "$DIR" || exit 1
  test "$(stat -c %u "$DIR")" = "$(id -u fleet-bridge)" || exit 1
done
rm -rf --one-file-system -- /var/lib/fleet-manager-bridge-remote-pilot /etc/fleet-manager-bridge-remote-pilot
```

Ops' file removals are limited to the reviewed pilot unit names, /etc/fleet-manager-bridge-pilot-renew.json, /etc/fleet-manager-bridge-pilot-publisher.json and the reviewed /etc/ssh/fleet-bridge-pilot-* generation files, plus its own installed include. Hash-check each against that generation before removal; a changed file requires review. This source runbook performs none of those host operations. Final S accounts, credentials, grants and state are generated fresh under S's approved final paths. **A pilot root must never become the final root.**

## 7. Offline proof and production limits

Run qualify_forge_transport.py --package dist/1112f --work-dir <fresh-NVMe-directory> --full-package-proof. It runs the complete R generated-systemd/timer/crash proof, legacy setup, seven mailbox lanes and real packaged shell/write/MCP turns, then real sshd/PAM on two private addresses at port 22 with a locked service password and no sudo. T covers all permitted/denied edges, per-frame lease/key/revocation/bearer checks, wrong pin/UID/generation, duplicate processes, both process deaths, commit/reply/caller cuts, bounded malformed replies and generated systemd restart. Fixture faults are feature-gated out of production. No owner login/external model/backend/fleet host is contacted.

One kernel/private namespaces prove the protocol and UID boundary, not production routing, two-host reboot behavior or Ubuntu's actual AppArmor policy. Ops must repeat the one-command launcher check, endpoint preflight, real tool turns and pilot conversation on the installed hosts. No automatic merge, deployment, S implementation or plan 003 work is authorized by package readiness.

## 8. Complete packaged launcher support

The helper generates the two wrappers above; this is the whole pinned package implementation they load. It is code, not a shell command for Ops to hand type:

```python
#!/usr/bin/python3 -I
"""Root-installed protocol login dispatcher and zero-argument launcher support."""
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import stat
import sys
import syslog
import uuid


def root_file(path, executable=False):
    path=Path(path)
    if not path.is_absolute() or '..' in path.parts: raise ValueError('path')
    for component in [*reversed(path.parents),path]:
        info=component.lstat()
        if stat.S_ISLNK(info.st_mode) or info.st_uid!=0 or info.st_mode&0o022: raise ValueError('owner')
    info=path.stat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink!=1: raise ValueError('type')
    if executable and stat.S_IMODE(info.st_mode)!=0o755: raise ValueError('mode')
    return path


def refuse(config, reason):
    def identity(name):
        try:return str(uuid.UUID(str(config.get(name))))
        except (ValueError,TypeError,AttributeError):return 'none'
    # Only fixed reason labels and UUIDs reach syslog. Never command/environment/data.
    syslog.openlog('fleet-bridge-entry',syslog.LOG_PID,syslog.LOG_AUTHPRIV)
    syslog.syslog(syslog.LOG_NOTICE,'entry_refused host='+identity('sourceHost')+' key='+identity('transportKey')+
                  ' generation='+identity('generation')+' code='+reason)
    raise SystemExit(1)


def configuration(path):
    c={}
    try:
        source=root_file(path)
        if source.stat().st_size>65536: raise ValueError('bound')
        c=json.loads(source.read_text())
        for name in ('generation','sourceHost','transportKey','hub'):
            if not uuid.UUID(c[name]).int:raise ValueError('id')
        if not 0< c['serviceUid'] <2**32-1 or os.geteuid()!=c['serviceUid']:refuse(c,'service_uid')
        root_file(c['launcher'],True);root_file(c['login'],True)
        for filename,digest in c['files'].items():
            with root_file(filename).open('rb') as f:
                if hashlib.file_digest(f,'sha256').hexdigest()!=digest:raise ValueError('digest')
        if Path(c['package'])!=Path(__file__).resolve().parent:raise ValueError('package')
        return c
    except SystemExit:raise
    except Exception:refuse(c,'configuration')


def dispatch(path):
    c=configuration(path)
    if sys.argv!=[c['login'],'-c',c['launcher']]:refuse(c,'login_arguments')
    os.execv(c['launcher'],[c['launcher']])


def launch(path):
    c=configuration(path)
    if sys.argv!=[c['launcher']]:refuse(c,'launcher_arguments')
    if os.environ.get('SSH_ORIGINAL_COMMAND')!='codex-factory-v1':refuse(c,'command')
    if os.environ.get('SSH_TTY'):refuse(c,'pty')
    try:
        source,source_port,target,target_port=os.environ['SSH_CONNECTION'].split()
        if ipaddress.ip_address(source)!=ipaddress.ip_address(c['forgeAddress']):raise ValueError('source')
        if not 0<int(source_port)<65536 or int(target_port)!=22:raise ValueError('port')
        ipaddress.ip_address(target)
    except Exception:refuse(c,'ssh_context')
    binary=str(Path(c['package'])/'bin/codex-factory-bridge')
    # Retain only the validated SSH context and fixed runtime environment.
    env={'PATH':'/usr/bin:/bin','LC_ALL':'C','HOME':'/','SSH_ORIGINAL_COMMAND':'codex-factory-v1',
         'SSH_CONNECTION':os.environ['SSH_CONNECTION']}
    os.execve(binary,[binary,'source-entry','--config',c['entryConfig']],env)
```
