//! Canonical manager approval and a receipted, non-executable foreman handoff.
use super::{Intent, Job, MAX_BLOB, bound, digest, load, save};
use crate::{protocol, recording, service};
use anyhow::{Context, Result, ensure};
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use std::path::Path;
use uuid::Uuid;

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct Binding {
    pub job_id: Uuid,
    pub intent_id: Uuid,
    pub manager: Uuid,
    pub manager_incarnation: Uuid,
    pub foreman: Uuid,
    pub foreman_incarnation: Uuid,
    pub project: String,
    pub forge_project: Uuid,
    pub ticket: i64,
    pub ticket_id: Uuid,
    pub spec_id: Uuid,
    pub spec_run_id: Uuid,
    pub round: i64,
    pub sealed_at: String,
    pub sha256: String,
    pub bytes: usize,
    pub message_id: Uuid,
}

#[derive(Deserialize, Serialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub(super) struct Handoff {
    pub binding: Binding,
    envelope: protocol::Envelope,
    pub queued: bool,
}

fn status_matches(status: &Value, binding: &Binding) -> Result<()> {
    ensure!(
        status["sealed"] == true
            && status["spec_id"] == json!(binding.spec_id)
            && status["run_id"] == json!(binding.spec_run_id)
            && status["latest"]["run_id"] == json!(binding.spec_run_id)
            && status["latest"]["status"] == "sealed"
            && status["latest"]["round"] == binding.round
            && status["ticket"]["id"] == json!(binding.ticket_id)
            && status["ticket"]["number"] == binding.ticket
            && status["ticket"]["project_id"] == json!(binding.forge_project),
        "manager_spec_not_current"
    );
    Ok(())
}

/// Read the current canonical artifact. A later open round invalidates an older seal.
pub async fn current(
    forge: &recording::Config,
    client: &reqwest::Client,
    binding: &Binding,
) -> Result<Value> {
    ensure!(
        forge.local_project == binding.project && forge.forge_project == binding.forge_project,
        "manager_handoff_project_denied"
    );
    let args = json!({"project_id":binding.forge_project,"ticket_number":binding.ticket});
    let status = recording::call(forge, client, "spec-status", args.clone()).await?;
    status_matches(&status, binding)?;
    let sealed = recording::call(forge, client, "spec-sealed", args.clone()).await?;
    ensure!(
        sealed["run_id"] == json!(binding.spec_run_id)
            && sealed["round"] == binding.round
            && sealed["sealed_at"] == binding.sealed_at
            && sealed["spec"]["id"] == json!(binding.spec_id)
            && sealed["spec"]["ticket_id"] == json!(binding.ticket_id)
            && sealed["spec"]["run_id"] == json!(binding.spec_run_id),
        "manager_spec_identity_changed"
    );
    let bytes = serde_json::to_vec(&sealed["spec"])?;
    ensure!(
        bytes.len() == binding.bytes && digest(&bytes) == binding.sha256,
        "manager_spec_digest_changed"
    );
    status_matches(
        &recording::call(forge, client, "spec-status", args).await?,
        binding,
    )?;
    Ok(sealed["spec"].clone())
}

fn receipt(handoff: &Handoff) -> Value {
    json!({"ok":true,"state":if handoff.queued {"queued"}else{"pending"},"binding":handoff.binding,
        "authority":"specification approval receipt. The foreman retains assignment authority."})
}

