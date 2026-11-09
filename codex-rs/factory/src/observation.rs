//! Scoped metadata and replay for a read-only observer. No execution authority.
use crate::protocol;
use crate::recording;
use crate::service;
use anyhow::Context;
use anyhow::Result;
use anyhow::ensure;
use serde::Deserialize;
use serde::Serialize;
use serde_json::Value;
use serde_json::json;
use sha2::Digest;
use sha2::Sha256;
use std::collections::BTreeMap;
use std::collections::BTreeSet;
use std::collections::VecDeque;
use std::os::unix::fs::MetadataExt;
use std::path::Path;
use uuid::Uuid;

const HISTORY: usize = 128;
const CURSORS: usize = 256;
const FRESH_SECONDS: u64 = 15;

#[derive(Deserialize, Serialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub(crate) struct Archive {
    history: VecDeque<Change>,
    gaps: BTreeMap<String, Gap>,
}

impl Archive {
    pub(crate) fn merge(&mut self, earlier: Self) -> Result<()> {
        let known: BTreeSet<Uuid> = self.history.iter().map(|change| change.id).collect();
        for change in earlier.history {
            if !known.contains(&change.id) {
                self.history.push_back(change);
            }
        }
        for (project, gap) in earlier.gaps {
            self.gaps.entry(project).or_insert(gap);
        }
        ensure!(
            self.history.len() <= 1024 && self.gaps.len() <= 128,
            "archive_history_capacity"
        );
        Ok(())
    }
}

pub(crate) fn archive(state: &Path) -> Result<Archive> {
    let _lock = service::lock(state)?;
    let journal = load(state)?;
    Ok(Archive {
        history: journal.history,
        gaps: journal.gaps,
    })
}

pub(crate) fn recover(state: &Path, reason: &str) -> Result<()> {
    let _lock = service::lock(state)?;
    let mut journal = load(state)?;
    journal.epoch = Uuid::new_v4();
    for presence in journal.presence.values() {
        journal.gaps.insert(
            presence.project.clone(),
            Gap {
                reason: reason.into(),
                at: service::now()?,
            },
        );
    }
    journal.presence.clear();
    journal.cursors.clear();
    save(state, &journal)
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct ProcessLease {
    pub pid: u32,
    pub boot_id: Uuid,
    pub start_ticks: String,
}

#[derive(Clone, Copy, Debug, Deserialize, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum ModelActivity {
    Busy,
    Idle,
    Unknown,
}

#[derive(Clone, Copy, Debug, Deserialize, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum TaskProgress {
    Unverified,
    InProgress,
    Blocked,
    ReportedCompleted,
    Unknown,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct PresenceReport {
    pub process: ProcessLease,
    pub model_activity: ModelActivity,
    pub task_progress: TaskProgress,
}

#[derive(Clone, Deserialize, Serialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct Presence {
    seat: Uuid,
    project: String,
    incarnation: Uuid,
    report: PresenceReport,
    at: u64,
    #[serde(default)]
    host_attested: bool,
}

#[derive(Clone, Deserialize, Serialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct Change {
    sequence: u64,
    id: Uuid,
    seat: Uuid,
    project: String,
    at: u64,
    kind: String,
    terminal: Option<recording::Event>,
}

#[derive(Deserialize, Serialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct Cursor {
    scope: String,
    epoch: Uuid,
    after: u64,
    anchor: Option<Uuid>,
    issued_at: u64,
}

#[derive(Clone, Deserialize, Serialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct Gap {
    reason: String,
    at: u64,
}

#[derive(Deserialize, Serialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct Journal {
    storage_version: u32,
    epoch: Uuid,
    next_sequence: u64,
    observed_time: u64,
    history: VecDeque<Change>,
    seen_terminals: BTreeMap<Uuid, String>,
    presence: BTreeMap<String, Presence>,
    cursors: BTreeMap<Uuid, Cursor>,
    gaps: BTreeMap<String, Gap>,
}

fn load(state: &Path) -> Result<Journal> {
    if !state.join("registry.json").try_exists()? {
        return Ok(Journal {
            storage_version: 2,
            epoch: Uuid::new_v4(),
            next_sequence: 0,
            observed_time: 0,
            history: VecDeque::new(),
            seen_terminals: BTreeMap::new(),
            presence: BTreeMap::new(),
            cursors: BTreeMap::new(),
            gaps: BTreeMap::new(),
        });
    }
    let mut journal: Journal = serde_json::from_slice(&service::private_read(
        &state.join("registry.json"),
        2 * 1024 * 1024,
    )?)?;
    ensure!(
        (1..=2).contains(&journal.storage_version) && !journal.epoch.is_nil(),
        "observation_storage_unsupported"
    );
    ensure!(
        journal.history.len() <= HISTORY
            && journal.seen_terminals.len() <= 128
            && journal.presence.len() <= 128
            && journal.cursors.len() <= CURSORS
            && journal.gaps.len() <= 128,
        "observation_storage_bound"
    );
    let mut previous = 0;
    for change in &journal.history {
        ensure!(
            change.sequence > previous && change.sequence <= journal.next_sequence,
            "observation_history_invalid"
        );
        previous = change.sequence;
    }
    journal.storage_version = 2;
    Ok(journal)
}

