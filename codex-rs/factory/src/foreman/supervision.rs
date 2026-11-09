//! Explicit supervision ticks. Heartbeats never constitute verified task progress.
use super::*;

#[derive(Clone, Copy, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum Reason {
    Blocked,
    RateLimit,
    ApprovalRequired,
}
#[derive(Clone, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct Report {
    pub incident_id: Uuid,
    pub reason: Reason,
    pub summary: String,
}
#[derive(Clone, Copy, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum Choice {
    Wait,
    RequestHuman,
    ReviewResult,
    RecommendReconciliation,
}
#[derive(Clone, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct Decision {
    pub choice: Choice,
    pub explanation: String,
}
#[derive(Deserialize, Serialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct Notice {
    run: Uuid,
    generation: Uuid,
    fingerprint: String,
    facts: Value,
    envelope: Option<protocol::Envelope>,
    state: String,
    decision: Option<Decision>,
}
#[derive(Deserialize, Serialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct Tick {
    run: Uuid,
    request_digest: String,
    notice: Option<Uuid>,
    response: Value,
}
#[derive(Deserialize, Serialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct Progress {
    event: Option<Uuid>,
    since_tick: u64,
}
#[derive(Default, Deserialize, Serialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub(super) struct Journal {
    reports: BTreeMap<Uuid, Report>,
    notices: BTreeMap<Uuid, Notice>,
    ticks: BTreeMap<Uuid, Tick>,
    last_notice: BTreeMap<Uuid, Uuid>,
    progress: BTreeMap<Uuid, Progress>,
}
impl Journal {
    pub(super) fn validate(&self) -> Result<()> {
        ensure!(
            self.reports.len() <= 128
                && self.notices.len() <= 128
                && self.ticks.len() <= 128
                && self.last_notice.len() <= 128
                && self.progress.len() <= 128,
            "supervision_journal_capacity"
        );
        Ok(())
    }
}
#[derive(Deserialize)]
#[serde(tag = "action", rename_all = "snake_case", deny_unknown_fields)]
pub enum Action {
    Tick {
        run_id: Uuid,
        tick_id: Uuid,
        stall_seconds: u64,
        foreman_pid: Option<u32>,
    },
    Status {
        run_id: Uuid,
    },
}

/// The caller holds the bridge lock. This function takes only the dispatch lock.
pub(crate) fn report(
    state: &Path,
    grant: &protocol::Grant,
    run: Uuid,
    report: Report,
) -> Result<Value> {
    ensure!(
        !report.incident_id.is_nil() && !report.summary.is_empty() && report.summary.len() <= 512,
        "supervision_report_invalid"
    );
    let _lock = service::lock(state)?;
    let mut store = load(state, None)?;
    let assignment = store
        .assignments
        .get(&run)
        .context("foreman_assignment_missing")?;
    ensure!(
        assignment.worker.seat == grant.seat && assignment.worker.incarnation == grant.incarnation,
        "supervision_worker_denied"
    );
    ensure!(
        assignment.disposition == "issued"
            && assignment
                .lease_tick
                .is_some_and(|end| end > tick().unwrap_or(u64::MAX)),
        "supervision_generation_inactive"
    );
    if let Some(prior) = store.supervision.reports.get(&run) {
        if prior.incident_id == report.incident_id {
            ensure!(*prior == report, "supervision_incident_conflict");
        } else {
            ensure!(
                prior.reason == Reason::Blocked,
                "supervision_human_hold_latched"
            );
            ensure!(
                store
                    .supervision
                    .last_notice
                    .get(&run)
                    .and_then(|id| store.supervision.notices.get(id))
                    .is_some_and(|notice| notice.decision.is_some()),
                "supervision_decision_pending"
            );
        }
    }
    store.supervision.reports.insert(run, report.clone());
    save(state, &store)?;
    Ok(
        json!({"ok":true,"runId":run,"incidentId":report.incident_id,"reason":report.reason,
        "authority":"Authenticated worker report. This is not verified progress."}),
    )
}

