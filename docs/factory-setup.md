# Forge / dev-3 bridge setup — complete package 1112c

Owner installation only. This runbook does not deploy or start anything from z690.
Install **all of dist/1112c**, including its root metadata and sibling directories.
The runtime source is 6cb7fd718329da87bfd1dcf4c10536cf82b1c367; CLI SHA-256 starts
87b000eb4f2b and code-mode-host starts 1b3567c03089. The version remains
0.167.2+ignibyte.1112b. Check the supplied manifest's ABI requirements on both hosts.

Forge is ai-agent-0-flex, 10.200.68.55. Its bridge serves spec-manager and
foreman-bot locally. Dev-3 is ai-agent-3-flex; its pilot-seat uses a local
connect-host proxy, which makes pinned outbound SSH requests to Forge.
The proxy **also requires a different UID from ai**. Both services run as
fleet-bridge. No Rust or protocol change is needed.

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
| /opt/fleet-codex and package 1112c, bin/, codex-path/, codex-resources/ | root:root | 0755 directories/executables; 0644 docs/metadata | both |
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
PKG=/opt/fleet-codex/1112c
BIN=$PKG/bin
STATE=/var/lib/fleet-manager-bridge
CFG=/etc/fleet-manager-bridge
```

After account creation, **Ops/root on each host** installs the whole verified
folder from its received staging copy. `SOURCE` is that absolute copy, not bin/:

```sh
# Both hosts — Ops/root; set SOURCE to the received whole dist/1112c folder
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
actual file, and prints its installed path and full digest. For 1112c the file is
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

`/opt/fleet-codex/1112c` is this runbook's example input, not a compiled package
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

## 2. Stage the hub and the remote transport

First record `id -u ai` on both hosts and `id -u fleet-bridge` on both hosts
(Ops/root; identifiers only). Supply the actual numbers as AI_UID, DEV3_AI_UID and
FLEET_UID below. Do not assume ai has the same UID on both hosts.

```sh
# Forge — fleet-bridge (Ops may use sudo -u fleet-bridge -- python3 ...)
python3 "$PKG/setup-forge-box.py" init --bin-dir "$BIN" \
  --state-root "$STATE" --config-root "$CFG" --run-root "$STATE/run" \
  --remote-state-root /var/lib/fleet-manager-bridge \
  --remote-config-root /etc/fleet-manager-bridge \
  --remote-run-root /var/lib/fleet-manager-bridge/run \
  --agent-uid "$AI_UID" --remote-agent-uid "$DEV3_AI_UID" \
  --remote-service-uid "$FLEET_UID" --ssh-address 10.200.68.55 \
  --ssh-port 22 --ssh-user ai --service-user fleet-bridge
```

This initializes the existing registry/service key through a local bootstrap,
generates hub/remote host IDs, three seat/incarnation IDs and the epoch, writes
three service-owned binding files, and calls admit-host for dev-3 (24-hour lease).
Only spec-manager↔foreman-bot and foreman-bot↔pilot-seat cross-seat grants exist,
with observe/queue/acknowledge. Each grant also contains itself because existing
sync/inbox calls require it; that scope also permits self-addressed messages.
Neither spec-manager→pilot-seat nor its reverse is granted. No listener is started.
Repeated init returns setup_already_initialized without mutation. A partial failed
init also refuses reuse; stop and inspect it rather than deleting registry state.

```sh
# Dev-3 — fleet-bridge
python3 "$PKG/setup-forge-box.py" keygen --config-root "$CFG"
```

The private ed25519 key stays at dev-3's `$CFG/transport/dev3-ed25519`, mode 0600.
**Ops/root on dev-3** stages only its `.pub` file for ai:
`install -o ai -g ai -m 0600 "$CFG/transport/dev3-ed25519.pub" /home/ai/.local/state/fleet-bridge-transfer/dev3.pub`.
**Dev-3 ai** copies that file to Forge ai's private transfer directory using the
existing operator SSH route. **Ops/root on Forge** installs it as
`$CFG/operator/dev3.pub`, owner fleet-bridge, group fleet-bridge-clients, mode 0600.

```sh
# Dev-3 — ai
scp -p /home/ai/.local/state/fleet-bridge-transfer/dev3.pub ai@10.200.68.55:/home/ai/.local/state/fleet-bridge-transfer/dev3.pub
# Forge — Ops/root
install -o fleet-bridge -g fleet-bridge-clients -m 0600 /home/ai/.local/state/fleet-bridge-transfer/dev3.pub "$CFG/operator/dev3.pub"
```

