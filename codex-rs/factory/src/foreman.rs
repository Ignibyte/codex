//! Dispatch receipts for canonical Forge pipeline runs. Forge owns ticket/run state.
use crate::manager::handoff;
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
use std::os::unix::fs::MetadataExt;
use std::path::Path;
use std::path::PathBuf;
use uuid::Uuid;

pub mod managed;
pub mod supervision;

#[derive(Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct Config {
    bridge_state: PathBuf,
    forge_config: PathBuf,
    foreman: Uuid,
    repository: PathBuf,
    worktree_root: PathBuf,
    #[serde(default)]
    manager_required: bool,
}

#[derive(Clone, Deserialize, Serialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct ContextReceipt {
    run: Uuid,
    incarnation: Uuid,
    digest: String,
    at: u64,
}

#[derive(Clone, Deserialize, Serialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct Assignment {
    ticket: i64,
    generation: Uuid,
    worker: protocol::Grant,
    context_digest: String,
    brief: protocol::Envelope,
    spec: Option<protocol::Envelope>,
    spec_digest: Option<String>,
    workspace: Option<PathBuf>,
    /// Local dispatch disposition, never canonical ticket status.
    disposition: String,
    lease_until: Option<u64>,
    lease_tick: Option<u64>,
    execution: Option<Execution>,
    work_branch: String,
    base_sha: String,
    reconciliation: Option<String>,
    result: Option<Candidate>,
    #[serde(default)]
    manager_handoff: Option<handoff::Binding>,
    #[serde(default)]
    result_return: Option<ResultReturn>,
}

#[derive(Clone, Deserialize, Serialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct ResultReturn {
    envelope: protocol::Envelope,
    queued: bool,
}

#[derive(Clone, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct Artifact {
    path: PathBuf,
    sha256: String,
}

#[derive(Clone, Deserialize, Serialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct Candidate {
    event_id: Uuid,
    terminal: recording::Event,
    artifacts: Vec<Artifact>,
}

#[derive(Clone, Deserialize, Serialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct Execution {
    pid: u32,
    boot: Uuid,
    start_ticks: String,
    unit: String,
    cgroup: PathBuf,
}

