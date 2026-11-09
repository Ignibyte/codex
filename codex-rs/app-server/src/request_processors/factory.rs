//! Experimental local coworker extension. Identity comes only from the protected
//! startup binding and authenticated mailbox, never from RPC sender fields.
#[path = "factory_reconcile.rs"]
mod reconcile;

use super::ClientResponsePayload;
use super::JSONRPCErrorError;
use super::invalid_request;
use super::thread_processor::ThreadRequestProcessor;
use anyhow::Context;
use anyhow::Result;
use anyhow::ensure;
use codex_app_server_protocol::FactoryListParams;
use codex_app_server_protocol::FactoryMessageIdParams;
use codex_app_server_protocol::FactoryResponse;
use codex_app_server_protocol::FactorySendParams;
use codex_app_server_protocol::ThreadFactoryMessageParams;
use codex_factory::client;
use codex_factory::protocol as fleet;
use codex_protocol::AgentPath;
use codex_protocol::ResponseItemId;
use codex_protocol::protocol::InterAgentCommunication;
use codex_protocol::turn_input::StartIfIdleSubmission;
use codex_protocol::turn_input::TurnInput;
use codex_protocol::turn_input::TurnInputRequest;
use codex_protocol::turn_input::TurnStartOptions;
use serde::Deserialize;
use serde_json::Value;
use serde_json::json;
use std::fs::OpenOptions;
use std::io::Read;
use std::os::unix::fs::MetadataExt;
use std::os::unix::fs::OpenOptionsExt;
use std::path::PathBuf;
use uuid::Uuid;

#[derive(Clone, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct Binding {
    service_uid: u32,
    socket: PathBuf,
    credential: PathBuf,
    state: PathBuf,
    grant: fleet::Grant,
    #[serde(default)]
    managed_assignments: bool,
}
pub(super) struct FactoryBridge {
    binding: Binding,
    serial: tokio::sync::Semaphore,
}
impl FactoryBridge {
    pub(super) fn from_environment() -> Result<Option<Self>> {
        let Some(path) = std::env::var_os("CODEX_FACTORY_BINDING") else {
            return Ok(None);
        };
        let service_uid: u32 = std::env::var("CODEX_FACTORY_SERVICE_UID")
            .context("factory_service_uid_missing")?
            .parse()?;
        let mut file = OpenOptions::new()
            .read(true)
            .custom_flags(libc::O_NOFOLLOW)
            .open(path)?;
        let info = file.metadata()?;
        ensure!(
            info.is_file()
                && info.uid() == service_uid
                && info.mode() & 0o022 == 0
                && info.len() <= 65536,
            "factory_binding_permissions"
        );
        let mut bytes = Vec::new();
        file.by_ref().take(65537).read_to_end(&mut bytes)?;
        let binding: Binding = serde_json::from_slice(&bytes)?;
        ensure!(
            binding.service_uid == service_uid && binding.state.is_absolute(),
            "factory_binding_invalid"
        );
        binding.grant.validate()?;
        Ok(Some(Self {
            binding,
            serial: tokio::sync::Semaphore::new(1),
        }))
    }
    async fn call(
        &self,
        project: &str,
        recipient: Uuid,
        operation: fleet::Operation,
        frame: Value,
    ) -> Result<Value> {
        let binding = self.binding.clone();
        let project = project.to_owned();
        tokio::task::spawn_blocking(move || {
            let sync = client::exchange(
                &binding.socket,
                binding.service_uid,
                &binding.credential,
                &client::request(
                    &binding.grant,
                    fleet::Operation::Observe,
                    binding.grant.seat,
                    &project,
                    1,
                ),
                json!({"method":"sync"}),
            )?;
            ensure!(
                sync["grant"] == serde_json::to_value(&binding.grant)?,
                "factory_grant_mismatch"
            );
            let next = sync["lastSequence"]
                .as_u64()
                .context("factory_sequence_missing")?
                .checked_add(1)
                .context("factory_sequence_exhausted")?;
            client::exchange(
                &binding.socket,
                binding.service_uid,
                &binding.credential,
                &client::request(&binding.grant, operation, recipient, &project, next),
                frame,
            )
        })
        .await?
    }
    async fn authorize_managed(
        &self,
        thread: &codex_core::CodexThread,
        envelope: &fleet::Envelope,
    ) -> Result<()> {
        if !self.binding.managed_assignments
            || envelope.kind != fleet::MessageKind::Assignment
            || envelope.mode == fleet::DeliveryMode::QueueOnly
        {
            return Ok(());
        }
        let snapshot = thread.config_snapshot().await;
        let cwd = snapshot.cwd().as_path();
        ensure!(
            !snapshot.full_access
                && snapshot
                    .workspace_roots
                    .iter()
                    .all(|root| root.as_path() == cwd),
            "factory_managed_workspace_isolation_required"
        );
        let (files, _network) = snapshot.permission_profile.to_runtime_permissions();
        ensure!(
            !files.has_full_disk_write_access()
                && files
                    .get_writable_roots_with_cwd(cwd)
                    .iter()
                    .all(|root| root.root.as_path().starts_with(cwd)),
            "factory_managed_workspace_isolation_required"
        );
        self.call(
            &envelope.project,
            self.binding.grant.seat,
            fleet::Operation::Observe,
            json!({"method":"authorize_assignment","message_id":envelope.message_id,"cwd":cwd}),
        )
        .await?;
        Ok(())
    }
    async fn transaction(&self) -> Result<client::LocalTransaction> {
        let state = self.binding.state.clone();
        tokio::task::spawn_blocking(move || client::LocalTransaction::begin(&state)).await?
    }
}
fn error(error: impl std::fmt::Display) -> JSONRPCErrorError {
    invalid_request(format!("factory: {error}"))
}