/// A decision records advice. It cannot resume execution or complete a canonical gate.
pub(crate) fn decide(
    state: &Path,
    grant: &protocol::Grant,
    run: Uuid,
    notice_id: Uuid,
    decision: Decision,
) -> Result<Value> {
    ensure!(
        !decision.explanation.is_empty() && decision.explanation.len() <= 512,
        "supervision_decision_invalid"
    );
    let _lock = service::lock(state)?;
    let mut store = load(state, None)?;
    let assignment = store
        .assignments
        .get(&run)
        .context("foreman_assignment_missing")?;
    let path = store
        .config_path
        .as_ref()
        .context("foreman_config_missing")?;
    let bytes = service::private_read(path, 16384)?;
    let config: Config = serde_json::from_slice(&bytes)?;
    let (forge, _) = recording::connection(&config.forge_config)?;
    ensure!(
        configuration_digest(&bytes, &forge)? == store.config_digest,
        "foreman_config_changed"
    );
    ensure!(
        grant.role == protocol::Role::Foreman
            && grant.seat == config.foreman
            && grant.projects.contains(&forge.local_project),
        "supervision_foreman_denied"
    );
    ensure!(
        store.supervision.last_notice.get(&run) == Some(&notice_id),
        "supervision_notice_superseded"
    );
    let notice = store
        .supervision
        .notices
        .get_mut(&notice_id)
        .context("supervision_notice_missing")?;
    ensure!(
        notice.run == run && notice.generation == assignment.generation,
        "supervision_generation_mismatch"
    );
    if notice.facts["stopRequired"] == true {
        ensure!(
            decision.choice == Choice::RequestHuman,
            "supervision_human_hold_latched"
        );
    }
    if let Some(prior) = &notice.decision {
        ensure!(*prior == decision, "supervision_decision_conflict");
    } else {
        notice.decision = Some(decision.clone());
        notice.state = "decided".into();
    }
    save(state, &store)?;
    Ok(
        json!({"ok":true,"runId":run,"noticeId":notice_id,"decision":decision,
        "executionResumed":false,"ticketCompleted":false}),
    )
}

fn notice_response(id: Uuid, notice: &Notice, state: &str) -> Value {
    json!({"ok":true,"runId":notice.run,"noticeId":id,"state":state,
        "humanEscalation":notice.state == "human_escalated"
            || notice.decision.as_ref().is_some_and(|d| d.choice == Choice::RequestHuman),
        "stopRequired":notice.facts["stopRequired"],"facts":notice.facts,
        "messageId":notice.envelope.as_ref().map(|e|e.message_id),
        "decision":notice.decision,"taskProductive":false,"executionResumed":false})
}

fn deliver(
    state: &Path,
    tick_id: Uuid,
    notice_id: Uuid,
    envelope: protocol::Envelope,
) -> Result<Value> {
    // No dispatch lock may be held while operator_send takes the bridge lock.
    let bridge = state.parent().context("foreman_state_binding_invalid")?;
    let delivered = service::operator_send(bridge, envelope).is_ok();
    service::fixture_fault(state, "supervision_after_delivery")?;
    let _lock = service::lock(state)?;
    let mut store = load(state, None)?;
    let notice = store
        .supervision
        .notices
        .get_mut(&notice_id)
        .context("supervision_notice_missing")?;
    if notice.state != "decided" {
        notice.state = if delivered {
            "queued"
        } else {
            "human_escalated"
        }
        .into();
    }
    let response = notice_response(notice_id, notice, &notice.state);
    store
        .supervision
        .ticks
        .get_mut(&tick_id)
        .context("supervision_tick_missing")?
        .response = response.clone();
    save(state, &store)?;
    Ok(response)
}

