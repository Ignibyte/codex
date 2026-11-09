//! Private sender acceptance and a bounded, authenticated local transport client.
use crate::protocol;
use crate::service;
use anyhow::Context;
use anyhow::Result;
use anyhow::ensure;
use serde::Deserialize;
use serde::Serialize;
use serde_json::Value;
use serde_json::json;
use std::collections::BTreeMap;
use std::io::BufRead;
use std::io::BufReader;
use std::io::Read;
use std::io::Write;
use std::os::unix::fs::MetadataExt;
use std::os::unix::net::UnixStream;
use std::path::Path;
use std::time::Duration;
use uuid::Uuid;

/// One authenticated worker/controller frame. Synchronize without exposing its credential.
pub fn scoped_call(
    grant: &protocol::Grant,
    socket: &Path,
    service_uid: u32,
    credential: &Path,
    project: &str,
    operation: protocol::Operation,
    frame: Value,
) -> Result<Value> {
    let sync = exchange(
        socket,
        service_uid,
        credential,
        &request(grant, protocol::Operation::Observe, grant.seat, project, 1),
        json!({"method":"sync"}),
    )?;
    ensure!(
        sync["grant"] == serde_json::to_value(grant)?,
        "factory_grant_mismatch"
    );
    let sequence = sync["lastSequence"]
        .as_u64()
        .context("factory_sequence_missing")?
        .checked_add(1)
        .context("sequence_exhausted")?;
    exchange(
        socket,
        service_uid,
        credential,
        &request(grant, operation, grant.seat, project, sequence),
        frame,
    )
}

#[derive(Deserialize, Serialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct Outbox {
    storage_version: u32,
    entries: BTreeMap<Uuid, Entry>,
    #[serde(default)]
    next_order: u64,
    #[serde(default)]
    observed_time: u64,
}
#[derive(Deserialize, Serialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct Entry {
    envelope: protocol::Envelope,
    receipts: Vec<protocol::Receipt>,
    attempt_sequence: Option<u64>,
    service_state: Option<String>,
    #[serde(default)]
    order: u64,
    #[serde(default)]
    attempts: u32,
    #[serde(default)]
    next_attempt_at: u64,
    #[serde(default)]
    dead_letter: Option<String>,
    #[serde(default)]
    last_error: Option<String>,
    #[serde(default)]
    replay_count: u32,
}
fn load(state: &Path) -> Result<Outbox> {
    if !state.join("registry.json").try_exists()? {
        return Ok(Outbox {
            storage_version: 2,
            entries: BTreeMap::new(),
            next_order: 0,
            observed_time: 0,
        });
    }
    let mut outbox: Outbox = serde_json::from_slice(&service::private_read(
        &state.join("registry.json"),
        2 * 1024 * 1024,
    )?)?;
    ensure!(
        (1..=2).contains(&outbox.storage_version),
        "outbox_storage_unsupported"
    );
    outbox.storage_version = 2;
    Ok(outbox)
}
fn save(state: &Path, outbox: &Outbox) -> Result<()> {
    service::atomic_write(state, &serde_json::to_vec(outbox)?)
}

/// The receipt acknowledges local bytes only; the bridge authenticates on relay.
pub fn enqueue(
    state: &Path,
    envelope: protocol::Envelope,
    grant: &protocol::Grant,
) -> Result<Value> {
    let _lock = service::lock(state)?;
    let request = request(
        grant,
        protocol::Operation::Queue,
        envelope.recipient,
        &envelope.project,
        1,
    );
    // A temporary route is service-owned authority. Local acceptance validates
    // all other scope; authenticated relay still decides recipient permission.
    let mut local_scope = grant.clone();
    local_scope.recipients.insert(envelope.recipient);
    protocol::authorize(&local_scope, grant.agent_uid, 0, &request)?;

    // Acceptance is not proof of remote authority. Actual UID is enforced by the service.
    let mut outbox = load(state)?;
    let time = service::now()?.max(outbox.observed_time);
    envelope.validate(&request, time)?;
    outbox.observed_time = time;
    let id = envelope.message_id;
    if let Some(entry) = outbox.entries.get(&id) {
        ensure!(
            serde_json::to_value(&entry.envelope)? == serde_json::to_value(&envelope)?,
            "message_conflict"
        );
        return Ok(json!({"ok":true,"duplicate":true,"receipts":entry.receipts}));
    }
    ensure!(outbox.entries.len() < 128, "outbox_full");
    let receipt = protocol::Receipt {
        protocol: protocol::VERSION,
        message_id: id,
        state: protocol::DeliveryState::LocalAccepted,
        writer: protocol::Authority::Outbox,
        turn_id: None,
    };
    outbox.next_order = outbox
        .next_order
        .checked_add(1)
        .context("outbox_order_exhausted")?;
    outbox.entries.insert(
        id,
        Entry {
            envelope,
            receipts: vec![receipt.clone()],
            attempt_sequence: None,
            service_state: None,
            order: outbox.next_order,
            attempts: 0,
            next_attempt_at: 0,
            dead_letter: None,
            last_error: None,
            replay_count: 0,
        },
    );
    save(state, &outbox)?;
    Ok(json!({"ok":true,"receipt":receipt}))
}
pub fn inspect(state: &Path) -> Result<Value> {
    let _lock = service::lock(state)?;
    Ok(serde_json::to_value(load(state)?)?)
}