pub fn approve(
    state: &Path,
    grant: &protocol::Grant,
    project: &str,
    id: Uuid,
    operation: Uuid,
) -> Result<Value> {
    ensure!(!operation.is_nil(), "manager_operation_required");
    let path = state.join(id.to_string());
    ensure!(
        path.join("registry.json").try_exists()?,
        "manager_job_unknown"
    );
    let initial = load(&path)?;
    let foreman = service::operator_grant(&initial.config.bridge_state, initial.config.foreman)?;
    let _lock = service::try_lock(&path)?.context("manager_job_busy")?;
    let mut job = load(&path)?;
    let forge = bound(&job, grant, project)?;
    ensure!(
        !job.attempts.contains_key(&operation) && !job.uploads.contains(&operation),
        "manager_operation_conflict"
    );
    ensure!(
        serde_json::to_value(&foreman)? == serde_json::to_value(&job.foreman)?,
        "manager_foreman_changed"
    );
    if let Some(handoff) = &job.handoff {
        ensure!(
            handoff.binding.message_id == operation,
            "manager_handoff_operation_conflict"
        );
        if handoff.queued {
            return Ok(receipt(handoff));
        }
    } else {
        let intent = job
            .config
            .intent
            .as_ref()
            .context("manager_intent_required")?;
        let run = job.run.context("manager_run_not_started")?;
        let runtime = tokio::runtime::Builder::new_current_thread()
            .enable_all()
            .build()?;
        let binding = runtime.block_on(async {
            let (_, client) = recording::connection(&job.config.forge_config)?;
            let args = json!({"project_id":forge.forge_project,"ticket_number":job.config.ticket});
            let status = recording::call(&forge, &client, "spec-status", args.clone()).await?;
            ensure!(
                status["latest"]["run_id"] == json!(run) && status["latest"]["status"] == "sealed",
                "manager_spec_not_current"
            );
            let sealed = recording::call(&forge, &client, "spec-sealed", args).await?;
            let bytes = serde_json::to_vec(&sealed["spec"])?;
            ensure!(
                sealed["spec"].is_object() && bytes.len() <= MAX_BLOB,
                "manager_spec_bound"
            );
            let binding = Binding {
                job_id: id,
                intent_id: intent.id,
                manager: job.manager.seat,
                manager_incarnation: job.manager.incarnation,
                foreman: job.foreman.seat,
                foreman_incarnation: job.foreman.incarnation,
                project: project.into(),
                forge_project: forge.forge_project,
                ticket: job.config.ticket,
                ticket_id: serde_json::from_value(sealed["ticket"]["id"].clone())?,
                spec_id: serde_json::from_value(sealed["spec"]["id"].clone())?,
                spec_run_id: run,
                round: sealed["round"]
                    .as_i64()
                    .context("manager_spec_round_invalid")?,
                sealed_at: sealed["sealed_at"]
                    .as_str()
                    .context("manager_spec_seal_invalid")?
                    .into(),
                sha256: digest(&bytes),
                bytes: bytes.len(),
                message_id: operation,
            };
            current(&forge, &client, &binding).await?;
            let artifact = path.join("approved-spec");
            let _artifact_lock = service::lock(&artifact)?;
            service::atomic_write(&artifact, &bytes)?;
            Ok::<Binding, anyhow::Error>(binding)
        })?;
        let at = service::now()?;
        let envelope = protocol::Envelope {
            protocol: protocol::VERSION,
            message_id: operation,
            conversation_id: id,
            assignment_id: None,
            reply_to: None,
            sender: job.manager.seat,
            incarnation: job.manager.incarnation,
            recipient: job.foreman.seat,
            project: project.into(),
            kind: protocol::MessageKind::Note,
            mode: protocol::DeliveryMode::WakeWhenIdle,
            created_at: at,
            expires_at: at + 3600,
            text: format!(
                "Handoff: Ticket #{}\nPhase: sealed\nWorks: The manager approved the current sealed spec.\nEvidence: Spec {}. Run {}. SHA256 {}.\nLimits: This message grants no execution or approval authority.\nNext: The foreman validates the version before assignment.",
                binding.ticket, binding.spec_id, binding.spec_run_id, binding.sha256
            ),
            artifact_refs: vec![format!(
                "factory-spec:{id}:{}:{}",
                binding.spec_id, binding.sha256
            )],
        };
        job.handoff = Some(Handoff {
            binding,
            envelope,
            queued: false,
        });
        save(&path, &job)?;
    }
    let envelope = job
        .handoff
        .as_ref()
        .context("manager_handoff_missing")?
        .envelope
        .clone();
    drop(_lock);
    // Never acquire the bridge lock while retaining the manager job lock.
    service::operator_handoff(&job.config.bridge_state, envelope)?;
    let _lock = service::try_lock(&path)?.context("manager_job_busy")?;
    let mut job = load(&path)?;
    let handoff = job.handoff.as_mut().context("manager_handoff_missing")?;
    ensure!(
        handoff.binding.message_id == operation,
        "manager_handoff_operation_conflict"
    );
    handoff.queued = true;
    let response = receipt(handoff);
    save(&path, &job)?;
    Ok(response)
}

/// The foreman reads approved data using its own current grant.
pub fn accepted(
    state: &Path,
    id: Uuid,
    grant: &protocol::Grant,
    project: &str,
) -> Result<(Binding, Intent)> {
    let path = state.join(id.to_string());
    ensure!(
        path.join("registry.json").try_exists()?,
        "manager_job_unknown"
    );
    let job: Job = load(&path)?;
    let handoff = job.handoff.as_ref().context("manager_handoff_missing")?;
    ensure!(
        handoff.queued
            && grant.role == protocol::Role::Foreman
            && serde_json::to_value(grant)? == serde_json::to_value(&job.foreman)?
            && handoff.binding.project == project,
        "manager_handoff_recipient_denied"
    );
    Ok((
        handoff.binding.clone(),
        job.config.intent.context("manager_intent_required")?,
    ))
}

pub fn artifact(state: &Path, binding: &Binding) -> Result<Value> {
    let path = state
        .join(binding.job_id.to_string())
        .join("approved-spec/registry.json");
    let bytes = service::private_read(&path, MAX_BLOB as u64)?;
    ensure!(
        bytes.len() == binding.bytes && digest(&bytes) == binding.sha256,
        "manager_spec_artifact_integrity"
    );
    let value: Value = serde_json::from_slice(&bytes)?;
    ensure!(
        value["id"] == json!(binding.spec_id) && value["run_id"] == json!(binding.spec_run_id),
        "manager_spec_artifact_identity"
    );
    Ok(value)
}