/// Run one explicit tick. This API installs no schedule and submits no model request.
pub async fn apply(state: &Path, config_path: &Path, action: Action) -> Result<Value> {
    let bytes = service::private_read(config_path, 16384)?;
    let config: Config = serde_json::from_slice(&bytes)?;
    ensure!(
        state == config.bridge_state.join("foreman"),
        "foreman_state_binding_invalid"
    );
    let (forge, client) = recording::connection(&config.forge_config)?;
    let config_digest = configuration_digest(&bytes, &forge)?;
    let sender = service::operator_grant(&config.bridge_state, config.foreman).ok();
    let local_host = service::operator_host(&config.bridge_state)?;
    let lock = service::lock(state)?;
    let mut store = load(state, Some(&config_digest))?;
    match action {
        Action::Status { run_id } => {
            ensure!(
                store.assignments.contains_key(&run_id),
                "foreman_assignment_missing"
            );
            Ok(
                json!({"ok":true,"runId":run_id,"report":store.supervision.reports.get(&run_id),
                "notices":store.supervision.notices.iter().filter(|(_,n)| n.run == run_id)
                    .map(|(id,n)| json!({"noticeId":id,"record":n})).collect::<Vec<_>>(),
                "ticks":store.supervision.ticks.iter().filter(|(_,t)|t.run == run_id)
                    .map(|(id,t)| json!({"tickId":id,"response":t.response})).collect::<Vec<_>>(),
                "taskProductive":false}),
            )
        }
        Action::Tick {
            run_id,
            tick_id,
            stall_seconds,
            foreman_pid,
        } => {
            ensure!(
                !tick_id.is_nil() && (1..=3600).contains(&stall_seconds),
                "supervision_tick_invalid"
            );
            let request_digest = digest(&serde_json::to_vec(&json!([
                run_id,
                stall_seconds,
                foreman_pid
            ]))?);
            if let Some(prior) = store.supervision.ticks.get(&tick_id) {
                ensure!(
                    prior.run == run_id && prior.request_digest == request_digest,
                    "supervision_tick_conflict"
                );
                if prior.response["state"] == "delivery_pending" {
                    let id = prior.notice.context("supervision_notice_missing")?;
                    ensure!(
                        store.supervision.last_notice.get(&run_id) == Some(&id),
                        "supervision_notice_superseded"
                    );
                    let envelope = store
                        .supervision
                        .notices
                        .get(&id)
                        .and_then(|n| n.envelope.clone())
                        .context("supervision_envelope_missing")?;
                    drop(lock);
                    return deliver(state, tick_id, id, envelope);
                }
                return Ok(prior.response.clone());
            }
            ensure!(
                store.supervision.ticks.len() < 128,
                "supervision_tick_capacity"
            );
            let assignment = store
                .assignments
                .get(&run_id)
                .context("foreman_assignment_missing")?;
            ensure!(
                ["issued", "spec_pending", "quarantined", "fenced"]
                    .contains(&assignment.disposition.as_str()),
                "supervision_generation_inactive"
            );
            let at = tick()?;
            let progress_event = assignment.result.as_ref().map(|r| r.event_id);
            let progress = store
                .supervision
                .progress
                .entry(run_id)
                .or_insert(Progress {
                    event: progress_event,
                    since_tick: at,
                });
            if progress.event != progress_event {
                *progress = Progress {
                    event: progress_event,
                    since_tick: at,
                };
            }
            let stalled =
                progress_event.is_none() && at.saturating_sub(progress.since_tick) >= stall_seconds;
            let context = canonical(&forge, &client, assignment.ticket).await;
            let canonical_available = context.is_ok();
            let context = context.unwrap_or(Value::Null);
            let canonical_owned = context["run"]["id"] == run_id.to_string()
                && context["run"]["owner_id"] == assignment.generation.to_string();
            let process_live = assignment
                .execution
                .as_ref()
                .is_some_and(|p| p.live().unwrap_or(false));
            let foreman_live = sender
                .as_ref()
                .filter(|s| s.role == protocol::Role::Foreman && s.host == local_host)
                .zip(foreman_pid)
                .is_some_and(|(s, p)| Execution::observe(p, s).is_ok());
            let report = store.supervision.reports.get(&run_id);
            let stop_required = report.is_some_and(|r| r.reason != Reason::Blocked);
            let mut reasons = Vec::new();
            if !canonical_available {
                reasons.push("canonical_unavailable");
            } else if !canonical_owned {
                reasons.push("canonical_owner_changed");
            } else if context["run"]["status"] == "held" {
                reasons.push("canonical_held");
            }
            if report.is_some() {
                reasons.push("worker_report");
            }
            if !process_live {
                reasons.push("worker_process_lost");
            }
            if assignment.lease_tick.is_none_or(|end| end <= at) {
                reasons.push("lease_expired");
            }
            if stalled {
                reasons.push("no_verified_progress");
            }
            if progress_event.is_some() {
                reasons.push("result_requires_review");
            }
            let facts = json!({"runId":run_id,"generation":assignment.generation,
                "canonicalAvailable":canonical_available,"canonicalOwned":canonical_owned,
                "canonicalRunStatus":context["run"]["status"],"canonicalTicketStatus":context["ticket"]["status"],
                "processLive":process_live,"foremanAvailable":foreman_live,"workerReport":report,
                "verifiedProgressEvent":progress_event,"stalled":stalled,"stopRequired":stop_required,
                "reasons":reasons});
            let fingerprint = digest(&serde_json::to_vec(&facts)?);
            let prior = store.supervision.last_notice.get(&run_id).copied();
            let unchanged = prior
                .and_then(|id| store.supervision.notices.get(&id))
                .is_some_and(|n| n.fingerprint == fingerprint);
            let (notice_id, response) = if reasons.is_empty() {
                (
                    None,
                    json!({"ok":true,"runId":run_id,"state":"no_decision_required","facts":facts,"taskProductive":false}),
                )
            } else if unchanged {
                let id = prior.context("supervision_notice_missing")?;
                let notice = store
                    .supervision
                    .notices
                    .get(&id)
                    .context("supervision_notice_missing")?;
                (Some(id), notice_response(id, notice, "unchanged"))
            } else {
                ensure!(
                    store.supervision.notices.len() < 128,
                    "supervision_notice_capacity"
                );
                let id = Uuid::new_v4();
                let human =
                    !foreman_live || stop_required || !canonical_available || !canonical_owned;
                let envelope = if human {
                    None
                } else {
                    let sender = sender.as_ref().context("foreman_sender_missing")?;
                    Some(message(
                        sender,
                        sender,
                        &forge.local_project,
                        run_id,
                        format!(
                            "Do: Review the changed facts. Record one decision for notice {id}.\nRun: {run_id}\nGeneration: {}\nFacts: {}\nA decision does not grant execution. Do not bypass a canonical gate.",
                            assignment.generation, facts
                        ),
                        protocol::MessageKind::Clarification,
                    )?)
                };
                let notice = Notice {
                    run: run_id,
                    generation: assignment.generation,
                    fingerprint,
                    facts,
                    envelope,
                    state: if human {
                        "human_escalated"
                    } else {
                        "delivery_pending"
                    }
                    .into(),
                    decision: None,
                };
                let response = notice_response(id, &notice, &notice.state);
                store.supervision.notices.insert(id, notice);
                store.supervision.last_notice.insert(run_id, id);
                (Some(id), response)
            };
            store.supervision.ticks.insert(
                tick_id,
                Tick {
                    run: run_id,
                    request_digest,
                    notice: notice_id,
                    response: response.clone(),
                },
            );
            store.supervision.validate()?;
            save(state, &store)?;
            if response["state"] == "delivery_pending" {
                let id = notice_id.context("supervision_notice_missing")?;
                let envelope = store
                    .supervision
                    .notices
                    .get(&id)
                    .and_then(|n| n.envelope.clone())
                    .context("supervision_envelope_missing")?;
                drop(lock);
                deliver(state, tick_id, id, envelope)
            } else {
                Ok(response)
            }
        }
    }
}