pub fn request(
    grant: &protocol::Grant,
    operation: protocol::Operation,
    recipient: Uuid,
    project: &str,
    sequence: u64,
) -> protocol::Request {
    protocol::Request {
        protocol: protocol::VERSION,
        sender: grant.seat,
        host: grant.host,
        incarnation: grant.incarnation,
        sequence,
        project: project.into(),
        recipient,
        operation,
    }
}

/// Credentials travel only to a kernel-authenticated, operator-selected service UID.
/// One frame and a three-second I/O timeout per call; no hidden retries.
pub fn exchange(
    socket: &Path,
    service_uid: u32,
    credential: &Path,
    request: &protocol::Request,
    mut frame: Value,
) -> Result<Value> {
    let parent = socket.parent().context("socket_parent_missing")?;
    let info = std::fs::symlink_metadata(parent)?;
    ensure!(
        socket.is_absolute()
            && info.is_dir()
            && info.uid() == service_uid
            && info.mode() & 0o022 == 0,
        "service_directory_denied"
    );
    let mut stream = UnixStream::connect(socket).context("transport_unavailable")?;
    ensure!(
        service::peer_uid(&stream)? == service_uid,
        "service_uid_denied"
    );
    stream.set_read_timeout(Some(Duration::from_secs(3)))?;
    stream.set_write_timeout(Some(Duration::from_secs(3)))?;
    let secret: Value = serde_json::from_slice(&service::private_read(
        credential,
        protocol::MAX_FRAME as u64,
    )?)?;
    ensure!(
        secret["seat"] == json!(request.sender),
        "credential_seat_mismatch"
    );
    frame["protocol"] = json!(protocol::VERSION);
    frame["auth"] =
        json!({"seat":request.sender,"credential":secret["credential"],"request":request});
    let mut bytes = serde_json::to_vec(&frame)?;
    bytes.push(b'\n');
    ensure!(bytes.len() <= protocol::MAX_FRAME, "frame_bound");
    stream.write_all(&bytes)?;
    let mut bytes = Vec::new();
    BufReader::new(stream)
        .take((protocol::MAX_FRAME + 1) as u64)
        .read_until(b'\n', &mut bytes)?;
    ensure!(
        bytes.len() <= protocol::MAX_FRAME && bytes.ends_with(b"\n"),
        "response_frame_bound"
    );
    let response: Value = serde_json::from_slice(&bytes)?;
    ensure!(
        response["ok"] == true,
        "{}",
        response["error"].as_str().unwrap_or("service_denied")
    );
    Ok(response)
}

fn same_stream(a: &Entry, b: &Entry) -> bool {
    a.envelope.sender == b.envelope.sender
        && a.envelope.recipient == b.envelope.recipient
        && a.envelope.conversation_id == b.envelope.conversation_id
}

/// Each explicit replay preserves the original immutable key and payload.
/// Expired work and replay that would cross later accepted work are refused.
pub fn replay(state: &Path, id: Uuid) -> Result<Value> {
    let _lock = service::lock(state)?;
    let mut outbox = load(state)?;
    let time = service::now()?.max(outbox.observed_time);
    let entry = outbox.entries.get(&id).context("outbox_message_unknown")?;
    ensure!(entry.envelope.expires_at > time, "replay_expired");
    ensure!(
        entry.dead_letter.as_deref() == Some("retry_exhausted") && entry.replay_count < 16,
        "replay_denied"
    );
    ensure!(
        !outbox
            .entries
            .values()
            .any(|later| same_stream(entry, later)
                && later.order > entry.order
                && later.service_state.is_some()),
        "replay_order_conflict"
    );
    let entry = outbox
        .entries
        .get_mut(&id)
        .context("outbox_message_unknown")?;
    entry.attempts = 0;
    entry.next_attempt_at = 0;
    entry.dead_letter = None;
    entry.replay_count += 1;
    save(state, &outbox)?;
    Ok(json!({"ok":true,"messageId":id,"replayCount":outbox.entries[&id].replay_count}))
}

