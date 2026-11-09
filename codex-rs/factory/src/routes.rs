//! Temporary conversation routes are operator authority, independent of model credentials.
use super::*;

#[derive(Clone, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct GrantRequest {
    id: Uuid,
    sender: Uuid,
    recipient: Uuid,
    project: String,
    conversation_id: Option<Uuid>,
    ttl_seconds: u64,
    reason: String,
}

#[derive(Deserialize, Serialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub(super) struct Route {
    request: GrantRequest,
    sender_incarnation: Uuid,
    recipient_incarnation: Uuid,
    created_at: u64,
    expires_at: u64,
    ended_reason: Option<String>,
}

fn reason(value: &str) -> Result<()> {
    ensure!(
        !value.trim().is_empty() && value.len() <= 512 && !value.chars().any(char::is_control),
        "route_reason_invalid"
    );
    Ok(())
}

fn active(registry: &Registry, route: &Route, time: u64) -> bool {
    route.ended_reason.is_none()
        && route.expires_at > time
        && registry
            .seats
            .get(&route.request.sender)
            .is_some_and(|s| !s.revoked && s.grant.incarnation == route.sender_incarnation)
        && registry
            .seats
            .get(&route.request.recipient)
            .is_some_and(|s| !s.revoked && s.grant.incarnation == route.recipient_incarnation)
}

pub(super) fn contact(
    registry: &Registry,
    sender: &protocol::Grant,
    recipient: Uuid,
    project: &str,
    time: u64,
) -> bool {
    sender.recipients.contains(&recipient)
        || registry.routes.values().any(|route| {
            route.request.sender == sender.seat
                && route.request.recipient == recipient
                && route.request.project == project
                && active(registry, route, time)
        })
}

pub(super) fn permits(
    registry: &Registry,
    request: &protocol::Request,
    envelope: Option<&protocol::Envelope>,
    time: u64,
) -> bool {
    registry.routes.values().any(|route| {
        let scope = &route.request;
        scope.sender == request.sender
            && scope.recipient == request.recipient
            && scope.project == request.project
            && envelope.is_none_or(|e| {
                scope
                    .conversation_id
                    .is_none_or(|id| id == e.conversation_id)
            })
            && active(registry, route, time)
    })
}

pub(super) fn mail_permitted(registry: &Registry, mail: &Mail, time: u64) -> bool {
    let Some(sender) = registry.seats.get(&mail.envelope.sender) else {
        return false;
    };
    if sender.grant.recipients.contains(&mail.envelope.recipient) {
        return true;
    }
    permits(
        registry,
        &protocol::Request {
            protocol: protocol::VERSION,
            sender: mail.envelope.sender,
            host: sender.grant.host,
            incarnation: mail.envelope.incarnation,
            sequence: 1,
            project: mail.envelope.project.clone(),
            recipient: mail.envelope.recipient,
            operation: protocol::Operation::Queue,
        },
        Some(&mail.envelope),
        time,
    )
}

fn quarantine(registry: &mut Registry, time: u64) -> (usize, usize) {
    let invalid: Vec<_> = registry
        .mail
        .iter()
        .filter(|(_, mail)| !mail_permitted(registry, mail, time))
        .map(|(id, _)| *id)
        .collect();
    let mut pending = 0;
    let mut delivered = 0;
    for id in invalid {
        if let Some(mail) = registry.mail.get_mut(&id) {
            if mail.state == "service_accepted" {
                mail.state = "quarantined".into();
                pending += 1;
            } else if matches!(mail.state.as_str(), "delivered" | "consumed")
                && !mail.reconcile_required
            {
                mail.reconcile_required = true;
                delivered += 1;
            }
        }
    }
    (pending, delivered)
}

pub(super) fn expire(registry: &mut Registry, time: u64) -> bool {
    let ended: Vec<_> = registry
        .routes
        .iter_mut()
        .filter(|(_, r)| r.ended_reason.is_none() && r.expires_at <= time)
        .map(|(id, r)| {
            r.ended_reason = Some("expired".into());
            *id
        })
        .collect();
    if ended.is_empty() {
        return false;
    }
    let (pending, delivered) = quarantine(registry, time);
    audit(
        registry,
        json!({"kind":"routes_expired","at":time,"routes":ended,
            "pendingQuarantined":pending,"deliveredRequiresReconciliation":delivered}),
    );
    true
}

/// Only the service OS owner can acquire the private registry lock.
pub fn grant(state: &Path, request: GrantRequest) -> Result<Value> {
    reason(&request.reason)?;
    ensure!(
        !request.id.is_nil()
            && request.sender != request.recipient
            && request.conversation_id.is_none_or(|id| !id.is_nil())
            && (1..=3600).contains(&request.ttl_seconds),
        "route_scope_invalid"
    );
    let _lock = lock(state)?;
    lifecycle::check_boot(state)?;
    let mut registry = load(state)?;
    let time = now()?.max(registry.observed_time);
    if let Some(existing) = registry.routes.get(&request.id) {
        ensure!(
            serde_json::to_value(&existing.request)? == serde_json::to_value(&request)?,
            "route_id_conflict"
        );
        return Ok(json!({"ok":true,"route":existing}));
    }
    ensure!(registry.routes.len() < 256, "route_registry_full");
    let sender = registry
        .seats
        .get(&request.sender)
        .context("route_sender_unknown")?;
    let recipient = registry
        .seats
        .get(&request.recipient)
        .context("route_recipient_unknown")?;
    ensure!(
        !sender.revoked
            && !recipient.revoked
            && sender.grant.projects.contains(&request.project)
            && recipient.grant.projects.contains(&request.project)
            && sender
                .grant
                .operations
                .contains(&protocol::Operation::Queue)
            && sender.grant.role.permits(protocol::Operation::Queue),
        "route_scope_denied"
    );
    ensure!(
        !sender.grant.recipients.contains(&request.recipient),
        "route_already_permanent"
    );
    let id = request.id;
    let route = Route {
        sender_incarnation: sender.grant.incarnation,
        recipient_incarnation: recipient.grant.incarnation,
        expires_at: time + request.ttl_seconds,
        created_at: time,
        ended_reason: None,
        request,
    };
    let response = json!({"ok":true,"route":route});
    registry.routes.insert(id, route);
    registry.observed_time = time;
    audit(
        &mut registry,
        json!({"kind":"route_granted","id":id,"at":time}),
    );
    save(state, &registry)?;
    Ok(response)
}

pub fn revoke(state: &Path, id: Uuid, explanation: &str) -> Result<Value> {
    reason(explanation)?;
    let _lock = lock(state)?;
    lifecycle::check_boot(state)?;
    let mut registry = load(state)?;
    let time = now()?.max(registry.observed_time);
    let route = registry.routes.get_mut(&id).context("route_unknown")?;
    if route.ended_reason.is_some() {
        return Ok(json!({"ok":true,"route":route,"alreadyEnded":true}));
    }
    route.ended_reason = Some(explanation.to_owned());
    let (pending, delivered) = quarantine(&mut registry, time);
    registry.observed_time = time;
    audit(
        &mut registry,
        json!({"kind":"route_revoked","id":id,"at":time,
        "pendingQuarantined":pending,"deliveredRequiresReconciliation":delivered}),
    );
    save(state, &registry)?;
    Ok(
        json!({"ok":true,"route":registry.routes.get(&id),"pendingQuarantined":pending,
        "deliveredRequiresReconciliation":delivered}),
    )
}
