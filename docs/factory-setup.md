# Forge bridge setup — Part R, complete package 1112e

Owner installation review only. This seat does not contact, deploy to or restart a fleet host.
Install the whole dist/1112e folder: root codex -> bin/codex launcher, bin/, codex-path/, codex-resources/voice/, metadata, checksums, setup helper and systemd/ templates. The package's MANIFEST.json records every hash, source revision and ABI requirement. Version remains 0.167.2+ignibyte.1112b; the source revision distinguishes R. Frozen 1112d remains the owner's installed version until an approved cutover.

Part R runs the Forge hub and its two local seats with unattended renewals before dev-3 is admitted. Use init --defer-remote below. Do not install the old dev-3 outbound proxy, old Forge SSH forced entry or its sudo rule: dev-3 to Forge port 22 remains closed. Part T supplies the owner-selected Forge-initiated transport only after R is installed and proven. Its seven launcher conditions are a separate gate; R implements none of that launcher. Part S and plan 003 remain held.

## 1. Ops provisions identities and directories through ucs-ops

Ask ucs-ops to provision the following on **each host**. Do not run useradd/groupadd
from this runbook. Reserve a system UID free on both hosts for fleet-bridge, with
the same numerical UID on both: the supplied routes schema has one service_uid.
The helper refuses a mismatch. The fixture's 65534/65532 numbers are not production assignments.

| Identity | Exact requirements on both hosts |
| --- | --- |
| fleet-bridge user | dedicated system user; primary group fleet-bridge; home /var/lib/fleet-manager-bridge; shell /usr/sbin/nologin; locked password; no sudo privileges |
| fleet-bridge group | dedicated primary group for that service user |
| fleet-bridge-clients group | only ai and fleet-bridge are members; no write access to service/config roots |
| ai | existing user and primary group retained; add supplementary fleet-bridge-clients membership |

Use a fresh ai login when starting endpoint processes after group provisioning.
An existing tmux server retains its old supplementary groups. Start the fork jobs
from a fresh login or a new tmux server (`tmux -L fleet-fork`), after the owner's drain.

Use these paths on both hosts. /home/ai roots from the earlier brief are replaced:
the existing proxy rejects an ai-owned ancestor of its protected config/key files.