pub fn relay(
    state: &Path,
    socket: &Path,
    service_uid: u32,
    credential: &Path,
    grant: &protocol::Grant,
    id: Uuid,
) -> Result<Value> {
    let _lock = service::lock(state)?;
    let mut outbox = load(state)?;
    let time = service::now()?.max(outbox.observed_time);
    outbox.observed_time = time;
    let entry = outbox.entries.get(&id).context("outbox_message_unknown")?;
    ensure!(entry.dead_letter.is_none(), "outbox_dead_letter");
    if entry.service_state.is_none() && entry.envelope.expires_at <= time {
        outbox
            .entries
            .get_mut(&id)
            .context("outbox_message_unknown")?
            .dead_letter = Some("expired".into());
        save(state, &outbox)?;
        anyhow::bail!("outbox_expired");
    }
    ensure!(time >= entry.next_attempt_at, "retry_not_due");
    ensure!(
        !outbox
            .entries
            .iter()
            .any(|(other_id, other)| *other_id != id
                && same_stream(entry, other)
                && entry.order == 0
                && other.order == 0
                && other.service_state.is_none()),
        "outbox_legacy_order_uncertain"
    );
    ensure!(
        !outbox
            .entries
            .values()
            .any(|earlier| same_stream(entry, earlier)
                && earlier.order < entry.order
                && earlier.service_state.is_none()
                && earlier.dead_letter.is_none()),
        "outbox_order_blocked"
    );
    if entry.attempts >= 3 {
        outbox
            .entries
            .get_mut(&id)
            .context("outbox_message_unknown")?
            .dead_letter = Some("retry_exhausted".into());
        save(state, &outbox)?;
        anyhow::bail!("outbox_retry_exhausted");
    }
    let entry = outbox
        .entries
        .get_mut(&id)
        .context("outbox_message_unknown")?;
    entry.attempts += 1;
    // Persist admission before I/O. A killed relay consumes one bounded attempt.
    entry.next_attempt_at = time.saturating_add(1 << (entry.attempts - 1));
    save(state, &outbox)?;
    let result = relay_attempt(
        state,
        socket,
        service_uid,
        credential,
        grant,
        id,
        &mut outbox,
    );
    match result {
        Ok(response) => Ok(response),
        Err(error) => {
            let entry = outbox
                .entries
                .get_mut(&id)
                .context("outbox_message_unknown")?;
            entry.last_error = Some(error.to_string());
            if entry.attempts >= 3 {
                entry.dead_letter = Some("retry_exhausted".into());
            }
            save(state, &outbox)?;
            Err(error)
        }
    }
}

