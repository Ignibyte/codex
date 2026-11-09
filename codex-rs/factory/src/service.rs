//! Local authenticated durable mailbox. No model turn or command execution.

use crate::protocol;
use anyhow::Context;
use anyhow::Result;
use anyhow::bail;
use anyhow::ensure;
use hmac::Hmac;
use hmac::Mac;
use rand::TryRngCore;
use rand::rngs::OsRng;
use serde::Deserialize;
use serde::Serialize;
use serde_json::Value;
use serde_json::json;
use sha2::Digest;
use sha2::Sha256;
use std::collections::BTreeMap;
use std::fs::File;
use std::fs::OpenOptions;
use std::io::BufRead;
use std::io::BufReader;
use std::io::Read;
use std::io::Write;
use std::os::fd::AsRawFd;
use std::os::unix::fs::DirBuilderExt;
use std::os::unix::fs::MetadataExt;
use std::os::unix::fs::OpenOptionsExt;
use std::os::unix::fs::PermissionsExt;
use std::os::unix::net::UnixListener;
use std::os::unix::net::UnixStream;
use std::path::Path;
use std::time::Duration;
use std::time::Instant;
use std::time::SystemTime;
use std::time::UNIX_EPOCH;
use uuid::Uuid;

type HmacSha256 = Hmac<Sha256>;

#[path = "lifecycle.rs"]
pub mod lifecycle;

#[path = "routes.rs"]
pub mod routes;

#[derive(Deserialize, Serialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct Registry {
    protocol: u32,
    host: Option<Uuid>,
    #[serde(default)]
    remote_hosts: BTreeMap<Uuid, RemoteHost>,
    #[serde(default)]
    routes: BTreeMap<Uuid, routes::Route>,
    seats: BTreeMap<Uuid, Seat>,
    bootstraps: BTreeMap<String, Bootstrap>,
    #[serde(default)]
    mail: BTreeMap<Uuid, Mail>,
    #[serde(default)]
    audit: Vec<Value>,
    #[serde(default)]
    audit_sequence: u64,
    #[serde(default = "storage_version")]
    storage_version: u32,
    #[serde(default)]
    observed_time: u64,
}

#[derive(Deserialize, Serialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct RemoteHost {
    bridge_uid: u32,
    current_key: Uuid,
    expires_at: u64,
    previous_key: Option<(Uuid, u64)>,
    used_keys: std::collections::BTreeSet<Uuid>,
    revoked: bool,
}

impl RemoteHost {
    fn active(&self, time: u64) -> bool {
        self.permits(self.current_key, time)
            || self
                .previous_key
                .is_some_and(|(key, _)| self.permits(key, time))
    }

    fn permits(&self, key: Uuid, time: u64) -> bool {
        !self.revoked
            && ((key == self.current_key && self.expires_at > time)
                || self
                    .previous_key
                    .is_some_and(|(old, until)| old == key && until > time))
    }
}

#[derive(Clone, Copy)]
struct Peer {
    uid: u32,
    host: Uuid,
    remote: bool,
}

#[derive(Deserialize, Serialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct Seat {
    grant: protocol::Grant,
    nonce: String,
    expires_at: u64,
    last_sequence: u64,
    #[serde(default)]
    previous: Option<Previous>,
    #[serde(default)]
    revoked: bool,
}

#[derive(Deserialize, Serialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct Previous {
    nonce: String,
    expires_at: u64,
    valid_until: u64,
}

#[derive(Clone, Deserialize, Serialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct Mail {
    envelope: protocol::Envelope,
    fingerprint: String,
    state: String,
    receipts: Vec<protocol::Receipt>,
    reconcile_required: bool,
    #[serde(default)]
    cancellation: Option<Cancellation>,
}

#[derive(Clone, Deserialize, Serialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct Cancellation {
    requested_by: Uuid,
    requested_at: u64,
    state: String,
    turn_id: Option<String>,
    reason: Option<String>,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Auth {
    seat: Uuid,
    credential: String,
    request: protocol::Request,
}

#[derive(Clone, Deserialize, Serialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct Bootstrap {
    grant: protocol::Grant,
    expires_at: u64,
    credential_ttl: u64,
    consumed: bool,
}

#[derive(Deserialize)]
#[serde(tag = "method", rename_all = "snake_case", deny_unknown_fields)]
enum WireRequest {
    ManagerReadResult {
        protocol: u32,
        auth: Auth,
        job_id: Uuid,
        read: crate::foreman::managed::ReadResult,
    },
    ForemanAction {
        protocol: u32,
        auth: Auth,
        action: crate::foreman::Action,
    },
    ReadAssignmentSpec {
        protocol: u32,
        auth: Auth,
        read: crate::foreman::managed::ReadSpec,
    },
    ReadHandoff {
        protocol: u32,
        auth: Auth,
        read: crate::foreman::managed::ReadHandoff,
    },
    ManagerHandoff {
        protocol: u32,
        auth: Auth,
        job_id: Uuid,
        operation_id: Uuid,
    },
    ManagerOperation {
        protocol: u32,
        auth: Auth,
        job_id: Uuid,
        operation_id: Uuid,
    },
    ManagerUpload {
        protocol: u32,
        auth: Auth,
        job_id: Uuid,
        chunk: crate::manager::Chunk,
    },
    ManagerRead {
        protocol: u32,
        auth: Auth,
        job_id: Uuid,
        operation_id: Uuid,
        offset: usize,
    },
    ManagerCatalogue {
        protocol: u32,
        auth: Auth,
        job_id: Uuid,
        #[serde(default)]
        offset: usize,
    },
    ManagerSpec {
        protocol: u32,
        auth: Auth,
        job_id: Uuid,
        operation_id: Uuid,
        tool: String,
        arguments: Value,
    },
    ReportBlockage {
        protocol: u32,
        auth: Auth,
        run_id: Uuid,
        report: crate::foreman::supervision::Report,
    },
    BlockageDecision {
        protocol: u32,
        auth: Auth,
        run_id: Uuid,
        notice_id: Uuid,
        decision: crate::foreman::supervision::Decision,
    },
    AssignmentResult {
        protocol: u32,
        auth: Auth,
        run_id: Uuid,
        thread_id: Uuid,
        turn_id: Uuid,
        artifacts: Vec<crate::foreman::Artifact>,
    },
    AcknowledgeContext {
        protocol: u32,
        auth: Auth,
        run_id: Uuid,
        context_digest: String,
    },
    AuthorizeAssignment {
        protocol: u32,
        auth: Auth,
        message_id: Uuid,
        cwd: std::path::PathBuf,
    },
    ReportPresence {
        protocol: u32,
        auth: Auth,
        report: crate::observation::PresenceReport,
    },
    ObserveWork {
        protocol: u32,
        auth: Auth,
        after: Option<Uuid>,
        agent_id: Option<Uuid>,
    },
    RecordNative {
        protocol: u32,
        auth: Auth,
        record: crate::recording::NativeRecord,
    },
    RecordingStatus {
        protocol: u32,
        auth: Auth,
    },
    Cancel {
        protocol: u32,
        auth: Auth,
        message_id: Uuid,
    },
    CancelResult {
        protocol: u32,
        auth: Auth,
        message_id: Uuid,
        state: String,
        turn_id: Option<String>,
        reason: String,
    },
    Peers {
        protocol: u32,
        auth: Auth,
    },
    Inbox {
        protocol: u32,
        auth: Auth,
    },
    Sync {
        protocol: u32,
        auth: Auth,
    },
    Ack {
        protocol: u32,
        auth: Auth,
        receipt: protocol::Receipt,
    },
    Status {
        protocol: u32,
        auth: Auth,
        message_id: Uuid,
    },
    Send {
        protocol: u32,
        auth: Auth,
        envelope: Box<protocol::Envelope>,
    },
    Receive {
        protocol: u32,
        auth: Auth,
        message_id: Uuid,
    },
    Enroll {
        protocol: u32,
        bootstrap: String,
    },
    Authorize {
        protocol: u32,
        seat: Uuid,
        credential: String,
        request: protocol::Request,
    },
}

fn storage_version() -> u32 {
    1
}

fn uid() -> u32 {
    // SAFETY: geteuid has no arguments or ownership requirements.
    unsafe { libc::geteuid() }
}

pub(crate) fn now() -> Result<u64> {
    Ok(SystemTime::now().duration_since(UNIX_EPOCH)?.as_secs())
}

fn random() -> Result<[u8; 32]> {
    let mut bytes = [0; 32];
    OsRng
        .try_fill_bytes(&mut bytes)
        .context("entropy_unavailable")?;
    Ok(bytes)
}

fn hex(bytes: &[u8]) -> String {
    bytes.iter().map(|b| format!("{b:02x}")).collect()
}

