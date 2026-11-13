# Notices-only pilot — operator assets revision 2 (1112f2)

Until S is live the mailbox carries notices only, never a secret.

This package reuses the WHOLE 1112f runtime and voice bundle unchanged. Version remains 0.167.2+ignibyte.1112f; runtime/voice source is c2f29c9f13bdd414736fc986284cb49672829c58. The folder suffix 1112f2 identifies the corrected operator assets. MANIFEST.json lists every reused runtime hash and old/new operator hashes. Install the complete folder, including codex -> bin/codex, bin/, codex-path/, codex-resources/voice/, metadata and sums. Never patch the frozen 1112f installation.

## A. Pilot contract and review gate

Renewal is OFF. The pilot must finish before the EARLIEST actual credential expiresAt across its three seats. Each credential lasts 3600 seconds from its own enrollment; setup time consumes earlier seats' lifetime. Bootstrap tokens last 300 seconds; explicit host admission lasts 86400 seconds. None of these clocks starts at package installation or service start.

### Exact END minute — binding before test 2 starts

Ops records the UTC issue time of EACH used bootstrap, the UTC enrollment time of EACH seat, and the UTC host-admission time. Use the service-returned metadata: bootstrapIssuedAt = bootstrap expiresAt - 300; enrolledAt = credential expiresAt - 3600; leaseAdmittedAt = the fresh admitted host expiresAt - 86400. Record those times and the returned expiry values when each operation succeeds; never record bearer/private-key contents. Compare the arithmetic against the returned expiresAt values and synchronize host clocks before proceeding.

The binding cutoff includes ALL of these: bootstrapIssuedAt + 300 for every used bootstrap, enrolledAt + 3600 for every seat, and leaseAdmittedAt + 86400. A bootstrap is consumed by enrollment, but this ruling still requires its 300-second cutoff; do not discard that bound to lengthen the test. It normally makes the available pilot window less than five minutes from the earliest used bootstrap issue, despite the one-hour credential lifetime.

Set EARLIEST_BOUND to the minimum of those seven timestamps. Reserve at least 60 seconds for teardown, and set the exact whole UTC END minute to floor((EARLIEST_BOUND - 60 seconds) / 60 seconds) * 60 seconds. Ops chooses an EARLIER minute if its teardown needs more time; it must never move the recorded END later. If enrollment/verification/setup has already consumed the window, or less than a minute remains before END, do not start test 2: stop and tear down immediately. Do not ignore an expired bound or re-enroll/reset state to extend the same run.

Before any test notice, Ops writes the computed value as a literal PILOT_END_UTC=YYYY-MM-DDTHH:MM:00Z in the pilot execution record on BOTH hosts, reads it back with the other operator, and prepares the exact scoped stop commands/roster in section H. An unset/ambiguous end time or missing teardown operator/roster blocks the test. The actual minute is computed after real enrollment; the package cannot invent a future enrollment time. No new timer service is introduced: Ops owns the clock watch and hard stop.

TIMING_JSON is a public JSON record with leaseAdmittedAtUtc and seats keyed exactly spec-manager, foreman-bot, pilot-seat; each seat has bootstrapIssuedAtUtc and enrolledAtUtc, all recorded UTC ISO timestamps. Run this immediately after the last enrollment, before test 2:

