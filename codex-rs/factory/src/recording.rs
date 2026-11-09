//! Durable, allowlisted native metadata. Forge records a projection, not execution authority.
use crate::protocol;
use crate::service;
use anyhow::Context;
use anyhow::Result;
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
use std::path::Path;
use uuid::Uuid;

const MAX_EVENTS: usize = 128;
type HmacSha256 = Hmac<Sha256>;

/// A historical fact, with no key, bearer credential or courier claim.
#[derive(Clone, Deserialize, Serialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub(crate) struct ArchiveEntry {
    pub(crate) event_id: Uuid,
    event: Event,
    state: String,
    accepted_at: u64,
    remote_ids: Vec<Uuid>,
}

pub(crate) fn archive(state: &Path) -> Result<Vec<ArchiveEntry>> {
    let _lock = service::lock(state)?;
    Ok(load(state)?
        .entries
        .into_values()
        .map(|entry| ArchiveEntry {
            event_id: entry.event_id,
            event: entry.event,
            state: entry.state,
            accepted_at: entry.accepted_at,
            remote_ids: entry.remote_ids,
        })
        .collect())
}

#[derive(Clone, Copy, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum Outcome {
    Completed,
    Failed,
    Interrupted,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct NativeRecord {
    pub message_id: Uuid,
    pub thread_id: Uuid,
    pub turn_id: Uuid,
    pub outcome: Outcome,
}

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct Event {
    pub schema: u32,
    pub host: Uuid,
    pub seat: Uuid,
    pub incarnation: Uuid,
    pub project: String,
    pub sender: Uuid,
    pub message_id: Uuid,
    pub assignment_id: Option<Uuid>,
    pub conversation_id: Uuid,
    pub thread_id: Uuid,
    pub turn_id: Uuid,
    pub outcome: Outcome,
    /// Message creation time, not an invented model completion timestamp.
    pub source_created_at: u64,
    pub authority: EventAuthority,
}

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum EventAuthority {
    AuthenticatedNativeRecipientAttestation,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct Entry {
    pub event_id: Uuid,
    pub event: Event,
    pub attestation: String,
    pub state: String,
    pub accepted_at: u64,
    pub attempts: u32,
    pub attempt_id: Option<Uuid>,
    pub remote_ids: Vec<Uuid>,
    pub last_error: Option<String>,
    pub claim_owner: Option<Owner>,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct Owner {
    pid: u32,
    start_ticks: String,
    boot_id: Uuid,
}

impl Owner {
    fn current() -> Result<Self> {
        let pid = std::process::id();
        let stat = std::fs::read_to_string(format!("/proc/{pid}/stat"))?;
        let fields = stat
            .rsplit_once(')')
            .context("recording_process_identity_invalid")?
            .1;
        Ok(Self {
            pid,
            start_ticks: fields
                .split_whitespace()
                .nth(19)
                .context("recording_process_identity_invalid")?
                .into(),
            boot_id: Uuid::parse_str(
                std::fs::read_to_string("/proc/sys/kernel/random/boot_id")?.trim(),
            )?,
        })
    }
    fn is_live(&self) -> Result<bool> {
        let boot =
            Uuid::parse_str(std::fs::read_to_string("/proc/sys/kernel/random/boot_id")?.trim())?;
        if boot != self.boot_id {
            return Ok(false);
        }
        let stat = match std::fs::read_to_string(format!("/proc/{}/stat", self.pid)) {
            Ok(value) => value,
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => return Ok(false),
            Err(error) => return Err(error.into()),
        };
        let fields = stat
            .rsplit_once(')')
            .context("recording_process_identity_invalid")?
            .1;
        let mut fields = fields.split_whitespace();
        let status = fields
            .next()
            .context("recording_process_identity_invalid")?;
        let start = fields
            .nth(18)
            .context("recording_process_identity_invalid")?;
        Ok(start == self.start_ticks && status != "Z" && status != "X")
    }
}

#[derive(Deserialize, Serialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct Store {
    storage_version: u32,
    entries: BTreeMap<Uuid, Entry>,
    #[serde(default)]
    observed_time: u64,
}

fn load(state: &Path) -> Result<Store> {
    if !state.join("registry.json").try_exists()? {
        return Ok(Store {
            storage_version: 1,
            entries: BTreeMap::new(),
            observed_time: 0,
        });
    }
    let store: Store = serde_json::from_slice(&service::private_read(
        &state.join("registry.json"),
        2 * 1024 * 1024,
    )?)?;
    ensure!(store.storage_version == 1, "recording_storage_unsupported");
    let key = signing_key(state)?;
    ensure!(store.entries.len() <= MAX_EVENTS, "recording_buffer_bound");
    for (id, entry) in &store.entries {
        ensure!(
            *id == entry.event_id && identity(&entry.event)? == *id && entry.event.schema == 1,
            "recording_local_identity_invalid"
        );
        ensure!(
            ["pending", "posting", "uncertain", "recorded"].contains(&entry.state.as_str())
                && entry.attempts <= 16
                && entry.remote_ids.len() <= 1000,
            "recording_local_state_invalid"
        );
        ensure!(
            entry.state != "recorded" || !entry.remote_ids.is_empty(),
            "recording_remote_receipt_missing"
        );
        verify_attestation(&key, *id, &entry.event, &entry.attestation)?;
    }
    Ok(store)
}

fn signing_key(state: &Path) -> Result<Vec<u8>> {
    let path = state.join("attestation.key");
    if !path.try_exists()? {
        ensure!(
            !state.join("registry.json").try_exists()?,
            "recording_attestation_key_missing"
        );
        let mut bytes = [0; 32];
        OsRng.try_fill_bytes(&mut bytes)?;
        service::exclusive_write(&path, &bytes)?;
        std::fs::File::open(state)?.sync_all()?;
    }
    let bytes = service::private_read(&path, 32)?;
    ensure!(bytes.len() == 32, "recording_attestation_key_invalid");
    Ok(bytes)
}

fn identity(event: &Event) -> Result<Uuid> {
    // One terminal fact per actual message/turn. An altered thread, outcome or
    // other payload must conflict with that fact, not obtain a fresh identity.
    let key = json!([
        "native-terminal-v1",
        event.host,
        event.seat,
        event.incarnation,
        event.project,
        event.message_id,
        event.turn_id
    ]);
    let hash = Sha256::digest(serde_json::to_vec(&key)?);
    let mut bytes = [0; 16];
    bytes.copy_from_slice(&hash[..16]);
    // RFC 9562 version 8: a domain-specific, content-derived UUID.
    bytes[6] = (bytes[6] & 0x0f) | 0x80;
    bytes[8] = (bytes[8] & 0x3f) | 0x80;
    Ok(Uuid::from_bytes(bytes))
}

fn mac(key: &[u8], id: Uuid, event: &Event) -> Result<HmacSha256> {
    let mut mac = HmacSha256::new_from_slice(key)?;
    mac.update(b"codex-factory-forge-recording-v1\0");
    mac.update(id.as_bytes());
    mac.update(&serde_json::to_vec(event)?);
    Ok(mac)
}

fn hex(bytes: &[u8]) -> String {
    bytes.iter().map(|byte| format!("{byte:02x}")).collect()
}

pub(crate) fn accept(state: &Path, event: Event, time: u64) -> Result<Value> {
    let _lock = service::lock(state)?;
    let mut store = load(state)?;
    store.observed_time = store.observed_time.max(time);
    let key = signing_key(state)?;
    let id = identity(&event)?;
    if let Some(entry) = store.entries.get(&id) {
        ensure!(entry.event == event, "recording_event_conflict");
        return Ok(json!({"ok":true,"duplicate":true,"eventId":id,"state":entry.state}));
    }
    ensure!(store.entries.len() < MAX_EVENTS, "recording_buffer_full");
    let attestation = hex(&mac(&key, id, &event)?.finalize().into_bytes());
    store.entries.insert(
        id,
        Entry {
            event_id: id,
            event,
            attestation,
            state: "pending".into(),
            accepted_at: time,
            attempts: 0,
            attempt_id: None,
            remote_ids: Vec::new(),
            last_error: None,
            claim_owner: None,
        },
    );
    service::atomic_write(state, &serde_json::to_vec(&store)?)?;
    Ok(json!({"ok":true,"duplicate":false,"eventId":id,"state":"pending"}))
}

pub fn inspect(state: &Path) -> Result<Value> {
    let _lock = service::lock(state)?;
    let store = load(state)?;
    Ok(
        json!({"storageVersion":1,"capacity":MAX_EVENTS,"entries":store.entries,
        "forgeDegraded":store.entries.values().any(|entry| entry.state != "recorded")}),
    )
}

pub(crate) fn scoped_status(state: &Path, seat: Uuid, project: &str) -> Result<Value> {
    let _lock = service::lock(state)?;
    let store = load(state)?;
    let entries: Vec<_> = store
        .entries
        .values()
        .filter(|entry| entry.event.seat == seat && entry.event.project == project)
        .map(|entry| {
            json!({"eventId":entry.event_id,"messageId":entry.event.message_id,
            "state":entry.state,"lastError":entry.last_error,"remoteCopies":entry.remote_ids.len()})
        })
        .collect();
    Ok(json!({"ok":true,"events":entries,"capacity":MAX_EVENTS}))
}

pub(crate) fn terminal_for_assignment(
    state: &Path,
    grant: &protocol::Grant,
    run: Uuid,
    message: Uuid,
    thread: Uuid,
    turn: Uuid,
) -> Result<(Uuid, Event)> {
    let _lock = service::lock(state)?;
    let store = load(state)?;
    let entry = store
        .entries
        .values()
        .find(|entry| {
            entry.event.seat == grant.seat
                && entry.event.incarnation == grant.incarnation
                && entry.event.assignment_id == Some(run)
                && entry.event.message_id == message
                && entry.event.thread_id == thread
                && entry.event.turn_id == turn
        })
        .context("foreman_native_terminal_missing")?;
    ensure!(
        entry.event.outcome == Outcome::Completed,
        "foreman_native_result_not_completed"
    );
    Ok((entry.event_id, entry.event.clone()))
}

/// Collection is an operator-owned job, never a Core model callback. Release
/// the native ledger lock before any local socket I/O.
pub fn collect(
    native_state: &Path,
    socket: &Path,
    service_uid: u32,
    credential: &Path,
    grant: &protocol::Grant,
) -> Result<Value> {
    let ledger = {
        let transaction = crate::client::LocalTransaction::begin(native_state)?;
        transaction.read()?
    };
    ensure!(
        ledger["storageVersion"] == 2,
        "recording_native_storage_unsupported"
    );
    let messages = ledger["messages"]
        .as_object()
        .context("recording_native_messages_missing")?;
    ensure!(
        messages.len() <= MAX_EVENTS,
        "recording_native_messages_bound"
    );
    let mut results = Vec::new();
    for (message_id, entry) in messages {
        let outcome = match entry["state"].as_str() {
            Some("completed") => Outcome::Completed,
            Some("failed") => Outcome::Failed,
            Some("interrupted") => Outcome::Interrupted,
            _ => continue,
        };
        let record = NativeRecord {
            message_id: Uuid::parse_str(message_id)?,
            thread_id: Uuid::parse_str(
                entry["threadId"]
                    .as_str()
                    .context("recording_thread_missing")?,
            )?,
            turn_id: Uuid::parse_str(entry["turnId"].as_str().context("recording_turn_missing")?)?,
            outcome,
        };
        let project = entry["envelope"]["project"]
            .as_str()
            .context("recording_project_missing")?;
        let request =
            crate::client::request(grant, protocol::Operation::Observe, grant.seat, project, 1);
        let sync = crate::client::exchange(
            socket,
            service_uid,
            credential,
            &request,
            json!({"method":"sync"}),
        )?;
        ensure!(
            sync["grant"] == serde_json::to_value(grant)?,
            "recording_grant_mismatch"
        );
        let sequence = sync["lastSequence"]
            .as_u64()
            .context("recording_sequence_missing")?
            .checked_add(1)
            .context("recording_sequence_exhausted")?;
        let request = crate::client::request(
            grant,
            protocol::Operation::Acknowledge,
            grant.seat,
            project,
            sequence,
        );
        results.push(crate::client::exchange(
            socket,
            service_uid,
            credential,
            &request,
            json!({"method":"record_native","record":record}),
        )?);
    }
    Ok(json!({"ok":true,"records":results}))
}

#[derive(Deserialize, Serialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct Config {
    pub endpoint: String,
    pub forge_identity: String,
    pub forge_project: Uuid,
    pub local_project: String,
    pub signer_argv: Vec<String>,
}

