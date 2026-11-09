//! The executive's scoped spec job. Forge owns phases, checks and seals.
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
use std::path::Path;
use std::path::PathBuf;
use uuid::Uuid;

pub mod handoff;

pub const TOOLS: &[&str] = &[
    "run-start",
    "run-status",
    "run-note",
    "run-calls",
    "served-list",
    "served-verdict",
    "spec-draft",
    "spec-status",
    "spec-get",
    "spec-sealed",
    "checker-record",
    "phase-complete",
    "ticket-get",
    "ticket-search",
    "code-find",
    "code-search",
    "code-impact",
    "code-callers",
    "docs-search",
    "docs-read",
    "memory-recall",
    "base-red-list",
    "rule-get",
    "rule-list",
    "time-now",
];

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct Intent {
    pub id: Uuid,
    pub reference: String,
    pub text: String,
}

#[derive(Deserialize, Serialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct Config {
    id: Uuid,
    bridge_state: PathBuf,
    forge_config: PathBuf,
    manager: Uuid,
    foreman: Uuid,
    ticket: i64,
    #[serde(default)]
    intent: Option<Intent>,
}

#[derive(Deserialize, Serialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct Attempt {
    fingerprint: String,
    tool: String,
    response: Option<Response>,
}

#[derive(Deserialize, Serialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct Response {
    sha256: String,
    bytes: usize,
}

#[derive(Deserialize, Serialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct Upload {
    tool: String,
    sha256: String,
    hex: String,
    complete: bool,
}

const MAX_BLOB: usize = 512 * 1024;

#[derive(Deserialize, Serialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct Job {
    schema: u32,
    config: Config,
    config_digest: String,
    forge_digest: String,
    manager: protocol::Grant,
    foreman: protocol::Grant,
    run: Option<Uuid>,
    start_attempted: bool,
    attempts: BTreeMap<Uuid, Attempt>,
    uploads: std::collections::BTreeSet<Uuid>,
    #[serde(default)]
    handoff: Option<handoff::Handoff>,
}

fn digest(value: &[u8]) -> String {
    format!("{:x}", Sha256::digest(value))
}

fn save(state: &Path, job: &Job) -> Result<()> {
    service::atomic_write(state, &serde_json::to_vec(job)?)
}

fn load(state: &Path) -> Result<Job> {
    let job: Job = serde_json::from_slice(&service::private_read(
        &state.join("registry.json"),
        2 * 1024 * 1024,
    )?)?;
    ensure!(job.schema == 1, "manager_storage_unsupported");
    Ok(job)
}

pub fn prepare(state: &Path, config_path: &Path) -> Result<Value> {
    let bytes = service::private_read(config_path, 16384)?;
    let config: Config = serde_json::from_slice(&bytes)?;
    ensure!(
        !config.id.is_nil() && config.ticket > 0 && state == config.bridge_state.join("manager"),
        "manager_configuration_invalid"
    );
    if let Some(intent) = &config.intent {
        ensure!(
            !intent.id.is_nil()
                && !intent.reference.trim().is_empty()
                && intent.reference.len() <= 512
                && !intent.text.trim().is_empty()
                && intent.text.len() <= 8192,
            "manager_intent_invalid"
        );
    }
    let (forge, _) = recording::connection(&config.forge_config)?;
    ensure!(
        forge
            .signer_argv
            .windows(2)
            .any(|pair| pair[0] == "--session" && pair[1] == format!("cf-spec-{}", config.id)),
        "manager_signer_session_required"
    );
    let manager = service::operator_grant(&config.bridge_state, config.manager)?;
    let foreman = service::operator_grant(&config.bridge_state, config.foreman)?;
    ensure!(
        manager.role == protocol::Role::Manager
            && foreman.role == protocol::Role::Foreman
            && manager.recipients.contains(&foreman.seat)
            && foreman.recipients.contains(&manager.seat)
            && manager.projects.contains(&forge.local_project)
            && foreman.projects.contains(&forge.local_project)
            && manager
                .operations
                .contains(&protocol::Operation::Acknowledge)
            && manager.operations.contains(&protocol::Operation::Queue),
        "manager_role_binding_denied"
    );
    let _parent = service::lock(state)?;
    let path = state.join(config.id.to_string());
    let _lock = service::lock(&path)?;
    let fingerprint = digest(&bytes);
    if path.join("registry.json").try_exists()? {
        let old = load(&path)?;
        ensure!(
            old.config_digest == fingerprint,
            "manager_configuration_conflict"
        );
        return Ok(json!({"ok":true,"jobId":config.id,"prepared":true,"duplicate":true}));
    }
    let id = config.id;
    let forge_digest = digest(&service::private_read(&config.forge_config, 16384)?);
    save(
        &path,
        &Job {
            schema: 1,
            config,
            config_digest: fingerprint,
            forge_digest,
            manager,
            foreman,
            run: None,
            start_attempted: false,
            attempts: BTreeMap::new(),
            uploads: std::collections::BTreeSet::new(),
            handoff: None,
        },
    )?;
    Ok(json!({"ok":true,"jobId":id,"prepared":true}))
}

