//! Durable proof for native consumption and cancellation. Missing evidence never
//! authorizes reinjection or claims completion.
use super::*;
use codex_core::CodexThread;
use codex_history::RolloutItem;
use codex_protocol::models::AgentMessageInputContent;
use codex_protocol::models::ResponseItem;
use codex_protocol::protocol::EventMsg;
use codex_protocol::protocol::Op;
use std::collections::BTreeMap;

pub(super) async fn fault(transaction: &client::LocalTransaction, point: &str) -> Result<()> {
    #[cfg(feature = "factory-fixture-faults")]
    {
        use std::io::Write;
        use std::os::unix::fs::OpenOptionsExt;
        let state = transaction.fixture_state();
        let arm = state.join(format!("fault.{point}"));
        if arm.try_exists()? {
            let info = std::fs::symlink_metadata(&arm)?;
            ensure!(
                info.is_file() && info.len() == 0 && info.mode() & 0o077 == 0,
                "fixture_fault_permissions"
            );
            let mut hit = OpenOptions::new()
                .write(true)
                .create_new(true)
                .mode(0o600)
                .open(state.join(format!("hit.{point}")))?;
            hit.write_all(b"")?;
            hit.sync_all()?;
            for _ in 0..3000 {
                if !arm.try_exists()? {
                    return Ok(());
                }
                tokio::time::sleep(std::time::Duration::from_millis(10)).await;
            }
            anyhow::bail!("fixture_fault_timeout");
        }
    }
    let _ = (transaction, point);
    Ok(())
}

pub(super) async fn cancel_result(
    bridge: &FactoryBridge,
    envelope: &fleet::Envelope,
    outcome: &str,
    turn: Option<&str>,
    reason: &str,
) -> Result<Value> {
    bridge.call(&envelope.project, bridge.binding.grant.seat, fleet::Operation::Acknowledge,
        json!({"method":"cancel_result","message_id":envelope.message_id,"state":outcome,"turn_id":turn,"reason":reason})).await
}

async fn uncertain(
    bridge: &FactoryBridge,
    transaction: &client::LocalTransaction,
    ledger: &mut Value,
    key: &str,
    envelope: &fleet::Envelope,
    status: &Value,
    reason: &str,
) -> Result<Value> {
    ledger["messages"][key]["state"] = json!("uncertain");
    ledger["messages"][key]["reconcileReason"] = json!(reason);
    transaction.save(ledger)?;
    if matches!(
        status["cancellation"]["state"].as_str(),
        Some("requested" | "uncertain")
    ) {
        let turn = ledger["messages"][key]["turnId"].as_str();
        let result = cancel_result(bridge, envelope, "uncertain", turn, reason).await?;
        ledger["messages"][key]["cancellation"] = result["cancellation"].clone();
        transaction.save(ledger)?;
    }
    Ok(ledger["messages"][key].clone())
}

/// History metadata is authoritative; admission alone is insufficient.
pub(super) struct HistorySource<'a> {
    pub thread: &'a CodexThread,
    pub store: &'a dyn codex_thread_store::ThreadStore,
    pub thread_id: codex_protocol::ThreadId,
}