| Path | Owner:group | Mode | Host |
| --- | --- | --- | --- |
| /opt/fleet-codex and package 453f5ee1190ded9792155d142ef751dcc3e13963-1112e, bin/, codex-path/, codex-resources/ | root:root | 0755 directories/executables; 0644 docs/metadata | both |
| /var/lib/fleet-manager-bridge | fleet-bridge:fleet-bridge-clients | 2710 | both |
| /var/lib/fleet-manager-bridge/run | fleet-bridge:fleet-bridge-clients | 2710 | both |
| run/bridge.sock or run/remote.sock | fleet-bridge:fleet-bridge-clients | 0660, created by service | Forge / dev-3 |
| state root/service and pending | fleet-bridge:fleet-bridge-clients | 0700 directories; 0600 contents | Forge |
| state root/proxy | fleet-bridge:fleet-bridge-clients | 0700 directories; 0600 contents | dev-3 |
| state root/native | fleet-bridge:fleet-bridge-clients | 0710 | both |
| native/spec-manager and native/foreman-bot | ai:ai | 0700 | Forge |
| native/pilot-seat | ai:ai | 0700 | dev-3 |
| /etc/fleet-manager-bridge | fleet-bridge:fleet-bridge-clients | 2750 | both |
| config root/grants, operator, transport | fleet-bridge:fleet-bridge-clients | 0700; 0600 contents | as used below |
| config root/*.binding.json, setup.json, routes*.json | fleet-bridge:fleet-bridge-clients | 0640 | bindings on their recipient host; metadata/routes on Forge |
| config root/credentials | ai:ai | 0700 | both |
| credentials/*.bootstrap and *.json | ai:ai | 0600 | only that host's endpoint credentials |
| /home/ai/.local/state/fleet-bridge-transfer | ai:ai | 0700; transferred files 0600 | both |
| /home/ai/.local/share/fleet-codex/homes/ROLE | ai:ai | 0700; auth/config files 0600 | local endpoint roles only |

SGID roots preserve the client group on sockets/bindings. Private child directories
are explicitly 0700. The service MAC key and registry stay inside Forge's service/;
never copy those to dev-3 or into the package.

Every code block below names its executor. `$PKG` is the absolute package root,
`$BIN` its absolute bin directory, `$STATE` the state root and `$CFG` the config root.
Set these in each operator shell; they contain no secrets:

```sh
# Both hosts — operator shell
PKG=/opt/fleet-codex/453f5ee1190ded9792155d142ef751dcc3e13963-1112e
BIN=$PKG/bin
STATE=/var/lib/fleet-manager-bridge
CFG=/etc/fleet-manager-bridge
```

After account creation, **Ops/root on each host** installs the whole verified
folder from its received staging copy. `SOURCE` is that absolute copy, not bin/:

```sh
# Both hosts — Ops/root; set SOURCE to the received whole dist/453f5ee1190ded9792155d142ef751dcc3e13963-1112e folder
(cd "$SOURCE" && sha256sum -c SHA256SUMS)
install -d -o root -g root -m 0755 /opt/fleet-codex
test ! -e "$PKG"
cp -a -- "$SOURCE" "$PKG"
chown -R root:root "$PKG"
chmod 0755 "$PKG" "$PKG/bin" "$PKG/codex-path" "$PKG/codex-resources"
install -d -o fleet-bridge -g fleet-bridge-clients -m 2710 "$STATE" "$STATE/run"
install -d -o fleet-bridge -g fleet-bridge-clients -m 2750 "$CFG"
install -d -o fleet-bridge -g fleet-bridge-clients -m 0710 "$STATE/native"
install -d -o ai -g ai -m 0700 "$CFG/credentials" /home/ai/.local/state/fleet-bridge-transfer
```

**Ops/root on Forge** creates native/spec-manager and native/foreman-bot with
`install -d -o ai -g ai -m 0700 "$STATE/native/spec-manager" "$STATE/native/foreman-bot"`.
**Ops/root on dev-3** creates native/pilot-seat with
`install -d -o ai -g ai -m 0700 "$STATE/native/pilot-seat"`.
No user or service is created by setup-forge-box.py.

### Sandbox precondition — every Ubuntu endpoint host

Before staging/enrolling endpoint jobs, **ai on each host**, in the same execution
context used for its Codex jobs, must run:

```sh
# Forge and dev-3 — ai, after Ops installs the whole package
python3 "$PKG/setup-forge-box.py" preflight --bin-dir "$BIN"
```

The script reads `bwrapEmbeddedSha256` from `$PKG/MANIFEST.json`, verifies the
actual file, and prints its installed path and full digest. For 453f5ee1190ded9792155d142ef751dcc3e13963-1112e the file is
`$PKG/codex-resources/bwrap`, SHA-256
`bd2ed101e19111f5c06ea4bb16ace19ca10693a56b343265b156937d4434f023`.
It runs `/usr/bin/true` through that exact bwrap with a read-only root and new
user, PID and network namespaces. This is a no-model test; the network namespace
also exercises the loopback initialization that failed on Forge. Enrollment and
the endpoint proof commands repeat the check as their endpoint UID and refuse
before credential/network actions when it fails. Service-only staging runs as
fleet-bridge and does not stand in for ai's sandbox permission.

On Ubuntu with `kernel.apparmor_restrict_unprivileged_userns=1`, **Ops must first
provide a root-owned bwrap, its required root-only xattr and an AppArmor profile
for this fork's exact installed path/file**, through ucs-ops (the codex-cli role).
Stock's permission does not cover a separate fork file. The owner measured
`bwrap: loopback: Failed RTM_NEWADDR: Operation not permitted` on Forge before
that Ops change; stock and danger-full-access success did not qualify the fork's
read-only/workspace-write sandbox there. z690 does not reproduce that host policy.

If the probe fails, stop and send Ops the script's path/digest/error. The helper
never edits AppArmor, sysctl, xattrs or ownership and never falls back to
danger-full-access. Do not globally disable the restriction as a setup step.
After Ops fixes it, ai reruns this probe and the owner reruns the packaged
read-only/workspace-write turns on the actual hosts. No binary rebuild is needed.

### Relocation and digest pins

`/opt/fleet-codex/453f5ee1190ded9792155d142ef751dcc3e13963-1112e` is this runbook's example input, not a compiled package
location. The package uses relative bin/resource/path metadata; the bridge takes
explicit state/socket/config paths; preflight derives bwrap and MANIFEST.json
from the supplied absolute bin directory. Nothing in the runtime/setup requires
`/home/ai/.local/share/fleet-codex`. The ai CODEX_HOME examples below are deliberate
operator choices. The generated application-journal defaults under
`/home/ai/fleet-spec-project` are client configuration and must match the actual
driver journals. Generated bindings, service units, proxy config and forced
SSH/sudoers commands contain the chosen absolute paths: regenerate/update those
operator files, and Ops' path/xattr/profile permission, if the installation moves.

The packager keeps revision stamps out of the bwrap component build, reuses Cargo
artifacts where possible, strips a staging copy before hashing, and never strips
the packaged copy afterward. Same-source cached builds can retain the digest;
clean builds with different source/toolchains/build paths are not claimed to be
bit reproducible. Every later package READY line prints the full manifest bwrap
digest. Any new bwrap path or digest needs an Ops policy update before use.

## 2. Initialize deferred admission, then start the local hub

Ops records the actual ai UIDs and the common fleet-bridge UID. Supply AI_UID, DEV3_AI_UID and FLEET_UID as identifiers; no remote login is needed. These three values reserve the future grants while leaving that host absent from the registry.

```sh
# Forge — fleet-bridge, after Ops provisions the section 1 directories
python3 "$PKG/setup-forge-box.py" init --defer-remote --bin-dir "$BIN" \
  --state-root "$STATE" --config-root "$CFG" --run-root "$STATE/run" \
  --remote-state-root /var/lib/fleet-manager-bridge \
  --remote-config-root /etc/fleet-manager-bridge \
  --remote-run-root /var/lib/fleet-manager-bridge/run \
  --agent-uid "$AI_UID" --remote-agent-uid "$DEV3_AI_UID" \
  --remote-service-uid "$FLEET_UID"
```

Init reserves hub/remote host/key IDs and all three seat/incarnation grants, stages bindings and disabled routes, and mints only spec-manager's local bootstrap. It starts no listener, creates no pilot credential and does not call admit-host. The later local foreman bootstrap is explicit. A repeated or partial init refuses reuse; preserve the registry and ask the owner. Never delete or re-initialize it to recover a token.

Ops installs the following hub unit, root:root 0644. Substitute the verified PKG path when generating the Ops PR; the service user cannot modify it.

```ini
[Unit]
Description=Fleet factory bridge hub
After=network.target
[Service]
Type=simple
User=fleet-bridge
Group=fleet-bridge
SupplementaryGroups=fleet-bridge-clients
UMask=0077
ExecStart=/opt/fleet-codex/453f5ee1190ded9792155d142ef751dcc3e13963-1112e/bin/codex-factory-bridge serve --state /var/lib/fleet-manager-bridge/service --socket /var/lib/fleet-manager-bridge/run/bridge.sock
Restart=no
KillMode=control-group
TimeoutStopSec=10
NoNewPrivileges=yes
ProtectHome=yes
ProtectSystem=strict
ReadWritePaths=/var/lib/fleet-manager-bridge
[Install]
WantedBy=multi-user.target
```

```sh
# Forge — Ops/root, after installing the reviewed hub unit
systemctl daemon-reload
systemctl enable --now fleet-manager-bridge.service
```

For each local ROLE (spec-manager, then foreman-bot), obtain its bootstrap path as fleet-bridge. Its stdout contains the path and expiry, never the token. Ops transfers that file locally to the endpoint; do not cat it or put its bytes in an argument or log.

```sh
# Forge — Ops/root; ROLE is spec-manager or foreman-bot
BOOTSTRAP=$(sudo -u fleet-bridge -- python3 "$PKG/setup-forge-box.py" bootstrap \
  --config-root "$CFG" --role "$ROLE" | python3 -c 'import json,sys; print(json.load(sys.stdin)["bootstrapFile"])')
install -o ai -g ai -m 0600 "$BOOTSTRAP" "$CFG/credentials/$ROLE.bootstrap"
# Forge — ai, fresh group membership and successful packaged-bwrap preflight
python3 "$PKG/setup-forge-box.py" enroll --bin-dir "$BIN" \
  --binding "$CFG/$ROLE.binding.json" --bootstrap "$CFG/credentials/$ROLE.bootstrap"
```

Enroll within the 300-second bootstrap lifetime. If an unused bootstrap expires, request another through bootstrap; enrolled seats instead use renewal. The local spec-manager/foreman bindings already authorize their directed conversation. Bridge delivery remains separate from native model-turn admission; use the API described in INTERFACE.md. No polling/wake controller is installed by R.

## 3. Install unattended renewals from the root-owned package

Only Ops/root renders this staging folder. Rendering reads the service-owned setup/bindings once and freezes their public IDs, grants, service/endpoint UIDs/groups, socket/destination paths and code digests into root:0600 allowlist.json. Review that complete JSON in the Ops PR. The renewal unit never reloads setup.json, bindings or Python modules from a service/ai-owned directory. Its only configuration is the root-owned allowlist; it runs the root-owned packaged helper with Python -I and the pinned packaged bridge with a scrubbed environment, dropping to fleet-bridge for authority and to the configured endpoint UID for local publication. Python and SSH themselves are trusted system runtimes. The unit bounds its capabilities to SETUID/SETGID and explicitly retains those two for the required UID drop under NoNewPrivileges; children lose them when changing to the nonroot actor UID.

```sh
# Forge — Ops/root, after the whole verified package is root-owned
python3 -I "$PKG/setup-forge-box.py" render-renewal \
  --config-root "$CFG" --output /var/lib/fleet-manager-bridge-renew/staged
# Ops PR installs the generated files verbatim, with these owners and modes:
install -o root -g root -m 0600 /var/lib/fleet-manager-bridge-renew/staged/allowlist.json /etc/fleet-manager-bridge-renew.json
install -o root -g root -m 0644 /var/lib/fleet-manager-bridge-renew/staged/fleet-manager-bridge-renew.service /etc/systemd/system/
install -o root -g root -m 0644 /var/lib/fleet-manager-bridge-renew/staged/fleet-manager-bridge-renew.timer /etc/systemd/system/
systemd-analyze verify /etc/systemd/system/fleet-manager-bridge-renew.service /etc/systemd/system/fleet-manager-bridge-renew.timer
systemctl daemon-reload
systemctl enable --now fleet-manager-bridge-renew.timer
```

The runtime directory /var/lib/fleet-manager-bridge-renew is root:root 0700. The generated service is exactly:

```ini
[Unit]
Description=Renew explicitly allowlisted fleet bridge authority
Requires=fleet-manager-bridge.service
After=fleet-manager-bridge.service network-online.target

[Service]
Type=oneshot
User=root
Group=root
WorkingDirectory=/
UMask=0077
ExecStart=/usr/bin/python3 -I /opt/fleet-codex/453f5ee1190ded9792155d142ef751dcc3e13963-1112e/setup-forge-box.py renew --config /etc/fleet-manager-bridge-renew.json
TimeoutStartSec=120
KillMode=control-group
NoNewPrivileges=yes
CapabilityBoundingSet=CAP_SETUID CAP_SETGID
AmbientCapabilities=CAP_SETUID CAP_SETGID
PrivateTmp=yes
ProtectHome=yes
ProtectSystem=strict
ProtectKernelTunables=yes
ProtectKernelModules=yes
ReadWritePaths=/var/lib/fleet-manager-bridge /var/lib/fleet-manager-bridge-renew /etc/fleet-manager-bridge/credentials
```

The generated timer is exactly:

```ini
[Unit]
Description=Renew allowlisted fleet bridge credentials before expiry

[Timer]
OnBootSec=30s
OnUnitActiveSec=5min
AccuracySec=5s
Unit=fleet-manager-bridge-renew.service

[Install]
WantedBy=timers.target
```

Every five minutes the singleton broker checks the explicitly allowlisted, already admitted host lease and enrolled seats. A deferred host/unenrolled pilot is skipped. It renews the current host key at twelve hours without consuming key-rotation slots, with a maximum 86400-second lease. It rotates seat credentials when thirty minutes remain, with TTL 3600 and overlap 60 seconds. It neither admits a missing host nor clears revocation/reboot fencing. A lapse can be restored only for the already admitted/current-key host in the approved allowlist.

Each seat has one private durable rotation record, an operation UUID and at most one pending candidate. Prepare, registry commit, candidate reconstruction, atomic publication and confirmation resume the same operation after a crash or lost acknowledgment. A current-token sync happens inside the endpoint's publication lock, preventing a delayed older publisher from overwriting a newer credential. Publication uses ai:0600 files, fsync, atomic rename and parent fsync. The broker retains pending state on failure; expired pending operations are closed before creating a fresh candidate. Tokens/nonces never enter argv, stdout or the journal. Existing endpoint calls reload the credential on every frame; no endpoint restart is required.

An operator sees a failed unit with Result=exit-code and ExecMainStatus=1. Its invocation emits one bounded line, for example:

```text
renewal_failed host=none seat=00000000-0000-4000-8000-000000000001 rotation=00000000-0000-4000-8000-000000000002 code=publication_candidate_not_current
```

Unassociated IDs are printed as none. No bearer credential, nonce, command body or raw subprocess error is included. Inspect with systemctl status fleet-manager-bridge-renew.service and journalctl -u fleet-manager-bridge-renew.service. Correct the reviewed path/key/UID/config problem and start that same unit again. Do not hand-edit rotation/registry/credential files or clear pending state. Concurrent brokers refuse with renewal_already_running. The timer cannot undo a revoke or approve recovery.

## 4. Later admission and the separate remote publisher

Only after the T transport/install review permits dev-3 admission, fleet-bridge on Forge runs:

```sh
# Forge — fleet-bridge, later explicit owner-approved admission
python3 "$PKG/setup-forge-box.py" admit-remote --config-root "$CFG"
```

It uses the saved host/key/seat/incarnation IDs, verifies an existing admission instead of replacing it, and preserves grants, messages and sequences. Default init without --defer-remote retains compatibility for offline fixtures; it is not the production R procedure. There is no need for dev-3 to Forge SSH.

R's remote credential publisher is separate from mailbox transport. Ops may later supply render-renewal --publisher-config with a root:0600 JSON containing kind=ssh, a verified private literal address, port=22, endpoint login user, root:0600 identityFile and knownHosts, and hostKeyAlias. The allowlist pins these values. The key uses only the existing Forge to dev-3 route, a fixed codex-factory-publish-v1 forced command and encrypted stdin. It permits no forwarding, interactive command, SSH agent, proxy or user SSH config. Ops pins the target's public host key from trusted inventory. Supply --publisher-public-key with its root-owned verified ed25519 public file when rendering this configuration. The helper stages pilot-publisher.json and the complete publisher-authorized-keys.fragment with that actual key; it generates no placeholder key line when the pilot is deferred. Ops installs these reviewed files verbatim. The receiver executes the pinned root-owned packaged helper as the endpoint UID with the root-owned public policy file. No sudo is granted to ai. Lost acknowledgments retry the same current candidate. Leave the pilot deferred until its publisher and T are approved and installed.

## 5. Start an interactive fork seat without a mailbox binding

Drain the stock TUI first. As ai, create FORK_HOME=/home/ai/.local/share/fleet-codex/dev-seat mode 0700, use a separate login, and use this self-contained command:

```sh
# Dev seat — ai; PKG is the verified root-owned fork package
FORK_HOME=/home/ai/.local/share/fleet-codex/dev-seat
env -u CODEX_FACTORY_BINDING -u CODEX_FACTORY_SERVICE_UID -u CODEX_EXEC_SERVER_URL \
  CODEX_HOME="$FORK_HOME" "$PKG/bin/codex" --no-daemon -m gpt-6.1-sol \
  -c 'model_reasoning_effort="high"' --ask-for-approval never --sandbox danger-full-access
```

The sandbox value above is the owner's interactive-seat preference, not a sandbox-preflight fallback. For days-long sessions, supervise the fork's own backend as ai:

```sh
# Dev seat — ai, supervised backend with a private socket
env -u CODEX_FACTORY_BINDING -u CODEX_FACTORY_SERVICE_UID -u CODEX_EXEC_SERVER_URL \
  CODEX_HOME="$FORK_HOME" "$PKG/bin/codex" app-server --listen "unix://$FORK_HOME/app-server.sock"
# Dev seat — ai, TUI in the tmux pane; same home's reviewed model/policy config
env -u CODEX_FACTORY_BINDING -u CODEX_FACTORY_SERVICE_UID -u CODEX_EXEC_SERVER_URL \
  CODEX_HOME="$FORK_HOME" "$PKG/bin/codex" --remote "unix://$FORK_HOME/app-server.sock"
```

Do not share/symlink stock's CODEX_HOME, model cache, session databases or daemon state. Copy reviewed config, MCP settings and custom command files; authenticate this home separately. Leave code-mode host/executor overrides unset so the package resolves its own helpers. From outside inspect pstree -ap "$(tmux display-message -p -t agent3 '#{pane_pid}')", then readlink /proc/PID/exe for every TUI/child process and the separately supervised backend tree. Confirm both codex and code-mode-host come from the fork package. /goal needs the goals feature enabled; standard slash/MCP behavior follows the pinned upstream version, with copied custom files/config. Daemon-specific commands can differ on --remote.

## 6. Offline qualification and limits

The plan records the exact branch/source and results. The proof runs the generated renewal service under isolated systemd with an accelerated timer, two real local endpoint UIDs, no admitted remote host, real loopback pinned SSH publication, process-death recovery, stale publication/outage/revocation tests, legacy setup compatibility, all seven mailbox qualifiers and actual packaged shell/write/MCP through a local Responses fixture. No owner login, external model request, live fleet-host contact, live installation or Part T transport is claimed. Repeat the endpoint packaged-bwrap preflight and host-specific tool proof after Ops installs the whole package. Keep the bwrap manifest digest pinned; changed digest means STOP.
