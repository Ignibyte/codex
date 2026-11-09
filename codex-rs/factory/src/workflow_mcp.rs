//! A role-scoped plugin interface. Tools record facts and advice without execution authority.
use crate::client;
use crate::foreman::supervision::{Decision, Report};
use crate::observation_mcp::{Binding, serve_rpc};
use crate::protocol;
use anyhow::{Context, Result, bail, ensure};
use serde::Deserialize;
use serde_json::{Value, json};
use uuid::Uuid;

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct ReportArgs {
    run_id: Uuid,
    report: Report,
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct DecisionArgs {
    run_id: Uuid,
    notice_id: Uuid,
    decision: Decision,
}

mod canonical;

fn tools(role: protocol::Role, canonical_specs: bool) -> Value {
    let common = json!({"type":"string","format":"uuid"});
    let mut tool = if role == protocol::Role::Worker {
        json!({"name":"workflow_report_blockage","description":"Report one blockage for your assigned run. A report does not prove progress.",
            "inputSchema":{"type":"object","properties":{"run_id":common,"report":{"type":"object",
                "properties":{"incidentId":common,"reason":{"type":"string","enum":["blocked","rate_limit","approval_required"]},
                    "summary":{"type":"string","minLength":1,"maxLength":512}},
                "required":["incidentId","reason","summary"],"additionalProperties":false}},
                "required":["run_id","report"],"additionalProperties":false}})
    } else {
        json!({"name":"workflow_decision","description":"Record advice for one current notice. Advice cannot resume execution or complete a canonical gate.",
            "inputSchema":{"type":"object","properties":{"run_id":common,"notice_id":common,
                "decision":{"type":"object","properties":{"choice":{"type":"string","enum":["wait","request_human","review_result","recommend_reconciliation"]},
                    "explanation":{"type":"string","minLength":1,"maxLength":512}},
                    "required":["choice","explanation"],"additionalProperties":false}},
                "required":["run_id","notice_id","decision"],"additionalProperties":false}})
    };
    tool["annotations"] = json!({"readOnlyHint":false,"destructiveHint":false,
        "idempotentHint":true,"openWorldHint":false});
    let mut tools = vec![tool];
    if canonical_specs {
        tools.extend(canonical::tools(role));
    }
    json!({"tools":tools})
}
fn rpc(binding: &Binding<'_>, canonical_specs: bool, request: &Value) -> Result<Value> {
    ensure!(request["jsonrpc"] == "2.0", "workflow_rpc_invalid");
    match request["method"].as_str().context("workflow_rpc_invalid")? {
        "initialize" => {
            let version = request["params"]["protocolVersion"]
                .as_str()
                .context("workflow_rpc_invalid")?;
            ensure!(
                ["2024-11-05", "2025-03-26", "2025-06-18", "2025-11-25"].contains(&version),
                "workflow_mcp_version_unsupported"
            );
            Ok(
                json!({"protocolVersion":version,"capabilities":{"tools":{}},
                "serverInfo":{"name":"codex-factory-workflow","version":env!("CARGO_PKG_VERSION")}}),
            )
        }
        "ping" => Ok(json!({})),
        "tools/list" => Ok(tools(binding.grant.role, canonical_specs)),
        "tools/call" => {
            let params = request["params"]
                .as_object()
                .context("workflow_arguments_invalid")?;
            ensure!(
                params
                    .keys()
                    .all(|k| k == "name" || k == "arguments" || k == "_meta"),
                "workflow_arguments_invalid"
            );
            // MCP transport metadata carries no authority. The bridge authenticates the configured grant.
            ensure!(
                params.get("_meta").is_none_or(Value::is_object),
                "workflow_metadata_invalid"
            );
            let canonical = if canonical_specs {
                canonical::frame(
                    binding.grant.role,
                    request["params"]["name"]
                        .as_str()
                        .context("workflow_tool_denied")?,
                    request["params"]["arguments"].clone(),
                )?
            } else {
                None
            };
            let (operation, frame) = if let Some(frame) = canonical {
                frame
            } else {
                (
                    protocol::Operation::Acknowledge,
                    match (binding.grant.role, request["params"]["name"].as_str()) {
                        (protocol::Role::Worker, Some("workflow_report_blockage")) => {
                            let args: ReportArgs =
                                serde_json::from_value(request["params"]["arguments"].clone())?;
                            json!({"method":"report_blockage","run_id":args.run_id,"report":args.report})
                        }
                        (protocol::Role::Foreman, Some("workflow_decision")) => {
                            let args: DecisionArgs =
                                serde_json::from_value(request["params"]["arguments"].clone())?;
                            json!({"method":"blockage_decision","run_id":args.run_id,"notice_id":args.notice_id,"decision":args.decision})
                        }
                        _ => bail!("workflow_tool_denied"),
                    },
                )
            };
            let value = client::scoped_call(
                binding.grant,
                binding.socket,
                binding.service_uid,
                binding.credential,
                binding.project,
                operation,
                frame,
            )?;
            Ok(
                json!({"content":[{"type":"text","text":serde_json::to_string(&value)?}],"structuredContent":value,"isError":false}),
            )
        }
        _ => bail!("workflow_method_denied"),
    }
}
pub fn serve(binding: &Binding<'_>, canonical_specs: bool) -> Result<()> {
    binding.grant.validate()?;
    ensure!(
        [protocol::Role::Worker, protocol::Role::Foreman].contains(&binding.grant.role)
            && binding
                .grant
                .operations
                .contains(&protocol::Operation::Observe)
            && binding
                .grant
                .operations
                .contains(&protocol::Operation::Acknowledge)
            && binding.grant.projects.contains(binding.project),
        "workflow_role_denied"
    );
    // Validate the actual grant and credential without exposing the sync response to the model.
    client::scoped_call(
        binding.grant,
        binding.socket,
        binding.service_uid,
        binding.credential,
        binding.project,
        protocol::Operation::Observe,
        json!({"method":"sync"}),
    )?;
    serve_rpc(
        |request| rpc(binding, canonical_specs, request),
        "The workflow request is denied.",
    )
}