fn bound(job: &Job, grant: &protocol::Grant, project: &str) -> Result<recording::Config> {
    ensure!(
        grant.role == protocol::Role::Manager
            && serde_json::to_value(grant)? == serde_json::to_value(&job.manager)?,
        "manager_caller_denied"
    );
    let (forge, _) = recording::connection(&job.config.forge_config)?;
    ensure!(
        digest(&service::private_read(&job.config.forge_config, 16384)?) == job.forge_digest,
        "manager_forge_configuration_changed"
    );
    ensure!(forge.local_project == project, "manager_project_denied");
    ensure!(
        forge
            .signer_argv
            .windows(2)
            .any(|pair| pair[0] == "--session" && pair[1] == format!("cf-spec-{}", job.config.id)),
        "manager_signer_session_required"
    );
    Ok(forge)
}

pub(crate) fn object(value: Value) -> Result<Value> {
    ensure!(value["isError"] != true, "manager_forge_tool_refused");
    if let Some(data) = value.get("structuredContent") {
        return Ok(data.clone());
    }
    ensure!(value["isError"] != true, "manager_forge_tool_refused");
    Ok(serde_json::from_str(
        value["content"][0]["text"]
            .as_str()
            .context("manager_forge_response_invalid")?,
    )?)
}

pub fn catalogue(
    state: &Path,
    grant: &protocol::Grant,
    project: &str,
    id: Uuid,
    offset: usize,
) -> Result<Value> {
    let path = state.join(id.to_string());
    ensure!(
        path.join("registry.json").try_exists()?,
        "manager_job_unknown"
    );
    let job = load(&path)?;
    let forge = bound(&job, grant, project)?;
    let runtime = tokio::runtime::Builder::new_current_thread()
        .enable_all()
        .build()?;
    runtime.block_on(async {
        let (_, client) = recording::connection(&job.config.forge_config)?;
        let response = recording::request(&forge, &client, "tools/list", json!({})).await?;
        let tools = response["tools"]
            .as_array()
            .context("manager_catalogue_invalid")?;
        let selected: Vec<_> = tools
            .iter()
            .filter(|t| t["name"].as_str().is_some_and(|n| TOOLS.contains(&n)))
            .collect();
        ensure!(offset <= selected.len(), "manager_catalogue_offset");
        let mut page = Vec::new();
        for tool in selected.iter().skip(offset) {
            if serde_json::to_vec(&page)?.len() + serde_json::to_vec(tool)?.len() > 24 * 1024 {
                break;
            }
            page.push(*tool);
        }
        ensure!(
            offset == selected.len() || !page.is_empty(),
            "manager_catalogue_tool_bound"
        );
        let next = offset + page.len();
        Ok(
            json!({"ok":true,"jobId":id,"tools":page,"total":selected.len(),
            "next":if next < selected.len(){Some(next)}else{None}}),
        )
    })
}

