//! Outbound, pinned OpenSSH transport with explicit host and Unix-UID attestation.
use crate::observation;
use crate::protocol;
use crate::service;
use anyhow::Context;
use anyhow::Result;
use anyhow::bail;
use anyhow::ensure;
use serde::Deserialize;
use serde::Serialize;
use serde_json::Value;
use serde_json::json;
use std::collections::BTreeSet;
use std::fs::OpenOptions;
use std::io::Read;
use std::io::Write;
use std::net::IpAddr;
use std::os::fd::AsRawFd;
use std::os::unix::fs::MetadataExt;
use std::os::unix::fs::OpenOptionsExt;
use std::os::unix::fs::PermissionsExt;
use std::os::unix::net::UnixListener;
use std::path::Path;
use std::path::PathBuf;
use std::process::Child;
use std::process::Command;
use std::process::Stdio;
use std::time::Duration;
use std::time::Instant;
use uuid::Uuid;

const COMMAND: &str = "codex-factory-v1";
const SSH: &str = "/usr/bin/ssh";
const TIMEOUT: Duration = Duration::from_millis(2500);

#[derive(Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct Config {
    source_host: Uuid,
    transport_key: Uuid,
    hub: Uuid,
    address: IpAddr,
    port: u16,
    user: String,
    identity_file: PathBuf,
    known_hosts: PathBuf,
    allowed_agent_uids: BTreeSet<u32>,
}

#[derive(Deserialize, Serialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct Forwarded {
    protocol: u32,
    source_host: Uuid,
    key_id: Uuid,
    peer_uid: u32,
    wire: Value,
}

#[derive(Deserialize, Serialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct Returned {
    protocol: u32,
    source_host: Uuid,
    key_id: Uuid,
    response: Value,
}

fn uid() -> u32 {
    // SAFETY: geteuid has no arguments or ownership requirements.
    unsafe { libc::geteuid() }
}

fn private_address(address: IpAddr) -> bool {
    match address {
        IpAddr::V4(address) => {
            let bytes = address.octets();
            address.is_private()
                || address.is_loopback()
                || (bytes[0] == 100 && (64..=127).contains(&bytes[1]))
        }
        IpAddr::V6(address) => address.is_loopback() || address.is_unique_local(),
    }
}

fn protected_file(path: &Path, limit: u64) -> Result<PathBuf> {
    ensure!(path.is_absolute(), "network_private_path_required");
    let canonical = path.canonicalize()?;
    ensure!(
        canonical
            .as_os_str()
            .as_encoded_bytes()
            .iter()
            .all(|byte| byte.is_ascii_alphanumeric() || b"/._-".contains(byte)),
        "network_private_path_invalid"
    );
    service::private_read(&canonical, limit)?;
    for parent in canonical.ancestors().skip(1) {
        let info = std::fs::metadata(parent)?;
        let root_sticky = info.uid() == 0 && info.mode() & 0o1000 != 0;
        ensure!(
            (info.uid() == 0 || info.uid() == uid()) && (info.mode() & 0o022 == 0 || root_sticky),
            "network_private_parent_denied"
        );
    }
    Ok(canonical)
}

fn config(path: &Path) -> Result<Config> {
    let path = protected_file(path, 8192)?;
    let mut config: Config = serde_json::from_slice(&service::private_read(&path, 8192)?)?;
    ensure!(
        !config.source_host.is_nil()
            && !config.transport_key.is_nil()
            && !config.hub.is_nil()
            && config.source_host != config.hub,
        "network_host_identity_invalid"
    );
    ensure!(
        private_address(config.address) && config.port > 0,
        "network_private_address_required"
    );
    ensure!(
        !config.user.is_empty()
            && config.user.len() <= 32
            && !config.user.starts_with('-')
            && config
                .user
                .bytes()
                .all(|byte| byte.is_ascii_alphanumeric() || b"_-".contains(&byte)),
        "network_user_invalid"
    );
    ensure!(
        !config.allowed_agent_uids.is_empty()
            && config.allowed_agent_uids.len() <= 128
            && config
                .allowed_agent_uids
                .iter()
                .all(|peer| *peer != 0 && *peer != uid() && *peer != u32::MAX),
        "network_agent_uid_invalid"
    );
    config.identity_file = protected_file(&config.identity_file, 16384)?;
    config.known_hosts = protected_file(&config.known_hosts, 65536)?;
    let binary = std::fs::metadata(SSH)?;
    ensure!(
        binary.is_file() && binary.uid() == 0 && binary.mode() & 0o022 == 0,
        "network_ssh_binary_denied"
    );
    Ok(config)
}