fn relay_attempt(
    state: &Path,
    socket: &Path,
    service_uid: u32,
    credential: &Path,
    grant: &protocol::Grant,
    id: Uuid,
    outbox: &mut Outbox,
) -> Result<Value> {
    let entry = outbox.entries.get(&id).context("outbox_message_unknown")?;
    let sync = exchange(
        socket,
        service_uid,
        credential,
        &request(
            grant,
            protocol::Operation::Observe,
            grant.seat,
            &entry.envelope.project,
            1,
        ),
        json!({"method":"sync"}),
    )?;
    ensure!(
        sync["grant"] == serde_json::to_value(grant)?,
        "grant_mismatch"
    );
    let sequence = sync["lastSequence"]
        .as_u64()
        .context("sequence_missing")?
        .checked_add(1)
        .context("sequence_exhausted")?;
    outbox
        .entries
        .get_mut(&id)
        .context("outbox_message_unknown")?
        .attempt_sequence = Some(sequence);
    save(state, outbox)?;
    let entry = outbox.entries.get(&id).context("outbox_message_unknown")?;
    let response = exchange(
        socket,
        service_uid,
        credential,
        &request(
            grant,
            protocol::Operation::Queue,
            entry.envelope.recipient,
            &entry.envelope.project,
            sequence,
        ),
        json!({"method":"send","envelope":entry.envelope}),
    )?;
    let receipts: Vec<protocol::Receipt> = serde_json::from_value(response["receipts"].clone())?;
    ensure!(
        receipts
            .first()
            .is_some_and(|r| r.state == protocol::DeliveryState::ServiceAccepted),
        "service_receipt_missing"
    );
    ensure!(
        response["state"].as_str().is_some_and(|state| [
            "service_accepted",
            "delivered",
            "consumed",
            "completed",
            "expired",
            "quarantined"
        ]
        .contains(&state)),
        "service_state_invalid"
    );
    for receipt in &receipts {
        receipt.validate()?;
        ensure!(receipt.message_id == id, "receipt_message_mismatch");
    }
    let entry = outbox
        .entries
        .get_mut(&id)
        .context("outbox_message_unknown")?;
    entry.receipts.truncate(1);
    entry.receipts.extend(receipts);
    entry.service_state = response["state"].as_str().map(str::to_owned);
    entry.attempts = 0;
    entry.next_attempt_at = 0;
    entry.last_error = None;
    save(state, outbox)?;
    Ok(
        json!({"ok":true,"state":outbox.entries[&id].service_state,"receipts":outbox.entries[&id].receipts}),
    )
}

/// At most three persisted attempts. Waiting uses monotonic sleep; deadlines
/// survive restart and wall-clock rollback cannot revive expired work.
pub fn retry(
    state: &Path,
    socket: &Path,
    service_uid: u32,
    credential: &Path,
    grant: &protocol::Grant,
    id: Uuid,
) -> Result<Value> {
    for _ in 0..3 {
        let delay = {
            let _lock = service::lock(state)?;
            let outbox = load(state)?;
            let entry = outbox.entries.get(&id).context("outbox_message_unknown")?;
            ensure!(entry.dead_letter.is_none(), "outbox_dead_letter");
            entry
                .next_attempt_at
                .saturating_sub(service::now()?.max(outbox.observed_time))
        };
        ensure!(delay <= 4, "retry_clock_reconciliation_required");
        std::thread::sleep(Duration::from_secs(delay));
        if let Ok(response) = relay(state, socket, service_uid, credential, grant, id) {
            return Ok(response);
        }
    }
    anyhow::bail!("outbox_retry_exhausted; inspect outbox for disposition")
}

/// One private local state transaction for native integration ledgers.
/// The file lock survives async suspension and process death releases it.
pub struct LocalTransaction {
    state: std::path::PathBuf,
    _lock: std::fs::File,
}
impl LocalTransaction {
    #[cfg(feature = "fixture-faults")]
    pub fn fixture_state(&self) -> &Path {
        &self.state
    }

    pub fn begin(state: &Path) -> Result<Self> {
        Ok(Self {
            state: state.into(),
            _lock: service::lock(state)?,
        })
    }
    pub fn read(&self) -> Result<Value> {
        if !self.state.join("registry.json").try_exists()? {
            return Ok(json!({"storageVersion":1,"messages":{}}));
        }
        Ok(serde_json::from_slice(&service::private_read(
            &self.state.join("registry.json"),
            2 * 1024 * 1024,
        )?)?)
    }
    pub fn save(&self, value: &Value) -> Result<()> {
        service::atomic_write(&self.state, &serde_json::to_vec(value)?)
    }
}

/// Cache receipts obtained through an authenticated service status exchange.
/// This local cache never grants authority at the service.
pub fn record_status(state: &Path, id: Uuid, response: &Value) -> Result<()> {
    let _lock = service::lock(state)?;
    let mut outbox = load(state)?;
    let receipts: Vec<protocol::Receipt> = serde_json::from_value(response["receipts"].clone())?;
    ensure!(
        receipts
            .first()
            .is_some_and(|r| r.state == protocol::DeliveryState::ServiceAccepted),
        "service_receipt_missing"
    );
    for receipt in &receipts {
        receipt.validate()?;
        ensure!(receipt.message_id == id, "receipt_message_mismatch");
    }
    let entry = outbox
        .entries
        .get_mut(&id)
        .context("outbox_message_unknown")?;
    entry.receipts.truncate(1);
    entry.receipts.extend(receipts);
    entry.service_state = Some(
        response["state"]
            .as_str()
            .context("service_state_invalid")?
            .into(),
    );
    save(state, &outbox)
}
