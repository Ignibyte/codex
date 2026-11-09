//! A fixed read-only Marley work provider over bounded newline-delimited MCP.
use crate::client;
use crate::observation::PresenceReport;
use crate::protocol;
use anyhow::Context;
use anyhow::Result;
use anyhow::bail;
use anyhow::ensure;
use serde_json::Value;
use serde_json::json;
use std::collections::BTreeSet;
use std::io::BufRead;
use std::io::Read;
use std::io::Write;
use std::path::Path;
use uuid::Uuid;

pub struct Binding<'a> {
    pub socket: &'a Path,
    pub service_uid: u32,
    pub credential: &'a Path,
    pub grant: &'a protocol::Grant,
    pub project: &'a str,
}

impl Binding<'_> {
    fn exchange(&self, operation: protocol::Operation, frame: Value) -> Result<Value> {
        self.grant.validate()?;
        let mut request = client::request(
            self.grant,
            protocol::Operation::Observe,
            self.grant.seat,
            self.project,
            1,
        );
        let sync = client::exchange(
            self.socket,
            self.service_uid,
            self.credential,
            &request,
            json!({"method":"sync"}),
        )?;
        ensure!(
            sync["grant"] == serde_json::to_value(self.grant)?,
            "observation_grant_changed"
        );
        request.sequence = sync["lastSequence"]
            .as_u64()
            .context("observation_sequence_invalid")?
            .checked_add(1)
            .context("sequence_exhausted")?;
        request.operation = operation;
        client::exchange(
            self.socket,
            self.service_uid,
            self.credential,
            &request,
            frame,
        )
    }

    pub fn report(&self, report: PresenceReport) -> Result<Value> {
        self.exchange(
            protocol::Operation::Acknowledge,
            json!({"method":"report_presence","report":report}),
        )
    }

    fn snapshot(&self, after: Option<Uuid>, agent_id: Option<Uuid>) -> Result<Value> {
        ensure!(
            self.grant.role == protocol::Role::Observer
                && self.grant.operations == BTreeSet::from([protocol::Operation::Observe]),
            "observation_reader_role_denied"
        );
        self.exchange(
            protocol::Operation::Observe,
            json!({"method":"observe_work","after":after,"agent_id":agent_id}),
        )
    }

    fn tool(&self, name: &str, arguments: &Value) -> Result<Value> {
        let arguments = arguments
            .as_object()
            .context("observation_arguments_invalid")?;
        let (after, agent) = match name {
            "work_handshake" | "work_agents" => {
                ensure!(arguments.is_empty(), "observation_arguments_invalid");
                (None, None)
            }
            "work_agent" => {
                ensure!(arguments.len() == 1, "observation_arguments_invalid");
                (
                    None,
                    Some(Uuid::parse_str(
                        arguments
                            .get("id")
                            .and_then(Value::as_str)
                            .context("observation_agent_id_invalid")?,
                    )?),
                )
            }
            "work_changes" => {
                ensure!(
                    arguments.len() <= 1 && arguments.keys().all(|key| key == "after"),
                    "observation_arguments_invalid"
                );
                (
                    arguments
                        .get("after")
                        .filter(|value| !value.is_null())
                        .map(|value| {
                            Uuid::parse_str(value.as_str().context("observation_cursor_invalid")?)
                                .map_err(anyhow::Error::from)
                        })
                        .transpose()?,
                    None,
                )
            }
            _ => bail!("observation_tool_denied"),
        };
        let snapshot = self.snapshot(after, agent)?;
        match name {
            "work_handshake" => Ok(json!({"contract":"marley.work/v1",
                "provider":{"name":"codex-factory-observer","version":env!("CARGO_PKG_VERSION")},
                "capabilities":["agents","events","changes"],"poll_s":5,"stale_after_s":15})),
            "work_agents" => Ok(json!({"agents":snapshot["agents"],"cursor":snapshot["cursor"]})),
            "work_changes" => Ok(
                json!({"cursor":snapshot["cursor"],"reset":snapshot["reset"],
                "changed":snapshot["changed"],"gap":snapshot["gap"]}),
            ),
            "work_agent" => Ok(snapshot["details"]
                [agent.context("observation_agent_id_invalid")?.to_string()]
            .clone()),
            _ => bail!("observation_tool_denied"),
        }
    }

    fn rpc(&self, request: &Value) -> Result<Value> {
        ensure!(request["jsonrpc"] == "2.0", "observation_rpc_invalid");
        match request["method"]
            .as_str()
            .context("observation_rpc_invalid")?
        {
            "initialize" => {
                let version = request["params"]["protocolVersion"]
                    .as_str()
                    .context("observation_rpc_invalid")?;
                ensure!(
                    ["2024-11-05", "2025-03-26", "2025-06-18", "2025-11-25"].contains(&version),
                    "observation_mcp_version_unsupported"
                );
                Ok(
                    json!({"protocolVersion":version,"capabilities":{"tools":{}},
                    "serverInfo":{"name":"codex-factory-observer","version":env!("CARGO_PKG_VERSION")}}),
                )
            }
            "ping" => Ok(json!({})),
            "tools/list" => Ok(json!({"tools":[
                {"name":"work_handshake","description":"Read the observation contract.","inputSchema":{"type":"object","properties":{},"additionalProperties":false}},
                {"name":"work_agents","description":"Read agents in the granted project and recipient scope.","inputSchema":{"type":"object","properties":{},"additionalProperties":false}},
                {"name":"work_agent","description":"Read one granted agent and four separate status reports.","inputSchema":{"type":"object","properties":{"id":{"type":"string","format":"uuid"}},"required":["id"],"additionalProperties":false}},
                {"name":"work_changes","description":"Read changes after an opaque cursor. A reset reports a history gap.","inputSchema":{"type":"object","properties":{"after":{"type":["string","null"]}},"additionalProperties":false}}
            ]})),
            "tools/call" => {
                let params = request["params"]
                    .as_object()
                    .context("observation_rpc_invalid")?;
                ensure!(
                    params.keys().all(|key| key == "name" || key == "arguments"),
                    "observation_arguments_invalid"
                );
                let empty = json!({});
                let value = self.tool(
                    params
                        .get("name")
                        .and_then(Value::as_str)
                        .context("observation_tool_denied")?,
                    params.get("arguments").unwrap_or(&empty),
                )?;
                Ok(
                    json!({"content":[{"type":"text","text":serde_json::to_string(&value)?}],"structuredContent":value,"isError":false}),
                )
            }
            _ => bail!("observation_method_denied"),
        }
    }

    pub fn serve(&self) -> Result<()> {
        // Reject a control grant before the first byte is served.
        self.snapshot(None, None)?;
        serve_rpc(
            |request| self.rpc(request),
            "The observation request is denied.",
        )
    }
}