```sh
# Forge — fleet-bridge
python3 "$PKG/setup-forge-box.py" ssh-config --config-root "$CFG" \
  --client-public-key "$CFG/operator/dev3.pub"
```

The generated files contain all real UUIDs. **Ops/root on Forge** validates and
installs the exact sudoers fragment:

```sh
# Forge — Ops/root
visudo -c -f "$CFG/operator/fleet-bridge-remote.sudoers"
install -o root -g root -m 0440 "$CFG/operator/fleet-bridge-remote.sudoers" /etc/sudoers.d/fleet-bridge-remote
visudo -c
```

It grants **ai**, not fleet-bridge, permission to run exactly this pinned command
as fleet-bridge, with these two SSH variables preserved only for that command:

```text
Cmnd_Alias FLEET_BRIDGE_DEV3 = /opt/fleet-codex/1112c/bin/codex-factory-bridge remote-entry --state /var/lib/fleet-manager-bridge/service --host REAL_DEV3_HOST_UUID --key-id REAL_TRANSPORT_KEY_UUID
Defaults!FLEET_BRIDGE_DEV3 env_keep += "SSH_ORIGINAL_COMMAND SSH_CONNECTION"
ai ALL=(fleet-bridge) NOPASSWD: FLEET_BRIDGE_DEV3
```

Use the generated file; the explanatory UUID names above are not installation
values. The service user has no sudo grant and needs no login shell. Ops/root
installs the generated authorized_keys.fragment as **one additional line** in ai's
existing `/home/ai/.ssh/authorized_keys` (directory ai:ai 0700, file ai:ai 0600),
preserving existing operator keys. Its exact shape is:

```text
restrict,command="/usr/bin/sudo -n -u fleet-bridge -- /opt/fleet-codex/1112c/bin/codex-factory-bridge remote-entry --state /var/lib/fleet-manager-bridge/service --host REAL_DEV3_HOST_UUID --key-id REAL_TRANSPORT_KEY_UUID" ssh-ed25519 PUBLIC_KEY fleet-dev3-bridge
```

No PTY/forwarding/agent forwarding is permitted for that key. remote-entry itself
requires SSH_ORIGINAL_COMMAND=codex-factory-v1 and SSH_CONNECTION; it checks the
source-host/key UUIDs and admitted host lease. Keep all binary ancestors root-owned
and non-writable by ai; the narrow sudoers grant relies on that immutability.

**Ops/root on Forge** stages these nonsecret files into ai's private transfer dir:
`operator/proxy.json` and `pilot-seat.binding.json` from `$CFG` (0600 copies).
**Forge ai** transfers them to dev-3 using `scp ... dev-3:/home/ai/.local/state/fleet-bridge-transfer/`.
**Ops/root on dev-3** installs proxy.json to `$CFG/transport/proxy.json` as
fleet-bridge:fleet-bridge-clients 0600, and pilot-seat.binding.json to
`$CFG/pilot-seat.binding.json` as fleet-bridge:fleet-bridge-clients 0640.

```sh
# Forge — Ops/root
install -o ai -g ai -m 0600 "$CFG/operator/proxy.json" /home/ai/.local/state/fleet-bridge-transfer/proxy.json
install -o ai -g ai -m 0600 "$CFG/pilot-seat.binding.json" /home/ai/.local/state/fleet-bridge-transfer/pilot-seat.binding.json
# Forge — ai
scp -p /home/ai/.local/state/fleet-bridge-transfer/proxy.json /home/ai/.local/state/fleet-bridge-transfer/pilot-seat.binding.json dev-3:/home/ai/.local/state/fleet-bridge-transfer/
# Dev-3 — Ops/root
install -o fleet-bridge -g fleet-bridge-clients -m 0600 /home/ai/.local/state/fleet-bridge-transfer/proxy.json "$CFG/transport/proxy.json"
install -o fleet-bridge -g fleet-bridge-clients -m 0640 /home/ai/.local/state/fleet-bridge-transfer/pilot-seat.binding.json "$CFG/pilot-seat.binding.json"
```

Ops obtains Forge's ed25519 **host public key from its trusted host inventory or
the Forge console**, verifies it, and transfers that public file to dev-3; do not
accept an unauthenticated keyscan as the trust decision. Dev-3 fleet-bridge writes
`$CFG/transport/known_hosts` (0600) containing exactly one line:

```text
factory-hub-REAL_HUB_UUID ssh-ed25519 VERIFIED_FORGE_HOST_PUBLIC_KEY
```

The hub UUID is in Forge `$CFG/setup.json` (`hub`); proxy.json also contains it.
The address is the private literal 10.200.68.55, not the operator's SSH alias:
connect-host deliberately ignores user SSH config and pins HostKeyAlias above.
The generated config's allowedAgentUids contains only dev-3 ai's UID.
After Ops installs the verified public file as transport/forge-host.pub,
fleet-bridge on dev-3 writes the actual UUID/key line without terminal output:

