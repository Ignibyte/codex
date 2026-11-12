use anyhow::Result;
use anyhow::ensure;
use clap::Parser;
use clap::Subcommand;
use codex_factory::protocol;
use serde::de::DeserializeOwned;
use std::path::PathBuf;

#[derive(Parser)]
#[command(version)]
struct Cli {
    #[command(subcommand)]
    command: Command,
}

#[cfg(target_os = "linux")]
#[derive(clap::Args)]
struct ObservationBinding {
    #[arg(long)]
    grant: PathBuf,
    #[arg(long)]
    socket: PathBuf,
    #[arg(long)]
    service_uid: u32,
    #[arg(long)]
    credential: PathBuf,
    #[arg(long)]
    project: String,
}

#[derive(Subcommand)]
enum Command {
    #[cfg(target_os = "linux")]
    /// Apply one explicit support observation or pause action. This installs no timer.
    Support {
        #[arg(long)]
        state: PathBuf,
        #[arg(long)]
        input: PathBuf,
    },
    #[cfg(target_os = "linux")]
    /// Read scoped mount statistics and fixed kernel metrics. This changes no host state.
    SupportRead {
        #[arg(long)]
        input: PathBuf,
    },
    #[cfg(target_os = "linux")]
    /// Describe one scoped supporting profile. This starts no agent or schedule.
    SupportProfile {
        #[arg(long)]
        input: PathBuf,
    },
    #[cfg(target_os = "linux")]
    ManagerMcp {
        #[command(flatten)]
        binding: ObservationBinding,
        #[arg(long)]
        job_id: uuid::Uuid,
    },
    #[cfg(target_os = "linux")]
    ManagerPrepare {
        #[arg(long)]
        state: PathBuf,
        #[arg(long)]
        config: PathBuf,
    },
    #[cfg(target_os = "linux")]
    ManagerCall {
        #[command(flatten)]
        binding: ObservationBinding,
        #[arg(long)]
        input: PathBuf,
    },
    #[cfg(target_os = "linux")]
    /// Serve only the incident or decision tools in the granted role.
    WorkflowMcp {
        #[command(flatten)]
        binding: ObservationBinding,
        #[arg(long)]
        canonical_specs: bool,
    },
    #[cfg(target_os = "linux")]
    /// Run one explicit supervision action. This installs no schedule.
    Supervise {
        #[arg(long)]
        state: PathBuf,
        #[arg(long)]
        config: PathBuf,
        #[arg(long)]
        input: PathBuf,
    },
    #[cfg(target_os = "linux")]
    /// Send one authenticated incident report or advisory decision.
    WorkflowCall {
        #[command(flatten)]
        binding: ObservationBinding,
        #[arg(long)]
        input: PathBuf,
    },
    #[cfg(target_os = "linux")]
    /// Acknowledge the required context for one canonical run.
    AckContext {
        #[command(flatten)]
        binding: ObservationBinding,
        #[arg(long)]
        run_id: uuid::Uuid,
        #[arg(long)]
        context_digest: String,
    },
    #[cfg(target_os = "linux")]
    /// Submit a native terminal and artifact hashes for review.
    AssignmentResult {
        #[command(flatten)]
        binding: ObservationBinding,
        #[arg(long)]
        run_id: uuid::Uuid,
        #[arg(long)]
        thread_id: uuid::Uuid,
        #[arg(long)]
        turn_id: uuid::Uuid,
        #[arg(long)]
        artifacts: PathBuf,
    },
    #[cfg(target_os = "linux")]
    /// Apply one operator action to canonical Forge workflow dispatch.
    Foreman {
        #[arg(long)]
        state: PathBuf,
        #[arg(long)]
        config: PathBuf,
        #[arg(long)]
        input: PathBuf,
    },
    #[cfg(target_os = "linux")]
    /// Save workflow facts without credentials or signing keys.
    Backup {
        #[arg(long)]
        state: PathBuf,
        #[arg(long)]
        output: PathBuf,
    },
    #[cfg(target_os = "linux")]
    /// Restore facts into a fresh host with revoked historical grants.
    Restore {
        #[arg(long)]
        input: PathBuf,
        #[arg(long)]
        destination: PathBuf,
    },
    #[cfg(target_os = "linux")]
    RecoveryStatus {
        #[arg(long)]
        state: PathBuf,
    },
    #[cfg(target_os = "linux")]
    /// Admit an explicit remote host transport key. This creates no seat grant.
    AdmitHost {
        #[arg(long)]
        state: PathBuf,
        #[arg(long)]
        host: uuid::Uuid,
        #[arg(long)]
        key_id: uuid::Uuid,
        #[arg(long)]
        bridge_uid: u32,
        #[arg(long, default_value_t = 3600)]
        ttl: u64,
    },
    #[cfg(target_os = "linux")]
    RenewHost {
        #[arg(long)]
        state: PathBuf,
        #[arg(long)]
        host: uuid::Uuid,
        #[arg(long)]
        key_id: uuid::Uuid,
        #[arg(long, default_value_t = 86400)]
        ttl: u64,
    },
    #[cfg(target_os = "linux")]
    RotateHost {
        #[arg(long)]
        state: PathBuf,
        #[arg(long)]
        host: uuid::Uuid,
        #[arg(long)]
        key_id: uuid::Uuid,
        #[arg(long, default_value_t = 3600)]
        ttl: u64,
        #[arg(long, default_value_t = 0)]
        overlap_seconds: u64,
    },
    #[cfg(target_os = "linux")]
    RevokeHost {
        #[arg(long)]
        state: PathBuf,
        #[arg(long)]
        host: uuid::Uuid,
    },
    #[cfg(target_os = "linux")]
    /// Mint a seat bootstrap only for an already admitted remote host.
    BootstrapRemote {
        #[arg(long)]
        state: PathBuf,
        #[arg(long)]
        grant: PathBuf,
        #[arg(long)]
        output: PathBuf,
        #[arg(long, default_value_t = 60)]
        bootstrap_ttl: u64,
        #[arg(long, default_value_t = 3600)]
        credential_ttl: u64,
    },
    #[cfg(target_os = "linux")]
    /// Run only as a key-specific, restricted sshd forced command.
    RemoteEntry {
        #[arg(long)]
        state: PathBuf,
        #[arg(long)]
        host: uuid::Uuid,
        #[arg(long)]
        key_id: uuid::Uuid,
    },
    #[cfg(target_os = "linux")]
    /// Serve a local Unix proxy with one bounded outbound SSH channel per request.
    ConnectHost {
        #[arg(long)]
        state: PathBuf,
        #[arg(long)]
        socket: PathBuf,
        #[arg(long)]
        config: PathBuf,
    },
    #[cfg(target_os = "linux")]
    /// Serve a fixed read-only marley.work/v1 provider over stdio MCP.
    ObserveWork {
        #[command(flatten)]
        binding: ObservationBinding,
    },
    #[cfg(target_os = "linux")]
    /// Publish a typed self-report with a kernel process incarnation.
    ReportPresence {
        #[command(flatten)]
        binding: ObservationBinding,
        #[arg(long)]
        report: PathBuf,
    },
    #[cfg(target_os = "linux")]
    /// Prune the operator-owned observation journal; callers need its private UID.
    PruneObservation {
        #[arg(long)]
        state: PathBuf,
        #[arg(long)]
        keep: usize,
    },
    #[cfg(target_os = "linux")]
    CollectRecording {
        #[arg(long)]
        native_state: PathBuf,
        #[arg(long)]
        grant: PathBuf,
        #[arg(long)]
        socket: PathBuf,
        #[arg(long)]
        service_uid: u32,
        #[arg(long)]
        credential: PathBuf,
    },
    #[cfg(target_os = "linux")]
    RecordingOutbox {
        #[arg(long)]
        state: PathBuf,
    },
    #[cfg(target_os = "linux")]
    FlushRecording {
        #[arg(long)]
        state: PathBuf,
        #[arg(long)]
        config: PathBuf,
        #[arg(long)]
        event_id: uuid::Uuid,
    },
    #[cfg(target_os = "linux")]
    Replay {
        #[arg(long)]
        state: PathBuf,
        #[arg(long)]
        message_id: uuid::Uuid,
    },
    #[cfg(target_os = "linux")]
    Retry {
        #[arg(long)]
        state: PathBuf,
        #[arg(long)]
        grant: PathBuf,
        #[arg(long)]
        socket: PathBuf,
        #[arg(long)]
        service_uid: u32,
        #[arg(long)]
        credential: PathBuf,
        #[arg(long)]
        message_id: uuid::Uuid,
    },
    #[cfg(target_os = "linux")]
    Enqueue {
        #[arg(long)]
        state: PathBuf,
        #[arg(long)]
        grant: PathBuf,
        #[arg(long)]
        envelope: PathBuf,
    },
    #[cfg(target_os = "linux")]
    Outbox {
        #[arg(long)]
        state: PathBuf,
    },
    #[cfg(target_os = "linux")]
    Relay {
        #[arg(long)]
        state: PathBuf,
        #[arg(long)]
        grant: PathBuf,
        #[arg(long)]
        socket: PathBuf,
        #[arg(long)]
        service_uid: u32,
        #[arg(long)]
        credential: PathBuf,
        #[arg(long)]
        message_id: uuid::Uuid,
    },
    #[cfg(target_os = "linux")]
    RotationStatus {
        #[arg(long)]
        state: PathBuf,
        #[arg(long)]
        seat: uuid::Uuid,
    },
    #[cfg(target_os = "linux")]
    FinishRotation {
        #[arg(long)]
        state: PathBuf,
        #[arg(long)]
        seat: uuid::Uuid,
        #[arg(long)]
        rotation_id: uuid::Uuid,
        #[arg(long)]
        abandon_expired: bool,
    },
    #[cfg(target_os = "linux")]
    Rotate {
        #[arg(long)]
        state: PathBuf,
        #[arg(long)]
        seat: uuid::Uuid,
        #[arg(long)]
        output: PathBuf,
        #[arg(long, default_value_t = 3600)]
        credential_ttl: u64,
        #[arg(long, default_value_t = 0)]
        overlap_seconds: u64,
        #[arg(long)]
        rotation_id: Option<uuid::Uuid>,
    },
    #[cfg(target_os = "linux")]
    RouteGrant {
        #[arg(long)]
        state: PathBuf,
        #[arg(long)]
        input: PathBuf,
    },
    #[cfg(target_os = "linux")]
    RouteRevoke {
        #[arg(long)]
        state: PathBuf,
        #[arg(long)]
        id: uuid::Uuid,
        #[arg(long)]
        reason: String,
    },
    #[cfg(target_os = "linux")]
    Revoke {
        #[arg(long)]
        state: PathBuf,
        #[arg(long)]
        seat: uuid::Uuid,
    },
    #[cfg(target_os = "linux")]
    Inspect {
        #[arg(long)]
        state: PathBuf,
    },
    #[cfg(target_os = "linux")]
    /// Mint one private, expiring bootstrap for an explicitly scoped fixture seat.
    Bootstrap {
        #[arg(long)]
        state: PathBuf,
        #[arg(long)]
        grant: PathBuf,
        #[arg(long)]
        output: PathBuf,
        #[arg(long, default_value_t = 60)]
        bootstrap_ttl: u64,
        #[arg(long, default_value_t = 3600)]
        credential_ttl: u64,
    },
    #[cfg(target_os = "linux")]
    /// Serve local authorization; this starts no model or agent command.
    Serve {
        #[arg(long)]
        state: PathBuf,
        #[arg(long)]
        socket: PathBuf,
    },
    /// Validate the authority contract using explicit fixture facts; performs no dispatch.
    CheckContract {
        #[arg(long)]
        grant: PathBuf,
        #[arg(long)]
        request: PathBuf,
        #[arg(long)]
        peer_uid: u32,
        #[arg(long, default_value_t = 0)]
        last_sequence: u64,
        #[arg(long)]
        envelope: Option<PathBuf>,
        #[arg(long)]
        receipt: Option<PathBuf>,
        #[arg(long)]
        now: u64,
    },
}