fn verified_remote(key: &[u8], expected: &Entry, row: &Value, project: Uuid) -> Result<Uuid> {
    ensure!(
        row["project_id"] == project.to_string(),
        "recording_remote_project_mismatch"
    );
    let coarse = match expected.event.outcome {
        Outcome::Completed => "done",
        Outcome::Failed => "error",
        Outcome::Interrupted => "idle",
    };
    ensure!(
        row["session_id"] == format!("factory/{}", expected.event.seat)
            && row["box"] == expected.event.host.to_string()
            && row["event_type"] == "turn-complete"
            && row["state"] == coarse
            && row["schema"] == 1,
        "recording_remote_scope_mismatch"
    );
    let body = &row["payload"]["factory"];
    ensure!(
        body["eventId"] == expected.event_id.to_string(),
        "recording_remote_identity_mismatch"
    );
    let event: Event = serde_json::from_value(body["event"].clone())?;
    ensure!(
        event == expected.event && identity(&event)? == expected.event_id,
        "recording_remote_event_conflict"
    );
    let signature = body["attestation"]
        .as_str()
        .context("recording_attestation_missing")?;
    verify_attestation(key, expected.event_id, &event, signature)?;
    let id = Uuid::parse_str(row["id"].as_str().context("recording_remote_id_missing")?)?;
    ensure!(!id.is_nil(), "recording_remote_id_invalid");
    Ok(id)
}