fn unhex(value: &str) -> Result<Vec<u8>> {
    ensure!(
        value.len() == 64 && value.bytes().all(|b| b.is_ascii_hexdigit()),
        "credential_invalid"
    );
    value
        .as_bytes()
        .as_chunks::<2>()
        .0
        .iter()
        .map(|part| Ok(u8::from_str_radix(std::str::from_utf8(part)?, 16)?))
        .collect()
}

fn private_state(state: &Path) -> Result<()> {
    ensure!(state.is_absolute(), "state_path_not_absolute");
    let mut missing = Vec::new();
    let mut ancestor = state;
    while !ancestor.try_exists()? {
        missing.push(ancestor.to_path_buf());
        ancestor = ancestor.parent().context("state_parent_missing")?;
    }
    std::fs::DirBuilder::new()
        .mode(0o700)
        .recursive(true)
        .create(state)?;
    let info = std::fs::symlink_metadata(state)?;
    ensure!(
        info.is_dir() && info.uid() == uid() && info.mode() & 0o077 == 0,
        "state_permissions"
    );
    for directory in missing.iter().rev() {
        File::open(directory.parent().context("state_parent_missing")?)?.sync_all()?;
        File::open(directory)?.sync_all()?;
    }
    Ok(())
}

fn lock_file(state: &Path) -> Result<File> {
    private_state(state)?;
    let file = OpenOptions::new()
        .read(true)
        .write(true)
        .create(true)
        .truncate(false)
        .mode(0o600)
        .custom_flags(libc::O_NOFOLLOW)
        .open(state.join("registry.lock"))?;
    let info = file.metadata()?;
    ensure!(
        info.is_file() && info.uid() == uid() && info.mode() & 0o077 == 0,
        "lock_permissions"
    );
    Ok(file)
}

pub(crate) fn lock(state: &Path) -> Result<File> {
    let file = lock_file(state)?;
    // SAFETY: file owns a valid descriptor for the full lock lifetime.
    ensure!(
        unsafe { libc::flock(file.as_raw_fd(), libc::LOCK_EX) } == 0,
        "state_lock_failed"
    );
    Ok(file)
}

pub(crate) fn try_lock(state: &Path) -> Result<Option<File>> {
    let file = lock_file(state)?;
    // SAFETY: this file owns its descriptor until the guard is released.
    if unsafe { libc::flock(file.as_raw_fd(), libc::LOCK_EX | libc::LOCK_NB) } == 0 {
        return Ok(Some(file));
    }
    let error = std::io::Error::last_os_error();
    if error.kind() == std::io::ErrorKind::WouldBlock {
        return Ok(None);
    }
    Err(error).context("state_lock_failed")
}

fn lock_until(state: &Path, deadline: Instant) -> Result<File> {
    let file = lock_file(state)?;
    loop {
        ensure!(Instant::now() < deadline, "network_timeout");
        // SAFETY: this owned file descriptor remains valid until the returned guard is dropped.
        if unsafe { libc::flock(file.as_raw_fd(), libc::LOCK_EX | libc::LOCK_NB) } == 0 {
            return Ok(file);
        }
        let error = std::io::Error::last_os_error();
        ensure!(
            error.kind() == std::io::ErrorKind::WouldBlock,
            "state_lock_failed"
        );
        std::thread::sleep(Duration::from_millis(10));
    }
}

pub(crate) fn private_read(path: &Path, limit: u64) -> Result<Vec<u8>> {
    let mut file = OpenOptions::new()
        .read(true)
        .custom_flags(libc::O_NOFOLLOW)
        .open(path)?;
    let info = file.metadata()?;
    ensure!(
        info.is_file() && info.uid() == uid() && info.mode() & 0o077 == 0 && info.len() <= limit,
        "private_file_permissions_or_bound"
    );
    let mut bytes = Vec::new();
    file.read_to_end(&mut bytes)?;
    ensure!(bytes.len() as u64 <= limit, "private_file_bound");
    Ok(bytes)
}

pub(crate) fn exclusive_write(path: &Path, bytes: &[u8]) -> Result<()> {
    let mut file = OpenOptions::new()
        .write(true)
        .create_new(true)
        .mode(0o600)
        .custom_flags(libc::O_NOFOLLOW)
        .open(path)?;
    file.write_all(bytes)?;
    file.sync_all()?;
    Ok(())
}

fn load(state: &Path) -> Result<Registry> {
    let path = state.join("registry.json");
    if !path.try_exists()? {
        return Ok(Registry {
            protocol: protocol::VERSION,
            host: None,
            remote_hosts: BTreeMap::new(),
            routes: BTreeMap::new(),
            seats: BTreeMap::new(),
            bootstraps: BTreeMap::new(),
            mail: BTreeMap::new(),
            audit: Vec::new(),
            audit_sequence: 0,
            storage_version: 7,
            observed_time: 0,
        });
    }
    let registry: Registry = serde_json::from_slice(&private_read(&path, 2 * 1024 * 1024)?)?;
    ensure!(
        registry.protocol == protocol::VERSION,
        "registry_protocol_unsupported"
    );
    ensure!(
        (1..=7).contains(&registry.storage_version),
        "storage_version_unsupported"
    );
    Ok(registry)
}

fn save(state: &Path, registry: &Registry) -> Result<()> {
    let mut value = serde_json::to_value(registry)?;
    value["storageVersion"] = json!(7);
    atomic_write(state, &serde_json::to_vec(&value)?)
}

pub(crate) fn atomic_write(state: &Path, bytes: &[u8]) -> Result<()> {
    ensure!(bytes.len() <= 2 * 1024 * 1024, "registry_capacity");
    let temp = state.join(format!(".registry-{}", Uuid::new_v4()));
    // A failed write is not an authoritative record. Exact owned temporary only.
    let result = (|| {
        exclusive_write(&temp, bytes)?;
        fixture_fault(state, "before_rename")?;
        std::fs::rename(&temp, state.join("registry.json"))?;
        File::open(state)?.sync_all()?;
        Ok(())
    })();
    if temp.exists() {
        let _ = std::fs::remove_file(&temp);
    }
    result
}

#[cfg(feature = "fixture-faults")]
pub(crate) fn fixture_fault(state: &Path, point: &str) -> Result<()> {
    let arm = state.join(format!("fault.{point}"));
    if arm.try_exists()? {
        private_read(&arm, 0)?;
        exclusive_write(&state.join(format!("hit.{point}")), b"")?;
        // Private operator fixture hook, absent from production builds.
        for _ in 0..3000 {
            if !arm.try_exists()? {
                return Ok(());
            }
            std::thread::sleep(Duration::from_millis(10));
        }
        bail!("fixture_fault_timeout");
    }
    Ok(())
}
#[cfg(not(feature = "fixture-faults"))]
pub(crate) fn fixture_fault(_state: &Path, _point: &str) -> Result<()> {
    Ok(())
}

fn key(state: &Path) -> Result<Vec<u8>> {
    let bytes = private_read(&state.join("service.key"), 32)?;
    ensure!(bytes.len() == 32, "service_key_invalid");
    Ok(bytes)
}

fn signature(key: &[u8], seat: &Seat) -> Result<HmacSha256> {
    mac_for(key, &seat.grant, &seat.nonce, seat.expires_at)
}

fn mac_for(
    key: &[u8],
    grant: &protocol::Grant,
    nonce: &str,
    expires_at: u64,
) -> Result<HmacSha256> {
    let mut mac = HmacSha256::new_from_slice(key).context("service_key_invalid")?;
    mac.update(&serde_json::to_vec(&(
        "ignibyte-factory-credential-v1",
        grant,
        nonce,
        expires_at,
    ))?);
    Ok(mac)
}

fn authenticated(
    registry: &Registry,
    key: &[u8],
    peer: Peer,
    auth: &Auth,
    time: u64,
) -> Result<protocol::Grant> {
    let record = registry
        .seats
        .get(&auth.seat)
        .context("credential_denied")?;
    let bytes = unhex(&auth.credential)?;
    let current = signature(key, record)?.verify_slice(&bytes).is_ok();
    let previous = match &record.previous {
        Some(old) if old.valid_until > time && old.expires_at > time => {
            mac_for(key, &record.grant, &old.nonce, old.expires_at)?
                .verify_slice(&bytes)
                .is_ok()
        }
        _ => false,
    };
    ensure!(current || previous, "credential_denied");
    ensure!(!record.revoked, "credential_revoked");
    ensure!(!current || record.expires_at > time, "credential_expired");
    ensure!(record.grant.host == peer.host, "authenticated_host_denied");
    let mut authority = record.grant.clone();
    if auth.request.operation == protocol::Operation::Queue
        && routes::permits(registry, &auth.request, None, time)
    {
        authority.recipients.insert(auth.request.recipient);
    }
    protocol::authorize(&authority, peer.uid, record.last_sequence, &auth.request)?;
    Ok(record.grant.clone())
}

