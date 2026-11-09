//! Executive spec tools with pinned Forge authority and chunked bridge transport.
use crate::client;
use crate::manager;
use crate::observation_mcp::{Binding, serve_rpc_bounded};
use crate::protocol;
use anyhow::{Context, Result, bail, ensure};
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use uuid::Uuid;

fn exchange(binding: &Binding<'_>, operation: protocol::Operation, frame: Value) -> Result<Value> {
    client::scoped_call(
        binding.grant,
        binding.socket,
        binding.service_uid,
        binding.credential,
        binding.project,
        operation,
        frame,
    )
}

fn tools(binding: &Binding<'_>, job: Uuid) -> Result<Value> {
    let mut offset = 0;
    let mut all = Vec::new();
    loop {
        let page = exchange(
            binding,
            protocol::Operation::Observe,
            json!({"method":"manager_catalogue","job_id":job,"offset":offset}),
        )?;
        all.extend(
            page["tools"]
                .as_array()
                .context("manager_catalogue_invalid")?
                .iter()
                .cloned(),
        );
        ensure!(all.len() <= manager::TOOLS.len(), "manager_catalogue_bound");
        let Some(next) = page["next"].as_u64() else {
            break;
        };
        ensure!(next as usize > offset, "manager_catalogue_offset");
        offset = next as usize;
    }
    let mut catalogue = json!({"tools":all});
    for tool in catalogue["tools"]
        .as_array_mut()
        .context("manager_catalogue_invalid")?
    {
        let schema = &mut tool["inputSchema"];
        schema["properties"]["_factory_operation_id"] = json!({"type":"string","format":"uuid",
            "description":"Use one new UUID for each operation. Reuse the UUID only to read the same operation's result."});
        if schema.get("required").is_none() {
            schema["required"] = json!([]);
        }
        schema["required"]
            .as_array_mut()
            .context("manager_schema_invalid")?
            .push(json!("_factory_operation_id"));
    }
    catalogue["tools"].as_array_mut().context("manager_catalogue_invalid")?.push(json!({
        "name":"factory_operation_status", "description":"Read one local operation receipt. Read Forge to confirm remote effects. Do not repeat an uncertain write.",
        "inputSchema":{"type":"object","properties":{"operation_id":{"type":"string","format":"uuid"}},
            "required":["operation_id"],"additionalProperties":false},
        "annotations":{"readOnlyHint":true,"destructiveHint":false,"idempotentHint":true,"openWorldHint":false}}));
    catalogue["tools"].as_array_mut().context("manager_catalogue_invalid")?.push(json!({
        "name":"factory_handoff", "description":"Approve the current sealed spec. Send its versioned reference to the configured foreman. This tool cannot assign a worker.",
        "inputSchema":{"type":"object","properties":{"operation_id":{"type":"string","format":"uuid"}},
            "required":["operation_id"],"additionalProperties":false},
        "annotations":{"readOnlyHint":false,"destructiveHint":false,"idempotentHint":true,"openWorldHint":false}}));
    catalogue["tools"].as_array_mut().context("manager_catalogue_invalid")?.push(json!({
        "name":"factory_read_result", "description":"Read a result returned by your foreman. Read each artifact by index. Follow next for complete bytes. Review remains pending.",
        "inputSchema":{"type":"object","properties":{"run_id":{"type":"string","format":"uuid"},
            "artifact":{"type":"integer","minimum":0,"maximum":7},"offset":{"type":"integer","minimum":0}},
            "required":["run_id"],"additionalProperties":false},
        "annotations":{"readOnlyHint":true,"destructiveHint":false,"idempotentHint":true,"openWorldHint":false}}));
    Ok(json!({"tools":catalogue["tools"]}))
}

fn hex(bytes: &[u8]) -> String {
    bytes.iter().map(|byte| format!("{byte:02x}")).collect()
}
fn decode(text: &str) -> Result<Vec<u8>> {
    ensure!(
        text.len().is_multiple_of(2) && text.bytes().all(|c| c.is_ascii_hexdigit()),
        "manager_response_chunk_invalid"
    );
    text.as_bytes()
        .as_chunks::<2>()
        .0
        .iter()
        .map(|pair| Ok(u8::from_str_radix(std::str::from_utf8(pair)?, 16)?))
        .collect()
}