fn verify_attestation(key: &[u8], id: Uuid, event: &Event, signature: &str) -> Result<()> {
    ensure!(
        signature.len() == 64 && signature.is_ascii(),
        "recording_attestation_invalid"
    );
    let bytes = (0..64)
        .step_by(2)
        .map(|index| u8::from_str_radix(&signature[index..index + 2], 16))
        .collect::<std::result::Result<Vec<_>, _>>()?;
    mac(key, id, event)?
        .verify_slice(&bytes)
        .map_err(|_| anyhow::anyhow!("recording_attestation_invalid"))?;
    Ok(())
}

async fn signer(config: &Config) -> Result<reqwest::header::HeaderMap> {
    use std::os::unix::fs::MetadataExt;
    use std::process::Stdio;
    use tokio::io::AsyncReadExt;
    ensure!(
        !config.signer_argv.is_empty() && config.signer_argv.len() <= 16,
        "recording_signer_invalid"
    );
    let program = Path::new(&config.signer_argv[0]);
    ensure!(program.is_absolute(), "recording_signer_not_absolute");
    let info = std::fs::symlink_metadata(program)?;
    // SAFETY: geteuid has no pointer arguments and does not mutate process state.
    let caller_uid = unsafe { libc::geteuid() };
    ensure!(
        info.is_file() && (info.uid() == 0 || info.uid() == caller_uid) && info.mode() & 0o022 == 0,
        "recording_signer_permissions"
    );
    let mut child = tokio::process::Command::new(program)
        .args(&config.signer_argv[1..])
        .env_clear()
        .env("PATH", "/usr/bin:/bin")
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::null())
        .kill_on_drop(true)
        .spawn()?;
    let mut bytes = Vec::new();
    let mut output = child
        .stdout
        .take()
        .context("recording_signer_output_missing")?
        .take(8193);
    let result = tokio::time::timeout(std::time::Duration::from_secs(3), async {
        output.read_to_end(&mut bytes).await?;
        let status = child.wait().await?;
        ensure!(
            status.success() && bytes.len() <= 8192,
            "recording_signer_failed"
        );
        Ok::<(), anyhow::Error>(())
    })
    .await;
    if result.is_err() {
        child.kill().await?;
        child.wait().await?;
    }
    result.context("recording_signer_timeout")??;
    let values: BTreeMap<String, String> = serde_json::from_slice(&bytes)?;
    ensure!(
        values.get("X-Forge-Identity") == Some(&config.forge_identity)
            && values.contains_key("X-Forge-Assertion"),
        "recording_signer_identity_mismatch"
    );
    let mut headers = reqwest::header::HeaderMap::new();
    for (name, value) in values {
        ensure!(
            ["X-Forge-Identity", "X-Forge-Assertion", "Authorization"].contains(&name.as_str()),
            "recording_signer_header_denied"
        );
        let mut header = reqwest::header::HeaderValue::from_str(&value)?;
        header.set_sensitive(true);
        headers.insert(
            reqwest::header::HeaderName::from_bytes(name.as_bytes())?,
            header,
        );
    }
    Ok(headers)
}