```sh
# Dev-3 — fleet-bridge
python3 - "$CFG/transport/forge-host.pub" "$CFG/transport/proxy.json" "$CFG/transport/known_hosts" <<'PY'
import json, os, pathlib, sys, uuid
public = pathlib.Path(sys.argv[1]).read_text().split()
assert len(public) >= 2 and public[0] == 'ssh-ed25519'
hub = str(uuid.UUID(json.loads(pathlib.Path(sys.argv[2]).read_text())['hub']))
fd = os.open(sys.argv[3], os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
with os.fdopen(fd, 'w') as f:
    f.write(f'factory-hub-{hub} {public[0]} {public[1]}\n')
PY
```

## 3. Ops starts system services, then endpoints enroll

Install these **system** units as root:root 0644 under /etc/systemd/system.
The earlier systemd --user suggestion is superseded by the separate-UID ruling.

Forge `/etc/systemd/system/fleet-manager-bridge.service`:

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
ExecStart=/opt/fleet-codex/1112c/bin/codex-factory-bridge serve --state /var/lib/fleet-manager-bridge/service --socket /var/lib/fleet-manager-bridge/run/bridge.sock
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

Dev-3 `/etc/systemd/system/fleet-manager-bridge-proxy.service`:

```ini
[Unit]
Description=Fleet factory bridge SSH proxy
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=fleet-bridge
Group=fleet-bridge
SupplementaryGroups=fleet-bridge-clients
UMask=0077
ExecStart=/opt/fleet-codex/1112c/bin/codex-factory-bridge connect-host --state /var/lib/fleet-manager-bridge/proxy --socket /var/lib/fleet-manager-bridge/run/remote.sock --config /etc/fleet-manager-bridge/transport/proxy.json
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

**Ops/root on the respective host** runs `systemctl daemon-reload` and then
`systemctl enable --now fleet-manager-bridge.service` (Forge) or
`systemctl enable --now fleet-manager-bridge-proxy.service` (dev-3).
The equivalent foreground commands are the exact ExecStart lines above, executed
as fleet-bridge. The Python helper never starts either service. Logs are
`journalctl -u fleet-manager-bridge.service` / `journalctl -u fleet-manager-bridge-proxy.service`.

For each role, just before enrollment:

```sh
# Forge — fleet-bridge; ROLE is spec-manager, then foreman-bot, then pilot-seat
python3 "$PKG/setup-forge-box.py" bootstrap --config-root "$CFG" --role "$ROLE"
```

The JSON contains a bootstrapFile **path**, not its contents; it may reuse a still
valid pending token or mint a new one after expiry. Bootstraps last 300 seconds.
**Ops/root on Forge** copies that file using `install -o ai -g ai -m 0600` to
`$CFG/credentials/$ROLE.bootstrap`. For pilot-seat, Forge ai transfers its file
over the existing operator SSH route to dev-3 ai's private transfer directory;
**Ops/root on dev-3** installs it as `$CFG/credentials/pilot-seat.bootstrap`, ai:ai 0600.
No credential or bootstrap value is put in a command argument or printed.

```sh
# Forge ai for spec-manager/foreman-bot; dev-3 ai for pilot-seat
python3 "$PKG/setup-forge-box.py" enroll --binding "$CFG/$ROLE.binding.json" \
  --bootstrap "$CFG/credentials/$ROLE.bootstrap"