```sh
# Forge — Ops, timing metadata only; record/read back the two printed UTC values on BOTH hosts
python3 - "$TIMING_JSON" <<'PY_END'
# PILOT_END_CALCULATION
import datetime as dt
import json
import sys

ROLES = {'spec-manager', 'foreman-bot', 'pilot-seat'}
def timestamp(value):
    parsed = dt.datetime.fromisoformat(value.replace('Z', '+00:00'))
    if parsed.tzinfo is None or parsed.utcoffset() != dt.timedelta(0):
        raise ValueError('UTC timestamps required')
    return int(parsed.timestamp())

def pilot_end(record, now):
    if set(record['seats']) != ROLES:
        raise ValueError('all three enrollment records required')
    admitted = timestamp(record['leaseAdmittedAtUtc'])
    if not 0 <= admitted <= now:
        raise ValueError('admission time invalid')
    bounds = [admitted + 86400]
    for seat in record['seats'].values():
        issued = timestamp(seat['bootstrapIssuedAtUtc'])
        enrolled = timestamp(seat['enrolledAtUtc'])
        if not 0 <= issued <= enrolled <= now or enrolled >= issued + 300:
            raise ValueError('bootstrap/enrollment times invalid')
        bounds.extend((issued + 300, enrolled + 3600))
    earliest = min(bounds)
    end = ((earliest - 60) // 60) * 60
    if end - now < 60:
        raise ValueError('NO SAFE WINDOW: do not start; tear down now')
    return earliest, end

def utc(value):
    return dt.datetime.fromtimestamp(value, dt.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')

if __name__ == '__main__':
    with open(sys.argv[1]) as stream:
        record = json.load(stream)
    earliest, end = pilot_end(record, int(dt.datetime.now(dt.timezone.utc).timestamp()))
    print('EARLIEST_BOUND_UTC=' + utc(earliest))
    print('PILOT_END_UTC=' + utc(end))
PY_END
```

Worked example ONLY, not the live schedule: admission 2026-11-13T02:20:00Z; bootstrap issues at 02:22:00Z / 02:22:10Z / 02:22:20Z; corresponding enrollments at 02:22:05Z / 02:22:15Z / 02:22:25Z. The earliest bootstrap cutoff is 02:27:00Z, earlier than the credential cutoff 03:22:05Z and next-day lease cutoff. Computed immediately afterward, this run must record PILOT_END_UTC=2026-11-13T02:26:00Z (20:26 CST on November 12). At exactly 02:26:00Z stop test 2 and execute section H, even if a notice, agent or tool turn is still running. An earlier minute may be recorded to allow more teardown time.

There is no pilot renewal allowlist, broker/runtime directory, publisher policy, publisher key/login, renewal service or timer installation. Do not invoke the legacy renewal commands for a pilot; pilot renewal rendering refuses. R templates/tests retained in the package/repository are compatibility artifacts, not part of this deployment. Future renewal through fleet-bridge's own entry needs its own plan. Root-to-ai publication and home-directory publisher keys are refused.

This pilot is enrolled under ai in explicitly throwaway roots. Never reuse a pilot root as a final root. Final per-role accounts, private paths, the once-only durable init and the stock start/attach guard belong to S. This package builds neither S nor plan 003, and installs no arrival watcher or content encryption.

Prepare the public staging generation OFF the fleet hosts, in an isolated Ops staging environment. Ops writes ONE ucs-ops PR showing all public staging files whole, the account/ownership installation and the generated SSH integration. The executive reviews it BEFORE any fleet host init, installation, policy change, reload, unit action or admission. Private-key bytes are never displayed in the PR. This fork seat changes no fleet host.

## B. Actors, paths and the only two boot units

Ops supplies real numeric IDs from inventory. fleet-bridge has the SAME nonroot UID on Forge/dev-3; its primary group is fleet-bridge. ai and fleet-bridge are members of fleet-bridge-clients. Use fresh endpoint logins/tmux servers after supplementary-group changes.