impl ThreadRequestProcessor {
    fn factory(&self) -> Result<&FactoryBridge> {
        self.factory_bridge
            .as_ref()
            .as_ref()
            .map_err(|e| anyhow::anyhow!("{e}"))?
            .as_ref()
            .context("factory_disabled")
    }
    pub(crate) async fn factory_status(
        &self,
        params: FactoryMessageIdParams,
        cancel: bool,
    ) -> Result<Option<ClientResponsePayload>, JSONRPCErrorError> {
        let data = self
            .factory_status_inner(params, cancel)
            .await
            .map_err(error)?;
        let response = FactoryResponse { data };
        Ok(Some(if cancel {
            ClientResponsePayload::FactoryCancel(response)
        } else {
            ClientResponsePayload::FactoryStatus(response)
        }))
    }
    async fn factory_status_inner(
        &self,
        params: FactoryMessageIdParams,
        cancel: bool,
    ) -> Result<Value> {
        let bridge = self.factory()?;
        let _serial = bridge.serial.acquire().await?;
        let _transaction = bridge.transaction().await?;
        let state = bridge.binding.state.join("outbox");
        let saved_state = state.clone();
        let outbox = tokio::task::spawn_blocking(move || client::inspect(&saved_state)).await??;
        let id = Uuid::parse_str(&params.message_id)?;
        let envelope: fleet::Envelope =
            serde_json::from_value(outbox["entries"][id.to_string()]["envelope"].clone())
                .context("factory_outbox_message_unknown")?;
        ensure!(
            envelope.sender == bridge.binding.grant.seat && envelope.project == params.project,
            "factory_outbox_scope_denied"
        );
        let response = bridge
            .call(
                &params.project,
                envelope.recipient,
                if cancel {
                    fleet::Operation::Queue
                } else {
                    fleet::Operation::Observe
                },
                json!({"method":if cancel { "cancel" } else { "status" },"message_id":id}),
            )
            .await?;
        if !cancel {
            let snapshot = response.clone();
            tokio::task::spawn_blocking(move || client::record_status(&state, id, &snapshot))
                .await??;
        }
        Ok(response)
    }
    pub(crate) async fn factory_list(
        &self,
        params: FactoryListParams,
        peers: bool,
    ) -> Result<Option<ClientResponsePayload>, JSONRPCErrorError> {
        let bridge = self.factory().map_err(error)?;
        let _serial = bridge.serial.acquire().await.map_err(error)?;
        let _transaction = bridge.transaction().await.map_err(error)?;
        let data = bridge
            .call(
                &params.project,
                bridge.binding.grant.seat,
                fleet::Operation::Observe,
                json!({"method":if peers { "peers" } else { "inbox" }}),
            )
            .await
            .map_err(error)?;
        let response = FactoryResponse { data };
        Ok(Some(if peers {
            ClientResponsePayload::FactoryPeers(response)
        } else {
            ClientResponsePayload::FactoryInbox(response)
        }))
    }
    pub(crate) async fn factory_send(
        &self,
        params: FactorySendParams,
    ) -> Result<Option<ClientResponsePayload>, JSONRPCErrorError> {
        self.factory_send_inner(params)
            .await
            .map_err(error)
            .map(|data| Some(ClientResponsePayload::FactorySend(FactoryResponse { data })))
    }
    async fn factory_send_inner(&self, params: FactorySendParams) -> Result<Value> {
        let bridge = self.factory()?;
        let _serial = bridge.serial.acquire().await?;
        let _transaction = bridge.transaction().await?;
        let recipient = Uuid::parse_str(&params.recipient)?;
        let id = Uuid::parse_str(&params.message_id)?;
        ensure!(
            (1..=3600).contains(&params.expires_in_seconds),
            "factory_expiry_bound"
        );
        if let Some(reply) = &params.reply_to {
            let prior = bridge
                .call(
                    &params.project,
                    bridge.binding.grant.seat,
                    fleet::Operation::Observe,
                    json!({"method":"status","message_id":Uuid::parse_str(reply)?}),
                )
                .await?;
            ensure!(
                prior["sender"] == params.recipient
                    && prior["conversationId"] == params.conversation_id,
                "factory_reply_scope_denied"
            );
        }
        let time = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)?
            .as_secs();
        let mut envelope = fleet::Envelope {
            protocol: fleet::VERSION,
            message_id: id,
            conversation_id: Uuid::parse_str(&params.conversation_id)?,
            assignment_id: None,
            reply_to: params
                .reply_to
                .as_deref()
                .map(Uuid::parse_str)
                .transpose()?,
            sender: bridge.binding.grant.seat,
            incarnation: bridge.binding.grant.incarnation,
            recipient,
            project: params.project,
            kind: serde_json::from_value(json!(params.kind))?,
            mode: serde_json::from_value(json!(params.mode))?,
            created_at: time,
            expires_at: time + params.expires_in_seconds,
            text: params.text,
            artifact_refs: Vec::new(),
        };
        let binding = bridge.binding.clone();
        tokio::task::spawn_blocking(move || {
            let state = binding.state.join("outbox");
            let existing = client::inspect(&state)?;
            if let Some(entry) = existing["entries"].get(id.to_string()) {
                let previous: fleet::Envelope = serde_json::from_value(entry["envelope"].clone())?;
                envelope.created_at = previous.created_at;
                envelope.expires_at = previous.expires_at;
                ensure!(
                    previous.expires_at - previous.created_at == params.expires_in_seconds
                        && serde_json::to_value(&envelope)? == serde_json::to_value(&previous)?,
                    "message_conflict"
                );
            } else {
                client::enqueue(&state, envelope, &binding.grant)?;
            }
            client::relay(
                &state,
                &binding.socket,
                binding.service_uid,
                &binding.credential,
                &binding.grant,
                id,
            )
        })
        .await?
    }
    pub(crate) async fn factory_message(
        &self,
        params: ThreadFactoryMessageParams,
    ) -> Result<Option<ClientResponsePayload>, JSONRPCErrorError> {
        self.factory_message_inner(params)
            .await
            .map_err(error)
            .map(|data| {
                Some(ClientResponsePayload::ThreadFactoryMessage(
                    FactoryResponse { data },
                ))
            })
    }
    async fn factory_message_inner(&self, params: ThreadFactoryMessageParams) -> Result<Value> {
        let bridge = self.factory()?;
        let _serial = bridge.serial.acquire().await?;
        let transaction = bridge.transaction().await?;
        let (thread_id, thread) = self
            .load_thread(&params.thread_id)
            .await
            .map_err(|_| anyhow::anyhow!("factory_thread_unknown"))?;
        let id = Uuid::parse_str(&params.message_id)?;
        let key = id.to_string();
        let mut ledger = transaction.read()?;
        ensure!(
            (ledger["storageVersion"] == 1 || ledger["storageVersion"] == 2)
                && ledger["messages"].is_object(),
            "factory_storage_unsupported"
        );
        if ledger["storageVersion"] == 1 {
            ledger["storageVersion"] = json!(2);
            transaction.save(&ledger)?;
        }
        // Authenticate even when returning a previously staged result.
        bridge
            .call(
                &params.project,
                bridge.binding.grant.seat,
                fleet::Operation::Observe,
                json!({"method":"sync"}),
            )
            .await?;
        if ledger["messages"].get(&key).is_none() {
            ensure!(
                ledger["messages"]
                    .as_object()
                    .context("factory_storage_invalid")?
                    .len()
                    < 64,
                "factory_queue_full"
            );
            let received = bridge
                .call(
                    &params.project,
                    bridge.binding.grant.seat,
                    fleet::Operation::Observe,
                    json!({"method":"receive","message_id":id}),
                )
                .await?;
            let envelope: fleet::Envelope = serde_json::from_value(received["envelope"].clone())?;
            // Reject a wrong workspace before binding a never-submitted message to this thread.
            bridge.authorize_managed(thread.as_ref(), &envelope).await?;
            ledger["messages"][&key] = json!({"threadId":params.thread_id,"state":"staged","envelope":received["envelope"],"turnId":null});
            transaction.save(&ledger)?;
        }
        let entry = &ledger["messages"][&key];
        ensure!(
            entry["threadId"] == params.thread_id,
            "factory_message_thread_conflict"
        );
        let envelope: fleet::Envelope = serde_json::from_value(entry["envelope"].clone())?;
        ensure!(
            envelope.recipient == bridge.binding.grant.seat && envelope.project == params.project,
            "factory_inbox_scope_denied"
        );
        let status = bridge
            .call(
                &params.project,
                bridge.binding.grant.seat,
                fleet::Operation::Observe,
                json!({"method":"status","message_id":id}),
            )
            .await?;
        if !matches!(
            entry["state"].as_str(),
            Some("staged" | "deferred" | "queue_only")
        ) {
            return reconcile::existing(
                bridge,
                &transaction,
                &mut ledger,
                &key,
                reconcile::HistorySource {
                    thread: thread.as_ref(),
                    store: self.thread_store.as_ref(),
                    thread_id,
                },
                &envelope,
                status,
            )
            .await;
        }
        if status["cancellation"]["state"] == "requested"
            || status["cancellation"]["state"] == "uncertain"
        {
            ledger["messages"][&key]["state"] = json!("cancelled_before_start");
            transaction.save(&ledger)?;
            let result =
                reconcile::cancel_result(bridge, &envelope, "applied", None, "not_submitted")
                    .await?;
            ledger["messages"][&key]["cancellation"] = result["cancellation"].clone();
            transaction.save(&ledger)?;
            return Ok(ledger["messages"][&key].clone());
        }
        if envelope.mode == fleet::DeliveryMode::QueueOnly {
            ledger["messages"][&key]["state"] = json!("queue_only");
            transaction.save(&ledger)?;
            return Ok(ledger["messages"][&key].clone());
        }
        // Revalidate pending input at delivery, including sender revocation and expiry.
        let fresh = bridge
            .call(
                &params.project,
                bridge.binding.grant.seat,
                fleet::Operation::Observe,
                json!({"method":"receive","message_id":id}),
            )
            .await?;
        ensure!(
            fresh["envelope"] == serde_json::to_value(&envelope)?,
            "factory_envelope_conflict"
        );
        bridge.authorize_managed(thread.as_ref(), &envelope).await?;
        let author = AgentPath::coworker(envelope.sender, envelope.incarnation);
        let recipient =
            AgentPath::coworker(bridge.binding.grant.seat, bridge.binding.grant.incarnation);
        let mut communication = InterAgentCommunication::new(
            author.clone(),
            recipient,
            Vec::new(),
            envelope.text,
            true,
        );
        communication.id = Some(ResponseItemId::with_suffix("amsg", id));
        let request = TurnInputRequest::new(TurnInput::InterAgentCommunication(communication))
            .on_start(TurnStartOptions {
                turn_trigger: Some("factory_message".into()),
                initiating_agent_path: Some(author),
                ..Default::default()
            });
        // A crash from this point is uncertain, never permission to blindly inject again.
        ledger["messages"][&key]["state"] = json!("intent");
        transaction.save(&ledger)?;
        reconcile::fault(&transaction, "after_intent").await?;
        match thread.start_turn_if_idle(request).await? {
            StartIfIdleSubmission::Started { turn_id, .. } => {
                reconcile::fault(&transaction, "after_submit").await?;
                ledger["messages"][&key]["state"] = json!("started");
                ledger["messages"][&key]["turnId"] = json!(turn_id);
            }
            StartIfIdleSubmission::NotSubmitted { reason } => {
                ledger["messages"][&key]["state"] = json!("deferred");
                ledger["messages"][&key]["reason"] = json!(format!("{reason:?}"));
            }
        }
        transaction.save(&ledger)?;
        Ok(ledger["messages"][&key].clone())
    }
}
