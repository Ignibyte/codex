//! Explicit one-shot observations. No timer, model call or maintenance runner.
use super::health;
use super::{Function, Profile};
use crate::client;
use crate::protocol;
use crate::service;
use anyhow::Context;
use anyhow::Result;
use anyhow::ensure;
use serde::Deserialize;
use serde::Serialize;
use serde_json::Value;
use serde_json::json;
use sha2::Digest;
use std::collections::BTreeMap;
use std::os::unix::fs::DirBuilderExt;
use std::path::Path;
use std::path::PathBuf;
use uuid::Uuid;

#[derive(Clone, Deserialize, Serialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct Config {
    pub grant: protocol::Grant,
    pub project: String,
    pub recipient: Uuid,
    pub socket: PathBuf,
    pub credential: PathBuf,
    pub service_uid: u32,
    pub start_at: u64,
    pub interval_seconds: u64,
    pub retention_slots: usize,
    pub max_archives: usize,
    pub probes: health::Config,
}
impl Config {
    fn validate(&self) -> Result<()> {
        super::profile(Profile {
            function: Function::Ops,
            grant: self.grant.clone(),
            project: self.project.clone(),
        })?;
        ensure!(
            self.recipient != self.grant.seat
                && self.grant.recipients.contains(&self.recipient)
                && self.grant.operations.contains(&protocol::Operation::Queue)
                && self.socket.is_absolute()
                && self.credential.is_absolute()
                && (1..=86400).contains(&self.interval_seconds)
                && (2..=64).contains(&self.retention_slots)
                && (1..=16).contains(&self.max_archives),
            "support_schedule_config_invalid"
        );
        self.probes.validate()
    }
}

#[derive(Deserialize)]
#[serde(tag = "action", rename_all = "snake_case", deny_unknown_fields)]
pub enum Action {
    Prepare { config: Box<Config> },
    Tick,
    Pause,
    Resume,
    Status { after: Option<u64> },
    Read { slot: u64 },
    Export { output: PathBuf },
}

#[derive(Clone, Deserialize, Serialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct Run {
    slot: u64,
    missed_slots: u64,
    snapshot: health::Snapshot,
    fingerprint: String,
    message_id: Option<Uuid>,
    disposition: String,
}
#[derive(Deserialize, Serialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct Pending {
    run: Run,
    envelope: protocol::Envelope,
}
#[derive(Deserialize, Serialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct Baseline {
    read_at: u64,
    metric: health::Metric,
}
#[derive(Deserialize, Serialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct Archive {
    file: String,
    sha256: String,
    first_slot: u64,
    through_slot: u64,
}
#[derive(Deserialize, Serialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct Store {
    schema: u32,
    generation: Uuid,
    config: Config,
    config_digest: String,
    paused: bool,
    hold: Option<String>,
    observed_time: u64,
    last_slot: Option<u64>,
    lower_cursor: u64,
    fingerprint: Option<String>,
    last_good: BTreeMap<String, Baseline>,
    pending: Option<Pending>,
    runs: BTreeMap<u64, Run>,
    archives: Vec<Archive>,
}

fn hash(bytes: &[u8]) -> String {
    format!("{:x}", sha2::Sha256::digest(bytes))
}

#[cfg(feature = "fixture-faults")]
fn observation_time(state: &Path) -> Result<u64> {
    let path = state.join("fixture-support-time.json");
    if path.try_exists()? {
        return Ok(serde_json::from_slice(&service::private_read(&path, 32)?)?);
    }
    service::now()
}