fn call(binding: &Binding<'_>, job: Uuid, params: &Value) -> Result<Value> {
    let params = params.as_object().context("manager_arguments_invalid")?;
    ensure!(
        params
            .keys()
            .all(|k| ["name", "arguments", "_meta"].contains(&k.as_str())),
        "manager_arguments_invalid"
    );
    ensure!(
        params.get("_meta").is_none_or(Value::is_object),
        "manager_metadata_invalid"
    );
    let name = params["name"].as_str().context("manager_tool_invalid")?;
    if name == "factory_read_result" {
        let read: crate::foreman::managed::ReadResult =
            serde_json::from_value(params["arguments"].clone())?;
        let value = exchange(
            binding,
            protocol::Operation::Observe,
            json!({"method":"manager_read_result","job_id":job,"read":{
                "run_id":read.run_id,"artifact":read.artifact,"offset":read.offset}}),
        )?;
        return Ok(
            json!({"content":[{"type":"text","text":serde_json::to_string(&value)?}],"structuredContent":value,"isError":false}),
        );
    }
    if name == "factory_handoff" {
        let args = params["arguments"]
            .as_object()
            .context("manager_arguments_invalid")?;
        ensure!(args.len() == 1, "manager_arguments_invalid");
        let operation: Uuid = serde_json::from_value(
            args.get("operation_id")
                .context("manager_operation_required")?
                .clone(),
        )?;
        let value = exchange(
            binding,
            protocol::Operation::Queue,
            json!({"method":"manager_handoff","job_id":job,"operation_id":operation}),
        )?;
        return Ok(
            json!({"content":[{"type":"text","text":serde_json::to_string(&value)?}],"structuredContent":value,"isError":false}),
        );
    }
    if name == "factory_operation_status" {
        let args = params["arguments"]
            .as_object()
            .context("manager_arguments_invalid")?;
        ensure!(args.len() == 1, "manager_arguments_invalid");
        let operation: Uuid = serde_json::from_value(
            args.get("operation_id")
                .context("manager_operation_required")?
                .clone(),
        )?;
        let value = exchange(
            binding,
            protocol::Operation::Observe,
            json!({"method":"manager_operation","job_id":job,"operation_id":operation}),
        )?;
        return Ok(
            json!({"content":[{"type":"text","text":serde_json::to_string(&value)?}],"structuredContent":value,"isError":false}),
        );
    }
    ensure!(manager::TOOLS.contains(&name), "manager_tool_denied");
    let mut args = params["arguments"]
        .as_object()
        .context("manager_arguments_invalid")?
        .clone();
    let operation: Uuid = serde_json::from_value(
        args.remove("_factory_operation_id")
            .context("manager_operation_required")?,
    )?;
    ensure!(!operation.is_nil(), "manager_operation_required");
    let arguments = Value::Object(args);
    let bytes = serde_json::to_vec(&arguments)?;
    ensure!(bytes.len() <= 512 * 1024, "manager_arguments_bound");
    let staged = bytes.len() > 24 * 1024;
    if staged {
        let digest = format!("{:x}", Sha256::digest(&bytes));
        for (index, chunk) in bytes.chunks(8192).enumerate() {
            exchange(
                binding,
                protocol::Operation::Acknowledge,
                json!({"method":"manager_upload","job_id":job,"chunk":{
                    "operation_id":operation,"tool":name,"sha256":digest,"offset":index*8192,
                    "hex":hex(chunk),"complete":(index+1)*8192>=bytes.len()}}),
            )?;
        }
    }
    let result = exchange(
        binding,
        protocol::Operation::Acknowledge,
        json!({"method":"manager_spec","job_id":job,"operation_id":operation,"tool":name,
            "arguments":if staged {Value::Null}else{arguments}}),
    )?;
    ensure!(
        result["state"] == "returned",
        "manager_operation_uncertain_do_not_resubmit"
    );
    if !result["response"].is_null() {
        return Ok(result["response"].clone());
    }
    let total = result["bytes"]
        .as_u64()
        .context("manager_response_invalid")? as usize;
    ensure!(total <= 512 * 1024, "manager_response_bound");
    let mut bytes = Vec::new();
    while bytes.len() < total {
        let chunk = exchange(
            binding,
            protocol::Operation::Observe,
            json!({"method":"manager_read","job_id":job,"operation_id":operation,"offset":bytes.len()}),
        )?;
        ensure!(
            chunk["sha256"] == result["sha256"] && chunk["bytes"] == result["bytes"],
            "manager_response_changed"
        );
        let part = decode(chunk["hex"].as_str().context("manager_response_invalid")?)?;
        ensure!(
            !part.is_empty() && bytes.len() + part.len() <= total,
            "manager_response_chunk_invalid"
        );
        bytes.extend_from_slice(&part);
    }
    ensure!(
        result["sha256"] == format!("{:x}", Sha256::digest(&bytes)),
        "manager_response_digest"
    );
    Ok(serde_json::from_slice(&bytes)?)
}

fn rpc(binding: &Binding<'_>, job: Uuid, request: &Value) -> Result<Value> {
    ensure!(request["jsonrpc"] == "2.0", "manager_rpc_invalid");
    match request["method"].as_str().context("manager_rpc_invalid")? {
        "initialize" => {
            let version = request["params"]["protocolVersion"]
                .as_str()
                .context("manager_rpc_invalid")?;
            ensure!(
                ["2024-11-05", "2025-03-26", "2025-06-18", "2025-11-25"].contains(&version),
                "manager_mcp_version_unsupported"
            );
            Ok(
                json!({"protocolVersion":version,"capabilities":{"tools":{}},
                "serverInfo":{"name":"codex-factory-manager","version":env!("CARGO_PKG_VERSION")}}),
            )
        }
        "ping" => Ok(json!({})),
        "tools/list" => tools(binding, job),
        "tools/call" => call(binding, job, &request["params"]),
        _ => bail!("manager_method_denied"),
    }
}

pub fn serve(binding: &Binding<'_>, job: Uuid) -> Result<()> {
    binding.grant.validate()?;
    ensure!(
        binding.grant.role == protocol::Role::Manager
            && binding.grant.projects.contains(binding.project)
            && binding
                .grant
                .operations
                .contains(&protocol::Operation::Observe)
            && binding
                .grant
                .operations
                .contains(&protocol::Operation::Acknowledge),
        "manager_role_denied"
    );
    tools(binding, job)?;
    serve_rpc_bounded(
        |request| rpc(binding, job, request),
        "The manager request is denied. Read the operation status before another write.",
        768 * 1024,
        2 * 1024 * 1024,
    )
}