fn ssh(config: &Config) -> Command {
    let mut command = Command::new(SSH);
    command
        .env_clear()
        .env("PATH", "/usr/bin:/bin")
        .env("LC_ALL", "C");
    command.args([
        "-F",
        "/dev/null",
        "-T",
        "-oBatchMode=yes",
        "-oIdentitiesOnly=yes",
        "-oIdentityAgent=none",
        "-oPasswordAuthentication=no",
        "-oKbdInteractiveAuthentication=no",
        "-oPreferredAuthentications=publickey",
        "-oPubkeyAuthentication=yes",
        "-oStrictHostKeyChecking=yes",
        "-oCheckHostIP=no",
        "-oUpdateHostKeys=no",
        "-oGlobalKnownHostsFile=/dev/null",
        "-oControlMaster=no",
        "-oControlPath=none",
        "-oClearAllForwardings=yes",
        "-oForwardAgent=no",
        "-oForwardX11=no",
        "-oPermitLocalCommand=no",
        "-oProxyCommand=none",
        "-oProxyJump=none",
        "-oCanonicalizeHostname=no",
        "-oConnectTimeout=1",
        "-oConnectionAttempts=1",
        "-oServerAliveInterval=1",
        "-oServerAliveCountMax=1",
        "-oEscapeChar=none",
        "-oLogLevel=ERROR",
    ]);
    command
        .arg(format!(
            "-oUserKnownHostsFile={}",
            config.known_hosts.display()
        ))
        .arg(format!("-oHostKeyAlias=factory-hub-{}", config.hub))
        .arg("-i")
        .arg(&config.identity_file)
        .arg("-l")
        .arg(&config.user)
        .arg("-p")
        .arg(config.port.to_string())
        .arg("--")
        .arg(config.address.to_string())
        .arg(COMMAND)
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::null());
    command
}

fn ready(fd: i32, events: i16, deadline: Instant) -> Result<()> {
    loop {
        let remaining = deadline
            .checked_duration_since(Instant::now())
            .context("network_timeout")?;
        let mut descriptor = libc::pollfd {
            fd,
            events,
            revents: 0,
        };
        let milliseconds =
            i32::try_from(remaining.as_millis().max(1)).context("network_timeout_bound")?;
        // SAFETY: descriptor points to one valid pollfd for this synchronous call.
        let result = unsafe { libc::poll(&mut descriptor, 1, milliseconds) };
        if result < 0 && std::io::Error::last_os_error().kind() == std::io::ErrorKind::Interrupted {
            continue;
        }
        ensure!(result > 0, "network_timeout");
        ensure!(
            descriptor.revents & (events | libc::POLLHUP) != 0,
            "network_io_unavailable"
        );
        return Ok(());
    }
}

fn line(reader: &mut (impl Read + AsRawFd), bound: usize, deadline: Instant) -> Result<Vec<u8>> {
    let mut bytes = Vec::new();
    loop {
        ready(reader.as_raw_fd(), libc::POLLIN, deadline)?;
        let mut part = [0_u8; 1024];
        let read = reader.read(&mut part)?;
        ensure!(read > 0, "network_frame_incomplete");
        bytes.extend_from_slice(&part[..read]);
        ensure!(bytes.len() <= bound, "network_frame_bound");
        if bytes.contains(&b'\n') {
            ensure!(
                bytes.iter().position(|byte| *byte == b'\n') == Some(bytes.len() - 1),
                "network_frame_trailing_data"
            );
            return Ok(bytes);
        }
    }
}