fn audit(registry: &mut Registry, event: Value) {
    registry.audit_sequence = registry.audit_sequence.saturating_add(1);
    if registry.audit.len() >= 512 {
        registry.audit.remove(0);
    }
    registry
        .audit
        .push(json!({"sequence": registry.audit_sequence, "event": event}));
}

/// Trusted service-owner operation; neither credential is returned on stdout.
pub fn rotate(state: &Path, seat: Uuid, output: &Path, ttl: u64, overlap: u64) -> Result<Value> {
    ensure!(
        (1..=3600).contains(&ttl) && overlap <= 60,
        "credential_ttl_bound"
    );
    let _lock = lock(state)?;
    let mut registry = load(state)?;
    let record = registry.seats.get_mut(&seat).context("seat_unknown")?;
    ensure!(!record.revoked, "credential_revoked");
    let time = now()?;
    record.previous = Some(Previous {
        nonce: record.nonce.clone(),
        expires_at: record.expires_at,
        valid_until: (time + overlap).min(record.expires_at),
    });
    record.nonce = hex(&random()?);
    record.expires_at = time + ttl;
    let credential = hex(&signature(&key(state)?, record)?.finalize().into_bytes());
    let response = json!({"ok":true,"seat":seat,"incarnation":record.grant.incarnation,
        "expiresAt":record.expires_at,"credential":credential});
    exclusive_write(output, &serde_json::to_vec(&response)?)?;
    File::open(output.parent().context("output_parent_missing")?)?.sync_all()?;
    audit(
        &mut registry,
        json!({"kind":"rotated","seat":seat,"at":time,"overlapSeconds":overlap,
        "grant":"preserved","sequence":"preserved"}),
    );
    save(state, &registry)?;
    Ok(json!({"seat":seat,"credentialFile":output,"overlapSeconds":overlap}))
}

pub fn revoke(state: &Path, seat: Uuid) -> Result<Value> {
    let _lock = lock(state)?;
    let mut registry = load(state)?;
    let record = registry.seats.get_mut(&seat).context("seat_unknown")?;
    record.revoked = true;
    record.previous = None;
    let mut pending = 0;
    let mut delivered = 0;
    for mail in registry
        .mail
        .values_mut()
        .filter(|m| m.envelope.sender == seat || m.envelope.recipient == seat)
    {
        if mail.state == "service_accepted" {
            mail.state = "quarantined".into();
            pending += 1;
        } else if mail.state == "delivered" || mail.state == "consumed" {
            mail.reconcile_required = true;
            delivered += 1;
        }
    }
    let event = json!({"kind":"revoked","seat":seat,"at":now()?,"pendingQuarantined":pending,
        "deliveredRequiresReconciliation":delivered,"completedEffects":"not_reversed",
        "authorizationBound":"each frame; maximum read wait 3 seconds"});
    audit(&mut registry, event.clone());
    save(state, &registry)?;
    Ok(event)
}

pub fn inspect(state: &Path) -> Result<Value> {
    let _lock = lock(state)?;
    let registry = load(state)?;
    Ok(
        json!({"protocol":registry.protocol,"host":registry.host,"remoteHosts":registry.remote_hosts,"audit":registry.audit,
        "auditSequence":registry.audit_sequence,"auditGapBefore":registry.audit.first().map(|e| &e["sequence"]),
        "seats":registry.seats.iter().map(|(id,s)| json!({"seat":id,"grant":s.grant,
            "revoked":s.revoked,"lastSequence":s.last_sequence,"expiresAt":s.expires_at})).collect::<Vec<_>>(),
        "mail":registry.mail.values().map(|m| json!({"messageId":m.envelope.message_id,"sender":m.envelope.sender,
            "recipient":m.envelope.recipient,"state":m.state,"reconcileRequired":m.reconcile_required,"cancellation":m.cancellation,
            "receipts":m.receipts})).collect::<Vec<_>>()}),
    )
}

/// Trusted service-owner dispatch. Agent callers cannot read this directory or mint this MAC.
pub(crate) fn operator_send(state: &Path, envelope: protocol::Envelope) -> Result<Value> {
    operator_send_role(state, envelope, protocol::Role::Foreman)
}

pub(crate) fn operator_handoff(state: &Path, envelope: protocol::Envelope) -> Result<Value> {
    ensure!(
        envelope.kind == protocol::MessageKind::Note && envelope.assignment_id.is_none(),
        "manager_handoff_kind_denied"
    );
    operator_send_role(state, envelope, protocol::Role::Manager)
}

fn operator_send_role(
    state: &Path,
    envelope: protocol::Envelope,
    role: protocol::Role,
) -> Result<Value> {
    let (request, credential, agent_uid) = {
        let _lock = lock(state)?;
        lifecycle::check_boot(state)?;
        let registry = load(state)?;
        let sender = registry
            .seats
            .get(&envelope.sender)
            .context("foreman_sender_unknown")?;
        ensure!(
            sender.grant.role == role,
            if role == protocol::Role::Foreman {
                "foreman_role_required"
            } else {
                "manager_role_required"
            }
        );
        if role == protocol::Role::Manager {
            ensure!(
                registry
                    .seats
                    .get(&envelope.recipient)
                    .is_some_and(|recipient| recipient.grant.role == protocol::Role::Foreman
                        && !recipient.revoked
                        && recipient.expires_at > now().unwrap_or(u64::MAX)
                        && recipient.grant.projects.contains(&envelope.project)),
                "manager_handoff_recipient_denied"
            );
        }
        let request = crate::client::request(
            &sender.grant,
            protocol::Operation::Queue,
            envelope.recipient,
            &envelope.project,
            sender
                .last_sequence
                .checked_add(1)
                .context("sequence_exhausted")?,
        );
        let credential = hex(&signature(&key(state)?, sender)?.finalize().into_bytes());
        (request, credential, sender.grant.agent_uid)
    };
    handle(
        state,
        agent_uid,
        None,
        WireRequest::Send {
            protocol: protocol::VERSION,
            auth: Auth {
                seat: envelope.sender,
                credential,
                request,
            },
            envelope: Box::new(envelope),
        },
    )
}

pub(crate) fn operator_host(state: &Path) -> Result<Uuid> {
    let _lock = lock(state)?;
    lifecycle::check_boot(state)?;
    load(state)?.host.context("registry_uninitialized")
}

pub(crate) fn operator_grant(state: &Path, seat: Uuid) -> Result<protocol::Grant> {
    let _lock = lock(state)?;
    lifecycle::check_boot(state)?;
    let registry = load(state)?;
    let seat = registry.seats.get(&seat).context("foreman_seat_unknown")?;
    ensure!(
        !seat.revoked && seat.expires_at > now()?,
        "foreman_seat_inactive"
    );
    Ok(seat.grant.clone())
}

pub(crate) fn operator_spec_ids(
    state: &Path,
    project: &str,
) -> Result<BTreeMap<Uuid, std::collections::BTreeSet<Uuid>>> {
    let _lock = lock(state)?;
    lifecycle::check_boot(state)?;
    let registry = load(state)?;
    let mut result: BTreeMap<Uuid, std::collections::BTreeSet<Uuid>> = BTreeMap::new();
    for mail in registry.mail.values() {
        let envelope = &mail.envelope;
        if envelope.project == project
            && envelope.kind == protocol::MessageKind::Assignment
            && registry
                .seats
                .get(&envelope.sender)
                .is_some_and(|seat| seat.grant.role == protocol::Role::Foreman)
            && let Some(run) = envelope.assignment_id
        {
            result.entry(run).or_default().insert(envelope.message_id);
        }
    }
    Ok(result)
}