pub fn call(
    state: &Path,
    grant: &protocol::Grant,
    project: &str,
    id: Uuid,
    operation: Uuid,
    tool: &str,
    mut arguments: Value,
) -> Result<Value> {
    ensure!(
        !operation.is_nil() && TOOLS.contains(&tool),
        "manager_tool_denied"
    );
    let path = state.join(id.to_string());
    ensure!(
        path.join("registry.json").try_exists()?,
        "manager_job_unknown"
    );
    let _lock = service::try_lock(&path)?.context("manager_job_busy")?;
    let mut job = load(&path)?;
    let forge = bound(&job, grant, project)?;
    ensure!(
        job.handoff
            .as_ref()
            .is_none_or(|h| h.binding.message_id != operation),
        "manager_operation_conflict"
    );
    if arguments.is_null() {
        ensure!(job.uploads.contains(&operation), "manager_upload_missing");
        let bytes = service::private_read(
            &path
                .join(format!("upload-{operation}"))
                .join("registry.json"),
            2 * MAX_BLOB as u64 + 4096,
        )?;
        let upload: Upload = serde_json::from_slice(&bytes)?;
        ensure!(
            upload.complete && upload.tool == tool,
            "manager_upload_incomplete_or_tool_conflict"
        );
        arguments = serde_json::from_slice(&decode(&upload.hex)?)?;
    }
    let fingerprint = digest(&serde_json::to_vec(
        &json!({"tool":tool,"arguments":arguments}),
    )?);
    if let Some(attempt) = job.attempts.get(&operation) {
        ensure!(
            attempt.fingerprint == fingerprint,
            "manager_operation_conflict"
        );
        return reply(&path, operation, attempt.response.as_ref());
    }
    ensure!(job.attempts.len() < 128, "manager_job_capacity");
    let args = arguments
        .as_object_mut()
        .context("manager_arguments_invalid")?;
    ensure!(
        args.get("project_id")
            .is_none_or(|v| *v == json!(forge.forge_project))
            && args
                .get("ticket_number")
                .is_none_or(|v| *v == json!(job.config.ticket))
            && !args.contains_key("ticket_id")
            && !args.contains_key("repo")
            && !args.contains_key("number")
            && !args.contains_key("id"),
        "manager_ticket_scope_denied"
    );
    if tool == "run-start" {
        ensure!(!job.start_attempted, "manager_run_start_already_attempted");
        ensure!(
            args.get("graph").is_none_or(|v| v == "spec"),
            "manager_graph_denied"
        );
        args.insert("project_id".into(), json!(forge.forge_project));
        args.insert("ticket_number".into(), json!(job.config.ticket));
        args.insert("graph".into(), json!("spec"));
        args.insert("served".into(), json!("manifest"));
    } else {
        ensure!(
            job.run.is_some()
                || [
                    "ticket-get",
                    "run-status",
                    "spec-status",
                    "spec-get",
                    "spec-sealed"
                ]
                .contains(&tool),
            "manager_run_not_started"
        );
        if let Some(run) = args.get("run_id") {
            ensure!(
                Some(serde_json::from_value(run.clone())?) == job.run,
                "manager_run_scope_denied"
            );
        }
        if ["ticket-get", "spec-status", "spec-get", "spec-sealed"].contains(&tool) {
            args.insert("project_id".into(), json!(forge.forge_project));
            args.insert("ticket_number".into(), json!(job.config.ticket));
        }
        if tool == "spec-get" {
            ensure!(!args.contains_key("spec_id"), "manager_spec_scope_denied");
            if job.run.is_some() {
                args.insert("run_id".into(), json!(job.run));
            }
        }
    }
    let runtime = tokio::runtime::Builder::new_current_thread()
        .enable_all()
        .build()?;
    runtime.block_on(async {
        let (_, client) = recording::connection(&job.config.forge_config)?;
        if tool != "run-start"
            && ![
                "ticket-get",
                "run-status",
                "spec-status",
                "spec-sealed",
                "spec-get",
            ]
            .contains(&tool)
        {
            let current = recording::call(&forge, &client, "run-status", json!({})).await?;
            ensure!(
                current["open_run"]["run_id"] == json!(job.run)
                    && current["open_run"]["graph"] == "spec"
                    && current["open_run"]["ticket"]["number"] == job.config.ticket,
                "manager_run_not_current"
            );
        }
        job.start_attempted |= tool == "run-start";
        job.attempts.insert(
            operation,
            Attempt {
                fingerprint,
                tool: tool.into(),
                response: None,
            },
        );
        save(&path, &job)?;
        let response = recording::request(
            &forge,
            &client,
            "tools/call",
            json!({"name":tool,"arguments":arguments}),
        )
        .await?;
        ensure!(
            serde_json::to_vec(&response)?.len() <= MAX_BLOB,
            "manager_response_bound"
        );
        if tool == "run-start" && response["isError"] != true {
            let value = object(response.clone())?;
            ensure!(
                value["graph"] == "spec"
                    && value["ticket"]["number"] == job.config.ticket
                    && value["ticket"]["project_id"] == json!(forge.forge_project),
                "manager_run_response_invalid"
            );
            job.run = Some(serde_json::from_value(value["run_id"].clone())?);
        }
        if tool == "run-status"
            && job.run.is_none()
            && job.start_attempted
            && response["isError"] != true
        {
            let value = object(response.clone())?;
            if value["open_run"].is_object() {
                ensure!(
                    value["open_run"]["graph"] == "spec"
                        && value["open_run"]["ticket"]["number"] == job.config.ticket
                        && value["open_run"]["ticket"]["project_id"] == json!(forge.forge_project),
                    "manager_run_response_invalid"
                );
                job.run = Some(serde_json::from_value(value["open_run"]["run_id"].clone())?);
            }
        }
        let bytes = serde_json::to_vec(&response)?;
        let metadata = Response {
            sha256: digest(&bytes),
            bytes: bytes.len(),
        };
        let response_path = path.join(format!("response-{operation}"));
        let _response_lock = service::lock(&response_path)?;
        service::atomic_write(&response_path, &bytes)?;
        job.attempts
            .get_mut(&operation)
            .context("manager_attempt_missing")?
            .response = Some(metadata);
        save(&path, &job)?;
        reply(&path, operation, job.attempts[&operation].response.as_ref())
    })
}