fn write(writer: &mut (impl Write + AsRawFd), bytes: &[u8], deadline: Instant) -> Result<()> {
    let descriptor = writer.as_raw_fd();
    // A blocked SSH handshake must not block the owner's write beyond the request deadline.
    // SAFETY: fcntl reads and updates flags on this valid owned pipe descriptor.
    let flags = unsafe { libc::fcntl(descriptor, libc::F_GETFL) };
    ensure!(flags >= 0, "network_pipe_flags");
    // SAFETY: only the descriptor flags change; ownership remains with writer.
    ensure!(
        unsafe { libc::fcntl(descriptor, libc::F_SETFL, flags | libc::O_NONBLOCK) } == 0,
        "network_pipe_flags"
    );
    let mut remaining = bytes;
    while !remaining.is_empty() {
        ready(descriptor, libc::POLLOUT, deadline)?;
        match writer.write(remaining) {
            Ok(0) => bail!("network_write_incomplete"),
            Ok(count) => remaining = &remaining[count..],
            Err(error)
                if [
                    std::io::ErrorKind::WouldBlock,
                    std::io::ErrorKind::Interrupted,
                ]
                .contains(&error.kind()) =>
            {
                continue;
            }
            Err(error) => return Err(error.into()),
        }
    }
    Ok(())
}

fn finish(child: &mut Child, deadline: Instant) -> Result<()> {
    loop {
        if let Some(status) = child.try_wait()? {
            ensure!(status.success(), "network_ssh_failed");
            return Ok(());
        }
        ensure!(Instant::now() < deadline, "network_timeout");
        std::thread::sleep(Duration::from_millis(10));
    }
}

fn exchange(config: &Config, actual_uid: u32, wire: Value) -> Result<Value> {
    ensure!(
        config.allowed_agent_uids.contains(&actual_uid),
        "network_peer_uid_denied"
    );
    ensure!(
        serde_json::to_vec(&wire)?.len() < protocol::MAX_FRAME,
        "network_frame_bound"
    );
    if wire["method"] == "report_presence" {
        let report: observation::PresenceReport = serde_json::from_value(wire["report"].clone())?;
        ensure!(
            observation::process_health(&report.process, actual_uid)? == "alive",
            "observation_process_unproven"
        );
    }
    let request = Forwarded {
        protocol: 1,
        source_host: config.source_host,
        key_id: config.transport_key,
        peer_uid: actual_uid,
        wire,
    };
    let mut bytes = serde_json::to_vec(&request)?;
    bytes.push(b'\n');
    ensure!(
        bytes.len() <= protocol::MAX_FRAME + 256,
        "network_frame_bound"
    );
    let deadline = Instant::now() + TIMEOUT;
    let mut child = ssh(config).spawn()?;
    let response: Result<Value> = (|| {
        let mut input = child.stdin.take().context("network_child_input_missing")?;
        write(&mut input, &bytes, deadline)?;
        drop(input);
        let mut output = child
            .stdout
            .take()
            .context("network_child_output_missing")?;
        let bytes = line(&mut output, protocol::MAX_FRAME + 256, deadline)?;
        finish(&mut child, deadline)?;
        let returned: Returned = serde_json::from_slice(&bytes)?;
        ensure!(
            returned.protocol == 1
                && returned.source_host == config.source_host
                && returned.key_id == config.transport_key,
            "network_host_binding_denied"
        );
        ensure!(
            serde_json::to_vec(&returned.response)?.len() < protocol::MAX_FRAME,
            "network_response_bound"
        );
        Ok(returned.response)
    })();
    // This Child owns the unreaped process incarnation. Its PID cannot be reused
    // before wait; no global process-name or unrelated PID cleanup is permitted.
    if child.try_wait()?.is_none() {
        match child.kill() {
            Ok(()) => {}
            Err(error) if error.kind() == std::io::ErrorKind::InvalidInput => {}
            Err(error) => return Err(error.into()),
        }
        child.wait()?;
    }
    response.map_err(|_| anyhow::anyhow!("network_transport_unavailable"))
}

fn response(result: Result<Value>) -> Value {
    match result {
        Ok(value) => value,
        Err(error) => {
            let text = error.to_string();
            let code = if !text.is_empty()
                && text.len() <= 128
                && text
                    .bytes()
                    .all(|byte| byte.is_ascii_lowercase() || byte.is_ascii_digit() || byte == b'_')
            {
                text.as_str()
            } else {
                "network_request_denied"
            };
            json!({"ok":false,"error":code})
        }
    }
}