/// Operator-only bootstrap. The agent never receives service state or the MAC key.
fn bootstrap_inner(
    state: &Path,
    grant: protocol::Grant,
    output: &Path,
    bootstrap_ttl: u64,
    credential_ttl: u64,
    remote: bool,
) -> Result<Value> {
    grant.validate()?;
    ensure!(
        (remote || grant.agent_uid != uid()) && grant.agent_uid != 0 && grant.agent_uid != u32::MAX,
        "agent_service_uid_boundary"
    );
    ensure!(
        (1..=300).contains(&bootstrap_ttl) && (1..=3600).contains(&credential_ttl),
        "credential_ttl_bound"
    );
    ensure!(!output.try_exists()?, "bootstrap_output_exists");
    let _lock = lock(state)?;
    lifecycle::prepare_locked(state)?;
    let key_path = state.join("service.key");
    if !key_path.try_exists()? {
        exclusive_write(&key_path, &random()?)?;
        File::open(state)?.sync_all()?;
    }
    key(state)?;
    let mut registry = load(state)?;
    if remote {
        ensure!(
            registry.host.is_some_and(|host| host != grant.host),
            "remote_host_required"
        );
        let host = registry
            .remote_hosts
            .get(&grant.host)
            .context("host_not_admitted")?;
        ensure!(host.active(now()?), "host_credential_inactive");
        ensure!(
            grant.agent_uid != host.bridge_uid,
            "agent_service_uid_boundary"
        );
    } else {
        ensure!(
            registry.host.is_none_or(|host| host == grant.host),
            "host_registry_mismatch"
        );
    }
    ensure!(
        !registry.seats.contains_key(&grant.seat),
        "seat_already_enrolled"
    );
    ensure!(
        registry.seats.len() < 128 && registry.bootstraps.len() < 512,
        "registry_capacity"
    );
    let time = now()?;
    ensure!(
        !registry
            .bootstraps
            .values()
            .any(|b| b.grant.seat == grant.seat && !b.consumed && b.expires_at > time),
        "seat_enrollment_pending"
    );
    let secret = random()?;
    let hash = hex(&Sha256::digest(secret));
    if !remote {
        registry.host = Some(grant.host);
    }
    let expires_at = time + bootstrap_ttl;
    let seat = grant.seat;
    registry.bootstraps.insert(
        hash,
        Bootstrap {
            grant,
            expires_at,
            credential_ttl,
            consumed: false,
        },
    );
    save(state, &registry)?;
    exclusive_write(output, hex(&secret).as_bytes())?;
    Ok(json!({"bootstrapFile": output, "seat": seat, "expiresAt": expires_at}))
}

pub fn bootstrap(
    state: &Path,
    grant: protocol::Grant,
    output: &Path,
    bootstrap_ttl: u64,
    credential_ttl: u64,
) -> Result<Value> {
    bootstrap_inner(state, grant, output, bootstrap_ttl, credential_ttl, false)
}

pub fn bootstrap_remote(
    state: &Path,
    grant: protocol::Grant,
    output: &Path,
    bootstrap_ttl: u64,
    credential_ttl: u64,
) -> Result<Value> {
    bootstrap_inner(state, grant, output, bootstrap_ttl, credential_ttl, true)
}

/// The forced SSH command pins host and transport-key IDs; caller bytes never choose them.
pub(crate) fn remote_frame(
    state: &Path,
    host: Uuid,
    key: Uuid,
    peer_uid: u32,
    value: Value,
    deadline: Instant,
) -> Result<Value> {
    let wire = serde_json::from_value(value).map_err(|_| anyhow::anyhow!("invalid_request"))?;
    handle(state, peer_uid, Some((host, key, deadline)), wire)
}

pub fn admit_host(
    state: &Path,
    host: Uuid,
    key_id: Uuid,
    bridge_uid: u32,
    ttl: u64,
) -> Result<Value> {
    ensure!(
        !host.is_nil() && !key_id.is_nil() && bridge_uid != 0 && bridge_uid != u32::MAX,
        "host_identity_invalid"
    );
    ensure!((1..=86400).contains(&ttl), "host_ttl_bound");
    let _lock = lock(state)?;
    let mut registry = load(state)?;
    ensure!(
        registry.host.is_some_and(|local| local != host),
        "remote_host_required"
    );
    ensure!(
        registry.remote_hosts.len() < 32 && !registry.remote_hosts.contains_key(&host),
        "host_already_admitted_or_capacity"
    );
    let time = now()?.max(registry.observed_time);
    registry.observed_time = time;
    let expires = time.checked_add(ttl).context("host_time_exhausted")?;
    registry.remote_hosts.insert(
        host,
        RemoteHost {
            bridge_uid,
            current_key: key_id,
            expires_at: expires,
            previous_key: None,
            used_keys: std::collections::BTreeSet::from([key_id]),
            revoked: false,
        },
    );
    audit(
        &mut registry,
        json!({"kind":"host_admitted","host":host,"keyId":key_id,"at":time}),
    );
    save(state, &registry)?;
    Ok(json!({"ok":true,"host":host,"keyId":key_id,"expiresAt":expires}))
}

pub fn rotate_host(
    state: &Path,
    host: Uuid,
    key_id: Uuid,
    ttl: u64,
    overlap: u64,
) -> Result<Value> {
    ensure!(
        !key_id.is_nil() && (1..=86400).contains(&ttl) && overlap <= 60,
        "host_rotation_bound"
    );
    let _lock = lock(state)?;
    let mut registry = load(state)?;
    let time = now()?.max(registry.observed_time);
    registry.observed_time = time;
    let record = registry
        .remote_hosts
        .get_mut(&host)
        .context("host_not_admitted")?;
    ensure!(
        !record.revoked && !record.used_keys.contains(&key_id) && record.used_keys.len() < 32,
        "host_rotation_denied"
    );
    record.previous_key = if overlap > 0 {
        Some((
            record.current_key,
            record
                .expires_at
                .min(time.checked_add(overlap).context("host_time_exhausted")?),
        ))
    } else {
        None
    };
    record.current_key = key_id;
    record.expires_at = time.checked_add(ttl).context("host_time_exhausted")?;
    record.used_keys.insert(key_id);
    audit(
        &mut registry,
        json!({"kind":"host_key_rotated","host":host,"keyId":key_id,"at":time,"overlapSeconds":overlap}),
    );
    save(state, &registry)?;
    Ok(json!({"ok":true,"host":host,"keyId":key_id,"overlapSeconds":overlap}))
}

pub fn revoke_host(state: &Path, host: Uuid) -> Result<Value> {
    let _lock = lock(state)?;
    let mut registry = load(state)?;
    let record = registry
        .remote_hosts
        .get_mut(&host)
        .context("host_not_admitted")?;
    record.revoked = true;
    record.previous_key = None;
    let mut seats = std::collections::BTreeSet::new();
    for (id, seat) in &mut registry.seats {
        if seat.grant.host == host {
            seat.revoked = true;
            seat.previous = None;
            seats.insert(*id);
        }
    }
    let mut pending = 0;
    let mut active = 0;
    for mail in registry.mail.values_mut().filter(|mail| {
        seats.contains(&mail.envelope.sender) || seats.contains(&mail.envelope.recipient)
    }) {
        if mail.state == "service_accepted" {
            mail.state = "quarantined".into();
            pending += 1;
        } else if mail.state == "delivered" || mail.state == "consumed" {
            mail.reconcile_required = true;
            active += 1;
        }
    }
    let event = json!({"ok":true,"kind":"host_revoked","host":host,"seatsRevoked":seats.len(),"pendingQuarantined":pending,
        "activeRequiresReconciliation":active,"completedEffects":"not_reversed","authorizationBound":"each frame; one request per SSH channel"});
    audit(&mut registry, event.clone());
    save(state, &registry)?;
    Ok(event)
}