#[cfg(not(feature = "fixture-faults"))]
fn observation_time(_state: &Path) -> Result<u64> {
    service::now()
}
fn save(state: &Path, store: &Store) -> Result<()> {
    service::atomic_write(state, &serde_json::to_vec(store)?)
}
fn load(state: &Path) -> Result<Store> {
    let store: Store = serde_json::from_slice(&service::private_read(
        &state.join("registry.json"),
        2 * 1024 * 1024,
    )?)?;
    store.config.validate()?;
    ensure!(
        store.schema == 1
            && !store.generation.is_nil()
            && store.config_digest == hash(&serde_json::to_vec(&store.config)?)
            && store.runs.len() <= store.config.retention_slots * 2
            && store.archives.len() <= store.config.max_archives,
        "support_schedule_state_invalid"
    );
    Ok(store)
}
fn status(store: &Store, after: Option<u64>) -> Value {
    json!({"schema":1,"generation":store.generation,"paused":store.paused,"hold":store.hold,
        "lastSlot":store.last_slot,"lowerCursor":store.lower_cursor,
        "historyGap":after.is_some_and(|cursor| cursor.saturating_add(1)<store.lower_cursor),
        "runs":store.runs.values().filter(|run|after.is_none_or(|cursor|run.slot>cursor)).map(|run|
            json!({"slot":run.slot,"missedSlots":run.missed_slots,"readAt":run.snapshot.read_at,
                "messageId":run.message_id,"disposition":run.disposition,
                "snapshotSha256":hash(&serde_json::to_vec(&run.snapshot).unwrap_or_default())})).collect::<Vec<_>>(),
        "pending":store.pending.as_ref().map(|p|json!({"slot":p.run.slot,"messageId":p.envelope.message_id,
            "disposition":"unresolved","expiresAt":p.envelope.expires_at})),
        "lastGood":store.last_good,"archives":store.archives,"modelRequests":0,"timerInstalled":false})
}

fn pause(state: &Path, store: &mut Store, reason: &str) -> Result<Value> {
    store.paused = true;
    store.hold = Some(reason.into());
    save(state, store)?;
    Ok(status(store, None))
}

/// Archive closed batches before forgetting their detailed rows. Old slots stay fenced.
fn compact(state: &Path, store: &mut Store) -> Result<bool> {
    if store.runs.len() < store.config.retention_slots * 2 {
        return Ok(true);
    }
    if store.archives.len() >= store.config.max_archives {
        return Ok(false);
    }
    let rows: BTreeMap<_, _> = store
        .runs
        .iter()
        .take(store.config.retention_slots)
        .map(|(slot, run)| (*slot, run.clone()))
        .collect();
    let first = *rows.first_key_value().context("support_archive_empty")?.0;
    let through = *rows.last_key_value().context("support_archive_empty")?.0;
    let bytes = serde_json::to_vec(&json!({"schema":1,"generation":store.generation,
        "firstSlot":first,"throughSlot":through,"runs":rows,"executableAuthority":false}))?;
    let directory = state.join("archives");
    std::fs::DirBuilder::new()
        .mode(0o700)
        .recursive(true)
        .create(&directory)?;
    // Validate the archive directory with the same owner/mode checks as live state.
    let _archive_lock = service::lock(&directory)?;
    let name = format!("{}-{first}-{through}.json", store.generation);
    let path = directory.join(&name);
    if path.try_exists()? {
        ensure!(
            service::private_read(&path, 1024 * 1024)? == bytes,
            "support_archive_conflict"
        );
    } else {
        service::exclusive_write(&path, &bytes)?;
        std::fs::File::open(&directory)?.sync_all()?;
    }
    service::fixture_fault(state, "support_after_archive")?;
    store.archives.push(Archive {
        file: name,
        sha256: hash(&bytes),
        first_slot: first,
        through_slot: through,
    });
    store.lower_cursor = through.checked_add(1).context("support_cursor_exhausted")?;
    store.runs.retain(|slot, _| *slot > through);
    save(state, store)?;
    Ok(true)
}