```

The endpoint validates the socket's kernel service UID before sending the token,
then writes its credential at the protected path in the binding. Enrollment is
one-time; a second attempt refuses rather than replacing a credential. Exactly
three seat UUIDs are enrolled. There is no manager/Forge execution grant or live
dispatch enabled by these mailbox grants alone.

## 4. No-model message and restart proof on the real hosts

Run this in the quiet startup window before native job controllers begin using
the enrolled credentials. The helper is an explicit setup proof client.
Use only inert queue_only notices. For each row, execute `send` as the sending
host's **ai**, retain the returned messageId, then `receive` as the receiving ai,
and `status` as the sender. Seat UUIDs come from the generated routes/setup data.

| Sender | Receiver | Host path |
| --- | --- | --- |
| spec-manager | foreman-bot | Forge local |
| foreman-bot | spec-manager | Forge local |
| foreman-bot | pilot-seat | Forge → dev-3 |
| pilot-seat | foreman-bot | dev-3 → Forge |

```sh
# Sending host — ai; ROLE and RECIPIENT are the row's sender role and receiver seat UUID
python3 "$PKG/setup-forge-box.py" send --bin-dir "$BIN" --binding "$CFG/$ROLE.binding.json" --recipient "$RECIPIENT"
# Receiving host — ai; ROLE is receiver role, MESSAGE is the returned messageId
python3 "$PKG/setup-forge-box.py" receive --binding "$CFG/$ROLE.binding.json" --message "$MESSAGE"
# Sending host — ai
python3 "$PKG/setup-forge-box.py" status --binding "$CFG/$ROLE.binding.json" --message "$MESSAGE" --recipient "$RECIPIENT"
# Forge — ai; RECIPIENT is pilot-seat UUID: must return forbiddenRefused=true
python3 "$PKG/setup-forge-box.py" forbidden --binding "$CFG/spec-manager.binding.json" --recipient "$RECIPIENT"
```

Receipts must be service_accepted/service then delivered/inbox, one of each. This
test does **not** claim consumed/completed or run a model. Keep all four original
message IDs and the senders' setup-proof-outbox state.

**Forge Ops/root** runs `systemctl stop fleet-manager-bridge.service`, verifies
the unit is inactive with MainPID=0 and its control group empty, then, as
fleet-bridge, removes only `$STATE/run/bridge.sock` if it is the owned stale Unix
socket. Start the same unit on the same state. Do not reinitialize, restore, mint
new message IDs or delete ledgers. Repeat for the proxy's own remote.sock only
when restarting that proxy. Restart=no deliberately leaves stale-socket
reconciliation to Ops instead of guessing at an unknown process incarnation.

```sh
# Each original sending host — ai, after the same-state hub restart
python3 "$PKG/setup-forge-box.py" resend --bin-dir "$BIN" --binding "$CFG/$ROLE.binding.json" --message "$MESSAGE"
# Each original receiver — ai
python3 "$PKG/setup-forge-box.py" receive --binding "$CFG/$ROLE.binding.json" --message "$MESSAGE"
```

All four retain delivered state and two receipts, without a second delivery
receipt. **Forge fleet-bridge** independently runs
`"$BIN/codex-factory-bridge" inspect --state "$STATE/service"` and checks exactly
three seats and four mail records for this initially empty proof registry.
Inspect does not output credentials. This is the required real-host smoke after
installation; the z690 result below does not replace it.

## 5. Bind actual native threads and receive in a running fork session

For each role, **ai on that role's host** starts a fresh **fork** app-server with
a dedicated CODEX_HOME, normal login in that home, fresh group membership, and:

```sh
# Role's host — ai; ROLE is one of that host's enrolled roles
export CODEX_HOME=/home/ai/.local/share/fleet-codex/homes/$ROLE
export CODEX_FACTORY_BINDING=/etc/fleet-manager-bridge/$ROLE.binding.json
export CODEX_FACTORY_SERVICE_UID="$FLEET_UID"
/opt/fleet-codex/1112c/bin/codex app-server --listen stdio://
```

The existing job controller must own that app-server transport; do not start a
second controller over the same native ledger. Its JSON-RPC initialize request
must opt into `capabilities.experimentalApi=true`, followed by initialized.
Use thread/start (no turn/model call required) or an authorized compatible
thread/resume and retain the **actual** returned thread IDs. An already running
bound fork app-server can receive through its existing controller. A running
stock session in dev-3 tmux agent3 lacks these fork RPCs; exporting variables in a
shell cannot retrofit them. The owner drains/restarts it with the installed fork
and binding at the named cutover; this script does not do that.

Ops stages a mode-0600 fleet-bridge-owned JSON file on Forge with these exact keys
and actual distinct thread UUIDs: `spec-manager`, `foreman-bot`, `pilot-seat`.
**Forge fleet-bridge** runs:

```sh
python3 "$PKG/setup-forge-box.py" routes --config-root "$CFG" --threads /etc/fleet-manager-bridge/operator/threads.json
```

It writes routes.json in the supplied client schema, with every UUID filled,
pilot_seat=dev-3 and enabled=false; no made-up native threads are substituted.
The initial routes.pending.json is explicitly incomplete staging data and is
never the adapter input. Copy the final routes file to the pilot adapter's host
as protected configuration if that adapter runs there. Keep the existing
application journal paths only when they are the actual driver/adapter journals.
The owner supplies smoke_reference and enables adapter dispatch at the separately
approved smoke/cutover; setup never enables it or changes LIVE host.env.

Send through the bound sender app-server's factory/send using a persisted
messageId/conversationId. Bridge delivery does not wake a model by itself. The
recipient controller explicitly calls:

```json
{"jsonrpc":"2.0","id":10,"method":"thread/factoryMessage","params":{"threadId":"ACTUAL_NATIVE_THREAD_UUID","project":"12c0b4e6-82d1-4840-a643-d1dfdb4ccf68","messageId":"ORIGINAL_MESSAGE_UUID"}}
```

queue_only stays queued. wake_when_idle may start an idle turn; busy, Plan mode,
draining and pending trigger input can defer admission. Inspect the returned
reason and retry the **same** message/thread IDs after the condition clears.
Repeated thread/factoryMessage reconciles durable native history; consumed/runtime
and completed/worker receipts must share the actual turn ID. The adapter must not
forge bridge acknowledgments or treat service_accepted/delivered as completed
work. A completed notice turn is not a Forge seal or a completed UCSOS ticket.
See INTERFACE.md for the source-cited RPC and receipt rules.

## 6. Lease maintenance and recovery

Seat credentials last at most 3600 seconds, host admission at most 86400, and
temporary operator routes at most one hour. This setup uses explicit permanent
seat recipient grants, not expiring route-grant entries. There is no renewal
timer. Before expiry, **Forge fleet-bridge** uses the existing rotate command:

```sh
"$BIN/codex-factory-bridge" rotate --state "$STATE/service" --seat "$SEAT_UUID" \
  --output "$STATE/pending/rotation-UNIQUE.json" --credential-ttl 3600 --overlap-seconds 60