pub(crate) async fn call(
    config: &Config,
    client: &reqwest::Client,
    name: &str,
    args: Value,
) -> Result<Value> {
    let result = request(
        config,
        client,
        "tools/call",
        json!({"name":name,"arguments":args}),
    )
    .await?;
    ensure!(result["isError"] != true, "recording_forge_tool_refused");
    if let Some(value) = result.get("structuredContent") {
        return Ok(value.clone());
    }
    let content = result["content"]
        .as_array()
        .context("recording_forge_content_missing")?;
    ensure!(
        content.len() == 1 && content[0]["type"] == "text",
        "recording_forge_content_invalid"
    );
    Ok(serde_json::from_str(
        content[0]["text"]
            .as_str()
            .context("recording_forge_text_missing")?,
    )?)
}

pub(crate) async fn request(
    config: &Config,
    client: &reqwest::Client,
    method: &str,
    params: Value,
) -> Result<Value> {
    let request_id = Uuid::new_v4().to_string();
    let mut response = client
        .post(&config.endpoint)
        .headers(signer(config).await?)
        .header("Accept", "application/json, text/event-stream")
        .header("MCP-Protocol-Version", "2025-06-18")
        .json(&json!({"jsonrpc":"2.0","id":request_id,"method":method,"params":params}))
        .send()
        .await?;
    ensure!(
        response.status().is_success(),
        "recording_forge_http_refused"
    );
    let mut bytes = Vec::new();
    while let Some(chunk) = response.chunk().await? {
        ensure!(
            bytes.len() + chunk.len() <= 2 * 1024 * 1024,
            "recording_forge_response_bound"
        );
        bytes.extend_from_slice(&chunk);
    }
    let text = std::str::from_utf8(&bytes)?;
    let wire: Value = if text.trim_start().starts_with('{') {
        serde_json::from_str(text)?
    } else {
        let candidates: Vec<_> = text
            .lines()
            .filter_map(|line| line.strip_prefix("data: "))
            .filter_map(|line| serde_json::from_str::<Value>(line).ok())
            .filter(|value| value["id"] == request_id)
            .collect();
        ensure!(candidates.len() == 1, "recording_forge_response_invalid");
        candidates
            .into_iter()
            .next()
            .context("recording_forge_response_missing")?
    };
    ensure!(
        wire["id"] == request_id && wire.get("error").is_none(),
        "recording_forge_rpc_refused"
    );
    Ok(wire["result"].clone())
}