/// A durable outbox receipt is an observed delivery fact, not native completion.
fn deliver(state: &Path, store: &mut Store) -> Result<Value> {
    let pending = store.pending.as_ref().context("support_pending_missing")?;
    let id = pending.envelope.message_id;
    let outbox = state.join("outbox");
    let entry = client::inspect(&outbox)?["entries"]
        .get(id.to_string())
        .cloned();
    if let Some(entry) = &entry {
        ensure!(
            entry["envelope"] == serde_json::to_value(&pending.envelope)?,
            "support_outbox_conflict"
        );
    } else {
        if client::enqueue(&outbox, pending.envelope.clone(), &store.config.grant).is_err() {
            return pause(state, store, "support_outbox_unresolved");
        }
    }
    service::fixture_fault(state, "support_after_enqueue")?;
    let disposition = if let Some(known) = entry
        .as_ref()
        .and_then(|entry| entry["serviceState"].as_str())
    {
        known.to_owned()
    } else {
        match client::relay(
            &outbox,
            &store.config.socket,
            store.config.service_uid,
            &store.config.credential,
            &store.config.grant,
            id,
        ) {
            Ok(response) => response["state"]
                .as_str()
                .context("support_delivery_state_missing")?
                .to_owned(),
            Err(_) => return pause(state, store, "support_delivery_unresolved"),
        }
    };
    if !["service_accepted", "delivered", "consumed", "completed"].contains(&disposition.as_str()) {
        return pause(state, store, "support_delivery_requires_reconciliation");
    }
    service::fixture_fault(state, "support_after_relay")?;
    let mut pending = store.pending.take().context("support_pending_missing")?;
    pending.run.disposition = disposition;
    store.runs.insert(pending.run.slot, pending.run);
    save(state, store)?;
    Ok(status(store, None))
}

fn observe(state: &Path, store: &mut Store) -> Result<Value> {
    if store.paused {
        return Ok(status(store, None));
    }
    let now = observation_time(state)?;
    if now < store.observed_time {
        return pause(state, store, "support_clock_regression");
    }
    store.observed_time = now;
    if store.pending.is_some() {
        return deliver(state, store);
    }
    if now < store.config.start_at {
        return Ok(json!({"state":"not_due","modelRequests":0}));
    }
    let slot = (now - store.config.start_at) / store.config.interval_seconds;
    if store.last_slot.is_some_and(|previous| slot <= previous) {
        return Ok(
            json!({"state":"duplicate_slot","slot":slot,"receipt":store.runs.get(&slot),"modelRequests":0}),
        );
    }
    if !compact(state, store)? {
        return pause(state, store, "support_archive_capacity");
    }
    let snapshot = health::read(&store.config.probes)?;
    let issues: Vec<_> = snapshot
        .metrics
        .iter()
        .filter(|metric| metric.state != health::State::Ok)
        .map(|metric| (&metric.key, metric.state))
        .collect();
    let fingerprint = hash(&serde_json::to_vec(&issues)?);
    let changed = store
        .fingerprint
        .as_ref()
        .map_or(!issues.is_empty(), |prior| prior != &fingerprint);
    for metric in &snapshot.metrics {
        if metric.basis_points.is_some() {
            store.last_good.insert(
                metric.key.clone(),
                Baseline {
                    read_at: snapshot.read_at,
                    metric: metric.clone(),
                },
            );
        }
    }
    let missed = store.last_slot.map_or(slot, |previous| slot - previous - 1);
    let mut run = Run {
        slot,
        missed_slots: missed,
        snapshot,
        fingerprint: fingerprint.clone(),
        message_id: None,
        disposition: "quiet".into(),
    };
    store.last_slot = Some(slot);
    store.fingerprint = Some(fingerprint);
    if !changed {
        store.runs.insert(slot, run);
        save(state, store)?;
        return Ok(status(store, None));
    }
    let id = Uuid::new_v4();
    run.message_id = Some(id);
    run.disposition = "unresolved".into();
    let mut text = format!(
        "Status: Health observations\nState: Review the changed facts.\nRead at: {} UTC epoch seconds.\nSlot: {slot}\nSource: {}\nDone:\n",
        run.snapshot.read_at, store.generation
    );
    for metric in &run.snapshot.metrics {
        let value = metric.basis_points.map_or("unavailable".into(), |v| {
            format!("{}.{:02}%", v / 100, v % 100)
        });
        text.push_str(&format!("{}: {value}, {:?}.\n", metric.key, metric.state));
    }
    text.push_str("Next: Review these health facts. This report grants no maintenance authority.");
    let envelope = protocol::Envelope {
        protocol: protocol::VERSION,
        message_id: id,
        conversation_id: store.generation,
        assignment_id: None,
        reply_to: None,
        sender: store.config.grant.seat,
        incarnation: store.config.grant.incarnation,
        recipient: store.config.recipient,
        project: store.config.project.clone(),
        kind: protocol::MessageKind::Note,
        mode: protocol::DeliveryMode::WakeWhenIdle,
        created_at: now,
        expires_at: now.saturating_add(3600),
        text,
        artifact_refs: Vec::new(),
    };
    store.pending = Some(Pending { run, envelope });
    save(state, store)?;
    service::fixture_fault(state, "support_after_plan")?;
    deliver(state, store)
}