/// Configure this exact entry as a key-specific restricted sshd forced command.
pub fn remote_entry(state: &Path, host: Uuid, key_id: Uuid) -> Result<()> {
    ensure!(
        std::env::var("SSH_ORIGINAL_COMMAND").as_deref() == Ok(COMMAND)
            && std::env::var_os("SSH_CONNECTION").is_some(),
        "network_forced_command_required"
    );
    let deadline = Instant::now() + TIMEOUT;
    let result = (|| {
        let bytes = line(
            &mut std::io::stdin().lock(),
            protocol::MAX_FRAME + 256,
            deadline,
        )?;
        let request: Forwarded =
            serde_json::from_slice(&bytes).map_err(|_| anyhow::anyhow!("network_frame_invalid"))?;
        ensure!(request.protocol == 1, "network_protocol_unsupported");
        ensure!(
            request.source_host == host && request.key_id == key_id,
            "network_host_binding_denied"
        );
        service::remote_frame(
            state,
            host,
            key_id,
            request.peer_uid,
            request.wire,
            deadline,
        )
    })();
    let returned = Returned {
        protocol: 1,
        source_host: host,
        key_id,
        response: response(result),
    };
    let mut bytes = serde_json::to_vec(&returned)?;
    bytes.push(b'\n');
    ensure!(
        bytes.len() <= protocol::MAX_FRAME + 256,
        "network_response_bound"
    );
    std::io::stdout().write_all(&bytes)?;
    Ok(())
}

pub fn serve(state: &Path, socket: &Path, configuration: &Path) -> Result<()> {
    let initial = config(configuration)?;
    let _state_lock = service::lock(state)?;
    let server_lock = OpenOptions::new()
        .read(true)
        .write(true)
        .create(true)
        .truncate(false)
        .mode(0o600)
        .custom_flags(libc::O_NOFOLLOW)
        .open(state.join("server.lock"))?;
    let info = server_lock.metadata()?;
    ensure!(
        info.is_file() && info.uid() == uid() && info.mode() & 0o077 == 0,
        "network_lock_permissions"
    );
    // SAFETY: the owned file descriptor remains valid for this server's lifetime.
    ensure!(
        unsafe { libc::flock(server_lock.as_raw_fd(), libc::LOCK_EX | libc::LOCK_NB) } == 0,
        "network_proxy_already_running"
    );
    drop(_state_lock);
    let info = std::fs::symlink_metadata(socket.parent().context("socket_parent_missing")?)?;
    ensure!(
        socket.is_absolute()
            && socket.as_os_str().len() < 104
            && info.is_dir()
            && info.uid() == uid()
            && info.mode() & 0o022 == 0,
        "socket_directory_permissions"
    );
    ensure!(
        std::fs::symlink_metadata(socket).is_err(),
        "socket_path_exists"
    );
    let listener = UnixListener::bind(socket)?;
    std::fs::set_permissions(socket, std::fs::Permissions::from_mode(0o660))?;
    println!(
        "{}",
        json!({"ready":true,"pid":std::process::id(),"uid":uid(),"socket":socket,
        "transport":"restricted_openssh","sourceHost":initial.source_host,"hub":initial.hub})
    );
    std::io::stdout().flush()?;
    crate::connections::serve(&listener, |mut stream| {
        stream.set_read_timeout(Some(TIMEOUT))?;
        stream.set_write_timeout(Some(TIMEOUT))?;
        let result = (|| {
            let actual_uid = service::peer_uid(&stream)?;
            // Read one bounded frame before an error reply. Closing with unread
            // input can reset the caller before it receives the refusal.
            let bytes = line(&mut stream, protocol::MAX_FRAME, Instant::now() + TIMEOUT)?;
            let config = config(configuration)?;
            ensure!(
                config.allowed_agent_uids.contains(&actual_uid),
                "network_peer_uid_denied"
            );
            let wire =
                serde_json::from_slice(&bytes).map_err(|_| anyhow::anyhow!("invalid_request"))?;
            exchange(&config, actual_uid, wire)
        })();
        let mut bytes = serde_json::to_vec(&response(result))?;
        bytes.push(b'\n');
        if bytes.len() > protocol::MAX_FRAME {
            return Ok(());
        }
        // A disconnected caller does not abandon a still-running SSH child; exchange reaps it first.
        match stream.write_all(&bytes) {
            Ok(()) => {}
            Err(error)
                if matches!(
                    error.kind(),
                    std::io::ErrorKind::BrokenPipe
                        | std::io::ErrorKind::ConnectionReset
                        | std::io::ErrorKind::TimedOut
                ) => {}
            Err(error) => return Err(error.into()),
        }
        Ok(())
    })
}