fn handle(
    state: &Path,
    peer_uid: u32,
    remote: Option<(Uuid, Uuid, Instant)>,
    wire: WireRequest,
) -> Result<Value> {
    let version = match &wire {
        WireRequest::ManagerReadResult { protocol, .. }
        | WireRequest::ForemanAction { protocol, .. }
        | WireRequest::ReadAssignmentSpec { protocol, .. }
        | WireRequest::ReadHandoff { protocol, .. }
        | WireRequest::ManagerHandoff { protocol, .. }
        | WireRequest::ManagerOperation { protocol, .. }
        | WireRequest::ManagerUpload { protocol, .. }
        | WireRequest::ManagerRead { protocol, .. }
        | WireRequest::ManagerCatalogue { protocol, .. }
        | WireRequest::ManagerSpec { protocol, .. }
        | WireRequest::ReportBlockage { protocol, .. }
        | WireRequest::BlockageDecision { protocol, .. }
        | WireRequest::AssignmentResult { protocol, .. }
        | WireRequest::AcknowledgeContext { protocol, .. }
        | WireRequest::AuthorizeAssignment { protocol, .. }
        | WireRequest::ReportPresence { protocol, .. }
        | WireRequest::ObserveWork { protocol, .. }
        | WireRequest::Enroll { protocol, .. }
        | WireRequest::Authorize { protocol, .. }
        | WireRequest::Send { protocol, .. }
        | WireRequest::Receive { protocol, .. }
        | WireRequest::Cancel { protocol, .. }
        | WireRequest::CancelResult { protocol, .. }
        | WireRequest::Peers { protocol, .. }
        | WireRequest::Inbox { protocol, .. }
        | WireRequest::Sync { protocol, .. }
        | WireRequest::Ack { protocol, .. }
        | WireRequest::RecordNative { protocol, .. }
        | WireRequest::RecordingStatus { protocol, .. }
        | WireRequest::Status { protocol, .. } => *protocol,
    };
    ensure!(version == protocol::VERSION, "protocol_unsupported");
    let _lock = if let Some((_, _, deadline)) = remote {
        lock_until(state, deadline)?
    } else {
        lock(state)?
    };
    lifecycle::check_boot(state)?;
    let mut registry = load(state)?;
    let key = key(state)?;
    let time = now()?.max(registry.observed_time);
    registry.observed_time = time;
    if routes::expire(&mut registry, time) {
        save(state, &registry)?;
    }
    let peer = if let Some((host, key, deadline)) = remote {
        ensure!(Instant::now() < deadline, "network_timeout");
        let record = registry
            .remote_hosts
            .get(&host)
            .context("host_not_admitted")?;
        ensure!(record.permits(key, time), "host_credential_inactive");
        ensure!(
            peer_uid != 0 && peer_uid != record.bridge_uid && peer_uid != u32::MAX,
            "host_agent_uid_denied"
        );
        Peer {
            uid: peer_uid,
            host,
            remote: true,
        }
    } else {
        Peer {
            uid: peer_uid,
            host: registry.host.context("registry_uninitialized")?,
            remote: false,
        }
    };
    let listing_peers = matches!(&wire, WireRequest::Peers { .. });
    match wire {
        WireRequest::ManagerUpload {
            auth,
            job_id,
            chunk,
            ..
        } => {
            let grant = authenticated(&registry, &key, peer, &auth, time)?;
            ensure!(
                grant.role == protocol::Role::Manager
                    && auth.request.operation == protocol::Operation::Acknowledge
                    && auth.request.recipient == grant.seat,
                "manager_caller_denied"
            );
            registry
                .seats
                .get_mut(&grant.seat)
                .context("credential_denied")?
                .last_sequence = auth.request.sequence;
            save(state, &registry)?;
            drop(_lock);
            crate::manager::upload(
                &state.join("manager"),
                &grant,
                &auth.request.project,
                job_id,
                chunk,
            )
        }
        WireRequest::ManagerReadResult {
            auth, job_id, read, ..
        } => {
            let grant = authenticated(&registry, &key, peer, &auth, time)?;
            ensure!(
                grant.role == protocol::Role::Manager
                    && auth.request.operation == protocol::Operation::Observe
                    && auth.request.recipient == grant.seat,
                "manager_caller_denied"
            );
            registry
                .seats
                .get_mut(&grant.seat)
                .context("credential_denied")?
                .last_sequence = auth.request.sequence;
            save(state, &registry)?;
            drop(_lock);
            crate::manager::read_result(
                &state.join("manager"),
                &grant,
                &auth.request.project,
                job_id,
                read,
            )
        }
        WireRequest::ManagerHandoff {
            auth,
            job_id,
            operation_id,
            ..
        } => {
            let grant = authenticated(&registry, &key, peer, &auth, time)?;
            ensure!(
                grant.role == protocol::Role::Manager
                    && auth.request.operation == protocol::Operation::Queue
                    && auth.request.recipient == grant.seat,
                "manager_caller_denied"
            );
            registry
                .seats
                .get_mut(&grant.seat)
                .context("credential_denied")?
                .last_sequence = auth.request.sequence;
            save(state, &registry)?;
            drop(_lock);
            crate::manager::handoff::approve(
                &state.join("manager"),
                &grant,
                &auth.request.project,
                job_id,
                operation_id,
            )
        }
        WireRequest::ReadHandoff { auth, read, .. } => {
            let grant = authenticated(&registry, &key, peer, &auth, time)?;
            ensure!(
                grant.role == protocol::Role::Foreman
                    && auth.request.operation == (protocol::Operation::Observe)
                    && auth.request.recipient == grant.seat,
                "foreman_caller_denied"
            );
            registry
                .seats
                .get_mut(&grant.seat)
                .context("credential_denied")?
                .last_sequence = auth.request.sequence;
            save(state, &registry)?;
            drop(_lock);
            crate::foreman::managed::read_handoff(state, &grant, &auth.request.project, read)
        }
        WireRequest::ReadAssignmentSpec { auth, read, .. } => {
            let grant = authenticated(&registry, &key, peer, &auth, time)?;
            ensure!(
                grant.role == protocol::Role::Worker
                    && auth.request.operation == (protocol::Operation::Observe)
                    && auth.request.recipient == grant.seat,
                "foreman_caller_denied"
            );
            registry
                .seats
                .get_mut(&grant.seat)
                .context("credential_denied")?
                .last_sequence = auth.request.sequence;
            save(state, &registry)?;
            drop(_lock);
            crate::foreman::managed::read(
                &state.join("foreman"),
                &grant,
                &auth.request.project,
                read,
            )
        }
        WireRequest::ForemanAction { auth, action, .. } => {
            let grant = authenticated(&registry, &key, peer, &auth, time)?;
            ensure!(
                grant.role == protocol::Role::Foreman
                    && auth.request.operation
                        == (if matches!(&action, crate::foreman::Action::Status { .. }) {
                            protocol::Operation::Observe
                        } else {
                            protocol::Operation::Acknowledge
                        })
                    && auth.request.recipient == grant.seat,
                "foreman_caller_denied"
            );
            registry
                .seats
                .get_mut(&grant.seat)
                .context("credential_denied")?
                .last_sequence = auth.request.sequence;
            save(state, &registry)?;
            drop(_lock);
            crate::foreman::managed::action(state, &grant, &auth.request.project, action)
        }
        WireRequest::ManagerOperation {
            auth,
            job_id,
            operation_id,
            ..
        } => {
            let grant = authenticated(&registry, &key, peer, &auth, time)?;
            ensure!(
                grant.role == protocol::Role::Manager
                    && auth.request.operation == protocol::Operation::Observe
                    && auth.request.recipient == grant.seat,
                "manager_caller_denied"
            );
            registry
                .seats
                .get_mut(&grant.seat)
                .context("credential_denied")?
                .last_sequence = auth.request.sequence;
            save(state, &registry)?;
            drop(_lock);
            crate::manager::status(
                &state.join("manager"),
                &grant,
                &auth.request.project,
                job_id,
                operation_id,
            )
        }
        WireRequest::ManagerRead {
            auth,
            job_id,
            operation_id,
            offset,
            ..
        } => {
            let grant = authenticated(&registry, &key, peer, &auth, time)?;
            ensure!(
                grant.role == protocol::Role::Manager
                    && auth.request.operation == protocol::Operation::Observe
                    && auth.request.recipient == grant.seat,
                "manager_caller_denied"
            );
            registry
                .seats
                .get_mut(&grant.seat)
                .context("credential_denied")?
                .last_sequence = auth.request.sequence;
            save(state, &registry)?;
            drop(_lock);
            crate::manager::read(
                &state.join("manager"),
                &grant,
                &auth.request.project,
                job_id,
                operation_id,
                offset,
            )
        }
        WireRequest::ManagerCatalogue {
            auth,
            job_id,
            offset,
            ..
        } => {
            let grant = authenticated(&registry, &key, peer, &auth, time)?;
            ensure!(
                grant.role == protocol::Role::Manager
                    && auth.request.operation == protocol::Operation::Observe
                    && auth.request.recipient == grant.seat,
                "manager_caller_denied"
            );
            registry
                .seats
                .get_mut(&grant.seat)
                .context("credential_denied")?
                .last_sequence = auth.request.sequence;
            save(state, &registry)?;
            drop(_lock);
            crate::manager::catalogue(
                &state.join("manager"),
                &grant,
                &auth.request.project,
                job_id,
                offset,
            )
        }
        WireRequest::ManagerSpec {
            auth,
            job_id,
            operation_id,
            tool,
            arguments,
            ..
        } => {
            let grant = authenticated(&registry, &key, peer, &auth, time)?;
            ensure!(
                grant.role == protocol::Role::Manager
                    && auth.request.operation == protocol::Operation::Acknowledge
                    && auth.request.recipient == grant.seat,
                "manager_caller_denied"
            );
            registry
                .seats
                .get_mut(&grant.seat)
                .context("credential_denied")?
                .last_sequence = auth.request.sequence;
            save(state, &registry)?;
            drop(_lock);
            crate::manager::call(
                &state.join("manager"),
                &grant,
                &auth.request.project,
                job_id,
                operation_id,
                &tool,
                arguments,
            )
        }
        WireRequest::ReportBlockage {
            auth,
            run_id,
            report,
            ..
        } => {
            let grant = authenticated(&registry, &key, peer, &auth, time)?;
            ensure!(
                grant.role == protocol::Role::Worker
                    && auth.request.operation == protocol::Operation::Acknowledge
                    && auth.request.recipient == grant.seat,
                "supervision_report_scope_denied"
            );
            let response = crate::foreman::supervision::report(
                &state.join("foreman"),
                &grant,
                run_id,
                report,
            )?;
            registry
                .seats
                .get_mut(&grant.seat)
                .context("credential_denied")?
                .last_sequence = auth.request.sequence;
            save(state, &registry)?;
            Ok(response)
        }
        WireRequest::BlockageDecision {
            auth,
            run_id,
            notice_id,
            decision,
            ..
        } => {
            let grant = authenticated(&registry, &key, peer, &auth, time)?;
            ensure!(
                grant.role == protocol::Role::Foreman
                    && auth.request.operation == protocol::Operation::Acknowledge
                    && auth.request.recipient == grant.seat,
                "supervision_decision_scope_denied"
            );
            let response = crate::foreman::supervision::decide(
                &state.join("foreman"),
                &grant,
                run_id,
                notice_id,
                decision,
            )?;
            registry
                .seats
                .get_mut(&grant.seat)
                .context("credential_denied")?
                .last_sequence = auth.request.sequence;
            save(state, &registry)?;
            Ok(response)
        }
        WireRequest::AssignmentResult {
            auth,
            run_id,
            thread_id,
            turn_id,
            artifacts,
            ..
        } => {
            let grant = authenticated(&registry, &key, peer, &auth, time)?;
            ensure!(
                grant.role == protocol::Role::Worker
                    && auth.request.operation == protocol::Operation::Acknowledge
                    && auth.request.recipient == grant.seat,
                "foreman_result_scope_denied"
            );
            let response = crate::foreman::result(
                &state.join("foreman"),
                &grant,
                run_id,
                thread_id,
                turn_id,
                artifacts,
            )?;
            registry
                .seats
                .get_mut(&grant.seat)
                .context("credential_denied")?
                .last_sequence = auth.request.sequence;
            save(state, &registry)?;
            Ok(response)
        }
        WireRequest::AcknowledgeContext {
            auth,
            run_id,
            context_digest,
            ..
        } => {
            let grant = authenticated(&registry, &key, peer, &auth, time)?;
            ensure!(
                grant.role == protocol::Role::Worker
                    && auth.request.operation == protocol::Operation::Acknowledge
                    && auth.request.recipient == grant.seat,
                "foreman_ack_scope_denied"
            );
            let response = crate::foreman::acknowledge(
                &state.join("foreman"),
                &grant,
                run_id,
                &context_digest,
            )?;
            registry
                .seats
                .get_mut(&grant.seat)
                .context("credential_denied")?
                .last_sequence = auth.request.sequence;
            save(state, &registry)?;
            Ok(response)
        }
        WireRequest::AuthorizeAssignment {
            auth,
            message_id,
            cwd,
            ..
        } => {
            let grant = authenticated(&registry, &key, peer, &auth, time)?;
            ensure!(
                grant.role == protocol::Role::Worker
                    && auth.request.operation == protocol::Operation::Observe
                    && auth.request.recipient == grant.seat,
                "foreman_execution_scope_denied"
            );
            let response =
                crate::foreman::authorize(&state.join("foreman"), &grant, message_id, &cwd)?;
            registry
                .seats
                .get_mut(&grant.seat)
                .context("credential_denied")?
                .last_sequence = auth.request.sequence;
            save(state, &registry)?;
            Ok(response)
        }
        WireRequest::ReportPresence { auth, report, .. } => {
            let grant = authenticated(&registry, &key, peer, &auth, time)?;
            ensure!(
                auth.request.operation == protocol::Operation::Acknowledge
                    && auth.request.recipient == auth.seat,
                "observation_report_scope_denied"
            );
            let response = crate::observation::report(
                &state.join("observation"),
                &grant,
                &auth.request.project,
                report,
                time,
                peer.remote,
            )?;
            registry
                .seats
                .get_mut(&auth.seat)
                .context("credential_denied")?
                .last_sequence = auth.request.sequence;
            save(state, &registry)?;
            Ok(response)
        }
        WireRequest::ObserveWork {
            auth,
            after,
            agent_id,
            ..
        } => {
            let grant = authenticated(&registry, &key, peer, &auth, time)?;
            ensure!(
                auth.request.operation == protocol::Operation::Observe
                    && auth.request.recipient == auth.seat,
                "observation_reader_scope_denied"
            );
            let known: Vec<_> = registry
                .seats
                .values()
                .map(|seat| crate::observation::KnownSeat {
                    grant: seat.grant.clone(),
                    revoked: seat.revoked
                        || (Some(seat.grant.host) != registry.host
                            && registry
                                .remote_hosts
                                .get(&seat.grant.host)
                                .is_none_or(|host| !host.active(time))),
                })
                .collect();
            let response = crate::observation::read(
                &state.join("observation"),
                &grant,
                &auth.request.project,
                after,
                agent_id,
                &known,
                time,
            )?;
            registry
                .seats
                .get_mut(&auth.seat)
                .context("credential_denied")?
                .last_sequence = auth.request.sequence;
            save(state, &registry)?;
            Ok(response)
        }
        WireRequest::RecordNative { auth, record, .. } => {
            let grant = authenticated(&registry, &key, peer, &auth, time)?;
            ensure!(
                auth.request.operation == protocol::Operation::Acknowledge
                    && auth.request.recipient == auth.seat,
                "recording_scope_denied"
            );
            let mail = registry
                .mail
                .get(&record.message_id)
                .context("message_unknown")?;
            ensure!(
                mail.envelope.recipient == auth.seat
                    && mail.envelope.project == auth.request.project,
                "recording_scope_denied"
            );
            ensure!(
                !record.thread_id.is_nil() && !record.turn_id.is_nil(),
                "recording_native_identity_invalid"
            );
            let required = if record.outcome == crate::recording::Outcome::Completed {
                protocol::DeliveryState::Completed
            } else {
                protocol::DeliveryState::Consumed
            };
            let turn = record.turn_id.to_string();
            ensure!(
                mail.receipts.iter().any(|receipt| receipt.state == required
                    && receipt.turn_id.as_deref() == Some(turn.as_str())),
                "recording_receipt_unproven"
            );
            let event = crate::recording::Event {
                schema: 1,
                host: grant.host,
                seat: grant.seat,
                incarnation: grant.incarnation,
                project: mail.envelope.project.clone(),
                sender: mail.envelope.sender,
                message_id: mail.envelope.message_id,
                assignment_id: mail.envelope.assignment_id,
                conversation_id: mail.envelope.conversation_id,
                thread_id: record.thread_id,
                turn_id: record.turn_id,
                outcome: record.outcome,
                source_created_at: mail.envelope.created_at,
                authority:
                    crate::recording::EventAuthority::AuthenticatedNativeRecipientAttestation,
            };
            let response = crate::recording::accept(&state.join("recording"), event.clone(), time)?;
            let id = serde_json::from_value(response["eventId"].clone())?;
            crate::observation::terminal(&state.join("observation"), id, event, time)?;
            registry
                .seats
                .get_mut(&auth.seat)
                .context("credential_denied")?
                .last_sequence = auth.request.sequence;
            save(state, &registry)?;
            Ok(response)
        }
        WireRequest::RecordingStatus { auth, .. } => {
            authenticated(&registry, &key, peer, &auth, time)?;
            ensure!(
                auth.request.operation == protocol::Operation::Observe
                    && auth.request.recipient == auth.seat,
                "recording_scope_denied"
            );
            let response = crate::recording::scoped_status(
                &state.join("recording"),
                auth.seat,
                &auth.request.project,
            )?;
            registry
                .seats
                .get_mut(&auth.seat)
                .context("credential_denied")?
                .last_sequence = auth.request.sequence;
            save(state, &registry)?;
            Ok(response)
        }
        WireRequest::Cancel {
            auth, message_id, ..
        } => {
            let grant = authenticated(&registry, &key, peer, &auth, time)?;
            ensure!(
                grant.role != protocol::Role::Support,
                "support_interrupt_denied"
            );
            ensure!(
                auth.request.operation == protocol::Operation::Queue,
                "cancel_scope_denied"
            );
            ensure!(
                registry
                    .mail
                    .get(&message_id)
                    .is_none_or(|mail| routes::mail_permitted(&registry, mail, time)),
                "route_task_denied"
            );
            let mail = registry
                .mail
                .get_mut(&message_id)
                .context("message_unknown")?;
            ensure!(
                mail.envelope.sender == auth.seat
                    && mail.envelope.recipient == auth.request.recipient
                    && mail.envelope.project == auth.request.project,
                "cancel_scope_denied"
            );
            if mail.cancellation.is_none() {
                mail.cancellation = Some(Cancellation {
                    requested_by: auth.seat,
                    requested_at: time,
                    state: "requested".into(),
                    turn_id: None,
                    reason: None,
                });
            }
            let response = json!({"ok":true,"cancellation":mail.cancellation});
            registry
                .seats
                .get_mut(&auth.seat)
                .context("credential_denied")?
                .last_sequence = auth.request.sequence;
            save(state, &registry)?;
            Ok(response)
        }
        WireRequest::CancelResult {
            auth,
            message_id,
            state: outcome,
            turn_id,
            reason,
            ..
        } => {
            authenticated(&registry, &key, peer, &auth, time)?;
            ensure!(
                auth.request.operation == protocol::Operation::Acknowledge
                    && auth.request.recipient == auth.seat,
                "cancel_result_scope_denied"
            );
            ensure!(
                ["applied", "too_late", "uncertain"].contains(&outcome.as_str())
                    && !reason.is_empty()
                    && reason.len() <= 128
                    && turn_id
                        .as_ref()
                        .is_none_or(|t| !t.is_empty() && t.len() <= 128),
                "cancel_result_invalid"
            );
            let mail = registry
                .mail
                .get_mut(&message_id)
                .context("message_unknown")?;
            ensure!(
                mail.envelope.recipient == auth.seat
                    && mail.envelope.project == auth.request.project,
                "cancel_result_scope_denied"
            );
            let cancellation = mail.cancellation.as_mut().context("cancel_not_requested")?;
            ensure!(
                cancellation.state == "requested"
                    || cancellation.state == "uncertain"
                    || (cancellation.state == outcome && cancellation.turn_id == turn_id),
                "cancel_result_conflict"
            );
            cancellation.state = outcome;
            cancellation.turn_id = turn_id;
            cancellation.reason = Some(reason);
            let response = json!({"ok":true,"cancellation":cancellation});
            registry
                .seats
                .get_mut(&auth.seat)
                .context("credential_denied")?
                .last_sequence = auth.request.sequence;
            save(state, &registry)?;
            Ok(response)
        }
        WireRequest::Peers { auth, .. } | WireRequest::Inbox { auth, .. } => {
            let peers = listing_peers;
            let grant = authenticated(&registry, &key, peer, &auth, time)?;
            ensure!(
                auth.request.operation == protocol::Operation::Observe
                    && auth.request.recipient == auth.seat,
                "discovery_scope_denied"
            );
            let response = if peers {
                json!({"ok":true,"peers":registry.seats.values().filter(|s| !s.revoked && routes::contact(&registry, &grant, s.grant.seat, &auth.request.project, time) && s.grant.projects.contains(&auth.request.project))
                .map(|s| json!({"seat":s.grant.seat,"incarnation":s.grant.incarnation})).collect::<Vec<_>>()})
            } else {
                json!({"ok":true,"messages":registry.mail.values().filter(|m| m.envelope.recipient == auth.seat && m.envelope.project == auth.request.project)
                .map(|m| json!({"messageId":m.envelope.message_id,"sender":m.envelope.sender,"mode":m.envelope.mode,"state":m.state})).collect::<Vec<_>>()})
            };
            registry
                .seats
                .get_mut(&auth.seat)
                .context("credential_denied")?
                .last_sequence = auth.request.sequence;
            save(state, &registry)?;
            Ok(response)
        }
        WireRequest::Sync { mut auth, .. } => {
            let last = registry
                .seats
                .get(&auth.seat)
                .context("credential_denied")?
                .last_sequence;
            ensure!(
                auth.request.operation == protocol::Operation::Observe
                    && auth.request.recipient == auth.seat,
                "sync_scope_denied"
            );
            auth.request.sequence = last.checked_add(1).context("sequence_exhausted")?;
            let grant = authenticated(&registry, &key, peer, &auth, time)?;
            Ok(json!({"ok":true,"lastSequence":last,"grant":grant}))
        }
        WireRequest::Status {
            auth, message_id, ..
        } => {
            // A conversation exception permits its sender to read delivery
            // receipts, not another seat's work or unrelated conversations.
            let mut receipt_auth = Auth {
                seat: auth.seat,
                credential: auth.credential.clone(),
                request: auth.request.clone(),
            };
            if registry
                .seats
                .get(&auth.seat)
                .is_some_and(|s| !s.grant.recipients.contains(&auth.request.recipient))
            {
                receipt_auth.request.operation = protocol::Operation::Queue;
            }
            let grant = authenticated(&registry, &key, peer, &receipt_auth, time)?;
            ensure!(
                auth.request.operation == protocol::Operation::Observe
                    && grant.operations.contains(&protocol::Operation::Observe),
                "status_scope_denied"
            );
            let mail = registry.mail.get(&message_id).context("message_unknown")?;
            ensure!(
                routes::mail_permitted(&registry, mail, time),
                "route_task_denied"
            );
            ensure!(
                (mail.envelope.sender == auth.seat || mail.envelope.recipient == auth.seat)
                    && mail.envelope.project == auth.request.project
                    && mail.envelope.recipient == auth.request.recipient,
                "status_scope_denied"
            );
            let response = json!({"ok":true,"state":mail.state,"receipts":mail.receipts,"reconcileRequired":mail.reconcile_required,"sender":mail.envelope.sender,"conversationId":mail.envelope.conversation_id,"cancellation":mail.cancellation});
            registry
                .seats
                .get_mut(&auth.seat)
                .context("credential_denied")?
                .last_sequence = auth.request.sequence;
            save(state, &registry)?;
            Ok(response)
        }
        WireRequest::Ack { auth, receipt, .. } => {
            authenticated(&registry, &key, peer, &auth, time)?;
            ensure!(
                auth.request.operation == protocol::Operation::Acknowledge
                    && auth.request.recipient == auth.seat,
                "ack_scope_denied"
            );
            receipt.validate()?;
            let mail = registry
                .mail
                .get_mut(&receipt.message_id)
                .context("message_unknown")?;
            ensure!(
                mail.envelope.recipient == auth.seat
                    && mail.envelope.project == auth.request.project,
                "ack_scope_denied"
            );
            let next = match receipt.state {
                protocol::DeliveryState::Consumed => "consumed",
                protocol::DeliveryState::Completed => "completed",
                _ => bail!("ack_state_denied"),
            };
            let previous = if next == "consumed" {
                "delivered"
            } else {
                "consumed"
            };
            if let Some(existing) = mail.receipts.iter().find(|r| r.state == receipt.state) {
                ensure!(existing.turn_id == receipt.turn_id, "receipt_turn_conflict");
            } else {
                ensure!(mail.state == previous, "receipt_order_denied");
                if next == "completed" {
                    ensure!(
                        mail.receipts
                            .last()
                            .is_some_and(|r| r.turn_id == receipt.turn_id),
                        "receipt_turn_conflict"
                    );
                }
                mail.state = next.into();
                mail.receipts.push(receipt);
            }
            let response = json!({"ok":true,"state":mail.state,"receipts":mail.receipts});
            registry
                .seats
                .get_mut(&auth.seat)
                .context("credential_denied")?
                .last_sequence = auth.request.sequence;
            save(state, &registry)?;
            Ok(response)
        }
        WireRequest::Enroll { bootstrap, .. } => {
            let hash = hex(&Sha256::digest(unhex(&bootstrap)?));
            let pending = registry
                .bootstraps
                .get(&hash)
                .context("bootstrap_unknown")?
                .clone();
            ensure!(!pending.consumed, "bootstrap_reused");
            ensure!(pending.expires_at > time, "bootstrap_expired");
            ensure!(pending.grant.host == peer.host, "authenticated_host_denied");
            ensure!(pending.grant.agent_uid == peer.uid, "peer_uid_denied");
            ensure!(
                !registry.seats.contains_key(&pending.grant.seat),
                "seat_already_enrolled"
            );
            let record = Seat {
                grant: pending.grant,
                nonce: hex(&random()?),
                expires_at: time + pending.credential_ttl,
                last_sequence: 0,
                previous: None,
                revoked: false,
            };
            let credential = hex(&signature(&key, &record)?.finalize().into_bytes());
            let result = json!({"ok": true, "seat": record.grant.seat,
                "incarnation": record.grant.incarnation, "expiresAt": record.expires_at,
                "credential": credential});
            registry
                .bootstraps
                .get_mut(&hash)
                .context("bootstrap_unknown")?
                .consumed = true;
            registry.seats.insert(record.grant.seat, record);
            save(state, &registry)?;
            Ok(result)
        }
        WireRequest::Authorize {
            seat,
            credential,
            request,
            ..
        } => {
            let auth = Auth {
                seat,
                credential,
                request,
            };
            authenticated(&registry, &key, peer, &auth, time)?;
            registry
                .seats
                .get_mut(&seat)
                .context("credential_denied")?
                .last_sequence = auth.request.sequence;
            save(state, &registry)?;
            Ok(json!({"ok": true, "authorized": true, "sender": seat,
                "sequence": auth.request.sequence, "dispatch": "not_performed"}))
        }
        WireRequest::Send {
            mut auth, envelope, ..
        } => {
            let fingerprint = hex(&Sha256::digest(serde_json::to_vec(&envelope)?));
            if let Some(mail) = registry.mail.get(&envelope.message_id) {
                // Only an exact previously committed send may bypass the sequence fence.
                let last = registry
                    .seats
                    .get(&auth.seat)
                    .context("credential_denied")?
                    .last_sequence;
                let sequence = auth.request.sequence;
                ensure!(
                    sequence > 0 && sequence <= last.saturating_add(1),
                    "sequence_replay_or_gap"
                );
                auth.request.sequence = last.checked_add(1).context("sequence_exhausted")?;
                authenticated(&registry, &key, peer, &auth, time)?;
                ensure!(
                    routes::mail_permitted(&registry, mail, time),
                    "route_task_denied"
                );
                ensure!(
                    mail.envelope.sender == auth.seat
                        && auth.request.operation == protocol::Operation::Queue
                        && auth.request.recipient == envelope.recipient
                        && auth.request.project == envelope.project
                        && auth.request.incarnation == envelope.incarnation,
                    "envelope_scope_mismatch"
                );
                ensure!(mail.fingerprint == fingerprint, "message_conflict");
                let response =
                    json!({"ok":true,"duplicate":true,"state":mail.state,"receipts":mail.receipts});
                if sequence > last {
                    registry
                        .seats
                        .get_mut(&auth.seat)
                        .context("credential_denied")?
                        .last_sequence = sequence;
                    save(state, &registry)?;
                }
                return Ok(response);
            }
            authenticated(&registry, &key, peer, &auth, time)?;
            envelope.validate(&auth.request, time)?;
            ensure!(
                registry
                    .seats
                    .get(&auth.seat)
                    .is_some_and(|s| s.grant.recipients.contains(&envelope.recipient))
                    || routes::permits(&registry, &auth.request, Some(&envelope), time),
                "route_task_denied"
            );
            let recipient = registry
                .seats
                .get(&envelope.recipient)
                .context("recipient_unknown")?;
            ensure!(
                !recipient.revoked && recipient.grant.projects.contains(&envelope.project),
                "recipient_unavailable"
            );
            ensure!(registry.mail.len() < 128, "queue_full");
            let id = envelope.message_id;
            let receipt = protocol::Receipt {
                protocol: protocol::VERSION,
                message_id: id,
                state: protocol::DeliveryState::ServiceAccepted,
                writer: protocol::Authority::Service,
                turn_id: None,
            };
            registry.mail.insert(
                id,
                Mail {
                    envelope: *envelope,
                    fingerprint,
                    state: "service_accepted".into(),
                    receipts: vec![receipt.clone()],
                    reconcile_required: false,
                    cancellation: None,
                },
            );
            registry
                .seats
                .get_mut(&auth.seat)
                .context("credential_denied")?
                .last_sequence = auth.request.sequence;
            ensure!(
                serde_json::to_vec(&registry)?.len() <= 1536 * 1024,
                "queue_byte_limit"
            );
            save(state, &registry)?;
            Ok(json!({"ok":true,"receipt":receipt,"receipts":[receipt],"state":"service_accepted"}))
        }
        WireRequest::Receive {
            auth, message_id, ..
        } => {
            let grant = authenticated(&registry, &key, peer, &auth, time)?;
            ensure!(
                grant.role != protocol::Role::Observer,
                "observer_delivery_denied"
            );
            ensure!(
                auth.request.operation == protocol::Operation::Observe
                    && auth.request.recipient == auth.seat,
                "inbox_scope_denied"
            );
            ensure!(
                registry
                    .mail
                    .get(&message_id)
                    .is_none_or(|mail| routes::mail_permitted(&registry, mail, time)),
                "route_inactive"
            );
            let mail = registry
                .mail
                .get_mut(&message_id)
                .context("message_unknown")?;
            ensure!(
                mail.envelope.recipient == auth.seat
                    && mail.envelope.project == auth.request.project,
                "inbox_scope_denied"
            );
            ensure!(
                mail.state == "service_accepted" || mail.state == "delivered",
                "message_quarantined"
            );
            ensure!(!mail.reconcile_required, "message_reconciliation_required");
            if mail.envelope.expires_at <= time {
                mail.state = "expired".into();
                save(state, &registry)?;
                bail!("message_expired");
            }
            if mail.state == "service_accepted" {
                mail.state = "delivered".into();
                mail.receipts.push(protocol::Receipt {
                    protocol: protocol::VERSION,
                    message_id,
                    state: protocol::DeliveryState::Delivered,
                    writer: protocol::Authority::Inbox,
                    turn_id: None,
                });
            }
            let response = json!({"ok":true,"envelope":mail.envelope,"receipts":mail.receipts});
            registry
                .seats
                .get_mut(&auth.seat)
                .context("credential_denied")?
                .last_sequence = auth.request.sequence;
            save(state, &registry)?;
            Ok(response)
        }
    }
}