| Item | Required state |
| --- | --- |
| fleet-bridge password, both hosts | locked, passwd -S status L; complete sudo -l -U listing denies all privileges |
| Forge fleet-bridge login | /usr/sbin/nologin |
| dev-3 fleet-bridge login | /etc/ssh/fleet-bridge-pilot-login, generated root:root 0755 dispatcher |
| Package and every ancestor | root-owned, no group/world write or symlink ancestors; preserve package-relative resource links |
| Forge STATE / CFG | /var/lib/fleet-manager-bridge-pilot / /etc/fleet-manager-bridge-pilot |
| dev-3 STATE / CFG | /var/lib/fleet-manager-bridge-remote-pilot / /etc/fleet-manager-bridge-remote-pilot |
| STATE, STATE/run | fleet-bridge:fleet-bridge-clients 2710 |
| CFG | fleet-bridge:fleet-bridge-clients 2750 |
| CFG/grants,operator,transport; private service/connector/source/pending | service-owned 0700; private files 0600 |
| STATE/native | service:clients 0710; native/ROLE ai:ai 0700 |
| CFG/credentials | ai:ai 0700; bootstrap/credential files ai:ai 0600 |
| bindings / public setup | service:clients 0640 |
| socket | service:clients 0660, created by service |
| Forge private-key directory | /etc/fleet-bridge-pilot-ssh, root:fleet-bridge 0750 |
| Forge private key | that directory's forge-ed25519, fleet-bridge:fleet-bridge 0600, empty passphrase |
| dev-3 authorized key | /etc/ssh/fleet-bridge-pilot.keys, root:root 0644, exactly one restricted/from/forced-command line |
| Entry/launcher JSON, Match policy | root:root 0644; dispatcher/launcher root:root 0755 |
| Public generation | /var/lib/fleet-codex-pilot-stage, root:root 0700; generated public contents shown whole |
| Host reports / combined verification | root:root 0600 in /etc; fresh, generation-bound and invalidated by failed re-verification |

Enabled ONLY AFTER successful verification:

- fleet-manager-bridge-pilot.service: local hub; dev-3 outage alone does not stop local communication. Existing grant/boot fences remain; its own failure has Restart=no.
- fleet-manager-bridge-pilot-source.service: service-UID Forge-initiated SSH, Requires the hub; failure retries after 5 seconds, with no model turn.

There is no third enabled pilot unit. No renewal unit/timer is installed. The helper's explicit activate-pilot operation is the only generated enable/start procedure and refuses without both host reports and the four launcher lines.

Only the existing Forge 10.200.68.55 -> dev-3 port 22 route is used. Dev-3 -> Forge port 22 stays closed. No firewall change, reverse SSH, forwarding, service sudo or password unlock is permitted. Inventory supplies the trusted dev-3 ed25519 host public key; an unauthenticated ssh-keyscan is not a pin.

## C. Offline preparation: one public generation, separate private key

The offline staging environment must hold the received complete package root-owned at the SAME absolute path intended on both hosts. The helper pins this path in the generated code/config. This can be an isolated Ops build container, not an early fleet install.

```sh
# Offline Ops staging environment — root; never run these preparation steps on a fleet host before PR approval
PKG=/opt/fleet-codex/c2f29c9f13bdd414736fc986284cb49672829c58-1112f2
STAGE=/var/lib/fleet-codex-pilot-stage
PRIVATE=/var/lib/fleet-codex-pilot-private
install -d -o root -g root -m 0700 "$PRIVATE"
ssh-keygen -q -t ed25519 -N '' -C fleet-forge-source -f "$PRIVATE/forge-ed25519"
chmod 0600 "$PRIVATE/forge-ed25519" "$PRIVATE/forge-ed25519.pub"
```

Create a root:0600 OPS_INPUT JSON from inventory. These numbers are EXAMPLES, not assignments; replace every ID/address/key with the reviewed values. Both local roles use Forge ai; the pilot uses dev-3 ai. No per-role S account parameters are introduced.

```json
{
  "forgeAddress": "10.200.68.55",
  "sourceAddress": "<inventory dev-3 private IPv4>",
  "port": 22,
  "sshUnit": "ssh.service",
  "serviceUid": 983,
  "serviceGid": 983,
  "clientsGid": 982,
  "agentUid": 1000,
  "agentGid": 1000,
  "remoteAgentUid": 1000,
  "remoteAgentGid": 1000,
  "clientPublicKey": "<public line from PRIVATE/forge-ed25519.pub>",
  "sourceHostKey": "<trusted inventory dev-3 ed25519 host public key>"
}
```