/// One bounded operator-owned courier attempt. A durable 'posting' state makes
/// process death ambiguous, never permission for blind duplicate submission.
pub(crate) fn connection(config_path: &Path) -> Result<(Config, reqwest::Client)> {
    let config: Config = serde_json::from_slice(&service::private_read(config_path, 16384)?)?;
    let url = reqwest::Url::parse(&config.endpoint)?;
    ensure!(
        url.scheme() == "https"
            || (url.scheme() == "http"
                && matches!(url.host_str(), Some("127.0.0.1" | "[::1]" | "localhost"))),
        "recording_endpoint_requires_tls"
    );
    ensure!(
        url.username().is_empty()
            && url.password().is_none()
            && url.query().is_none()
            && url.fragment().is_none(),
        "recording_endpoint_credentials_denied"
    );
    let client = reqwest::Client::builder()
        .no_proxy()
        .redirect(reqwest::redirect::Policy::none())
        .timeout(std::time::Duration::from_secs(8))
        .build()?;
    Ok((config, client))
}

pub async fn flush(state: &Path, config_path: &Path, event_id: Uuid) -> Result<Value> {
    let (config, client) = connection(config_path)?;
    let attempt = Uuid::new_v4();
    let (entry, key, previous) = {
        let _lock = service::lock(state)?;
        let mut store = load(state)?;
        let time = service::now()?.max(store.observed_time);
        store.observed_time = time;
        let key = signing_key(state)?;
        let entry = store
            .entries
            .get_mut(&event_id)
            .context("recording_event_unknown")?;
        ensure!(
            entry.event.project == config.local_project,
            "recording_config_project_mismatch"
        );
        if entry.state == "recorded" {
            return Ok(
                json!({"ok":true,"state":"recorded","eventId":event_id,"remoteIds":entry.remote_ids}),
            );
        }
        ensure!(entry.attempts < 16, "recording_attempt_bound");
        ensure!(
            time.saturating_sub(entry.accepted_at) <= 3600,
            "recording_replay_expired"
        );
        let previous = entry.state.clone();
        // Concurrent couriers cannot supersede an in-flight claimant. A crashed
        // 'posting' claimant can reconcile only after that incarnation ends.
        if previous == "posting" {
            ensure!(
                !entry
                    .claim_owner
                    .as_ref()
                    .context("recording_owner_missing")?
                    .is_live()?,
                "recording_courier_busy"
            );
        }
        entry.state = "posting".into();
        entry.attempt_id = Some(attempt);
        entry.attempts += 1;
        entry.claim_owner = Some(Owner::current()?);
        entry.last_error = None;
        let copy = entry.clone();
        service::atomic_write(state, &serde_json::to_vec(&store)?)?;
        (copy, key, previous)
    };
    let mut post_attempted = false;
    let mut remote_uncertain = false;
    let outcome: Result<Vec<Uuid>>=async {
        let listed=call(&config,&client,"seat-events-list",json!({"session_id":format!("factory/{}",entry.event.seat),"limit":1000})).await?;
        let events=listed["events"].as_array().context("recording_forge_events_missing")?;
        let mut found=Vec::new();
        for row in events {
            if row["payload"]["factory"]["eventId"]==event_id.to_string() {
                remote_uncertain=true;
                found.push(verified_remote(&key,&entry,row,config.forge_project)?);
            }
        }
        if !found.is_empty() { found.sort_unstable(); found.dedup(); return Ok(found); }
        if events.len()>=1000 { remote_uncertain=true; }
        ensure!(previous=="pending" && events.len()<1000,"recording_remote_evidence_uncertain");
        let coarse=match entry.event.outcome { Outcome::Completed=>"done",Outcome::Failed=>"error",Outcome::Interrupted=>"idle" };
        post_attempted=true;
        let recorded=call(&config,&client,"seat-event-emit",json!({"session_id":format!("factory/{}",entry.event.seat),
            "box":entry.event.host.to_string(),"state":coarse,"event_type":"turn-complete","schema":1,
            "payload":{"factory":{"eventId":entry.event_id,"event":entry.event,"attestation":entry.attestation}}})).await?;
        Ok(vec![verified_remote(&key,&entry,&recorded["event"],config.forge_project)?])
    }.await;
    let _lock = service::lock(state)?;
    let mut store = load(state)?;
    let current = store
        .entries
        .get_mut(&event_id)
        .context("recording_event_unknown")?;
    ensure!(
        current.attempt_id == Some(attempt),
        "recording_attempt_conflict"
    );
    match outcome {
        Ok(ids) => {
            current.state = "recorded".into();
            current.remote_ids = ids;
            current.last_error = None;
        }
        Err(_) => {
            current.state = if previous == "pending" && !post_attempted && !remote_uncertain {
                "pending"
            } else {
                "uncertain"
            }
            .into();
            current.last_error = Some("forge_recording_unconfirmed".into());
        }
    }
    let response = json!({"ok":true,"eventId":event_id,"state":current.state,"remoteIds":current.remote_ids,"lastError":current.last_error});
    service::atomic_write(state, &serde_json::to_vec(&store)?)?;
    Ok(response)
}