pub(super) async fn existing(
    bridge: &FactoryBridge,
    transaction: &client::LocalTransaction,
    ledger: &mut Value,
    key: &str,
    source: HistorySource<'_>,
    envelope: &fleet::Envelope,
    status: Value,
) -> Result<Value> {
    let thread = source.thread;
    if ledger["messages"][key]["state"] == "cancelled_before_start" {
        let result = cancel_result(bridge, envelope, "applied", None, "not_submitted").await?;
        ledger["messages"][key]["cancellation"] = result["cancellation"].clone();
        transaction.save(ledger)?;
        return Ok(ledger["messages"][key].clone());
    }
    if thread.flush_rollout().await.is_err() {
        return uncertain(
            bridge,
            transaction,
            ledger,
            key,
            envelope,
            &status,
            "history_flush_failed",
        )
        .await;
    }
    let history = match source
        .store
        .load_latest_model_context(codex_thread_store::LoadThreadHistoryParams {
            thread_id: source.thread_id,
            include_archived: false,
        })
        .await
    {
        Ok(history) if history.items.len() <= 100_000 => history,
        _ => {
            return uncertain(
                bridge,
                transaction,
                ledger,
                key,
                envelope,
                &status,
                "history_unavailable_or_bound",
            )
            .await;
        }
    };
    let expected = ResponseItemId::with_suffix("amsg", envelope.message_id);
    let legacy = ResponseItemId::with_suffix("factory", envelope.message_id);
    let author = AgentPath::coworker(envelope.sender, envelope.incarnation).to_string();
    let recipient =
        AgentPath::coworker(bridge.binding.grant.seat, bridge.binding.grant.incarnation)
            .to_string();
    let mut correlations = Vec::new();
    let mut terminal: BTreeMap<String, Vec<&str>> = BTreeMap::new();
    let mut invalid = false;
    for item in &history.items {
        match item {
            RolloutItem::ResponseItem(item) => {
                if let ResponseItem::AgentMessage {
                    id: Some(id),
                    author: actual_author,
                    recipient: actual_recipient,
                    content,
                    ..
                } = &item.item
                    && (id == &expected || id == &legacy)
                {
                    if actual_author != &author
                        || actual_recipient != &recipient
                        || !matches!(content.as_slice(), [AgentMessageInputContent::InputText { text }] if text == &envelope.text)
                    {
                        invalid = true;
                    }
                    correlations.push(item.turn_id().map(str::to_owned));
                }
            }
            RolloutItem::EventMsg(EventMsg::TurnComplete(event)) => {
                terminal
                    .entry(event.turn_id.clone())
                    .or_default()
                    .push(if event.error.is_none() {
                        "completed"
                    } else {
                        "failed"
                    });
            }
            RolloutItem::EventMsg(EventMsg::TurnAborted(event)) => {
                if let Some(id) = &event.turn_id {
                    terminal.entry(id.clone()).or_default().push("interrupted");
                }
            }
            _ => {}
        }
    }
    if invalid || correlations.len() != 1 || correlations[0].is_none() {
        return uncertain(
            bridge,
            transaction,
            ledger,
            key,
            envelope,
            &status,
            "message_history_ambiguous",
        )
        .await;
    }
    let turn = correlations
        .pop()
        .flatten()
        .context("factory_turn_missing")?;
    if ledger["messages"][key]["turnId"]
        .as_str()
        .is_some_and(|prior| prior != turn)
    {
        return uncertain(
            bridge,
            transaction,
            ledger,
            key,
            envelope,
            &status,
            "message_turn_conflict",
        )
        .await;
    }
    let terminal = terminal.get(&turn);
    if terminal.is_some_and(|events| events.len() != 1) {
        return uncertain(
            bridge,
            transaction,
            ledger,
            key,
            envelope,
            &status,
            "terminal_history_ambiguous",
        )
        .await;
    }
    ledger["messages"][key]["turnId"] = json!(turn);
    ledger["messages"][key]["state"] = json!("consumed");
    ledger["messages"][key]["reconcileReason"] = Value::Null;
    transaction.save(ledger)?;
    if ledger["messages"][key]["consumptionAcked"] != true {
        fault(transaction, "before_consumed_ack").await?;
        bridge.call(&envelope.project, bridge.binding.grant.seat, fleet::Operation::Acknowledge,
            json!({"method":"ack","receipt":{"protocol":fleet::VERSION,"messageId":envelope.message_id,
                "state":"consumed","writer":"runtime","turnId":turn}})).await?;
        fault(transaction, "after_consumed_ack").await?;
        ledger["messages"][key]["consumptionAcked"] = json!(true);
        transaction.save(ledger)?;
    }
    let terminal = terminal.and_then(|events| events.first()).copied();
    if let Some(outcome) = terminal {
        if outcome == "completed" && ledger["messages"][key]["completionAcked"] != true {
            bridge.call(&envelope.project, bridge.binding.grant.seat, fleet::Operation::Acknowledge,
                json!({"method":"ack","receipt":{"protocol":fleet::VERSION,"messageId":envelope.message_id,
                    "state":"completed","writer":"worker","turnId":turn}})).await?;
            ledger["messages"][key]["completionAcked"] = json!(true);
        }
        ledger["messages"][key]["state"] = json!(outcome);
        transaction.save(ledger)?;
    }
    if matches!(
        status["cancellation"]["state"].as_str(),
        Some("requested" | "uncertain")
    ) {
        if let Some(outcome) = terminal {
            if outcome == "interrupted"
                && ledger["messages"][key]["interruptIntent"] == true
                && ledger["messages"][key]["interruptAdmitted"]
                    .as_bool()
                    .is_none()
            {
                return uncertain(
                    bridge,
                    transaction,
                    ledger,
                    key,
                    envelope,
                    &status,
                    "interrupt_application_ambiguous",
                )
                .await;
            }
            let applied =
                outcome == "interrupted" && ledger["messages"][key]["interruptAdmitted"] == true;
            let result = cancel_result(
                bridge,
                envelope,
                if applied { "applied" } else { "too_late" },
                Some(&turn),
                if applied {
                    "durable_turn_abort"
                } else {
                    "already_terminal"
                },
            )
            .await?;
            ledger["messages"][key]["cancellation"] = result["cancellation"].clone();
            transaction.save(ledger)?;
        } else if ledger["messages"][key]["interruptAdmitted"] != true {
            // Intent is durable before interruption. Lost admission acknowledgement is uncertain.
            if ledger["messages"][key]["interruptIntent"] == true {
                return uncertain(
                    bridge,
                    transaction,
                    ledger,
                    key,
                    envelope,
                    &status,
                    "interrupt_ack_lost",
                )
                .await;
            }
            ledger["messages"][key]["interruptIntent"] = json!(true);
            transaction.save(ledger)?;
            let (reply, result) = tokio::sync::oneshot::channel();
            thread
                .submit(Op::InterruptIfNoPendingInput {
                    turn_id: turn.clone(),
                    reply,
                })
                .await?;
            let admitted = result.await?;
            fault(transaction, "after_interrupt_admission").await?;
            ledger["messages"][key]["interruptAdmitted"] = json!(admitted);
            transaction.save(ledger)?;
            if !admitted {
                return uncertain(
                    bridge,
                    transaction,
                    ledger,
                    key,
                    envelope,
                    &status,
                    "turn_not_current_or_pending_input",
                )
                .await;
            }
            // Admission is not application. A later durable abort proves application.
            ledger["messages"][key]["cancellation"] = json!({"state":"requested","turnId":turn});
            transaction.save(ledger)?;
        }
    } else {
        ledger["messages"][key]["cancellation"] = status["cancellation"].clone();
        transaction.save(ledger)?;
    }
    Ok(ledger["messages"][key].clone())
}