sshUnit is the actual inventory service name, ssh.service or sshd.service. No hostname/service guessing occurs during the generated reload. Input contains public keys, never a private key or bearer.

```sh
# Offline Ops staging environment — root; output must be absent
python3 -I "$PKG/setup-forge-box.py" prepare-pilot --input "$OPS_INPUT" --output "$STAGE"
# Public output only: show EVERY generated file whole in the ONE Ops PR
for FILE in "$STAGE"/*; do
  printf '\nFILE %s\n' "$FILE"
  cat -- "$FILE"
done
```

prepare-pilot creates only that public staging folder: no runtime state, enrollment, host admission, service start or host policy mutation. It prepares fixed hub/host/key/seat/incarnation IDs for on-host init AFTER approval. pilot-plan.json is public; no service MAC, bearer or private key is created there. generation.json records all public file hashes/modes. Any changed file invalidates the generation. Keep PRIVATE separate and never include it in the display loop.

The public generation includes the complete key line, dispatcher and launcher, entry/launch JSON, source config/pin, pilot plan, key installation specification, both units, SSH Include/expected-options/integration instructions, dev-3 validation/reload/verification procedure, Forge verification/activation procedure and pilot contract. Ops shows all of them whole; no key/policy/unit is hand typed.

The generated public key line is exactly:

```text
restrict,from="10.200.68.55",command="/etc/ssh/fleet-bridge-pilot-launcher" ssh-ed25519 <actual-public-key> fleet-forge-source
```

The generated Match body is:

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

The generated sshd-include.fragment is:

```sshconfig
Match all
Include /etc/ssh/fleet-bridge-pilot-match.conf
Match all
```

Ops incorporates that literal fragment through its reviewed host configuration after global-only settings. Match all resets criteria; it does not permit global-only directives after Match. Earlier applicable Match values may win. Global UsePAM yes and PermitUserEnvironment no are required; PermitUserEnvironment is not legal inside Match. The helper validates the actual complete host configuration/readback, not just the fragment. No home AuthorizedKeysFile/command/certificate fallback is accepted for this service principal.

The dispatcher and launcher are each generated whole with the actual PACKAGE path:

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

Both are root:root 0755. The dispatcher accepts only sshd's exact -c forced-launcher invocation. The zero-argument launcher accepts exactly SSH_ORIGINAL_COMMAND=codex-factory-v1, no SSH_TTY and the fixed authenticated route, validates root package/config/code hashes and actual service UID, scrubs the environment and execs only the pinned bridge source-entry. nologin cannot remain on dev-3: sshd uses the login shell's -c route for a forced command. The full pinned implementation is included in section J. A refusal exits 1 and writes one authpriv ids/reason-only syslog line, never command text, payload or bearer.

## D. After executive approval: install/apply through the one Ops PR

Both hosts receive the same WHOLE package and public generation. Ops privately transfers the Forge transport private key into protected root:0600 staging on Forge. It is not an ai-home key and is not sent to dev-3. The service account's locked password, group memberships and exact host-specific shell are provisioned through the reviewed Ops account task; the helper does not create users or grant sudo.

```sh
# Both hosts — Ops/root, ONLY AFTER executive approval; SOURCE is the received whole package
PKG=/opt/fleet-codex/c2f29c9f13bdd414736fc986284cb49672829c58-1112f2
STAGE=/var/lib/fleet-codex-pilot-stage
(cd "$SOURCE" && sha256sum -c SHA256SUMS)
test ! -e "$PKG"
install -d -o root -g root -m 0755 /opt/fleet-codex
cp -a -- "$SOURCE" "$PKG"
chown -R root:root "$PKG"
chmod 0755 "$PKG" "$PKG/bin" "$PKG/codex-path" "$PKG/codex-resources"
```