pub fn call(state: &Path, action: Action) -> Result<Value> {
    let _lock = service::lock(state)?;
    if let Action::Prepare { config } = action {
        let config = *config;
        config.validate()?;
        let digest = hash(&serde_json::to_vec(&config)?);
        if state.join("registry.json").try_exists()? {
            let prior = load(state)?;
            ensure!(
                prior.config_digest == digest,
                "support_configuration_conflict"
            );
            return Ok(status(&prior, None));
        }
        let store = Store {
            schema: 1,
            generation: Uuid::new_v4(),
            config,
            config_digest: digest,
            paused: true,
            hold: Some("operator_paused".into()),
            observed_time: service::now()?,
            last_slot: None,
            lower_cursor: 0,
            fingerprint: None,
            last_good: BTreeMap::new(),
            pending: None,
            runs: BTreeMap::new(),
            archives: Vec::new(),
        };
        save(state, &store)?;
        return Ok(status(&store, None));
    }
    let mut store = load(state)?;
    match action {
        Action::Tick => observe(state, &mut store),
        Action::Pause => pause(state, &mut store, "operator_paused"),
        Action::Resume => {
            store.paused = false;
            store.hold = None;
            save(state, &store)?;
            Ok(status(&store, None))
        }
        Action::Status { after } => Ok(status(&store, after)),
        Action::Read { slot } => {
            if slot < store.lower_cursor {
                let archive = store
                    .archives
                    .iter()
                    .find(|a| a.first_slot <= slot && slot <= a.through_slot);
                let run = if let Some(archive) = archive {
                    let bytes = service::private_read(
                        &state.join("archives").join(&archive.file),
                        1024 * 1024,
                    )?;
                    ensure!(hash(&bytes) == archive.sha256, "support_archive_changed");
                    let facts: Value = serde_json::from_slice(&bytes)?;
                    facts["runs"].get(slot.to_string()).cloned()
                } else {
                    None
                };
                return Ok(json!({"state":if run.is_some(){"archived"}else{"gap"},
                    "historyGap":true,"lowerCursor":store.lower_cursor,"archive":archive,"run":run}));
            }
            let run = store.runs.get(&slot);
            let pending = store
                .pending
                .as_ref()
                .filter(|p| p.run.slot == slot)
                .map(|p| &p.run);
            let missed = run.is_none()
                && pending.is_none()
                && store.last_slot.is_some_and(|last| slot <= last);
            Ok(
                json!({"state":if pending.is_some(){"pending"}else if run.is_some(){"retained"}else if missed{"gap"}else{"not_observed"},
                "run":run,"pending":pending,"historyGap":missed}),
            )
        }
        Action::Export { output } => {
            ensure!(output.is_absolute(), "support_archive_path_invalid");
            let mut archives = Vec::new();
            for archive in &store.archives {
                ensure!(
                    Path::new(&archive.file).components().count() == 1,
                    "support_archive_name_invalid"
                );
                let bytes = service::private_read(
                    &state.join("archives").join(&archive.file),
                    1024 * 1024,
                )?;
                ensure!(hash(&bytes) == archive.sha256, "support_archive_changed");
                archives.push(serde_json::from_slice::<Value>(&bytes)?);
            }
            let value = json!({"schema":1,"generation":store.generation,"archives":archives,"runs":store.runs,
                "pending":store.pending.as_ref().map(|p|&p.run),"lowerCursor":store.lower_cursor,
                "lastGood":store.last_good,"executableAuthority":false,"restorable":false});
            let bytes = serde_json::to_vec(&value)?;
            service::exclusive_write(&output, &bytes)?;
            std::fs::File::open(output.parent().context("support_archive_parent_missing")?)?
                .sync_all()?;
            Ok(
                json!({"exported":true,"sha256":hash(&bytes),"executableAuthority":false,"restorable":false}),
            )
        }
        Action::Prepare { .. } => unreachable!(),
    }
}