fn save(state: &Path, journal: &Journal) -> Result<()> {
    service::atomic_write(state, &serde_json::to_vec(journal)?)
}

fn append(journal: &mut Journal, mut change: Change) -> Result<()> {
    journal.next_sequence = journal
        .next_sequence
        .checked_add(1)
        .context("observation_sequence_exhausted")?;
    change.sequence = journal.next_sequence;
    journal.history.push_back(change);
    while journal.history.len() > HISTORY {
        journal.history.pop_front();
    }
    Ok(())
}

pub(crate) fn terminal(state: &Path, id: Uuid, event: recording::Event, time: u64) -> Result<()> {
    let _lock = service::lock(state)?;
    let mut journal = load(state)?;
    let fingerprint = format!("{:x}", Sha256::digest(serde_json::to_vec(&event)?));
    if let Some(prior) = journal.seen_terminals.get(&id) {
        ensure!(prior == &fingerprint, "observation_terminal_conflict");
        return Ok(());
    }
    ensure!(
        journal.seen_terminals.len() < 128,
        "observation_terminal_bound"
    );
    journal.observed_time = journal.observed_time.max(time);
    journal.seen_terminals.insert(id, fingerprint);
    append(
        &mut journal,
        Change {
            sequence: 0,
            id,
            seat: event.seat,
            project: event.project.clone(),
            at: time,
            kind: "native_terminal".into(),
            terminal: Some(event),
        },
    )?;
    save(state, &journal)
}

fn validate_process(process: &ProcessLease) -> Result<()> {
    ensure!(
        !process.boot_id.is_nil()
            && process.pid > 1
            && process.start_ticks.len() <= 32
            && !process.start_ticks.is_empty()
            && process.start_ticks.bytes().all(|c| c.is_ascii_digit()),
        "observation_process_invalid"
    );
    Ok(())
}

pub(crate) fn process_health(process: &ProcessLease, uid: u32) -> Result<&'static str> {
    validate_process(process)?;
    let boot = Uuid::parse_str(std::fs::read_to_string("/proc/sys/kernel/random/boot_id")?.trim())?;
    if boot != process.boot_id {
        return Ok("replaced");
    }
    let path = std::path::PathBuf::from(format!("/proc/{}", process.pid));
    let info = match std::fs::metadata(&path) {
        Ok(value) => value,
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => return Ok("dead"),
        Err(error) if error.kind() == std::io::ErrorKind::PermissionDenied => return Ok("unknown"),
        Err(error) => return Err(error.into()),
    };
    if info.uid() != uid {
        return Ok("different_owner");
    }
    let stat = match std::fs::read_to_string(path.join("stat")) {
        Ok(value) => value,
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => return Ok("dead"),
        Err(error) if error.kind() == std::io::ErrorKind::PermissionDenied => return Ok("unknown"),
        Err(error) => return Err(error.into()),
    };
    let mut fields = stat
        .rsplit_once(')')
        .context("observation_process_stat_invalid")?
        .1
        .split_whitespace();
    let status = fields.next().context("observation_process_stat_invalid")?;
    let start = fields.nth(18).context("observation_process_stat_invalid")?;
    if start != process.start_ticks {
        return Ok("replaced");
    }
    Ok(if status == "Z" || status == "X" {
        "dead"
    } else {
        "alive"
    })
}

pub(crate) fn report(
    state: &Path,
    grant: &protocol::Grant,
    project: &str,
    report: PresenceReport,
    time: u64,
    host_attested: bool,
) -> Result<Value> {
    validate_process(&report.process)?;
    ensure!(
        host_attested || process_health(&report.process, grant.agent_uid)? == "alive",
        "observation_process_unproven"
    );
    let _lock = service::lock(state)?;
    let mut journal = load(state)?;
    let key = format!("{project}/{}", grant.seat);
    ensure!(
        journal.presence.contains_key(&key) || journal.presence.len() < 128,
        "observation_presence_bound"
    );
    let time = time.max(journal.observed_time);
    journal.observed_time = time;
    journal.presence.insert(
        key,
        Presence {
            seat: grant.seat,
            project: project.into(),
            incarnation: grant.incarnation,
            report,
            at: time,
            host_attested,
        },
    );
    append(
        &mut journal,
        Change {
            sequence: 0,
            id: Uuid::new_v4(),
            seat: grant.seat,
            project: project.into(),
            at: time,
            kind: "presence".into(),
            terminal: None,
        },
    )?;
    save(state, &journal)?;
    Ok(json!({"ok":true,"state":"reported","freshForSeconds":FRESH_SECONDS}))
}