fn encode(bytes: &[u8]) -> String {
    bytes.iter().map(|byte| format!("{byte:02x}")).collect()
}

fn decode(text: &str) -> Result<Vec<u8>> {
    ensure!(
        text.len().is_multiple_of(2) && text.bytes().all(|c| c.is_ascii_hexdigit()),
        "manager_chunk_invalid"
    );
    text.as_bytes()
        .as_chunks::<2>()
        .0
        .iter()
        .map(|pair| Ok(u8::from_str_radix(std::str::from_utf8(pair)?, 16)?))
        .collect()
}

fn reply(path: &Path, operation: Uuid, metadata: Option<&Response>) -> Result<Value> {
    let Some(metadata) = metadata else {
        return Ok(json!({"ok":true,"state":"uncertain","response":null}));
    };
    let response = if metadata.bytes <= 24 * 1024 {
        let bytes = response_bytes(path, operation, metadata)?;
        serde_json::from_slice::<Value>(&bytes)?
    } else {
        Value::Null
    };
    Ok(
        json!({"ok":true,"state":"returned","response":response,"responseId":operation,
        "sha256":metadata.sha256,"bytes":metadata.bytes}),
    )
}

fn response_bytes(path: &Path, operation: Uuid, metadata: &Response) -> Result<Vec<u8>> {
    let bytes = service::private_read(
        &path
            .join(format!("response-{operation}"))
            .join("registry.json"),
        MAX_BLOB as u64,
    )?;
    ensure!(
        bytes.len() == metadata.bytes && digest(&bytes) == metadata.sha256,
        "manager_response_integrity"
    );
    Ok(bytes)
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Chunk {
    pub operation_id: Uuid,
    pub tool: String,
    pub sha256: String,
    pub offset: usize,
    pub hex: String,
    pub complete: bool,
}

pub fn upload(
    state: &Path,
    grant: &protocol::Grant,
    project: &str,
    id: Uuid,
    chunk: Chunk,
) -> Result<Value> {
    let path = state.join(id.to_string());
    ensure!(
        path.join("registry.json").try_exists()?,
        "manager_job_unknown"
    );
    let _lock = service::try_lock(&path)?.context("manager_job_busy")?;
    let mut job = load(&path)?;
    bound(&job, grant, project)?;
    ensure!(
        job.handoff
            .as_ref()
            .is_none_or(|h| h.binding.message_id != chunk.operation_id),
        "manager_operation_conflict"
    );
    ensure!(
        !chunk.operation_id.is_nil()
            && TOOLS.contains(&chunk.tool.as_str())
            && chunk.sha256.len() == 64
            && chunk.sha256.bytes().all(|c| c.is_ascii_hexdigit())
            && chunk.hex.len() <= 16 * 1024
            && chunk.offset <= MAX_BLOB,
        "manager_upload_invalid"
    );
    decode(&chunk.hex)?;
    if !job.uploads.contains(&chunk.operation_id) {
        ensure!(
            job.uploads.len() < 128 && chunk.offset == 0,
            "manager_upload_capacity_or_offset"
        );
        job.uploads.insert(chunk.operation_id);
        save(&path, &job)?;
    }
    let upload_path = path.join(format!("upload-{}", chunk.operation_id));
    let _upload_lock = service::lock(&upload_path)?;
    let mut upload: Upload = if upload_path.join("registry.json").try_exists()? {
        serde_json::from_slice(&service::private_read(
            &upload_path.join("registry.json"),
            2 * MAX_BLOB as u64 + 4096,
        )?)?
    } else {
        Upload {
            tool: chunk.tool.clone(),
            sha256: chunk.sha256.clone(),
            hex: String::new(),
            complete: false,
        }
    };
    ensure!(
        upload.tool == chunk.tool && upload.sha256 == chunk.sha256,
        "manager_upload_conflict"
    );
    let offset = chunk.offset * 2;
    if offset < upload.hex.len() || upload.complete {
        ensure!(
            offset + chunk.hex.len() <= upload.hex.len()
                && upload.hex[offset..offset + chunk.hex.len()] == chunk.hex
                && (!chunk.complete || upload.complete),
            "manager_upload_conflict"
        );
    } else {
        ensure!(
            !job.attempts.contains_key(&chunk.operation_id),
            "manager_upload_already_submitted"
        );
        ensure!(
            offset == upload.hex.len() && offset + chunk.hex.len() <= 2 * MAX_BLOB,
            "manager_upload_offset_or_bound"
        );
        upload.hex.push_str(&chunk.hex);
        if chunk.complete {
            let bytes = decode(&upload.hex)?;
            ensure!(digest(&bytes) == upload.sha256, "manager_upload_digest");
            let _: Value = serde_json::from_slice(&bytes)?;
            upload.complete = true;
        }
        service::atomic_write(&upload_path, &serde_json::to_vec(&upload)?)?;
    }
    Ok(json!({"ok":true,"bytes":upload.hex.len()/2,"complete":upload.complete}))
}

pub fn read(
    state: &Path,
    grant: &protocol::Grant,
    project: &str,
    id: Uuid,
    operation: Uuid,
    offset: usize,
) -> Result<Value> {
    let path = state.join(id.to_string());
    ensure!(
        path.join("registry.json").try_exists()?,
        "manager_job_unknown"
    );
    let job = load(&path)?;
    bound(&job, grant, project)?;
    let metadata = job
        .attempts
        .get(&operation)
        .and_then(|a| a.response.as_ref())
        .context("manager_response_missing")?;
    let bytes = response_bytes(&path, operation, metadata)?;
    ensure!(offset <= bytes.len(), "manager_response_offset");
    let end = bytes.len().min(offset + 8192);
    Ok(
        json!({"ok":true,"responseId":operation,"offset":offset,"hex":encode(&bytes[offset..end]),
        "next":if end < bytes.len(){Some(end)}else{None},"sha256":metadata.sha256,"bytes":bytes.len()}),
    )
}

/// Read one candidate returned through this job's bound foreman. Review remains pending.
pub fn read_result(
    state: &Path,
    grant: &protocol::Grant,
    project: &str,
    id: Uuid,
    request: crate::foreman::managed::ReadResult,
) -> Result<Value> {
    let job = load(&state.join(id.to_string()))?;
    bound(&job, grant, project)?;
    let handoff = job
        .handoff
        .as_ref()
        .filter(|h| h.queued)
        .context("manager_handoff_missing")?;
    crate::foreman::managed::result(
        &job.config.bridge_state.join("foreman"),
        grant,
        &handoff.binding,
        request,
    )
}

/// Read a local submission receipt. Forge remains the authority for remote effects.
pub fn status(
    state: &Path,
    grant: &protocol::Grant,
    project: &str,
    id: Uuid,
    operation: Uuid,
) -> Result<Value> {
    let path = state.join(id.to_string());
    ensure!(
        path.join("registry.json").try_exists()?,
        "manager_job_unknown"
    );
    let job = load(&path)?;
    bound(&job, grant, project)?;
    if let Some(handoff) = &job.handoff
        && handoff.binding.message_id == operation
    {
        return Ok(
            json!({"ok":true,"jobId":id,"operationId":operation,"tool":"factory_handoff",
            "state":if handoff.queued {"returned"}else{"uncertain"},"binding":handoff.binding}),
        );
    }
    let attempt = job.attempts.get(&operation);
    Ok(json!({"ok":true,"jobId":id,"operationId":operation,
        "state": match attempt {Some(a) if a.response.is_some() => "returned", Some(_) => "uncertain",
            None if job.uploads.contains(&operation) => "staged", None => "not_submitted"},
        "tool":attempt.map(|a| &a.tool),"receipt":attempt.and_then(|a| a.response.as_ref()),
        "authority":"local submission receipt. Read Forge for remote effects."}))
}