```

Ops copies the protected result to the correct host and atomically replaces that
role's ai-owned 0600 credential file within the overlap. Clients read it on each
call; preserve grants, sequences, ledgers and message IDs. Do not print its
contents. Host renewal uses rotate-host with a **fresh transport-key UUID**, then
coordinated updates of the exact forced-command/sudoers line and proxy config;
the previous UUID is not reusable. Do not pretend the initial 24-hour admission
is permanent. A reboot fence, uncertain send or restore quarantine needs the
existing operator reconciliation flow, not a fresh message/work order.

## 7. z690 qualification and its limits

The packaged test-setup-forge-box.py owns its fixture processes only; the setup
helper itself never starts services. The test uses the actual package bridge,
UID 65534 for service/proxy and UID 65532 for endpoints, separate logical host
IDs, a real pinned loopback OpenSSH connection and the generated restricted sudo
entry executing a nologin account. Its SSH frontend is the existing root fixture
account, not the production ai account. /tmp, /run and sudoers are private mount
namespace overlays backed by one isolated NVMe directory; no host config or
system account is changed. No model is started. Production systemd units,
production ai sudoers admission, host routing/firewall/SSH pins and two actual
kernels remain **unproven on Forge/dev-3** until Ops installs and runs section 4.
The updated fixture also checks real endpoint-UID sandbox preflight, relocated
metadata/resource lookup, and a simulated RTM_NEWADDR denial that prevents
enrollment without modifying policy. This refusal test does not claim to enable
or reproduce Ubuntu's actual AppArmor policy on z690.

Executed on z690 against the packaged helper and production bridge:

```text
sudo -n unshare --mount --propagation private python3 <isolated NVMe directory>/entry.py
# entry.py binds private /tmp and /run, then runs:
python3 -B dist/1112c/test-setup-forge-box.py --root /tmp/setup-proof-apparmor-1112c --dist /srv/stacks/codex/dist/1112c
exit 0
```

Actual stdout:

```json
{
  "passed": true,
  "checks": [
    "sandbox_preflight_passes_as_endpoint_prints_manifest_digest_and_relocates",
    "simulated_namespace_denial_refuses_before_enrollment_without_policy_changes_or_fallback",
    "init_stages_three_seats_without_services_and_repeat_refuses_without_mutation",
    "real_pinned_OpenSSH_generated_restricted_sudo_entry_and_nologin_proxy_start",
    "three_real_enrollments_local_pair_and_remote_pilot_with_separate_service_endpoint_uids",
    "all_four_directed_edges_send_receive_and_read_authentic_ordered_receipts_no_model",
    "both_ungranted_cross_seat_directions_refused",
    "same_state_serve_restart_replay_keeps_four_unique_messages_and_single_delivery_receipts",
    "disabled_routes_all_uuid_fields_bound_and_binding_credential_owners_modes_correct"
  ],
  "seats": 3,
  "directedEdges": 4,
  "durableMessagesAfterRestart": 4,
  "receiptsPerMessage": 2,
  "serviceUid": 65534,
  "endpointUid": 65532,
  "transport": "actual loopback OpenSSH; generated sudo forced command; one kernel",
  "modelsStarted": 0,
  "realFleetHostsTested": false
}
```