pub(crate) struct KnownSeat {
    pub grant: protocol::Grant,
    pub revoked: bool,
}

fn scope(grant: &protocol::Grant, project: &str) -> Result<String> {
    Ok(format!(
        "{:x}",
        Sha256::digest(serde_json::to_vec(&json!([
            grant.seat,
            grant.incarnation,
            project,
            grant.recipients
        ]))?)
    ))
}

fn cursor(journal: &mut Journal, scope: &str, time: u64) -> Uuid {
    let anchor = journal.history.back().map(|event| event.id);
    if let Some((id, _)) = journal.cursors.iter().find(|(_, cursor)| {
        cursor.scope == scope
            && cursor.epoch == journal.epoch
            && cursor.after == journal.next_sequence
            && cursor.anchor == anchor
    }) {
        return *id;
    }
    if journal.cursors.len() >= CURSORS
        && let Some(id) = journal
            .cursors
            .iter()
            .min_by_key(|(id, cursor)| (cursor.issued_at, **id))
            .map(|(id, _)| *id)
    {
        journal.cursors.remove(&id);
    }
    let id = Uuid::new_v4();
    journal.cursors.insert(
        id,
        Cursor {
            scope: scope.into(),
            epoch: journal.epoch,
            after: journal.next_sequence,
            anchor,
            issued_at: time,
        },
    );
    id
}