Install STAGE verbatim at its fixed path, root-owned with no symlink/writable ancestors. Preserve its generated modes. APPROVED_GENERATION is its reviewed generation.json UUID. The explicit value records Ops' approval input; it is not a substitute for the PR/executive approval.

```sh
# Forge — Ops/root, after approval/account provisioning; PRIVATE_KEY is root:0600 with root-owned ancestors
python3 -I "$PKG/setup-forge-box.py" apply-pilot --stage "$STAGE" --host forge \
  --approved-generation "$APPROVED_GENERATION" --private-key "$PRIVATE_KEY"
```

```sh
# dev-3 — Ops/root, after approval/account provisioning
python3 -I "$PKG/setup-forge-box.py" apply-pilot --stage "$STAGE" --host dev-3 \
  --approved-generation "$APPROVED_GENERATION"
```

apply-pilot refuses wrong generation/existing pilot roots. On Forge it initializes the planned IDs exactly once with init --pilot --defer-remote --pilot-plan, starts nothing, admits nobody and installs only the two disabled units. It installs the transport key as fleet-bridge:0600 beneath root:fleet-bridge:0750 /etc/fleet-bridge-pilot-ssh. Key generation has already occurred in separate private staging, so the service never needs write permission on that root-owned directory. The 0640 root-owned key alternative is deliberately unused: this runtime requires service-owned private 0600 files. On dev-3 it installs the root SSH files and public pilot binding, with no init/MAC/credential authority.

The CLI init --pilot remains guarded: it emits no reverse operator/proxy.json. ssh-config refuses a saved pilot before reading its supplied public key or writing sudo/key artifacts. Partial init/apply must stop for owner review; never delete/re-init a root to repair a token.

## E. Per-host verification and reload; no unit activation yet

On dev-3 keep another authenticated operator session OPEN. Ops' PR has installed the generated Include fragment through its managed sshd configuration. Then run the generated procedure:

```sh
# dev-3 — Ops/root; keep the existing fallback session open until fresh-session verification passes
FALLBACK_SESSION_OPEN=yes /bin/sh "$STAGE/dev3-validate-reload-verify.sh"
```

This procedure runs sshd -t, reads/compares sshd -T -C user=fleet-bridge,host=dev-3,addr=10.200.68.55, then invokes systemctl RELOAD on the reviewed SSH unit, NEVER restart/reload-or-restart. Syntax/readback failure means no reload. FALLBACK_SESSION_OPEN is an explicit operator assertion, not proof that the helper inspected a terminal. After reload, verify-host runs locally as Ops/root and produces /etc/fleet-bridge-pilot-dev3-verified.json, root:0600. The fallback stays open until the later fresh launcher probes pass.

sshd-expected.txt contains these normalized lowercase lines (OpenSSH output capitalization can differ):

```text
authorizedkeysfile /etc/ssh/fleet-bridge-pilot.keys
authorizedkeyscommand none
pubkeyacceptedalgorithms ssh-ed25519
pubkeyauthentication yes
authenticationmethods publickey
passwordauthentication no
kbdinteractiveauthentication no
permittty no
allowtcpforwarding no
x11forwarding no
allowagentforwarding no
permittunnel no
disableforwarding yes
permituserrc no
permituserenvironment no
usepam yes
```

Condition 4 is part of verify-host on EACH host: nonroot service UID/primary GID, exact login shell, passwd -S fleet-bridge status L, and the complete sudo -n -l -U fleet-bridge listing explicitly denying all privileges. It prints the actual passwd/sudo lines and an ACCOUNT ... PASS line. A failed sudo true is not sufficient. Root's -U query can return 0 while explicitly denying the target user: verification requires the explicit denial and rejects errors/allowed-command listings, not a chosen exit status alone. Unlocked account, wrong shell, any allowed command, query error, changed dispatcher/hash/mode or active/enabled pilot unit fails closed. No service sudo or remote root diagnostic command is added.