pub(crate) fn peer_uid(stream: &UnixStream) -> Result<u32> {
    let mut credential = libc::ucred {
        pid: 0,
        uid: 0,
        gid: 0,
    };
    let mut length = std::mem::size_of::<libc::ucred>() as libc::socklen_t;
    // SAFETY: the descriptor is valid and the writable buffer has the supplied size.
    let result = unsafe {
        libc::getsockopt(
            stream.as_raw_fd(),
            libc::SOL_SOCKET,
            libc::SO_PEERCRED,
            std::ptr::from_mut(&mut credential).cast(),
            &mut length,
        )
    };
    ensure!(
        result == 0 && length as usize == std::mem::size_of::<libc::ucred>(),
        "peer_identity_unavailable"
    );
    Ok(credential.uid)
}

fn connection(state: &Path, stream: &mut UnixStream) -> Result<Value> {
    stream.set_read_timeout(Some(Duration::from_secs(3)))?;
    stream.set_write_timeout(Some(Duration::from_secs(3)))?;
    let actual_uid = peer_uid(stream)?;
    let mut bytes = Vec::new();
    BufReader::new(&*stream)
        .take((protocol::MAX_FRAME + 1) as u64)
        .read_until(b'\n', &mut bytes)?;
    ensure!(
        bytes.len() <= protocol::MAX_FRAME && bytes.ends_with(b"\n"),
        "frame_bound"
    );
    let wire: WireRequest =
        serde_json::from_slice(&bytes).map_err(|_| anyhow::anyhow!("invalid_request"))?;
    handle(state, actual_uid, None, wire)
}