pub(crate) fn read(
    state: &Path,
    reader: &protocol::Grant,
    project: &str,
    after: Option<Uuid>,
    agent_id: Option<Uuid>,
    known: &[KnownSeat],
    time: u64,
) -> Result<Value> {
    ensure!(
        reader.role == protocol::Role::Observer
            && reader.operations == BTreeSet::from([protocol::Operation::Observe]),
        "observation_reader_role_denied"
    );
    let _lock = service::lock(state)?;
    let mut journal = load(state)?;
    let time = time.max(journal.observed_time);
    journal.observed_time = time;
    let scope = scope(reader, project)?;
    let mut gap = None;
    if let Some(id) = after {
        if let Some(cursor) = journal.cursors.get(&id) {
            ensure!(cursor.scope == scope, "observation_cursor_scope_denied");
            let first = journal
                .history
                .front()
                .map(|entry| entry.sequence)
                .unwrap_or(journal.next_sequence.saturating_add(1));
            let anchor = journal
                .history
                .iter()
                .find(|entry| entry.sequence == cursor.after)
                .map(|entry| entry.id);
            if cursor.epoch != journal.epoch || cursor.after > journal.next_sequence {
                gap = Some("journal_restored");
            } else if cursor.after.saturating_add(1) < first {
                gap = Some("history_pruned");
            } else if cursor.after > 0 && anchor != cursor.anchor {
                gap = Some("anchor_unavailable");
            }
        } else {
            gap = Some("cursor_unknown_or_expired");
        }
    }
    if let Some(reason) = gap {
        ensure!(
            journal.gaps.contains_key(&scope) || journal.gaps.len() < 128,
            "observation_reader_bound"
        );
        journal.gaps.insert(
            scope.clone(),
            Gap {
                reason: reason.into(),
                at: time,
            },
        );
    }
    let visible: Vec<_> = known
        .iter()
        .filter(|seat| {
            seat.grant.role != protocol::Role::Observer
                && reader.recipients.contains(&seat.grant.seat)
                && seat.grant.projects.contains(project)
        })
        .collect();
    ensure!(
        agent_id.is_none_or(|id| visible.iter().any(|seat| seat.grant.seat == id)),
        "observation_agent_scope_denied"
    );
    let mut agents = Vec::new();
    let mut details = BTreeMap::new();
    for known in &visible {
        let grant = &known.grant;
        let pulse = journal
            .presence
            .get(&format!("{project}/{}", grant.seat))
            .filter(|presence| presence.incarnation == grant.incarnation);
        let fresh = pulse.is_some_and(|pulse| time.saturating_sub(pulse.at) <= FRESH_SECONDS)
            && !known.revoked;
        let health = if let Some(pulse) = pulse {
            if pulse.host_attested {
                if fresh { "reported_alive" } else { "unknown" }
            } else {
                process_health(&pulse.report.process, grant.agent_uid)?
            }
        } else {
            "unknown"
        };
        let connectivity = if known.revoked {
            "revoked"
        } else if pulse.is_none() {
            "never_reported"
        } else if fresh {
            "fresh_report"
        } else {
            "stale_report"
        };
        let model = if fresh && (health == "alive" || health == "reported_alive") {
            pulse
                .map(|pulse| pulse.report.model_activity)
                .unwrap_or(ModelActivity::Unknown)
        } else {
            ModelActivity::Unknown
        };
        let task = if fresh {
            pulse
                .map(|pulse| pulse.report.task_progress)
                .unwrap_or(TaskProgress::Unknown)
        } else {
            TaskProgress::Unknown
        };
        let model_text = serde_json::to_value(model)?
            .as_str()
            .context("observation_model_invalid")?
            .to_owned();
        let task_text = serde_json::to_value(task)?
            .as_str()
            .context("observation_task_invalid")?
            .to_owned();
        let coarse = if !fresh {
            "unknown"
        } else if health != "alive" && health != "reported_alive" {
            "error"
        } else {
            match model {
                ModelActivity::Busy => "working",
                ModelActivity::Idle => "idle",
                ModelActivity::Unknown => "unknown",
            }
        };
        let name = format!("seat-{}", &grant.seat.to_string()[..8]);
        let at = pulse.map(|pulse| pulse.at.saturating_mul(1000));
        let agent = json!({"id":grant.seat,"name":name,"runtime":"codex","state":coarse,
            "last_seen_ms":at,"host_id":grant.host});
        agents.push(agent.clone());
        if agent_id != Some(grant.seat) {
            continue;
        }
        let process_source = if pulse.is_some_and(|pulse| pulse.host_attested) {
            "authenticated host report; source proxy checked UID, boot ID and start ticks"
        } else {
            "kernel UID, boot ID and start ticks"
        };
        let stamp = time.saturating_mul(1000);
        let mut events = vec![
            json!({"at_ms":stamp,"kind":"Connectivity","text":format!("Connectivity: {connectivity}. Source: authenticated report freshness.")}),
            json!({"at_ms":stamp,"kind":"Process health","text":format!("Process health: {health}. Source: {process_source}.")}),
            json!({"at_ms":stamp,"kind":"Model activity","text":format!("Model activity: {model_text}. Source: authenticated recipient report.")}),
            json!({"at_ms":stamp,"kind":"Task progress","text":format!("Task progress: {task_text}. This is a separate report, not a model completion verdict.")}),
        ];
        if let Some(gap) = journal.gaps.get(&scope) {
            events.push(json!({"at_ms":gap.at.saturating_mul(1000),"kind":"History gap",
                "text":format!("History gap observed: {}. The observer requires a fresh snapshot.",gap.reason)}));
        }
        for change in journal
            .history
            .iter()
            .rev()
            .filter(|entry| entry.seat == grant.seat && entry.project == project)
        {
            if let Some(terminal) = &change.terminal {
                events.push(json!({"at_ms":change.at.saturating_mul(1000),"kind":"Native terminal",
                    "text":format!("Message {}. Assignment {}. Thread {}. Turn {}. Outcome {:?}. Recording ID {}.",
                        terminal.message_id,terminal.assignment_id.map(|id|id.to_string()).unwrap_or_else(||"unknown".into()),
                        terminal.thread_id,terminal.turn_id,terminal.outcome,change.id)}));
                if events.len() >= 20 {
                    break;
                }
            }
        }
        details.insert(grant.seat.to_string(),json!({"agent":agent,"recent_events":events,
            "observation":{"connectivity":connectivity,"processHealth":health,"modelActivity":model,
                "taskProgress":task,"fresh":fresh,"incarnation":grant.incarnation,"readOnly":true}}));
    }
    // Freshness and kernel health can change without a new journal entry.
    let changed: BTreeSet<_> = visible.iter().map(|seat| seat.grant.seat).collect();
    let next = cursor(&mut journal, &scope, time);
    let result = json!({"ok":true,"agents":agents,"details":details,"cursor":next,
        "reset":after.is_none() || gap.is_some(),"gap":gap,
        "changed":changed.into_iter().map(|id|json!({"kind":"agent","id":id})).collect::<Vec<_>>()});
    ensure!(
        serde_json::to_vec(&result)?.len() < protocol::MAX_FRAME - 1,
        "observation_response_bound"
    );
    save(state, &journal)?;
    Ok(result)
}

pub fn prune(state: &Path, keep: usize) -> Result<Value> {
    ensure!(keep <= HISTORY, "observation_retention_bound");
    let _lock = service::lock(state)?;
    let mut journal = load(state)?;
    while journal.history.len() > keep {
        journal.history.pop_front();
    }
    save(state, &journal)?;
    Ok(json!({"ok":true,"retained":journal.history.len()}))
}