Forge verify-host also checks the private key's root:0750 directory/group, service:0600 file, no passphrase and matching public key. Dev-3 checks actual root SSH policy/code/file hashes and modes. Both require renewal files/units to be absent. A failed re-verification removes its older success report. Reports are generation/hash/host/boot bound and valid for at most 300 seconds; repeat after any account/policy/config change. These are local privileged Ops checks, not claims fabricated by the unprivileged SSH entry.

Ops transfers the fresh dev-3 root report to the SAME /etc filename on Forge, root:root 0600, through its established authenticated administrative procedure. Never accept an endpoint-supplied/user-owned report. No new automated SSH login path is introduced. If transfer/application takes too long, rerun dev-3 verification; do not extend freshness.

## F. Both host results + four lines gate BOTH boot units

```sh
# Forge — Ops/root, after approval and receipt of the fresh dev-3 root report
/bin/sh "$STAGE/forge-verify-activate.sh"
```

The generated procedure first runs Forge verify-host, then verify-pilot with BOTH report files. verify-pilot checks freshness/generation/hash/Forge boot, rechecks Forge's live account/key, and compares the authenticated source boot to the dev-3 report. It runs the four real pinned SSH probes. Required launcher lines are:

```text
AUTHENTICATED_PROTOCOL PASS
SHELL REFUSED
PTY REFUSED
FORWARD REFUSED
```

A network/authentication failure or timeout cannot count as refusal. PTY/forward tests require the actual OpenSSH refusal diagnostic. Missing/mismatched/stale reports, incomplete lines or a failed check mean no activation. The older verify-launcher CLI now refuses and directs the operator to this combined gate; four network lines alone do not satisfy condition 4. First activation requires the old owner drained. The connection locks prevent taking over an existing live owner.

Only after success does activate-pilot revalidate the root proof, both report hashes/freshness, live Forge account/key and drained/disabled units, explicitly admit-remote with the reserved IDs, then enable/start the hub and source. The source Requires the hub. It never installs/starts a renewal job. A failed combined check invalidates its older combined proof. This is an explicit Ops action; staging and apply start no service.

## G. Enrollment and notices-only test 2

Run endpoint preflight as ai on EACH actual host before enrollment or real tool turns:

```sh
# Both hosts — ai, same execution context as endpoint jobs; no model call
python3 "$PKG/setup-forge-box.py" preflight --bin-dir "$PKG/bin"
```

The bwrap digest is UNCHANGED from 1112f: 0ce4fa5b9da214841641f12032afa6cda3d93b45f321705fa8bde5fb2c270b74. The NEW install path still requires Ops' approved sandbox policy/profile/xattr before use. A failed probe blocks enrollment; no AppArmor/sysctl/xattr changes or full-access fallback are made by the helper.

For spec-manager and foreman-bot, on Forge:

```sh
# Forge — Ops/root; ROLE is spec-manager or foreman-bot; only the bootstrap PATH is printed
BOOTSTRAP=$(sudo -u fleet-bridge -- python3 "$PKG/setup-forge-box.py" bootstrap \
  --config-root /etc/fleet-manager-bridge-pilot --role "$ROLE" | python3 -c 'import json,sys; print(json.load(sys.stdin)["bootstrapFile"])')
install -o ai -g ai -m 0600 "$BOOTSTRAP" "/etc/fleet-manager-bridge-pilot/credentials/$ROLE.bootstrap"
```

```sh
# Forge — ai, fresh group membership and successful preflight
python3 "$PKG/setup-forge-box.py" enroll --bin-dir "$PKG/bin" \
  --binding "/etc/fleet-manager-bridge-pilot/$ROLE.binding.json" \
  --bootstrap "/etc/fleet-manager-bridge-pilot/credentials/$ROLE.bootstrap"
```