pub(crate) fn serve_rpc(rpc: impl Fn(&Value) -> Result<Value>, denial: &str) -> Result<()> {
    serve_rpc_bounded(rpc, denial, protocol::MAX_FRAME, 4 * protocol::MAX_FRAME)
}

pub(crate) fn serve_rpc_bounded(
    rpc: impl Fn(&Value) -> Result<Value>,
    denial: &str,
    input_bound: usize,
    output_bound: usize,
) -> Result<()> {
    let mut input = std::io::stdin().lock();
    let mut output = std::io::stdout().lock();
    loop {
        let mut bytes = Vec::new();
        let read = input
            .by_ref()
            .take((input_bound + 1) as u64)
            .read_until(b'\n', &mut bytes)?;
        if read == 0 {
            return Ok(());
        }
        ensure!(
            bytes.len() <= input_bound && bytes.ends_with(b"\n"),
            "observation_frame_bound"
        );
        let request: Value = serde_json::from_slice(&bytes)?;
        if request.get("id").is_none() {
            continue;
        }
        ensure!(
            request["id"].is_string() || request["id"].is_number(),
            "observation_rpc_id_invalid"
        );
        let response = match rpc(&request) {
            Ok(result) => json!({"jsonrpc":"2.0","id":request["id"],"result":result}),
            // Do not reflect caller text, private paths or credential parsing errors.
            Err(_) => {
                json!({"jsonrpc":"2.0","id":request["id"],"error":{"code":-32602,"message":denial}})
            }
        };
        let mut bytes = serde_json::to_vec(&response)?;
        ensure!(bytes.len() <= output_bound, "observation_response_bound");
        bytes.push(b'\n');
        output.write_all(&bytes)?;
        output.flush()?;
    }
}
