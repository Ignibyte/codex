//! Foreman access to approved specifications. Private configuration owns authority.
use super::{Action, Config, apply, load};
use crate::{manager::handoff, protocol, recording, service};
use anyhow::{Context, Result, ensure};
use serde::Deserialize;
use serde_json::{Value, json};
use std::path::Path;
use uuid::Uuid;

pub(super) fn executable_reference(binding: &handoff::Binding) -> String {
    format!(
        "Specification: {}\nSpec run: {}\nRound: {}\nSHA256: {}\nManager job: {}\nDo: Read your canonical specification with workflow_read_spec. Follow its scope and acceptance criteria.\nLimits: A completed turn is a review candidate. It does not complete the canonical ticket.",
        binding.spec_id, binding.spec_run_id, binding.round, binding.sha256, binding.job_id
    )
}

/// The authenticated foreman may use only prepared canonical claim/issue/status actions.
pub fn action(
    state: &Path,
    grant: &protocol::Grant,
    project: &str,
    action: Action,
) -> Result<Value> {
    ensure!(
        grant.role == protocol::Role::Foreman && grant.projects.contains(project),
        "foreman_caller_denied"
    );
    ensure!(
        matches!(
            &action,
            Action::Claim {
                manager_job: Some(_),
                ..
            } | Action::IssueSpec { spec: None, .. }
                | Action::Status { .. }
                | Action::ForwardResult { .. }
        ),
        "foreman_action_denied"
    );
    let current = service::operator_grant(state, grant.seat)?;
    ensure!(
        serde_json::to_value(&current)? == serde_json::to_value(grant)?,
        "foreman_grant_changed"
    );
    let root = state.join("foreman");
    let config_path = {
        let _lock = service::lock(&root)?;
        load(&root, None)?
            .config_path
            .context("foreman_config_missing")?
    };
    let config: Config = serde_json::from_slice(&service::private_read(&config_path, 16384)?)?;
    let (forge, _) = recording::connection(&config.forge_config)?;
    ensure!(
        config.manager_required && config.foreman == grant.seat && forge.local_project == project,
        "foreman_native_configuration_denied"
    );
    let runtime = tokio::runtime::Builder::new_current_thread()
        .enable_all()
        .build()?;
    let status = matches!(&action, Action::Status { .. });
    let value = runtime.block_on(apply(&root, &config_path, action))?;
    if status {
        let a = &value["dispatch"];
        return Ok(json!({"ok":true,"runId":a["brief"]["conversationId"],
            "worker":a["worker"]["seat"],"managerHandoff":a["managerHandoff"],
            "disposition":a["disposition"],"assignmentState":value["assignmentState"],
            "canonicalConflict":value["canonicalConflict"],"resultPresent":a["result"].is_object(),
            "resultMessageId":a["resultReturn"]["envelope"]["messageId"],
            "resultAuthority":value["resultAuthority"]}));
    }
    Ok(value)
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ReadResult {
    pub run_id: Uuid,
    pub artifact: Option<usize>,
    #[serde(default)]
    pub offset: usize,
}

/// The manager can read only a result returned through its bound foreman.
pub(crate) fn result(
    state: &Path,
    grant: &protocol::Grant,
    binding: &handoff::Binding,
    request: ReadResult,
) -> Result<Value> {
    ensure!(
        grant.role == protocol::Role::Manager
            && grant.seat == binding.manager
            && grant.incarnation == binding.manager_incarnation,
        "foreman_result_reader_denied"
    );
    let assignment = {
        let _lock = service::lock(state)?;
        load(state, None)?
            .assignments
            .get(&request.run_id)
            .context("foreman_assignment_missing")?
            .clone()
    };
    ensure!(
        assignment.manager_handoff.as_ref() == Some(binding),
        "foreman_result_binding_denied"
    );
    let returned = assignment
        .result_return
        .as_ref()
        .filter(|r| r.queued)
        .context("foreman_result_not_returned")?;
    let candidate = assignment
        .result
        .as_ref()
        .context("foreman_result_missing")?;
    if let Some(index) = request.artifact {
        let artifact = candidate
            .artifacts
            .get(index)
            .context("foreman_artifact_index_denied")?;
        let bytes = super::artifact_bytes(
            artifact,
            assignment
                .workspace
                .as_ref()
                .context("foreman_workspace_missing")?,
            assignment.worker.agent_uid,
        )?;
        ensure!(
            request.offset <= bytes.len(),
            "foreman_artifact_offset_denied"
        );
        let end = bytes.len().min(request.offset.saturating_add(4096));
        let part = &bytes[request.offset..end];
        let hex: String = part.iter().map(|b| format!("{b:02x}")).collect();
        return Ok(
            json!({"ok":true,"runId":request.run_id,"binding":binding,"artifact":index,
            "sha256":artifact.sha256,"bytes":bytes.len(),"offset":request.offset,"hex":hex,
            "text":std::str::from_utf8(part).ok(),"next":if end<bytes.len(){Some(end)}else{None},
            "ticketCompleted":false}),
        );
    }
    ensure!(request.offset == 0, "foreman_artifact_offset_denied");
    let artifacts: Vec<_> = candidate.artifacts.iter().enumerate().map(|(i,a)|
        json!({"index":i,"name":a.path.file_name().map(|s|s.to_string_lossy().into_owned()),"sha256":a.sha256})).collect();
    Ok(json!({"ok":true,"runId":request.run_id,"binding":binding,
        "eventId":candidate.event_id,"terminal":candidate.terminal,"artifacts":artifacts,
        "messageId":returned.envelope.message_id,"state":"result_pending_review","ticketCompleted":false}))
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ReadSpec {
    pub run_id: Uuid,
    pub field: String,
    #[serde(default)]
    pub offset: usize,
}

pub fn read(
    state: &Path,
    grant: &protocol::Grant,
    project: &str,
    request: ReadSpec,
) -> Result<Value> {
    ensure!(
        grant.role == protocol::Role::Worker && grant.projects.contains(project),
        "foreman_spec_reader_denied"
    );
    let _lock = service::lock(state)?;
    let store = load(state, None)?;
    let assignment = store
        .assignments
        .get(&request.run_id)
        .context("foreman_assignment_missing")?;
    ensure!(
        assignment.worker.seat == grant.seat
            && assignment.worker.incarnation == grant.incarnation
            && ["issued", "review"].contains(&assignment.disposition.as_str())
            && assignment.spec.is_some(),
        "foreman_spec_reader_denied"
    );
    let receipt = store
        .contexts
        .get(&grant.seat)
        .context("foreman_context_not_acknowledged")?;
    ensure!(
        receipt.run == request.run_id
            && receipt.incarnation == grant.incarnation
            && receipt.digest == assignment.context_digest,
        "foreman_context_mismatch"
    );
    let binding = assignment
        .manager_handoff
        .as_ref()
        .context("foreman_canonical_spec_missing")?;
    ensure!(binding.project == project, "foreman_spec_project_denied");
    let root = state
        .parent()
        .context("foreman_state_binding_invalid")?
        .join("manager");
    let spec = handoff::artifact(&root, binding)?;
    ensure!(
        [
            "scope",
            "done_when",
            "evidence",
            "not_in_scope",
            "body",
            "regression_suites",
            "named_symbols",
            "changed_symbols",
            "research_citations"
        ]
        .contains(&request.field.as_str()),
        "foreman_spec_field_denied"
    );
    let value = &spec[&request.field];
    let text = if let Some(text) = value.as_str() {
        text.into()
    } else {
        serde_json::to_string(value)?
    };
    let mut result = part(binding, &request.field, request.offset, &text)?;
    result["runId"] = json!(request.run_id);
    Ok(result)
}

fn part(binding: &handoff::Binding, field: &str, offset: usize, text: &str) -> Result<Value> {
    let total = text.chars().count();
    ensure!(offset <= total, "foreman_spec_offset_denied");
    let part: String = text.chars().skip(offset).take(4096).collect();
    let next = offset + part.chars().count();
    Ok(
        json!({"ok":true,"binding":binding,"field":field,"offset":offset,"text":part,
        "totalChars":total,"next":if next<total{Some(next)}else{None}}),
    )
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ReadHandoff {
    pub job_id: Uuid,
    pub field: String,
    #[serde(default)]
    pub offset: usize,
}

pub fn read_handoff(
    state: &Path,
    grant: &protocol::Grant,
    project: &str,
    request: ReadHandoff,
) -> Result<Value> {
    let (binding, intent) =
        handoff::accepted(&state.join("manager"), request.job_id, grant, project)?;
    let text = match request.field.as_str() {
        "intent" => intent.text,
        "intent_reference" => intent.reference,
        field => {
            ensure!(
                [
                    "scope",
                    "done_when",
                    "evidence",
                    "not_in_scope",
                    "body",
                    "regression_suites",
                    "named_symbols",
                    "changed_symbols",
                    "research_citations"
                ]
                .contains(&field),
                "foreman_spec_field_denied"
            );
            let spec = handoff::artifact(&state.join("manager"), &binding)?;
            if let Some(text) = spec[field].as_str() {
                text.into()
            } else {
                serde_json::to_string(&spec[field])?
            }
        }
    };
    part(&binding, &request.field, request.offset, &text)
}