fn read<T: DeserializeOwned>(path: &PathBuf) -> Result<T> {
    ensure!(
        std::fs::metadata(path)?.len() <= protocol::MAX_FRAME as u64,
        "frame_bound"
    );
    Ok(serde_json::from_slice(&std::fs::read(path)?)?)
}

fn main() -> Result<()> {
    match Cli::parse().command {
        #[cfg(target_os = "linux")]
        Command::Support { state, input } => {
            println!(
                "{}",
                codex_factory::support::schedule::call(&state, read(&input)?)?
            )
        }
        #[cfg(target_os = "linux")]
        Command::SupportRead { input } => {
            println!(
                "{}",
                serde_json::to_string(&codex_factory::support::health::read(&read(&input)?)?)?
            )
        }
        #[cfg(target_os = "linux")]
        Command::SupportProfile { input } => {
            println!("{}", codex_factory::support::profile(read(&input)?)?)
        }
        #[cfg(target_os = "linux")]
        Command::ManagerMcp { binding, job_id } => {
            let grant = read(&binding.grant)?;
            codex_factory::manager_mcp::serve(
                &codex_factory::observation_mcp::Binding {
                    socket: &binding.socket,
                    service_uid: binding.service_uid,
                    credential: &binding.credential,
                    grant: &grant,
                    project: &binding.project,
                },
                job_id,
            )?;
        }
        #[cfg(target_os = "linux")]
        Command::ManagerPrepare { state, config } => {
            println!("{}", codex_factory::manager::prepare(&state, &config)?)
        }
        #[cfg(target_os = "linux")]
        Command::ManagerCall { binding, input } => {
            let grant: protocol::Grant = read(&binding.grant)?;
            println!(
                "{}",
                codex_factory::client::scoped_call(
                    &grant,
                    &binding.socket,
                    binding.service_uid,
                    &binding.credential,
                    &binding.project,
                    protocol::Operation::Acknowledge,
                    read(&input)?
                )?
            );
        }
        #[cfg(target_os = "linux")]
        Command::WorkflowMcp {
            binding,
            canonical_specs,
        } => {
            let grant: protocol::Grant = read(&binding.grant)?;
            codex_factory::workflow_mcp::serve(
                &codex_factory::observation_mcp::Binding {
                    grant: &grant,
                    socket: &binding.socket,
                    credential: &binding.credential,
                    service_uid: binding.service_uid,
                    project: &binding.project,
                },
                canonical_specs,
            )?;
        }
        #[cfg(target_os = "linux")]
        Command::Supervise {
            state,
            config,
            input,
        } => {
            let runtime = tokio::runtime::Builder::new_current_thread()
                .enable_all()
                .build()?;
            println!(
                "{}",
                runtime.block_on(codex_factory::foreman::supervision::apply(
                    &state,
                    &config,
                    read(&input)?
                ))?
            );
        }
        #[cfg(target_os = "linux")]
        Command::WorkflowCall { binding, input } => {
            let frame: serde_json::Value = read(&input)?;
            ensure!(
                ["report_blockage", "blockage_decision"]
                    .iter()
                    .any(|method| frame["method"] == *method),
                "workflow_method_denied"
            );
            let grant: protocol::Grant = read(&binding.grant)?;
            println!(
                "{}",
                codex_factory::client::scoped_call(
                    &grant,
                    &binding.socket,
                    binding.service_uid,
                    &binding.credential,
                    &binding.project,
                    protocol::Operation::Acknowledge,
                    frame
                )?
            );
        }
        #[cfg(target_os = "linux")]
        Command::AckContext {
            binding,
            run_id,
            context_digest,
        } => {
            let grant: protocol::Grant = read(&binding.grant)?;
            println!(
                "{}",
                codex_factory::client::scoped_call(
                    &grant,
                    &binding.socket,
                    binding.service_uid,
                    &binding.credential,
                    &binding.project,
                    protocol::Operation::Acknowledge,
                    serde_json::json!({"method":"acknowledge_context","run_id":run_id,"context_digest":context_digest})
                )?
            );
        }
        #[cfg(target_os = "linux")]
        Command::AssignmentResult {
            binding,
            run_id,
            thread_id,
            turn_id,
            artifacts,
        } => {
            let grant: protocol::Grant = read(&binding.grant)?;
            let artifacts: Vec<codex_factory::foreman::Artifact> = read(&artifacts)?;
            println!(
                "{}",
                codex_factory::client::scoped_call(
                    &grant,
                    &binding.socket,
                    binding.service_uid,
                    &binding.credential,
                    &binding.project,
                    protocol::Operation::Acknowledge,
                    serde_json::json!({"method":"assignment_result","run_id":run_id,"thread_id":thread_id,
                    "turn_id":turn_id,"artifacts":artifacts})
                )?
            );
        }
        #[cfg(target_os = "linux")]
        Command::Foreman {
            state,
            config,
            input,
        } => {
            let runtime = tokio::runtime::Builder::new_current_thread()
                .enable_all()
                .build()?;
            println!(
                "{}",
                runtime.block_on(codex_factory::foreman::apply(
                    &state,
                    &config,
                    read(&input)?
                ))?
            );
        }
        #[cfg(target_os = "linux")]
        Command::Backup { state, output } => {
            println!(
                "{}",
                codex_factory::service::lifecycle::backup(&state, &output)?
            );
        }
        #[cfg(target_os = "linux")]
        Command::Restore { input, destination } => {
            println!(
                "{}",
                codex_factory::service::lifecycle::restore(&input, &destination)?
            );
        }
        #[cfg(target_os = "linux")]
        Command::RecoveryStatus { state } => {
            println!("{}", codex_factory::service::lifecycle::status(&state)?);
        }
        #[cfg(target_os = "linux")]
        Command::AdmitHost {
            state,
            host,
            key_id,
            bridge_uid,
            ttl,
        } => {
            println!(
                "{}",
                codex_factory::service::admit_host(&state, host, key_id, bridge_uid, ttl)?
            );
        }
        #[cfg(target_os = "linux")]
        Command::RenewHost {
            state,
            host,
            key_id,
            ttl,
        } => {
            println!(
                "{}",
                codex_factory::service::renew_host(&state, host, key_id, ttl)?
            );
        }
        #[cfg(target_os = "linux")]
        Command::RotateHost {
            state,
            host,
            key_id,
            ttl,
            overlap_seconds,
        } => {
            println!(
                "{}",
                codex_factory::service::rotate_host(&state, host, key_id, ttl, overlap_seconds)?
            );
        }
        #[cfg(target_os = "linux")]
        Command::RevokeHost { state, host } => {
            println!("{}", codex_factory::service::revoke_host(&state, host)?);
        }
        #[cfg(target_os = "linux")]
        Command::BootstrapRemote {
            state,
            grant,
            output,
            bootstrap_ttl,
            credential_ttl,
        } => {
            println!(
                "{}",
                codex_factory::service::bootstrap_remote(
                    &state,
                    read(&grant)?,
                    &output,
                    bootstrap_ttl,
                    credential_ttl
                )?
            );
        }
        #[cfg(target_os = "linux")]
        Command::RemoteEntry {
            state,
            host,
            key_id,
        } => {
            codex_factory::network::remote_entry(&state, host, key_id)?;
        }
        #[cfg(target_os = "linux")]
        Command::ConnectHost {
            state,
            socket,
            config,
        } => {
            codex_factory::network::serve(&state, &socket, &config)?;
        }
        #[cfg(target_os = "linux")]
        Command::ObserveWork { binding } => {
            let grant = read(&binding.grant)?;
            codex_factory::observation_mcp::Binding {
                socket: &binding.socket,
                service_uid: binding.service_uid,
                credential: &binding.credential,
                grant: &grant,
                project: &binding.project,
            }
            .serve()?;
        }
        #[cfg(target_os = "linux")]
        Command::ReportPresence { binding, report } => {
            let grant = read(&binding.grant)?;
            let value = codex_factory::observation_mcp::Binding {
                socket: &binding.socket,
                service_uid: binding.service_uid,
                credential: &binding.credential,
                grant: &grant,
                project: &binding.project,
            }
            .report(read(&report)?)?;
            println!("{value}");
        }
        #[cfg(target_os = "linux")]
        Command::PruneObservation { state, keep } => {
            println!("{}", codex_factory::observation::prune(&state, keep)?);
        }
        #[cfg(target_os = "linux")]
        Command::CollectRecording {
            native_state,
            grant,
            socket,
            service_uid,
            credential,
        } => {
            println!(
                "{}",
                codex_factory::recording::collect(
                    &native_state,
                    &socket,
                    service_uid,
                    &credential,
                    &read(&grant)?
                )?
            );
        }
        #[cfg(target_os = "linux")]
        Command::RecordingOutbox { state } => {
            println!("{}", codex_factory::recording::inspect(&state)?)
        }
        #[cfg(target_os = "linux")]
        Command::FlushRecording {
            state,
            config,
            event_id,
        } => {
            let runtime = tokio::runtime::Builder::new_current_thread()
                .enable_all()
                .build()?;
            println!(
                "{}",
                runtime.block_on(codex_factory::recording::flush(&state, &config, event_id))?
            );
        }
        #[cfg(target_os = "linux")]
        Command::Replay { state, message_id } => {
            println!("{}", codex_factory::client::replay(&state, message_id)?)
        }
        #[cfg(target_os = "linux")]
        Command::Retry {
            state,
            grant,
            socket,
            service_uid,
            credential,
            message_id,
        } => println!(
            "{}",
            codex_factory::client::retry(
                &state,
                &socket,
                service_uid,
                &credential,
                &read(&grant)?,
                message_id
            )?
        ),
        #[cfg(target_os = "linux")]
        Command::Enqueue {
            state,
            grant,
            envelope,
        } => println!(
            "{}",
            codex_factory::client::enqueue(&state, read(&envelope)?, &read(&grant)?)?
        ),
        #[cfg(target_os = "linux")]
        Command::Outbox { state } => println!("{}", codex_factory::client::inspect(&state)?),
        #[cfg(target_os = "linux")]
        Command::Relay {
            state,
            grant,
            socket,
            service_uid,
            credential,
            message_id,
        } => println!(
            "{}",
            codex_factory::client::relay(
                &state,
                &socket,
                service_uid,
                &credential,
                &read(&grant)?,
                message_id
            )?
        ),
        #[cfg(target_os = "linux")]
        Command::RotationStatus { state, seat } => {
            println!("{}", codex_factory::service::rotation_status(&state, seat)?)
        }
        #[cfg(target_os = "linux")]
        Command::FinishRotation {
            state,
            seat,
            rotation_id,
            abandon_expired,
        } => println!(
            "{}",
            codex_factory::service::finish_rotation(&state, seat, rotation_id, abandon_expired)?
        ),
        #[cfg(target_os = "linux")]
        Command::Rotate {
            state,
            seat,
            output,
            credential_ttl,
            overlap_seconds,
            rotation_id,
        } => println!(
            "{}",
            codex_factory::service::rotate_with_id(
                &state,
                seat,
                &output,
                credential_ttl,
                overlap_seconds,
                rotation_id
            )?
        ),
        #[cfg(target_os = "linux")]
        Command::RouteGrant { state, input } => println!(
            "{}",
            codex_factory::service::routes::grant(&state, read(&input)?)?
        ),
        #[cfg(target_os = "linux")]
        Command::RouteRevoke { state, id, reason } => println!(
            "{}",
            codex_factory::service::routes::revoke(&state, id, &reason)?
        ),
        #[cfg(target_os = "linux")]
        Command::Revoke { state, seat } => {
            println!("{}", codex_factory::service::revoke(&state, seat)?)
        }
        #[cfg(target_os = "linux")]
        Command::Inspect { state } => println!("{}", codex_factory::service::inspect(&state)?),
        #[cfg(target_os = "linux")]
        Command::Bootstrap {
            state,
            grant,
            output,
            bootstrap_ttl,
            credential_ttl,
        } => {
            let grant: protocol::Grant = read(&grant)?;
            println!(
                "{}",
                codex_factory::service::bootstrap(
                    &state,
                    grant,
                    &output,
                    bootstrap_ttl,
                    credential_ttl
                )?
            );
        }
        #[cfg(target_os = "linux")]
        Command::Serve { state, socket } => codex_factory::service::serve(&state, &socket)?,
        Command::CheckContract {
            grant,
            request,
            peer_uid,
            last_sequence,
            envelope,
            receipt,
            now,
        } => {
            let grant: protocol::Grant = read(&grant)?;
            let request: protocol::Request = read(&request)?;
            protocol::authorize(&grant, peer_uid, last_sequence, &request)?;
            let mut message_id = None;
            if let Some(path) = envelope {
                let envelope: protocol::Envelope = read(&path)?;
                envelope.validate(&request, now)?;
                message_id = Some(envelope.message_id);
            }
            if let Some(path) = receipt {
                let receipt: protocol::Receipt = read(&path)?;
                receipt.validate()?;
                ensure!(
                    message_id == Some(receipt.message_id),
                    "receipt_message_mismatch"
                );
            }
            println!(
                "{}",
                serde_json::json!({"authorized": true, "sender": grant.seat,
                "operation": request.operation, "dispatch": "not_performed"})
            );
        }
    }
    Ok(())
}