impl Execution {
    fn observe(pid: u32, worker: &protocol::Grant) -> Result<Self> {
        ensure!(pid > 1, "foreman_process_invalid");
        let root = PathBuf::from(format!("/proc/{pid}"));
        ensure!(
            root.metadata()?.uid() == worker.agent_uid,
            "foreman_process_uid_mismatch"
        );
        let stat = std::fs::read_to_string(root.join("stat"))?;
        let fields: Vec<_> = stat
            .rsplit_once(')')
            .context("foreman_process_identity_invalid")?
            .1
            .split_whitespace()
            .collect();
        ensure!(
            fields.len() > 19 && !["Z", "X"].contains(&fields[0]),
            "foreman_process_not_live"
        );
        let raw = std::fs::read_to_string(root.join("cgroup"))?;
        let group = raw
            .lines()
            .find_map(|line| line.strip_prefix("0::"))
            .context("foreman_cgroup_v2_required")?;
        let unit = format!("cf-worker-{}.service", worker.seat);
        ensure!(
            Path::new(group)
                .file_name()
                .is_some_and(|name| name == unit.as_str())
                && !Path::new(group)
                    .components()
                    .any(|part| part == std::path::Component::ParentDir),
            "foreman_cgroup_binding_denied"
        );
        Ok(Self {
            pid,
            boot: boot()?,
            start_ticks: fields[19].into(),
            unit,
            cgroup: Path::new("/sys/fs/cgroup").join(group.trim_start_matches('/')),
        })
    }
    fn live(&self) -> Result<bool> {
        if self.boot != boot()? {
            return Ok(false);
        }
        let stat = match std::fs::read_to_string(format!("/proc/{}/stat", self.pid)) {
            Ok(value) => value,
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => return Ok(false),
            Err(error) => return Err(error.into()),
        };
        let fields: Vec<_> = stat
            .rsplit_once(')')
            .context("foreman_process_identity_invalid")?
            .1
            .split_whitespace()
            .collect();
        ensure!(fields.len() > 19, "foreman_process_identity_invalid");
        Ok(fields[19] == self.start_ticks && !["Z", "X"].contains(&fields[0]))
    }
    fn empty(&self) -> Result<bool> {
        let path = self.cgroup.join("cgroup.events");
        match std::fs::read_to_string(path) {
            Ok(value) => Ok(value.lines().any(|line| line == "populated 0")),
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => Ok(true),
            Err(error) => Err(error.into()),
        }
    }
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct Reconciliation {
    run_id: Uuid,
    generation: Uuid,
    confirmed_no_outstanding_effects: bool,
    evidence: Vec<String>,
}

#[derive(Deserialize, Serialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct Store {
    schema: u32,
    boot: Uuid,
    config_digest: String,
    config_path: Option<PathBuf>,
    assignments: BTreeMap<Uuid, Assignment>,
    contexts: BTreeMap<Uuid, ContextReceipt>,
    /// Persist before a non-idempotent remote start. Missing remote evidence remains uncertain.
    attempts: BTreeMap<Uuid, Value>,
    recovered_runs: std::collections::BTreeSet<Uuid>,
    #[serde(default)]
    supervision: supervision::Journal,
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct RecoveryReceipt {
    ticket: i64,
    prior_runs: std::collections::BTreeSet<Uuid>,
    confirmed_all_prior_processes_stopped: bool,
    confirmed_no_outstanding_effects: bool,
    evidence: Vec<String>,
}

#[derive(Deserialize)]
#[serde(tag = "action", rename_all = "snake_case", deny_unknown_fields)]
pub enum Action {
    Initialize,
    Claim {
        operation_id: Uuid,
        ticket: Option<i64>,
        worker: Uuid,
        context: String,
        brief: String,
        #[serde(default)]
        manager_job: Option<Uuid>,
    },
    RegisterWorkspace {
        run_id: Uuid,
        workspace: PathBuf,
    },
    IssueSpec {
        run_id: Uuid,
        spec: Option<String>,
        lease_seconds: u64,
    },
    RegisterExecution {
        run_id: Uuid,
        pid: u32,
    },
    QuarantineExpired {
        run_id: Uuid,
    },
    Fence {
        run_id: Uuid,
    },
    Reconcile {
        run_id: Uuid,
        report: PathBuf,
    },
    RecoverTicket {
        ticket: i64,
        report: PathBuf,
    },
    Status {
        run_id: Uuid,
    },
    ForwardResult {
        run_id: Uuid,
    },
}

fn digest(bytes: &[u8]) -> String {
    format!("{:x}", Sha256::digest(bytes))
}
fn tick() -> Result<u64> {
    let mut time = libc::timespec {
        tv_sec: 0,
        tv_nsec: 0,
    };
    // SAFETY: clock_gettime writes this live timespec and uses a Linux monotonic clock.
    ensure!(
        unsafe { libc::clock_gettime(libc::CLOCK_BOOTTIME, &mut time) } == 0 && time.tv_sec >= 0,
        "foreman_monotonic_clock_unavailable"
    );
    Ok(time.tv_sec as u64)
}
fn configuration_digest(bytes: &[u8], forge: &recording::Config) -> Result<String> {
    Ok(digest(&serde_json::to_vec(&json!([digest(bytes), forge]))?))
}
fn boot() -> Result<Uuid> {
    Ok(Uuid::parse_str(
        std::fs::read_to_string("/proc/sys/kernel/random/boot_id")?.trim(),
    )?)
}
fn load(state: &Path, config_digest: Option<&str>) -> Result<Store> {
    if !state.join("registry.json").try_exists()? {
        return Ok(Store {
            schema: 3,
            boot: boot()?,
            config_digest: config_digest.context("foreman_not_initialized")?.into(),
            config_path: None,
            assignments: BTreeMap::new(),
            contexts: BTreeMap::new(),
            attempts: BTreeMap::new(),
            recovered_runs: std::collections::BTreeSet::new(),
            supervision: supervision::Journal::default(),
        });
    }
    let mut store: Store = serde_json::from_slice(&service::private_read(
        &state.join("registry.json"),
        2 * 1024 * 1024,
    )?)?;
    ensure!(
        matches!(store.schema, 1..=3)
            && store.assignments.len() <= 128
            && store.attempts.len() <= 128
            && store.contexts.len() <= 128
            && store.recovered_runs.len() <= 128,
        "foreman_store_unsupported_or_full"
    );
    store.supervision.validate()?;
    store.schema = 3;
    ensure!(
        store.boot == boot()?,
        "foreman_boot_reconciliation_required"
    );
    if let Some(expected) = config_digest {
        ensure!(store.config_digest == expected, "foreman_config_changed");
    }
    Ok(store)
}
fn save(state: &Path, store: &Store) -> Result<()> {
    service::atomic_write(state, &serde_json::to_vec(store)?)
}
async fn canonical(
    forge: &recording::Config,
    client: &reqwest::Client,
    ticket: i64,
) -> Result<Value> {
    recording::call(
        forge,
        client,
        "pipeline-context",
        json!({"ticket_number":ticket,"project_id":forge.forge_project}),
    )
    .await
}

async fn branches(
    forge: &recording::Config,
    client: &reqwest::Client,
    ticket: i64,
    branch: &str,
) -> Result<Value> {
    recording::call(
        forge,
        client,
        "pipeline-branch-map",
        json!({"ticket_number":ticket,"branch":branch,
        "include_stale":true,"limit":500,"project_id":forge.forge_project}),
    )
    .await
}

async fn prior_runs(
    forge: &recording::Config,
    client: &reqwest::Client,
    ticket: i64,
) -> Result<Vec<Value>> {
    let listed = recording::call(
        forge,
        client,
        "pipeline-branch-map",
        json!({"ticket_number":ticket,
        "include_stale":true,"limit":500,"project_id":forge.forge_project}),
    )
    .await?;
    let rows = listed["rows"]
        .as_array()
        .context("foreman_history_missing")?;
    ensure!(
        rows.len() < 500,
        "foreman_history_boundary_requires_operator"
    );
    Ok(rows
        .iter()
        .filter(|row| {
            row["work_branch"]
                .as_str()
                .is_some_and(|branch| branch.starts_with("codex/factory-"))
        })
        .cloned()
        .collect())
}

async fn git_text(path: &Path, args: &[&str]) -> Result<String> {
    use std::process::Stdio;
    use tokio::io::AsyncReadExt;
    let mut child = tokio::process::Command::new("/usr/bin/git")
        .args(["-c", &format!("safe.directory={}", path.display()), "-C"])
        .arg(path)
        .args(args)
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
        .context("foreman_git_output_missing")?
        .take(8193);
    let result = tokio::time::timeout(std::time::Duration::from_secs(3), async {
        output.read_to_end(&mut bytes).await?;
        ensure!(
            child.wait().await?.success() && bytes.len() <= 8192,
            "foreman_git_failed"
        );
        Ok::<(), anyhow::Error>(())
    })
    .await;
    if result.is_err() {
        child.kill().await?;
        child.wait().await?;
    }
    result.context("foreman_git_timeout")??;
    Ok(String::from_utf8(bytes)?.trim().into())
}
fn owns(context: &Value, run: Uuid, assignment: &Assignment) -> Result<()> {
    ensure!(
        context["run"]["id"] == run.to_string()
            && context["run"]["owner_id"] == assignment.generation.to_string()
            && context["run"]["status"] == "running",
        "foreman_canonical_owner_inactive"
    );
    ensure!(
        !["done", "closed", "qa-review"]
            .contains(&context["ticket"]["status"].as_str().unwrap_or("")),
        "foreman_ticket_not_executable"
    );
    Ok(())
}
fn message(
    sender: &protocol::Grant,
    worker: &protocol::Grant,
    project: &str,
    run: Uuid,
    text: String,
    kind: protocol::MessageKind,
) -> Result<protocol::Envelope> {
    ensure!(
        !text.is_empty() && text.len() <= 16384,
        "foreman_message_capacity"
    );
    let at = service::now()?;
    Ok(protocol::Envelope {
        protocol: protocol::VERSION,
        message_id: Uuid::new_v4(),
        conversation_id: run,
        assignment_id: Some(run),
        reply_to: None,
        sender: sender.seat,
        incarnation: sender.incarnation,
        recipient: worker.seat,
        project: project.into(),
        kind,
        mode: protocol::DeliveryMode::WakeWhenIdle,
        created_at: at,
        expires_at: at + 3600,
        text,
        artifact_refs: Vec::new(),
    })
}

/// Operator-only workflow adapter. The private configuration contains a scoped signer path, not model credentials.
pub async fn apply(state: &Path, config_path: &Path, action: Action) -> Result<Value> {
    let bytes = service::private_read(config_path, 16384)?;
    let config: Config = serde_json::from_slice(&bytes)?;
    ensure!(
        state == config.bridge_state.join("foreman"),
        "foreman_state_binding_invalid"
    );
    let (forge, client) = recording::connection(&config.forge_config)?;
    let config_digest = configuration_digest(&bytes, &forge)?;
    let known_specs = service::operator_spec_ids(&config.bridge_state, &forge.local_project)?;
    let local_host = service::operator_host(&config.bridge_state)?;
    // Never take the bridge lock while retaining the dispatch lock. Authenticated ACKs use bridge -> dispatch.
    let sender = if matches!(
        &action,
        Action::Initialize
            | Action::Claim { .. }
            | Action::IssueSpec { .. }
            | Action::RegisterExecution { .. }
            | Action::ForwardResult { .. }
    ) {
        Some(service::operator_grant(
            &config.bridge_state,
            config.foreman,
        )?)
    } else {
        None
    };
    let claim_worker = if let Action::Claim { worker, .. } = &action {
        Some(service::operator_grant(&config.bridge_state, *worker)?)
    } else {
        None
    };
    let manager_claim = if let Action::Claim { manager_job, .. } = &action {
        if let Some(job) = manager_job {
            Some(
                handoff::accepted(
                    &config.bridge_state.join("manager"),
                    *job,
                    sender.as_ref().context("foreman_sender_missing")?,
                    &forge.local_project,
                )?
                .0,
            )
        } else {
            ensure!(!config.manager_required, "foreman_manager_handoff_required");
            None
        }
    } else {
        None
    };
    let _lock = service::lock(state)?;
    let mut store = load(state, Some(&config_digest))?;
    store.config_path = Some(config_path.canonicalize()?);
    match action {
        Action::Initialize => {
            let sender = sender.context("foreman_sender_missing")?;
            ensure!(
                sender.role == protocol::Role::Foreman
                    && sender.projects.contains(&forge.local_project)
                    && sender.operations.contains(&protocol::Operation::Queue),
                "foreman_role_required"
            );
            save(state, &store)?;
            Ok(
                json!({"ok":true,"prepared":true,"managerRequired":config.manager_required,"foreman":sender.seat,"project":forge.local_project}),
            )
        }
        Action::Claim {
            operation_id,
            ticket,
            worker: _,
            mut context,
            brief,
            manager_job: _,
        } => {
            let ticket = if let Some(binding) = &manager_claim {
                ensure!(
                    ticket.is_none_or(|number| number == binding.ticket),
                    "foreman_handoff_ticket_denied"
                );
                binding.ticket
            } else {
                ticket.context("foreman_ticket_required")?
            };
            ensure!(
                !operation_id.is_nil()
                    && ticket > 0
                    && context.len() <= 8192
                    && !context.is_empty()
                    && brief.len() <= 4096
                    && !brief.is_empty(),
                "foreman_claim_invalid"
            );
            let worker = claim_worker.context("foreman_worker_missing")?;
            ensure!(
                worker.role == protocol::Role::Worker
                    && worker.projects.contains(&forge.local_project),
                "foreman_worker_scope_denied"
            );
            let sender = sender.context("foreman_sender_missing")?;
            ensure!(
                sender.role == protocol::Role::Foreman,
                "foreman_role_required"
            );
            ensure!(
                worker.host == sender.host && worker.host == local_host,
                "foreman_remote_supervisor_required"
            );
            ensure!(
                sender.recipients.contains(&worker.seat)
                    && sender.projects.contains(&forge.local_project)
                    && sender.operations.contains(&protocol::Operation::Queue),
                "foreman_worker_contact_denied"
            );
            if let Some(binding) = &manager_claim {
                ensure!(binding.ticket == ticket, "foreman_handoff_ticket_denied");
                handoff::current(&forge, &client, binding).await?;
                context = format!(
                    "Intent: {}\nManager job: {}\nCanonical spec: {}\nSpec SHA256: {}\n{}",
                    binding.intent_id, binding.job_id, binding.spec_id, binding.sha256, context
                );
                ensure!(context.len() <= 8192, "foreman_context_bound");
            }
            let mut fingerprint_value =
                json!([ticket, worker.seat, worker.incarnation, context, brief]);
            if let Some(binding) = &manager_claim {
                fingerprint_value
                    .as_array_mut()
                    .context("foreman_fingerprint_invalid")?
                    .push(serde_json::to_value(binding)?);
            }
            let fingerprint = digest(&serde_json::to_vec(&fingerprint_value)?);
            if let Some(prior) = store.attempts.get(&operation_id) {
                ensure!(
                    prior["fingerprint"] == fingerprint,
                    "foreman_operation_conflict"
                );
            } else {
                ensure!(
                    store.assignments.len() < 128 && store.attempts.len() < 128,
                    "foreman_capacity"
                );
                ensure!(
                    !store
                        .assignments
                        .values()
                        .any(|entry| entry.worker.seat == worker.seat
                            && !["review", "reconciled", "abandoned"]
                                .contains(&entry.disposition.as_str())),
                    "foreman_worker_busy"
                );
                store.attempts.insert(
                    operation_id,
                    json!({"fingerprint":fingerprint,"ticket":ticket,"attempted":false}),
                );
                save(state, &store)?;
            }
            let work_branch = format!("codex/factory-{operation_id}");
            let history = branches(&forge, &client, ticket, &work_branch).await?;
            let rows = history["rows"]
                .as_array()
                .context("foreman_branch_rows_missing")?;
            ensure!(rows.len() <= 1, "foreman_generation_ambiguous");
            ensure!(
                !store
                    .assignments
                    .values()
                    .any(|entry| entry.ticket == ticket
                        && entry.generation != operation_id
                        && !["review", "reconciled", "abandoned"]
                            .contains(&entry.disposition.as_str())),
                "foreman_prior_effects_unreconciled"
            );
            let current = canonical(&forge, &client, ticket).await?;
            if current["run"].is_null() {
                ensure!(rows.is_empty(), "foreman_operation_already_terminal");
            }
            let mut base_sha = git_text(&config.repository, &["rev-parse", "HEAD"]).await?;
            for prior in prior_runs(&forge, &client, ticket).await? {
                let id = Uuid::parse_str(
                    prior["run_id"]
                        .as_str()
                        .context("foreman_history_identity_missing")?,
                )?;
                let pending = prior["work_branch"] == work_branch
                    && current["run"]["id"] == id.to_string()
                    && current["run"]["owner_id"] == operation_id.to_string()
                    && store.attempts[&operation_id]["attempted"] == true
                    && !known_specs.contains_key(&id);
                ensure!(
                    store.assignments.contains_key(&id)
                        || store.recovered_runs.contains(&id)
                        || pending,
                    "foreman_prior_generation_recovery_required"
                );
            }
            let run = if current["run"].is_object() {
                ensure!(
                    current["run"]["owner_id"] == operation_id.to_string(),
                    "foreman_ticket_owned"
                );
                ensure!(
                    store.attempts[&operation_id]["attempted"] == true,
                    "foreman_claim_recovery_required"
                );
                let previous = rows.first().context("foreman_run_correlation_missing")?;
                ensure!(
                    previous["run_id"] == current["run"]["id"],
                    "foreman_run_correlation_mismatch"
                );
                base_sha = previous["base_commit_sha"]
                    .as_str()
                    .context("foreman_base_missing")?
                    .into();
                Uuid::parse_str(
                    current["run"]["id"]
                        .as_str()
                        .context("foreman_run_missing")?,
                )?
            } else {
                ensure!(
                    store.attempts[&operation_id]["attempted"] != true,
                    "foreman_claim_uncertain"
                );
                store
                    .attempts
                    .get_mut(&operation_id)
                    .context("foreman_attempt_missing")?["attempted"] = json!(true);
                save(state, &store)?;
                let started=recording::call(&forge,&client,"pipeline-run-start",json!({"ticket_number":ticket,
                    "project_id":forge.forge_project,"owner_id":operation_id,"adopt":false,"pipeline_type":"work",
                    "work_branch":work_branch,"base_commit_sha":base_sha,
                    "bridge_session_id":format!("factory/{worker}/{operation_id}",worker=worker.seat)})).await?;
                Uuid::parse_str(
                    started["run"]["id"]
                        .as_str()
                        .context("foreman_run_missing")?,
                )?
            };
            if let std::collections::btree_map::Entry::Vacant(entry) = store.assignments.entry(run)
            {
                ensure!(
                    !known_specs.contains_key(&run),
                    "foreman_issued_assignment_recovery_required"
                );
                let context_digest = digest(context.as_bytes());
                let text = format!(
                    "Brief: Ticket #{ticket}\nAssignment run ID: {run}\nContext SHA256: {context_digest}\n{brief}\nRequired context:\n{context}\nDo: Use the assignment run ID with workflow_ack_context. Acknowledge the context digest. Do not execute the specification."
                );
                let envelope = message(
                    &sender,
                    &worker,
                    &forge.local_project,
                    run,
                    text,
                    protocol::MessageKind::Note,
                )?;
                entry.insert(Assignment {
                    ticket,
                    generation: operation_id,
                    worker: worker.clone(),
                    context_digest,
                    brief: envelope,
                    spec: None,
                    spec_digest: None,
                    workspace: None,
                    disposition: "brief_pending".into(),
                    lease_until: None,
                    lease_tick: None,
                    execution: None,
                    work_branch: work_branch.clone(),
                    base_sha,
                    reconciliation: None,
                    result: None,
                    manager_handoff: manager_claim.clone(),
                    result_return: None,
                });
                save(state, &store)?;
            }
            let assignment = store
                .assignments
                .get_mut(&run)
                .context("foreman_assignment_missing")?;
            owns(&canonical(&forge, &client, ticket).await?, run, assignment)?;
            let envelope = assignment.brief.clone();
            let response = json!({"ok":true,"runId":run,"generation":operation_id,"briefMessageId":assignment.brief.message_id,
                "contextDigest":assignment.context_digest,"worker":worker.seat,"workBranch":assignment.work_branch,
                "baseCommit":assignment.base_sha,"managerHandoff":assignment.manager_handoff,"authority":"Forge pipeline run"});
            save(state, &store)?;
            drop(_lock);
            service::operator_send(&config.bridge_state, envelope.clone())?;
            let _lock = service::lock(state)?;
            let mut store = load(state, Some(&config_digest))?;
            let assignment = store
                .assignments
                .get_mut(&run)
                .context("foreman_assignment_missing")?;
            ensure!(
                assignment.brief.message_id == envelope.message_id,
                "foreman_brief_conflict"
            );
            if assignment.disposition == "brief_pending" {
                assignment.disposition = "briefed".into();
            }
            save(state, &store)?;
            Ok(response)
        }
        Action::RegisterWorkspace { run_id, workspace } => {
            let assignment = store
                .assignments
                .get_mut(&run_id)
                .context("foreman_assignment_missing")?;
            owns(
                &canonical(&forge, &client, assignment.ticket).await?,
                run_id,
                assignment,
            )?;
            ensure!(
                workspace.is_absolute()
                    && workspace.as_os_str().len() <= 1024
                    && workspace.canonicalize()? == workspace
                    && workspace.parent() == Some(config.worktree_root.as_path()),
                "foreman_workspace_scope_denied"
            );
            let info = std::fs::symlink_metadata(&workspace)?;
            ensure!(
                info.is_dir()
                    && info.uid() == assignment.worker.agent_uid
                    && info.mode() & 0o022 == 0,
                "foreman_workspace_permissions"
            );
            let common =
                PathBuf::from(git_text(&workspace, &["rev-parse", "--git-common-dir"]).await?);
            let common = if common.is_absolute() {
                common
            } else {
                workspace.join(common)
            };
            ensure!(
                common.canonicalize()? == config.repository.join(".git").canonicalize()?,
                "foreman_repository_mismatch"
            );
            ensure!(
                git_text(&workspace, &["rev-parse", "HEAD"]).await? == assignment.base_sha,
                "foreman_workspace_base_changed"
            );
            ensure!(
                git_text(&workspace, &["rev-parse", "--abbrev-ref", "HEAD"]).await?
                    == assignment.work_branch,
                "foreman_workspace_branch_changed"
            );
            ensure!(assignment.spec.is_none(), "foreman_workspace_already_bound");
            assignment.workspace = Some(workspace.clone());
            save(state, &store)?;
            Ok(json!({"ok":true,"runId":run_id,"workspace":workspace}))
        }
        Action::IssueSpec {
            run_id,
            spec,
            lease_seconds,
        } => {
            ensure!((1..=3600).contains(&lease_seconds), "foreman_spec_invalid");
            let assignment = store
                .assignments
                .get_mut(&run_id)
                .context("foreman_assignment_missing")?;
            let spec = if let Some(binding) = &assignment.manager_handoff {
                handoff::current(&forge, &client, binding).await?;
                let reference = managed::executable_reference(binding);
                ensure!(
                    spec.as_ref().is_none_or(|text| text == &reference),
                    "foreman_spec_override_denied"
                );
                reference
            } else {
                ensure!(!config.manager_required, "foreman_manager_handoff_required");
                spec.context("foreman_spec_required")?
            };
            ensure!(
                !spec.is_empty() && spec.len() <= 12000,
                "foreman_spec_invalid"
            );
            owns(
                &canonical(&forge, &client, assignment.ticket).await?,
                run_id,
                assignment,
            )?;
            let receipt = store
                .contexts
                .get(&assignment.worker.seat)
                .context("foreman_context_not_acknowledged")?;
            ensure!(
                receipt.run == run_id
                    && receipt.incarnation == assignment.worker.incarnation
                    && receipt.digest == assignment.context_digest,
                "foreman_context_mismatch"
            );
            ensure!(assignment.workspace.is_some(), "foreman_workspace_missing");
            ensure!(
                assignment
                    .execution
                    .as_ref()
                    .is_some_and(|owner| owner.live().unwrap_or(false)),
                "foreman_execution_not_registered"
            );
            if let Some(issued) = known_specs.get(&run_id) {
                ensure!(
                    assignment
                        .spec
                        .as_ref()
                        .is_some_and(|spec| issued.len() == 1 && issued.contains(&spec.message_id)),
                    "foreman_spec_recovery_required"
                );
            }
            let sender = sender.context("foreman_sender_missing")?;
            if let Some(prior) = &assignment.spec {
                ensure!(
                    assignment.spec_digest.as_deref() == Some(digest(spec.as_bytes()).as_str()),
                    "foreman_spec_conflict"
                );
                ensure!(
                    prior.assignment_id == Some(run_id),
                    "foreman_spec_identity_invalid"
                );
            } else {
                ensure!(
                    assignment.disposition == "briefed",
                    "foreman_assignment_not_ready"
                );
                assignment.spec = Some(message(
                    &sender,
                    &assignment.worker,
                    &forge.local_project,
                    run_id,
                    format!(
                        "Assignment: Ticket #{}\nAssignment run ID: {run_id}\nGeneration: {}\nBrief: {}\nContext SHA256: {}\nWorkspace: {}\nAcknowledged briefing (historical):\n{}\nThe worker acknowledged this context. The waiting instruction above is historical.\nDo: Use the assignment run ID with workflow_read_spec.\nExecutable specification:\n{}",
                        assignment.ticket,
                        assignment.generation,
                        assignment.brief.message_id,
                        assignment.context_digest,
                        assignment
                            .workspace
                            .as_ref()
                            .context("foreman_workspace_missing")?
                            .display(),
                        assignment.brief.text,
                        spec
                    ),
                    protocol::MessageKind::Assignment,
                )?);
                assignment.lease_until = Some(service::now()? + lease_seconds);
                assignment.lease_tick = Some(tick()? + lease_seconds);
                assignment.spec_digest = Some(digest(spec.as_bytes()));
                assignment.disposition = "spec_pending".into();
                save(state, &store)?;
            }
            let assignment = store
                .assignments
                .get_mut(&run_id)
                .context("foreman_assignment_missing")?;
            ensure!(
                assignment
                    .lease_tick
                    .is_some_and(|until| until > tick().unwrap_or(u64::MAX)),
                "foreman_lease_expired"
            );
            let envelope = assignment
                .spec
                .as_ref()
                .context("foreman_spec_missing")?
                .clone();
            let response = json!({"ok":true,"runId":run_id,"messageId":envelope.message_id,
                "leaseUntil":assignment.lease_until,"workspace":assignment.workspace});
            save(state, &store)?;
            drop(_lock);
            service::operator_send(&config.bridge_state, envelope.clone())?;
            let _lock = service::lock(state)?;
            let mut store = load(state, Some(&config_digest))?;
            let assignment = store
                .assignments
                .get_mut(&run_id)
                .context("foreman_assignment_missing")?;
            ensure!(
                ["spec_pending", "issued"].contains(&assignment.disposition.as_str())
                    && assignment
                        .spec
                        .as_ref()
                        .is_some_and(|spec| spec.message_id == envelope.message_id),
                "foreman_spec_superseded"
            );
            assignment.disposition = "issued".into();
            save(state, &store)?;
            Ok(response)
        }
        Action::RegisterExecution { run_id, pid } => {
            let assignment = store
                .assignments
                .get_mut(&run_id)
                .context("foreman_assignment_missing")?;
            owns(
                &canonical(&forge, &client, assignment.ticket).await?,
                run_id,
                assignment,
            )?;
            ensure!(assignment.spec.is_none(), "foreman_execution_already_bound");
            let sender = sender.context("foreman_sender_missing")?;
            ensure!(
                assignment.worker.host == sender.host && assignment.worker.host == local_host,
                "foreman_remote_supervisor_required"
            );
            let execution = Execution::observe(pid, &assignment.worker)?;
            assignment.execution = Some(execution.clone());
            save(state, &store)?;
            Ok(
                json!({"ok":true,"runId":run_id,"execution":execution,"evidence":"kernel UID, boot, start ticks and cgroup"}),
            )
        }
        Action::QuarantineExpired { run_id } => {
            let assignment = store
                .assignments
                .get_mut(&run_id)
                .context("foreman_assignment_missing")?;
            ensure!(
                assignment
                    .lease_tick
                    .is_some_and(|until| until <= tick().unwrap_or(0)),
                "foreman_lease_not_expired"
            );
            ensure!(
                ["spec_pending", "issued", "quarantined", "fenced"]
                    .contains(&assignment.disposition.as_str()),
                "foreman_not_running"
            );
            if assignment.disposition == "fenced" {
                return Ok(json!({"ok":true,"runId":run_id,"disposition":"fenced"}));
            }
            assignment.disposition = "quarantined".into();
            let ticket = assignment.ticket;
            let generation = assignment.generation;
            save(state, &store)?;
            let context = canonical(&forge, &client, ticket).await?;
            ensure!(
                context["run"]["id"] == run_id.to_string()
                    && context["run"]["owner_id"] == generation.to_string(),
                "foreman_canonical_owner_changed"
            );
            if context["run"]["status"] == "running" {
                recording::call(&forge,&client,"pipeline-run-event",json!({"run_id":run_id,"project_id":forge.forge_project,
                    "kind":"run_held","summary":"The lease expired. Stop the owned process set and reconcile effects before replacement."})).await?;
            }
            Ok(
                json!({"ok":true,"runId":run_id,"disposition":"quarantined","processStopped":false,"replacementAllowed":false}),
            )
        }
        Action::Fence { run_id } => {
            let assignment = store
                .assignments
                .get_mut(&run_id)
                .context("foreman_assignment_missing")?;
            ensure!(
                ["spec_pending", "issued", "quarantined", "fenced"]
                    .contains(&assignment.disposition.as_str()),
                "foreman_fence_order_denied"
            );
            let execution = assignment
                .execution
                .as_ref()
                .context("foreman_execution_not_registered")?;
            ensure!(
                !execution.live()? && execution.empty()?,
                "foreman_process_set_still_live"
            );
            assignment.disposition = "fenced".into();
            save(state, &store)?;
            Ok(
                json!({"ok":true,"runId":run_id,"disposition":"fenced","replacementAllowed":false,
                "reconciliationRequired":true,"evidence":"old incarnation ended and its entire cgroup is empty"}),
            )
        }
        Action::Reconcile { run_id, report } => {
            ensure!(
                report == state.join("reconciliations").join(format!("{run_id}.json")),
                "foreman_reconciliation_path_denied"
            );
            let bytes = service::private_read(&report, 16384)?;
            let receipt: Reconciliation = serde_json::from_slice(&bytes)?;
            let assignment = store
                .assignments
                .get_mut(&run_id)
                .context("foreman_assignment_missing")?;
            ensure!(
                receipt.run_id == run_id
                    && receipt.generation == assignment.generation
                    && receipt.confirmed_no_outstanding_effects
                    && !receipt.evidence.is_empty()
                    && receipt.evidence.len() <= 8
                    && receipt
                        .evidence
                        .iter()
                        .all(|v| !v.is_empty() && v.len() <= 256),
                "foreman_effects_not_reconciled"
            );
            let fingerprint = digest(&bytes);
            if let Some(prior) = &assignment.reconciliation {
                ensure!(prior == &fingerprint, "foreman_reconciliation_conflict");
            }
            ensure!(
                ["fenced", "abandon_pending", "abandoned"]
                    .contains(&assignment.disposition.as_str()),
                "foreman_reconciliation_order_denied"
            );
            if assignment.disposition == "abandoned" {
                return Ok(
                    json!({"ok":true,"runId":run_id,"disposition":"abandoned","receiptSha256":fingerprint}),
                );
            }
            let execution = assignment
                .execution
                .as_ref()
                .context("foreman_execution_not_registered")?;
            ensure!(
                !execution.live()? && execution.empty()?,
                "foreman_process_set_still_live"
            );
            let history =
                branches(&forge, &client, assignment.ticket, &assignment.work_branch).await?;
            let row = history["rows"]
                .as_array()
                .context("foreman_branch_rows_missing")?
                .iter()
                .find(|row| row["run_id"] == run_id.to_string())
                .context("foreman_canonical_run_missing")?;
            if row["run_status"] != "abandoned" {
                let context = canonical(&forge, &client, assignment.ticket).await?;
                ensure!(
                    context["run"]["id"] == run_id.to_string()
                        && context["run"]["owner_id"] == assignment.generation.to_string()
                        && ["running", "held"]
                            .contains(&context["run"]["status"].as_str().unwrap_or("")),
                    "foreman_canonical_owner_changed"
                );
                assignment.reconciliation = Some(fingerprint.clone());
                assignment.disposition = "abandon_pending".into();
                save(state, &store)?;
                recording::call(&forge,&client,"pipeline-run-complete",json!({"run_id":run_id,"project_id":forge.forge_project,
                    "outcome":"abandoned","summary":"The owned process set stopped. The operator reconciled its effects.",
                    "abandon_reason":"reconciled","actor_id":receipt.generation})).await?;
            }
            let assignment = store
                .assignments
                .get_mut(&run_id)
                .context("foreman_assignment_missing")?;
            assignment.reconciliation = Some(fingerprint.clone());
            assignment.disposition = "abandoned".into();
            save(state, &store)?;
            Ok(
                json!({"ok":true,"runId":run_id,"disposition":"abandoned","receiptSha256":fingerprint,
                "reconciliationAuthority":"explicit protected operator receipt","newOwnerGranted":false}),
            )
        }
        Action::RecoverTicket { ticket, report } => {
            ensure!(
                report == state.join("recoveries").join(format!("{ticket}.json")),
                "foreman_recovery_path_denied"
            );
            let bytes = service::private_read(&report, 16384)?;
            let receipt: RecoveryReceipt = serde_json::from_slice(&bytes)?;
            ensure!(
                receipt.ticket == ticket
                    && receipt.confirmed_all_prior_processes_stopped
                    && receipt.confirmed_no_outstanding_effects
                    && !receipt.evidence.is_empty()
                    && receipt.evidence.len() <= 8
                    && receipt
                        .evidence
                        .iter()
                        .all(|v| !v.is_empty() && v.len() <= 256),
                "foreman_recovery_evidence_required"
            );
            ensure!(
                canonical(&forge, &client, ticket).await?["run"].is_null(),
                "foreman_recovery_active_owner"
            );
            let rows = prior_runs(&forge, &client, ticket).await?;
            ensure!(
                rows.iter().all(|row| ["abandoned", "completed"]
                    .contains(&row["run_status"].as_str().unwrap_or(""))),
                "foreman_recovery_active_owner"
            );
            let ids = rows
                .iter()
                .map(|row| {
                    Uuid::parse_str(
                        row["run_id"]
                            .as_str()
                            .context("foreman_history_identity_missing")?,
                    )
                    .map_err(Into::into)
                })
                .collect::<Result<std::collections::BTreeSet<_>>>()?;
            ensure!(
                ids == receipt.prior_runs && ids.len() <= 128,
                "foreman_recovery_history_mismatch"
            );
            store.recovered_runs.extend(ids);
            ensure!(
                store.recovered_runs.len() <= 128,
                "foreman_recovery_capacity"
            );
            save(state, &store)?;
            Ok(
                json!({"ok":true,"ticket":ticket,"receiptSha256":digest(&bytes),
                "authority":"explicit protected operator recovery receipt","newOwnerGranted":false}),
            )
        }
        Action::ForwardResult { run_id } => {
            let sender = sender.context("foreman_sender_missing")?;
            let assignment = store
                .assignments
                .get_mut(&run_id)
                .context("foreman_assignment_missing")?;
            let binding = assignment
                .manager_handoff
                .as_ref()
                .context("foreman_canonical_spec_missing")?;
            ensure!(
                sender.role == protocol::Role::Foreman
                    && sender.seat == binding.foreman
                    && sender.incarnation == binding.foreman_incarnation,
                "foreman_result_sender_denied"
            );
            let candidate = assignment
                .result
                .as_ref()
                .context("foreman_result_missing")?;
            owns(
                &canonical(&forge, &client, assignment.ticket).await?,
                run_id,
                assignment,
            )?;
            handoff::current(&forge, &client, binding).await?;
            let workspace = assignment
                .workspace
                .as_ref()
                .context("foreman_workspace_missing")?;
            for artifact in &candidate.artifacts {
                artifact_hash(artifact, workspace, assignment.worker.agent_uid)?;
            }
            if assignment.result_return.is_none() {
                let at = service::now()?;
                assignment.result_return = Some(ResultReturn {
                    queued: false,
                    envelope: protocol::Envelope {
                        protocol: protocol::VERSION,
                        message_id: Uuid::new_v4(),
                        conversation_id: binding.job_id,
                        assignment_id: Some(run_id),
                        reply_to: Some(binding.message_id),
                        sender: sender.seat,
                        incarnation: sender.incarnation,
                        recipient: binding.manager,
                        project: binding.project.clone(),
                        kind: protocol::MessageKind::Result,
                        mode: protocol::DeliveryMode::WakeWhenIdle,
                        created_at: at,
                        expires_at: at + 3600,
                        text: format!(
                            "Report: A verified result awaits manager review.\nTicket: #{}\nEvidence: Run {}. Spec {}. SHA256 {}. Native thread {}. Native turn {}.\nNeeds: Read the result with factory_read_result. This result does not complete the canonical ticket.",
                            assignment.ticket,
                            run_id,
                            binding.spec_id,
                            binding.sha256,
                            candidate.terminal.thread_id,
                            candidate.terminal.turn_id
                        ),
                        artifact_refs: vec![format!(
                            "factory-result:{}:{}",
                            binding.job_id, run_id
                        )],
                    },
                });
            }
            let returned = assignment
                .result_return
                .as_ref()
                .context("foreman_result_missing")?;
            let envelope = returned.envelope.clone();
            let queued = returned.queued;
            save(state, &store)?;
            drop(_lock);
            if !queued {
                service::operator_send(&config.bridge_state, envelope.clone())?;
                let _lock = service::lock(state)?;
                let mut latest = load(state, Some(&config_digest))?;
                let returned = latest
                    .assignments
                    .get_mut(&run_id)
                    .and_then(|a| a.result_return.as_mut())
                    .context("foreman_result_missing")?;
                ensure!(
                    serde_json::to_value(&returned.envelope)? == serde_json::to_value(&envelope)?,
                    "foreman_result_conflict"
                );
                returned.queued = true;
                save(state, &latest)?;
            }
            Ok(
                json!({"ok":true,"runId":run_id,"messageId":envelope.message_id,
                "state":"result_pending_review","queued":true,"ticketCompleted":false}),
            )
        }
        Action::Status { run_id } => {
            let assignment = store
                .assignments
                .get(&run_id)
                .context("foreman_assignment_missing")?;
            let context = canonical(&forge, &client, assignment.ticket).await?;
            let listed =
                branches(&forge, &client, assignment.ticket, &assignment.work_branch).await?;
            let row = listed["rows"]
                .as_array()
                .context("foreman_branch_rows_missing")?
                .iter()
                .find(|row| row["run_id"] == run_id.to_string())
                .context("foreman_canonical_run_missing")?;
            let conflict = ["running", "held"].contains(&row["run_status"].as_str().unwrap_or(""))
                && context["run"].is_object()
                && (context["run"]["id"] != run_id.to_string()
                    || context["run"]["owner_id"] != assignment.generation.to_string());
            let mapped = match row["run_status"].as_str() {
                Some("completed") => "completed",
                Some("abandoned") => "failed",
                Some("held") => "blocked",
                Some("running") if !conflict => {
                    if context["ticket"]["status"] == "qa-review" {
                        "review"
                    } else if assignment.spec.is_some() {
                        "running"
                    } else {
                        "claimed"
                    }
                }
                _ => "blocked",
            };
            Ok(
                json!({"ok":true,"dispatch":assignment,"canonical":context,"canonicalRun":row,"canonicalConflict":conflict,"assignmentState":mapped,
                "statusAuthority":"Forge ticket and pipeline run","resultAuthority":"Recipient attestation and verified artifact hashes. Review is pending."}),
            )
        }
    }
}

/// The bridge authenticates the caller and holds its registry lock during this idempotent context receipt.
pub(crate) fn acknowledge(
    state: &Path,
    grant: &protocol::Grant,
    run: Uuid,
    context_digest: &str,
) -> Result<Value> {
    let _lock = service::lock(state)?;
    let mut store = load(state, None)?;
    let assignment = store
        .assignments
        .get(&run)
        .context("foreman_assignment_missing")?;
    ensure!(
        assignment.worker.seat == grant.seat && assignment.worker.incarnation == grant.incarnation,
        "foreman_context_worker_denied"
    );
    ensure!(
        assignment.context_digest == context_digest,
        "foreman_context_mismatch"
    );
    ensure!(
        assignment.disposition == "briefed",
        "foreman_context_order_denied"
    );
    store.contexts.insert(
        grant.seat,
        ContextReceipt {
            run,
            incarnation: grant.incarnation,
            digest: context_digest.into(),
            at: service::now()?,
        },
    );
    save(state, &store)?;
    Ok(json!({"ok":true,"runId":run,"contextDigest":context_digest,"acknowledged":true}))
}

pub(crate) fn authorize(
    state: &Path,
    grant: &protocol::Grant,
    message_id: Uuid,
    cwd: &Path,
) -> Result<Value> {
    let _lock = service::lock(state)?;
    let store = load(state, None)?;
    let (run, assignment) = store
        .assignments
        .iter()
        .find(|(_, a)| {
            a.spec
                .as_ref()
                .is_some_and(|spec| spec.message_id == message_id)
        })
        .context("foreman_spec_unknown")?;
    ensure!(
        assignment.worker.seat == grant.seat && assignment.worker.incarnation == grant.incarnation,
        "foreman_execution_worker_denied"
    );
    ensure!(
        assignment.disposition == "issued"
            && assignment
                .lease_tick
                .is_some_and(|until| until > tick().unwrap_or(u64::MAX)),
        "foreman_lease_inactive"
    );
    ensure!(
        assignment
            .execution
            .as_ref()
            .is_some_and(|owner| owner.live().unwrap_or(false)),
        "foreman_worker_process_lost"
    );
    ensure!(
        assignment.workspace.as_deref() == Some(cwd) && cwd.canonicalize()? == cwd,
        "foreman_execution_workspace_denied"
    );
    let path = store
        .config_path
        .as_ref()
        .context("foreman_config_missing")?;
    let bytes = service::private_read(path, 16384)?;
    let config: Config = serde_json::from_slice(&bytes)?;
    let (forge, client) = recording::connection(&config.forge_config)?;
    let config_digest = configuration_digest(&bytes, &forge)?;
    ensure!(
        config_digest == store.config_digest,
        "foreman_config_changed"
    );
    let runtime = tokio::runtime::Builder::new_current_thread()
        .enable_all()
        .build()?;
    runtime.block_on(async {
        let context = tokio::time::timeout(
            std::time::Duration::from_secs(2),
            canonical(&forge, &client, assignment.ticket),
        )
        .await
        .context("foreman_authority_timeout")??;
        owns(&context, *run, assignment)?;
        if let Some(binding) = &assignment.manager_handoff {
            handoff::current(&forge, &client, binding).await?;
        }
        Ok::<(), anyhow::Error>(())
    })?;
    Ok(
        json!({"ok":true,"runId":run,"generation":assignment.generation,"workspace":cwd,"leaseUntil":assignment.lease_until}),
    )
}

fn artifact_hash(artifact: &Artifact, workspace: &Path, uid: u32) -> Result<()> {
    artifact_bytes(artifact, workspace, uid).map(|_| ())
}

fn artifact_bytes(artifact: &Artifact, workspace: &Path, uid: u32) -> Result<Vec<u8>> {
    use std::ffi::CString;
    use std::io::Read;
    use std::os::fd::{AsRawFd, FromRawFd};
    use std::os::unix::ffi::OsStrExt;
    use std::os::unix::fs::OpenOptionsExt;
    use std::path::Component;
    ensure!(
        artifact.path.is_absolute()
            && artifact.path.canonicalize()? == artifact.path
            && artifact.path.starts_with(workspace)
            && artifact.sha256.len() == 64
            && artifact.sha256.bytes().all(|b| b.is_ascii_hexdigit()),
        "foreman_artifact_scope_denied"
    );
    let mut directory = std::fs::OpenOptions::new()
        .read(true)
        .custom_flags(libc::O_NOFOLLOW | libc::O_DIRECTORY | libc::O_CLOEXEC)
        .open(workspace)?;
    let components: Vec<_> = artifact
        .path
        .strip_prefix(workspace)?
        .components()
        .collect();
    ensure!(!components.is_empty(), "foreman_artifact_scope_denied");
    let mut opened = None;
    for (index, component) in components.iter().enumerate() {
        let Component::Normal(name) = component else {
            anyhow::bail!("foreman_artifact_scope_denied");
        };
        let name = CString::new(name.as_bytes())?;
        let last = index + 1 == components.len();
        let flags = libc::O_RDONLY
            | libc::O_NOFOLLOW
            | libc::O_CLOEXEC
            | libc::O_NONBLOCK
            | if last { 0 } else { libc::O_DIRECTORY };
        // SAFETY: The directory descriptor and terminated component remain live for openat.
        let fd = unsafe { libc::openat(directory.as_raw_fd(), name.as_ptr(), flags) };
        if fd < 0 {
            return Err(std::io::Error::last_os_error().into());
        }
        // SAFETY: A successful openat returns a new descriptor. This File owns it exactly once.
        let file = unsafe { std::fs::File::from_raw_fd(fd) };
        if last {
            opened = Some(file);
        } else {
            directory = file;
        }
    }
    let mut file = opened.context("foreman_artifact_scope_denied")?;
    let before = file.metadata()?;
    ensure!(
        before.is_file()
            && before.uid() == uid
            && before.mode() & 0o022 == 0
            && before.len() <= 8 * 1024 * 1024,
        "foreman_artifact_permissions_or_bound"
    );
    let mut bytes = Vec::new();
    file.by_ref()
        .take(8 * 1024 * 1024 + 1)
        .read_to_end(&mut bytes)?;
    ensure!(
        bytes.len() <= 8 * 1024 * 1024 && digest(&bytes) == artifact.sha256,
        "foreman_artifact_changed"
    );
    let after = file.metadata()?;
    ensure!(
        before.len() == after.len()
            && before.mtime() == after.mtime()
            && before.mtime_nsec() == after.mtime_nsec(),
        "foreman_artifact_changed"
    );
    Ok(bytes)
}

/// A result remains a review candidate. It cannot close a canonical ticket or grant a new execution owner.
pub(crate) fn result(
    state: &Path,
    grant: &protocol::Grant,
    run: Uuid,
    thread: Uuid,
    turn: Uuid,
    artifacts: Vec<Artifact>,
) -> Result<Value> {
    let _lock = service::lock(state)?;
    let mut store = load(state, None)?;
    let assignment = store
        .assignments
        .get_mut(&run)
        .context("foreman_assignment_missing")?;
    ensure!(
        assignment.worker.seat == grant.seat && assignment.worker.incarnation == grant.incarnation,
        "foreman_result_worker_denied"
    );
    ensure!(
        assignment.disposition == "issued"
            && assignment
                .lease_tick
                .is_some_and(|until| until > tick().unwrap_or(u64::MAX)),
        "foreman_result_generation_inactive"
    );
    let config_bytes = service::private_read(
        store
            .config_path
            .as_ref()
            .context("foreman_config_missing")?,
        16384,
    )?;
    let config: Config = serde_json::from_slice(&config_bytes)?;
    let (forge, client) = recording::connection(&config.forge_config)?;
    ensure!(
        configuration_digest(&config_bytes, &forge)? == store.config_digest,
        "foreman_config_changed"
    );
    let runtime = tokio::runtime::Builder::new_current_thread()
        .enable_all()
        .build()?;
    runtime.block_on(async {
        let context = tokio::time::timeout(
            std::time::Duration::from_secs(2),
            canonical(&forge, &client, assignment.ticket),
        )
        .await
        .context("foreman_authority_timeout")??;
        owns(&context, run, assignment)?;
        if let Some(binding) = &assignment.manager_handoff {
            handoff::current(&forge, &client, binding).await?;
        }
        Ok::<(), anyhow::Error>(())
    })?;
    let message = assignment
        .spec
        .as_ref()
        .context("foreman_spec_missing")?
        .message_id;
    let bridge_state = state.parent().context("foreman_state_binding_invalid")?;
    let (event_id, terminal) = recording::terminal_for_assignment(
        &bridge_state.join("recording"),
        grant,
        run,
        message,
        thread,
        turn,
    )?;
    ensure!(
        !artifacts.is_empty() && artifacts.len() <= 8,
        "foreman_artifact_count"
    );
    let workspace = assignment
        .workspace
        .as_ref()
        .context("foreman_workspace_missing")?;
    for artifact in &artifacts {
        artifact_hash(artifact, workspace, grant.agent_uid)?;
    }
    if let Some(prior) = &assignment.result {
        ensure!(
            prior.event_id == event_id && prior.artifacts == artifacts,
            "foreman_result_conflict"
        );
    } else {
        assignment.result = Some(Candidate {
            event_id,
            terminal,
            artifacts,
        });
    }
    save(state, &store)?;
    Ok(
        json!({"ok":true,"runId":run,"eventId":event_id,"state":"result_pending_review",
        "ticketCompleted":false,"newOwnerGranted":false}),
    )
}