pub fn serve(state: &Path, socket: &Path) -> Result<()> {
    let _lock = lock(state)?;
    ensure!(load(state)?.host.is_some(), "registry_uninitialized");
    key(state)?;
    let server_lock = OpenOptions::new()
        .read(true)
        .write(true)
        .create(true)
        .truncate(false)
        .mode(0o600)
        .custom_flags(libc::O_NOFOLLOW)
        .open(state.join("server.lock"))?;
    let lock_info = server_lock.metadata()?;
    ensure!(
        lock_info.is_file() && lock_info.uid() == uid() && lock_info.mode() & 0o077 == 0,
        "lock_permissions"
    );
    // SAFETY: this file owns a valid descriptor until the service ends.
    ensure!(
        unsafe { libc::flock(server_lock.as_raw_fd(), libc::LOCK_EX | libc::LOCK_NB) } == 0,
        "service_already_running"
    );
    lifecycle::prepare_locked(state)?;
    drop(_lock);
    let parent = socket.parent().context("socket_parent_missing")?;
    let info = std::fs::symlink_metadata(parent)?;
    ensure!(
        socket.is_absolute()
            && socket.as_os_str().len() < 104
            && info.is_dir()
            && info.uid() == uid()
            && info.mode() & 0o022 == 0,
        "socket_directory_permissions"
    );
    if std::fs::symlink_metadata(socket).is_ok() {
        bail!("socket_path_exists");
    }
    let listener = UnixListener::bind(socket)?;
    std::fs::set_permissions(socket, std::fs::Permissions::from_mode(0o660))?;
    println!(
        "{}",
        json!({"ready": true, "pid": std::process::id(), "uid": uid(), "socket": socket})
    );
    std::io::stdout().flush()?;
    crate::connections::serve(&listener, |mut stream| {
        let result = match connection(state, &mut stream) {
            Ok(value) => value,
            Err(error) => json!({"ok": false, "error": error.to_string()}),
        };
        fixture_fault(state, "before_ack")?;
        let mut bytes = serde_json::to_vec(&result)?;
        bytes.push(b'\n');
        let _ = stream.write_all(&bytes);
        Ok(())
    })
}
