//! Optional canonical-workflow tools. The bridge rechecks the role and prepared scope.
use crate::{
    foreman::managed::{ReadHandoff, ReadSpec},
    protocol,
};
use anyhow::Result;
use serde::Deserialize;
use serde_json::{Value, json};
use uuid::Uuid;

fn tool(name: &str, description: &str, properties: Value, required: Value, read: bool) -> Value {
    json!({"name":name,"description":description,"inputSchema":{"type":"object","properties":properties,
        "required":required,"additionalProperties":false},"annotations":{"readOnlyHint":read,
        "destructiveHint":false,"idempotentHint":true,"openWorldHint":false}})
}

pub(super) fn tools(role: protocol::Role) -> Vec<Value> {
    let id = json!({"type":"string","format":"uuid"});
    let field = json!({"type":"string","enum":["scope","done_when","evidence","not_in_scope","body",
        "regression_suites","named_symbols","changed_symbols","research_citations"]});
    let offset = json!({"type":"integer","minimum":0});
    if role == protocol::Role::Worker {
        vec![
            tool(
                "workflow_read_spec",
                "Read one field of your issued canonical specification. Follow next to read the complete field.",
                json!({"run_id":id,"field":field,"offset":offset}),
                json!(["run_id", "field"]),
                true,
            ),
            tool(
                "workflow_ack_context",
                "Acknowledge the briefing for your assigned run. This receipt does not authorize execution.",
                json!({"run_id":id,"context_digest":{"type":"string","pattern":"^[a-f0-9]{64}$"}}),
                json!(["run_id", "context_digest"]),
                false,
            ),
        ]
    } else {
        let mut foreman_fields = field;
        if let Some(values) = foreman_fields["enum"].as_array_mut() {
            values.extend([json!("intent"), json!("intent_reference")]);
        }
        vec![
            tool(
                "workflow_read_handoff",
                "Read one field of a handoff addressed to you. Read the original intent before assignment.",
                json!({"job_id":id,"field":foreman_fields,"offset":offset}),
                json!(["job_id", "field"]),
                true,
            ),
            tool(
                "workflow_claim",
                "Claim the approved canonical work for one permitted worker. This tool sends the briefing. It does not issue execution.",
                json!({"operation_id":id,"job_id":id,"worker":id,"context":{"type":"string","minLength":1,"maxLength":7000},
                    "brief":{"type":"string","minLength":1,"maxLength":4096}}),
                json!(["operation_id", "job_id", "worker", "context", "brief"]),
                false,
            ),
            tool(
                "workflow_issue_spec",
                "Issue the bound specification after context acknowledgement and operator preparation. This tool accepts no replacement specification.",
                json!({"run_id":id,"lease_seconds":{"type":"integer","minimum":1,"maximum":3600}}),
                json!(["run_id", "lease_seconds"]),
                false,
            ),
            tool(
                "workflow_return_result",
                "Return a verified review candidate to its manager. This tool cannot complete a canonical ticket.",
                json!({"run_id":id}),
                json!(["run_id"]),
                false,
            ),
            tool(
                "workflow_assignment",
                "Read one assignment in your prepared project. A reported result still needs review.",
                json!({"run_id":id}),
                json!(["run_id"]),
                true,
            ),
        ]
    }
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Ack {
    run_id: Uuid,
    context_digest: String,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Claim {
    operation_id: Uuid,
    job_id: Uuid,
    worker: Uuid,
    context: String,
    brief: String,
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Issue {
    run_id: Uuid,
    lease_seconds: u64,
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Status {
    run_id: Uuid,
}

pub(super) fn frame(
    role: protocol::Role,
    name: &str,
    arguments: Value,
) -> Result<Option<(protocol::Operation, Value)>> {
    let (operation, frame) = match (role, name) {
        (protocol::Role::Worker, "workflow_ack_context") => {
            let args: Ack = serde_json::from_value(arguments)?;
            (
                protocol::Operation::Acknowledge,
                json!({"method":"acknowledge_context","run_id":args.run_id,"context_digest":args.context_digest}),
            )
        }
        (protocol::Role::Worker, "workflow_read_spec") => {
            let read: ReadSpec = serde_json::from_value(arguments)?;
            (
                protocol::Operation::Observe,
                json!({"method":"read_assignment_spec","read":{
                "run_id":read.run_id,"field":read.field,"offset":read.offset}}),
            )
        }
        (protocol::Role::Foreman, "workflow_read_handoff") => {
            let read: ReadHandoff = serde_json::from_value(arguments)?;
            (
                protocol::Operation::Observe,
                json!({"method":"read_handoff","read":{
                "job_id":read.job_id,"field":read.field,"offset":read.offset}}),
            )
        }
        (protocol::Role::Foreman, "workflow_claim") => {
            let args: Claim = serde_json::from_value(arguments)?;
            (
                protocol::Operation::Acknowledge,
                json!({"method":"foreman_action","action":{"action":"claim",
                "operation_id":args.operation_id,"manager_job":args.job_id,"worker":args.worker,
                "context":args.context,"brief":args.brief}}),
            )
        }
        (protocol::Role::Foreman, "workflow_issue_spec") => {
            let args: Issue = serde_json::from_value(arguments)?;
            (
                protocol::Operation::Acknowledge,
                json!({"method":"foreman_action","action":{"action":"issue_spec",
                "run_id":args.run_id,"lease_seconds":args.lease_seconds}}),
            )
        }
        (protocol::Role::Foreman, "workflow_assignment") => {
            let args: Status = serde_json::from_value(arguments)?;
            (
                protocol::Operation::Observe,
                json!({"method":"foreman_action","action":{"action":"status","run_id":args.run_id}}),
            )
        }
        (protocol::Role::Foreman, "workflow_return_result") => {
            let args: Status = serde_json::from_value(arguments)?;
            (
                protocol::Operation::Acknowledge,
                json!({"method":"foreman_action",
                "action":{"action":"forward_result","run_id":args.run_id}}),
            )
        }
        _ => return Ok(None),
    };
    Ok(Some((operation, frame)))
}