For pilot-seat, Forge fleet-bridge obtains bootstrap --config-root /etc/fleet-manager-bridge-pilot --role pilot-seat. Ops privately transfers that one bootstrap to dev-3's /etc/fleet-manager-bridge-remote-pilot/credentials/pilot-seat.bootstrap as ai:ai 0600 within 300 seconds. Never display/log its contents.

```sh
# dev-3 — ai, successful preflight
python3 "$PKG/setup-forge-box.py" enroll --bin-dir "$PKG/bin" \
  --binding /etc/fleet-manager-bridge-remote-pilot/pilot-seat.binding.json \
  --bootstrap /etc/fleet-manager-bridge-remote-pilot/credentials/pilot-seat.bootstrap
```

The helpers preserve host/seat/incarnation IDs. Expired unused bootstraps are reminted through bootstrap, never re-init. Credentials last one hour from each enrollment. BEFORE test 2, compute and record the exact PILOT_END_UTC in section A from all enrollment/bootstrap/admission times and returned expiries. Stop and tear down at that minute even if still running; do not merely watch the credential expiresAt. Renew nothing. Test the local pair and foreman/pilot in both directions with notices only. Preserve the original message UUID/payload/sequence for retry; receipt delivery is not a Forge seal or automatic model admission. Existing INTERFACE.md describes thread/factoryMessage and deferred/queue_only behavior. T2 adds no watcher.

The standing connection uses actual Unix peer UIDs, pinned host/key/generation/service identity, bounded frames/2.5-second request deadlines, per-frame lease/key/revocation/bearer/boot checks and keepalive 15s/count 3. Idle/reconnect creates no model calls. Duplicate owners cannot replace a live socket; only matching durable inode/device/dead-incarnation proof permits stale cleanup. Public socket publication follows durable owner recording. Existing reboot fencing still requires explicit recovery; none of the operator helpers approves recovery automatically.

At each drained fork cutover Ops merges {"updater":{"autoUpdateEnabled":false}} into CODEX_HOME/app-server-daemon/settings.json, stops BOTH stock backend and separate updater, and launches fleet jobs by absolute fork path with --no-daemon. Factory compile-time updates remain OFF. The stronger stock provenance guard is deferred to S. Keep endpoint CODEX_HOMEs/private state separate from stock; sharing ai still permits those ai processes to read each other's files, hence notices only.

## H. Remove only the throwaway pilot after drain

The recorded PILOT_END_UTC is a HARD STOP, not a reminder to wait for the test to finish. Before starting test 2, Ops records the exact three pilot endpoint job/controller stop commands and verified unit/cgroup/process incarnations on each host in its approved execution roster. Do not invent unit names, kill all ai processes, or target an unverified/reused PID. Both operators have this roster and the same literal UTC end minute open before any notice is sent.

AT that minute, even if still running, execute these teardown steps; do not extend END for a reply, receipt, agent, tool or successful test result:

1. Stop new notices/model-turn admission immediately. Forge Ops/root disables/stops the source and hub using the command below, cutting off both remote and local mailbox service. Do not wait for model work to complete first.
2. Concurrently, Forge and dev-3 Ops stop/cancel their three scoped pilot endpoint jobs using the recorded controller/unit/cgroup commands. Allow at most ten seconds for normal termination, then kill only still-running verified pilot cgroups/incarnations through the recorded controller. An unfinished test stays unfinished; preserve IDs and facts for later reconciliation, never fabricate completed receipts.
3. Confirm both bridge units/cgroups and the matching dev-3 SSH/source entry have exited. If anything fails to stop, block further use, use the scoped forced termination in the roster and report the failure immediately; do not resume the pilot. Never remove a live/unproven socket.
4. Remove this generation's transport authorization/key and SSH integration, restore the locked/no-sudo service account to nologin, validate/read back and RELOAD with the fallback session open, then remove only the verified throwaway roots/files below. Begin this cleanup at END rather than postponing it for ongoing work. Budget these operations inside the reserved margin; choose an earlier END before starting if that requires more than 60 seconds.

