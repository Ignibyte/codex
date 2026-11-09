use anyhow::Result;
use anyhow::ensure;
use serde::Deserialize;
use serde::Serialize;
use std::collections::BTreeSet;
use uuid::Uuid;

pub const VERSION: u32 = 1;
pub const MAX_FRAME: usize = 65536;

#[derive(Clone, Copy, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum Role {
    Observer,
    Controller,
    Manager,
    Foreman,
    Worker,
    Ops,
    Support,
}

#[derive(Clone, Copy, Debug, Deserialize, Serialize, PartialEq, Eq, PartialOrd, Ord)]
#[serde(rename_all = "snake_case")]
pub enum Operation {
    Observe,
    Queue,
    Acknowledge,
    Interrupt,
    Approve,
    Execute,
}

impl Role {
    pub fn permits(self, operation: Operation) -> bool {
        match operation {
            Operation::Observe => true,
            Operation::Queue | Operation::Acknowledge => matches!(
                self,
                Self::Controller | Self::Manager | Self::Foreman | Self::Worker | Self::Support
            ),
            Operation::Interrupt => {
                matches!(self, Self::Controller | Self::Manager | Self::Foreman)
            }
            Operation::Approve => self == Self::Controller,
            Operation::Execute => false,
        }
    }
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct Grant {
    pub seat: Uuid,
    pub host: Uuid,
    pub incarnation: Uuid,
    pub agent_uid: u32,
    pub role: Role,
    pub projects: BTreeSet<String>,
    pub recipients: BTreeSet<Uuid>,
    pub operations: BTreeSet<Operation>,
}

fn project_id(value: &str) -> bool {
    !value.is_empty()
        && value.len() <= 64
        && value
            .bytes()
            .all(|c| c.is_ascii_alphanumeric() || c == b'-' || c == b'_')
}

impl Grant {
    pub fn validate(&self) -> Result<()> {
        ensure!(
            !self.seat.is_nil() && !self.host.is_nil() && !self.incarnation.is_nil(),
            "identity_nil"
        );
        ensure!(
            !self.projects.is_empty() && self.projects.len() <= 32,
            "project_grant_bound"
        );
        ensure!(
            self.projects.iter().all(|p| project_id(p)),
            "project_id_invalid"
        );
        ensure!(
            !self.recipients.is_empty() && self.recipients.len() <= 64,
            "recipient_grant_bound"
        );
        ensure!(self.recipients.iter().all(|r| !r.is_nil()), "recipient_nil");
        ensure!(
            self.operations.iter().all(|op| self.role.permits(*op)),
            "role_capability_denied"
        );
        Ok(())
    }
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct Request {
    pub protocol: u32,
    pub sender: Uuid,
    pub host: Uuid,
    pub incarnation: Uuid,
    pub sequence: u64,
    pub project: String,
    pub recipient: Uuid,
    pub operation: Operation,
}

/// The caller supplies authenticated grant/peer facts, never request-derived identity.
pub fn authorize(
    grant: &Grant,
    peer_uid: u32,
    last_sequence: u64,
    request: &Request,
) -> Result<()> {
    grant.validate()?;
    ensure!(request.protocol == VERSION, "protocol_unsupported");
    ensure!(peer_uid == grant.agent_uid, "peer_uid_denied");
    ensure!(request.sender == grant.seat, "sender_mismatch");
    ensure!(request.host == grant.host, "host_mismatch");
    ensure!(
        request.incarnation == grant.incarnation,
        "incarnation_stale"
    );
    ensure!(
        last_sequence.checked_add(1) == Some(request.sequence),
        "sequence_replay_or_gap"
    );
    ensure!(grant.projects.contains(&request.project), "project_denied");
    ensure!(
        grant.recipients.contains(&request.recipient),
        "recipient_denied"
    );
    ensure!(
        grant.role.permits(request.operation) && grant.operations.contains(&request.operation),
        "operation_denied"
    );
    Ok(())
}

#[derive(Clone, Copy, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum MessageKind {
    Note,
    Assignment,
    Clarification,
    Reply,
    Result,
    CancellationRequest,
}

#[derive(Clone, Copy, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum DeliveryMode {
    QueueOnly,
    WakeWhenIdle,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct Envelope {
    pub protocol: u32,
    pub message_id: Uuid,
    pub conversation_id: Uuid,
    pub assignment_id: Option<Uuid>,
    pub reply_to: Option<Uuid>,
    pub sender: Uuid,
    pub incarnation: Uuid,
    pub recipient: Uuid,
    pub project: String,
    pub kind: MessageKind,
    pub mode: DeliveryMode,
    pub created_at: u64,
    pub expires_at: u64,
    pub text: String,
    pub artifact_refs: Vec<String>,
}

impl Envelope {
    pub fn validate(&self, request: &Request, now: u64) -> Result<()> {
        ensure!(self.protocol == VERSION, "protocol_unsupported");
        ensure!(
            request.operation == Operation::Queue,
            "envelope_operation_denied"
        );
        ensure!(
            !self.message_id.is_nil() && !self.conversation_id.is_nil(),
            "message_identity_nil"
        );
        ensure!(
            self.assignment_id.is_none_or(|id| !id.is_nil())
                && self.reply_to.is_none_or(|id| !id.is_nil()),
            "message_reference_nil"
        );
        ensure!(
            self.sender == request.sender && self.incarnation == request.incarnation,
            "envelope_sender_mismatch"
        );
        ensure!(
            self.recipient == request.recipient && self.project == request.project,
            "envelope_scope_mismatch"
        );
        ensure!(
            self.created_at <= now.saturating_add(60)
                && self.expires_at > now
                && self.expires_at > self.created_at
                && self.expires_at - self.created_at <= 3600,
            "message_expired_or_invalid_time"
        );
        ensure!(
            !self.text.is_empty() && self.text.len() <= 16384,
            "payload_bound"
        );
        ensure!(
            self.artifact_refs.len() <= 8
                && self.artifact_refs.iter().all(|r| !r.is_empty()
                    && r.len() <= 256
                    && r.bytes().all(|b| b.is_ascii_graphic())),
            "artifact_reference_bound"
        );
        Ok(())
    }
}

#[derive(Clone, Copy, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum Authority {
    Outbox,
    Service,
    Inbox,
    Runtime,
    Worker,
}

#[derive(Clone, Copy, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum DeliveryState {
    LocalAccepted,
    ServiceAccepted,
    Delivered,
    Consumed,
    Completed,
}

impl DeliveryState {
    pub fn authority(self) -> Authority {
        match self {
            Self::LocalAccepted => Authority::Outbox,
            Self::ServiceAccepted => Authority::Service,
            Self::Delivered => Authority::Inbox,
            Self::Consumed => Authority::Runtime,
            Self::Completed => Authority::Worker,
        }
    }
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct Receipt {
    pub protocol: u32,
    pub message_id: Uuid,
    pub state: DeliveryState,
    pub writer: Authority,
    pub turn_id: Option<String>,
}

impl Receipt {
    pub fn validate(&self) -> Result<()> {
        ensure!(self.protocol == VERSION, "protocol_unsupported");
        ensure!(!self.message_id.is_nil(), "message_identity_nil");
        ensure!(
            self.writer == self.state.authority(),
            "receipt_writer_denied"
        );
        if matches!(
            self.state,
            DeliveryState::Consumed | DeliveryState::Completed
        ) {
            ensure!(
                self.turn_id
                    .as_ref()
                    .is_some_and(|s| !s.is_empty() && s.len() <= 128),
                "receipt_turn_required"
            );
        }
        Ok(())
    }
}