```sh
# Forge — Ops/root AT the recorded PILOT_END_UTC, even if test 2 is still running
systemctl disable --now fleet-manager-bridge-pilot-source.service fleet-manager-bridge-pilot.service
```

These bridge units already bound stop time to ten seconds each. The scoped endpoint stop/kill roster is mandatory because this repo does not know the live pilot's external job/controller names. The hard stop does not depend on automatic credential expiry or renewal. A no-safe-window result from section A means execute this teardown immediately instead of starting a test.

Remove only this generation's root key/SSH files, its generated include from managed configuration, units, public plan/reports and transport key directory after matching their recorded hashes/owners. Restore fleet-bridge's dev-3 shell to nologin with password locked/no sudo; keep restricted key lookup until the account is inaccessible. Validate/read back and reload sshd through Ops with fallback session open; never restart. Preserve unrelated operator keys and all received packages/CODEX_HOMEs/project directories. No renewal files need removal because none were installed.

Exact disposable state/config roots, after those checks:

```sh
# Forge — Ops/root; stopped/drained pilot only
for DIR in /var/lib/fleet-manager-bridge-pilot /etc/fleet-manager-bridge-pilot; do
  test ! -L "$DIR" && test -d "$DIR" || exit 1
  test "$(stat -c %u "$DIR")" = "$(id -u fleet-bridge)" || exit 1
done
rm -rf --one-file-system -- /var/lib/fleet-manager-bridge-pilot /etc/fleet-manager-bridge-pilot
```

```sh
# dev-3 — Ops/root; matching entry and endpoint stopped
for DIR in /var/lib/fleet-manager-bridge-remote-pilot /etc/fleet-manager-bridge-remote-pilot; do
  test ! -L "$DIR" && test -d "$DIR" || exit 1
  test "$(stat -c %u "$DIR")" = "$(id -u fleet-bridge)" || exit 1
done
rm -rf --one-file-system -- /var/lib/fleet-manager-bridge-remote-pilot /etc/fleet-manager-bridge-remote-pilot
```

Ops removes its own root-owned public staging/private transfer material after the retained PR is complete; never reuse these roots for S final enrollment.

## I. Repository-context proof and limits

The executable full proof is scripts/factory/qualify_forge_transport.py in the qualification checkout recorded in MANIFEST.json, not a command supplied standalone in the package. From that repository checkout, pass the received package by absolute path and a FRESH isolated NVMe work directory:

```sh
# Fork/build seat — repository checkout; no Cargo/Bazel/voice build
TMPDIR="$TASK/tmp" python3 scripts/factory/qualify_forge_transport.py \
  --package "$RECEIVED_PACKAGE" --work-dir "$TASK/proof" --full-package-proof
```

It reuses verified existing same-source fault binaries, runs labeled R/legacy compatibility lanes and the seven mailbox/actual packaged shell-write-MCP lanes, then the corrected renewal-OFF T2 deployment proof. The current pilot proof includes account-policy negatives, generated Include/readback/reload with a real retained fallback SSH session, complete verification before BOTH enables, key-directory modes, failed-proof invalidation, no legacy sudo/proxy output, no R deployment and actual three-seat/four-edge conversation. Current script hashes/results are in the plan and manifest; older 1112f results are labeled compatibility only. Packaged test-setup-forge-box.py remains a legacy fixture.

No compiler, owner Codex login/config/cache, external model/backend or fleet host is used. Isolated one-kernel/mount/network fixtures prove the protocol, account checks and reload procedure; Ops must run the generated verification, installed endpoint sandbox/tool proof and notices-only test on the actual hosts. Package readiness is not a merge/deployment approval.

## J. Whole pinned dispatcher support implementation

This is the complete packaged implementation loaded by the two generated wrappers. Ops installs the reviewed whole package and generated files; it does not hand type this code:

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
