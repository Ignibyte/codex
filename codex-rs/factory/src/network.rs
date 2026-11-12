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
use std::ffi::CString;
use std::ffi::OsStr;
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

#[derive(Clone, Deserialize, Serialize, PartialEq, Eq)]
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
    validate_config(serde_json::from_slice(&service::private_read(
        &path, 8192,
    )?)?)
}

fn validate_config(mut config: Config) -> Result<Config> {
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

// The source peer owns UID attestation; only the hub owns mailbox authority.
// Protocol 2 is a standing transport envelope around unchanged mailbox protocol 1.
#[derive(Clone, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct SourceConfig {
    generation: Uuid,
    transport: Config,
    service_state: PathBuf,
    source_address: IpAddr,
}

#[derive(Clone, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct EntryConfig {
    generation: Uuid,
    source_host: Uuid,
    transport_key: Uuid,
    hub: Uuid,
    service_uid: u32,
    allowed_agent_uids: BTreeSet<u32>,
    state: PathBuf,
    socket: PathBuf,
}

#[derive(Deserialize, Serialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct SourceHello {
    protocol: u32,
    generation: Uuid,
    source_host: Uuid,
    key_id: Uuid,
    hub: Uuid,
    service_uid: u32,
    boot_id: Uuid,
    available: bool,
}

#[derive(Deserialize, Serialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct SourceFrame {
    protocol: u32,
    generation: Uuid,
    request_id: Uuid,
    source_host: Uuid,
    key_id: Uuid,
    peer_uid: u32,
    wire: Value,
}

#[derive(Deserialize, Serialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct SourceReply {
    protocol: u32,
    generation: Uuid,
    request_id: Uuid,
    source_host: Uuid,
    key_id: Uuid,
    response: Value,
}

#[derive(Default)]
struct Frames {
    pending: Vec<u8>,
}

impl Frames {
    fn read(&mut self, reader: &mut (impl Read + AsRawFd), deadline: Instant) -> Result<Vec<u8>> {
        loop {
            if let Some(end) = self.pending.iter().position(|byte| *byte == b'\n') {
                ensure!(end < protocol::MAX_FRAME + 512, "network_frame_bound");
                return Ok(self.pending.drain(..=end).collect());
            }
            ensure!(
                self.pending.len() <= protocol::MAX_FRAME + 512,
                "network_frame_bound"
            );
            ready(reader.as_raw_fd(), libc::POLLIN, deadline)?;
            let mut buffer = [0_u8; 1024];
            let count = reader.read(&mut buffer)?;
            ensure!(count > 0, "network_frame_incomplete");
            self.pending.extend_from_slice(&buffer[..count]);
        }
    }
}

fn source_config(path: &Path) -> Result<SourceConfig> {
    let path = protected_file(path, 8192)?;
    let mut result: SourceConfig = serde_json::from_slice(&service::private_read(&path, 8192)?)?;
    ensure!(
        !result.generation.is_nil() && private_address(result.source_address),
        "network_source_config_invalid"
    );
    result.transport = validate_config(result.transport)?;
    ensure!(
        result.service_state.is_absolute(),
        "network_private_path_required"
    );
    let state = service::inspect(&result.service_state)?;
    ensure!(
        state["host"] == json!(result.transport.hub),
        "network_hub_identity_denied"
    );
    ensure!(
        state["remoteHosts"][result.transport.source_host.to_string()]["bridgeUid"] == uid(),
        "network_service_uid_denied"
    );
    Ok(result)
}

fn entry_config(path: &Path) -> Result<EntryConfig> {
    ensure!(path.is_absolute(), "network_root_config_required");
    for part in path.ancestors() {
        let info = std::fs::symlink_metadata(part)?;
        ensure!(
            info.uid() == 0 && info.mode() & 0o022 == 0 && !info.file_type().is_symlink(),
            "network_root_config_required"
        );
    }
    let info = std::fs::metadata(path)?;
    ensure!(
        info.is_file() && info.len() <= 8192 && info.nlink() == 1,
        "network_root_config_required"
    );
    let result: EntryConfig = serde_json::from_slice(&std::fs::read(path)?)?;
    ensure!(
        !result.generation.is_nil()
            && !result.source_host.is_nil()
            && !result.transport_key.is_nil()
            && !result.hub.is_nil()
            && result.hub != result.source_host,
        "network_host_identity_invalid"
    );
    ensure!(
        result.service_uid == uid() && uid() != 0,
        "network_service_uid_denied"
    );
    ensure!(
        !result.allowed_agent_uids.is_empty()
            && result.allowed_agent_uids.len() <= 128
            && result
                .allowed_agent_uids
                .iter()
                .all(|id| *id != 0 && *id != uid() && *id != u32::MAX),
        "network_agent_uid_invalid"
    );
    Ok(result)
}

fn send_frame(
    writer: &mut (impl Write + AsRawFd),
    value: &impl Serialize,
    deadline: Instant,
) -> Result<()> {
    let mut bytes = serde_json::to_vec(value)?;
    bytes.push(b'\n');
    ensure!(
        bytes.len() <= protocol::MAX_FRAME + 512,
        "network_frame_bound"
    );
    write(writer, &bytes, deadline)
}

struct OwnedSsh(Child);
impl Drop for OwnedSsh {
    fn drop(&mut self) {
        if self.0.try_wait().ok().flatten().is_none() {
            let _ = self.0.kill();
            let _ = self.0.wait();
        }
    }
}

fn instance_lock(state: &Path) -> Result<std::fs::File> {
    let _lock = service::lock(state)?;
    let file = OpenOptions::new()
        .read(true)
        .write(true)
        .create(true)
        .truncate(false)
        .mode(0o600)
        .custom_flags(libc::O_NOFOLLOW)
        .open(state.join("server.lock"))?;
    let info = file.metadata()?;
    ensure!(
        info.is_file() && info.uid() == uid() && info.nlink() == 1 && info.mode() & 0o077 == 0,
        "network_lock_permissions"
    );
    // SAFETY: the descriptor is owned by file until the instance exits.
    ensure!(
        unsafe { libc::flock(file.as_raw_fd(), libc::LOCK_EX | libc::LOCK_NB) } == 0,
        "network_source_already_running"
    );
    Ok(file)
}

fn boot_id() -> Result<Uuid> {
    Ok(Uuid::parse_str(
        std::fs::read_to_string("/proc/sys/kernel/random/boot_id")?.trim(),
    )?)
}

#[derive(Clone, Deserialize, Serialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct SocketOwner {
    pid: u32,
    start_ticks: String,
    boot_id: Uuid,
    executable: PathBuf,
    socket: PathBuf,
    device: u64,
    inode: u64,
}

fn start_ticks(pid: u32) -> Result<String> {
    let text = std::fs::read_to_string(format!("/proc/{pid}/stat"))?;
    Ok(text
        .rsplit_once(')')
        .context("network_process_identity_unavailable")?
        .1
        .split_whitespace()
        .nth(19)
        .context("network_process_identity_unavailable")?
        .to_owned())
}

fn owner_alive(owner: &SocketOwner) -> Result<bool> {
    if owner.boot_id != boot_id()? {
        return Ok(false);
    }
    let path = PathBuf::from(format!("/proc/{}/stat", owner.pid));
    match std::fs::read_to_string(&path) {
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => return Ok(false),
        Err(error) => return Err(error.into()),
        Ok(_) => {}
    }
    // A replaced executable can appear as '(deleted)' while the original
    // process still owns its socket. Same boot/PID/start-time remains live.
    Ok(start_ticks(owner.pid)? == owner.start_ticks)
}

struct OwnedSocket {
    listener: UnixListener,
    state: PathBuf,
    owner: SocketOwner,
}
impl OwnedSocket {
    fn bind(state: &Path, socket: &Path) -> Result<Self> {
        use std::os::unix::fs::FileTypeExt;
        ensure!(
            socket.is_absolute() && socket.as_os_str().len() < 104,
            "socket_directory_permissions"
        );
        for parent in socket.ancestors().skip(1) {
            let info = std::fs::symlink_metadata(parent)?;
            let sticky = info.uid() == 0 && info.mode() & 0o1000 != 0;
            ensure!(
                info.is_dir()
                    && (info.uid() == uid() || info.uid() == 0)
                    && (info.mode() & 0o022 == 0 || sticky),
                "socket_directory_permissions"
            );
        }
        let parent = std::fs::metadata(socket.parent().context("socket_parent_missing")?)?;
        ensure!(parent.uid() == uid(), "socket_directory_permissions");
        if let Ok(info) = std::fs::symlink_metadata(socket) {
            ensure!(
                info.file_type().is_socket() && info.uid() == uid(),
                "network_stale_socket_denied"
            );
            let old: SocketOwner = serde_json::from_slice(&service::private_read(
                &state.join("listener.json"),
                8192,
            )?)?;
            ensure!(
                old.socket == socket
                    && old.device == info.dev()
                    && old.inode == info.ino()
                    && !owner_alive(&old)?,
                "network_stale_socket_unproven"
            );
            std::fs::remove_file(socket)?;
        }
        // Publish the durable owner proof before the public socket appears.
        // A death before rename leaves only a private, unadvertised temporary;
        // a death after rename leaves a fully proven stale socket for restart.
        let temporary_socket = socket
            .parent()
            .context("socket_parent_missing")?
            .join(format!(".{}", &Uuid::new_v4().simple().to_string()[..8]));
        let listener = UnixListener::bind(&temporary_socket)?;
        let info = std::fs::symlink_metadata(&temporary_socket)?;
        let owner = SocketOwner {
            pid: std::process::id(),
            start_ticks: start_ticks(std::process::id())?,
            boot_id: boot_id()?,
            executable: std::env::current_exe()?,
            socket: temporary_socket.clone(),
            device: info.dev(),
            inode: info.ino(),
        };
        let mut server = Self {
            listener,
            state: state.to_owned(),
            owner,
        };
        std::fs::set_permissions(&temporary_socket, std::fs::Permissions::from_mode(0o660))?;
        let mut published_owner = server.owner.clone();
        published_owner.socket = socket.to_owned();
        let record = state.join("listener.json");
        let temporary = state.join(format!(".listener-{}", Uuid::new_v4()));
        service::exclusive_write(&temporary, &serde_json::to_vec(&published_owner)?)?;
        std::fs::rename(temporary, record)?;
        std::fs::File::open(state)?.sync_all()?;
        let from = CString::new(temporary_socket.as_os_str().as_encoded_bytes())?;
        let to = CString::new(socket.as_os_str().as_encoded_bytes())?;
        // SAFETY: both NUL-terminated paths remain valid during the syscall.
        ensure!(
            unsafe {
                libc::renameat2(
                    libc::AT_FDCWD,
                    from.as_ptr(),
                    libc::AT_FDCWD,
                    to.as_ptr(),
                    libc::RENAME_NOREPLACE,
                )
            } == 0,
            "network_socket_publish_denied"
        );
        server.owner = published_owner;
        std::fs::File::open(socket.parent().context("socket_parent_missing")?)?.sync_all()?;
        Ok(server)
    }
}
impl Drop for OwnedSocket {
    fn drop(&mut self) {
        if let Ok(info) = std::fs::symlink_metadata(&self.owner.socket)
            && info.uid() == uid()
            && info.dev() == self.owner.device
            && info.ino() == self.owner.inode
        {
            let _ = std::fs::remove_file(&self.owner.socket);
            let _ = std::fs::remove_file(self.state.join("listener.json"));
        }
    }
}

fn hello(config: &EntryConfig, available: bool) -> Result<SourceHello> {
    Ok(SourceHello {
        protocol: 2,
        generation: config.generation,
        source_host: config.source_host,
        key_id: config.transport_key,
        hub: config.hub,
        service_uid: uid(),
        boot_id: boot_id()?,
        available,
    })
}

/// Forge initiates one pinned SSH channel. Only an attested source may submit frames.
pub fn connect_source(state: &Path, configuration: &Path) -> Result<()> {
    let initial = source_config(configuration)?;
    let _owner = instance_lock(state)?;
    let command = ssh(&initial.transport);
    // Explicit bind permits an exact from= policy, including the offline two-IP fixture.
    let mut args: Vec<_> = command.get_args().map(OsStr::to_os_string).collect();
    // Insert SSH options before the -- host separator, never after the remote command.
    let end = args
        .iter()
        .position(|arg| arg == "--")
        .context("network_ssh_options_invalid")?;
    args.splice(
        end..end,
        [
            format!("-oBindAddress={}", initial.source_address).into(),
            format!(
                "-oHostKeyAlias=factory-source-{}",
                initial.transport.source_host
            )
            .into(),
            "-oServerAliveInterval=15".into(),
            "-oServerAliveCountMax=3".into(),
        ],
    );
    // First-value SSH options win: remove the legacy keepalive/alias entries.
    let old_alias = format!("-oHostKeyAlias=factory-hub-{}", initial.transport.hub);
    args.retain(|arg| {
        arg != "-oServerAliveInterval=1"
            && arg != "-oServerAliveCountMax=1"
            && arg.as_os_str() != OsStr::new(&old_alias)
    });
    let mut command = Command::new(SSH);
    command
        .env_clear()
        .env("PATH", "/usr/bin:/bin")
        .env("LC_ALL", "C")
        .args(args)
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::null());
    let mut child = OwnedSsh(command.spawn()?);
    let mut input = child
        .0
        .stdin
        .take()
        .context("network_child_input_missing")?;
    let mut output = child
        .0
        .stdout
        .take()
        .context("network_child_output_missing")?;
    let mut frames = Frames::default();
    let greeting: SourceHello =
        serde_json::from_slice(&frames.read(&mut output, Instant::now() + TIMEOUT)?)
            .map_err(|_| anyhow::anyhow!("network_source_hello_invalid"))?;
    ensure!(
        greeting.protocol == 2
            && greeting.generation == initial.generation
            && greeting.source_host == initial.transport.source_host
            && greeting.key_id == initial.transport.transport_key
            && greeting.hub == initial.transport.hub
            && greeting.service_uid == uid()
            && !greeting.boot_id.is_nil()
            && greeting.available,
        "network_source_identity_denied"
    );
    println!(
        "{}",
        json!({"ready":true,"pid":std::process::id(),"transport":"forge_initiated_openssh", "generation":initial.generation})
    );
    std::io::stdout().flush()?;
    loop {
        if frames.pending.is_empty() {
            ready(
                output.as_raw_fd(),
                libc::POLLIN,
                Instant::now() + Duration::from_secs(60),
            )
            .or_else(|error| {
                if error.to_string() == "network_timeout" {
                    Ok(())
                } else {
                    Err(error)
                }
            })?;
            // Idle has no request deadline and creates no model work. Only read when data/EOF is ready.
            let mut descriptor = libc::pollfd {
                fd: output.as_raw_fd(),
                events: libc::POLLIN,
                revents: 0,
            };
            // SAFETY: one valid descriptor is borrowed for this nonblocking readiness check.
            if unsafe { libc::poll(&mut descriptor, 1, 0) } == 0 {
                continue;
            }
        }
        let deadline = Instant::now() + TIMEOUT;
        let request: SourceFrame = serde_json::from_slice(&frames.read(&mut output, deadline)?)
            .map_err(|_| anyhow::anyhow!("network_frame_invalid"))?;
        let current = source_config(configuration)?;
        ensure!(
            current == initial
                && request.protocol == 2
                && !request.request_id.is_nil()
                && request.generation == initial.generation
                && request.source_host == initial.transport.source_host
                && request.key_id == initial.transport.transport_key,
            "network_host_binding_denied"
        );
        let result = if initial
            .transport
            .allowed_agent_uids
            .contains(&request.peer_uid)
        {
            service::remote_frame(
                &initial.service_state,
                initial.transport.source_host,
                initial.transport.transport_key,
                request.peer_uid,
                request.wire,
                deadline,
            )
        } else {
            Err(anyhow::anyhow!("network_peer_uid_denied"))
        };
        let returned = SourceReply {
            protocol: 2,
            generation: initial.generation,
            request_id: request.request_id,
            source_host: initial.transport.source_host,
            key_id: initial.transport.transport_key,
            response: response(result),
        };
        service::fixture_fault(&initial.service_state, "transport_before_reply")?;
        ensure!(
            serde_json::to_vec(&returned.response)?.len() < protocol::MAX_FRAME,
            "network_response_bound"
        );
        send_frame(&mut input, &returned, deadline)?;
    }
}

/// sshd launches this as the service UID through the root-controlled protocol-only entry.
pub fn source_entry(configuration: &Path) -> Result<()> {
    ensure!(
        std::env::var("SSH_ORIGINAL_COMMAND").as_deref() == Ok(COMMAND)
            && std::env::var_os("SSH_CONNECTION").is_some()
            && std::env::var_os("SSH_TTY").is_none(),
        "network_forced_command_required"
    );
    let initial = entry_config(configuration)?;
    let mut input = std::io::stdin().lock();
    let mut output = std::io::stdout().lock();
    let _owner = match instance_lock(&initial.state) {
        Ok(lock) => lock,
        Err(error) if error.to_string() == "network_source_already_running" => {
            send_frame(
                &mut output,
                &hello(&initial, false)?,
                Instant::now() + TIMEOUT,
            )?;
            return Ok(());
        }
        Err(error) => return Err(error),
    };
    let server = OwnedSocket::bind(&initial.state, &initial.socket)?;
    send_frame(
        &mut output,
        &hello(&initial, true)?,
        Instant::now() + TIMEOUT,
    )?;
    let mut frames = Frames::default();
    loop {
        let mut fds = [
            libc::pollfd {
                fd: server.listener.as_raw_fd(),
                events: libc::POLLIN,
                revents: 0,
            },
            libc::pollfd {
                fd: input.as_raw_fd(),
                events: libc::POLLIN,
                revents: 0,
            },
        ];
        // SAFETY: both borrowed descriptors remain open while the source listener lives.
        let count = unsafe { libc::poll(fds.as_mut_ptr(), 2, -1) };
        if count < 0 && std::io::Error::last_os_error().kind() == std::io::ErrorKind::Interrupted {
            continue;
        }
        ensure!(count > 0, "network_io_unavailable");
        if fds[1].revents & libc::POLLHUP != 0 {
            return Ok(());
        }
        ensure!(fds[1].revents == 0, "network_channel_closed");
        let (mut stream, _) = server.listener.accept()?;
        stream.set_read_timeout(Some(TIMEOUT))?;
        stream.set_write_timeout(Some(TIMEOUT))?;
        let deadline = Instant::now() + TIMEOUT;
        let request = (|| -> Result<(u32, Value)> {
            ensure!(
                entry_config(configuration)? == initial,
                "network_generation_changed"
            );
            let peer = service::peer_uid(&stream)?;
            let bytes = line(&mut stream, protocol::MAX_FRAME, deadline)?;
            ensure!(
                initial.allowed_agent_uids.contains(&peer),
                "network_peer_uid_denied"
            );
            let wire: Value =
                serde_json::from_slice(&bytes).map_err(|_| anyhow::anyhow!("invalid_request"))?;
            if wire["method"] == "report_presence" {
                let report: observation::PresenceReport =
                    serde_json::from_value(wire["report"].clone())
                        .map_err(|_| anyhow::anyhow!("invalid_request"))?;
                ensure!(
                    observation::process_health(&report.process, peer)? == "alive",
                    "observation_process_unproven"
                );
            }
            Ok((peer, wire))
        })();
        let (peer, wire) = match request {
            Ok(value) => value,
            Err(error) => {
                stream.write_all(
                    &[serde_json::to_vec(&response(Err(error)))?, b"\n".to_vec()].concat(),
                )?;
                continue;
            }
        };
        let id = Uuid::new_v4();
        let request = SourceFrame {
            protocol: 2,
            generation: initial.generation,
            request_id: id,
            source_host: initial.source_host,
            key_id: initial.transport_key,
            peer_uid: peer,
            wire,
        };
        let returned = (|| -> Result<Value> {
            send_frame(&mut output, &request, deadline)?;
            let reply: SourceReply = serde_json::from_slice(&frames.read(&mut input, deadline)?)
                .map_err(|_| anyhow::anyhow!("network_frame_invalid"))?;
            ensure!(
                reply.protocol == 2
                    && reply.generation == initial.generation
                    && reply.request_id == id
                    && reply.source_host == initial.source_host
                    && reply.key_id == initial.transport_key,
                "network_reply_mismatch"
            );
            ensure!(
                serde_json::to_vec(&reply.response)?.len() < protocol::MAX_FRAME,
                "network_response_bound"
            );
            Ok(reply.response)
        })();
        let fatal = returned.is_err();
        let mut bytes = serde_json::to_vec(&if fatal {
            response(Err(anyhow::anyhow!("network_transport_unavailable")))
        } else {
            response(returned)
        })?;
        bytes.push(b'\n');
        let _ = stream.write_all(&bytes);
        ensure!(!fatal, "network_transport_unavailable");
    }
}

#[cfg(test)]
mod source_tests {
    use super::*;
    use std::os::unix::net::UnixStream;

    struct Temporary(PathBuf);
    impl Temporary {
        fn new() -> Self {
            let path = std::env::temp_dir().join(format!("t-{}", Uuid::new_v4()));
            std::fs::create_dir(&path).unwrap();
            std::fs::set_permissions(&path, std::fs::Permissions::from_mode(0o700)).unwrap();
            Self(path)
        }
    }
    impl Drop for Temporary {
        fn drop(&mut self) {
            let _ = std::fs::remove_dir_all(&self.0);
        }
    }

    #[test]
    fn standing_frames_preserve_coalesced_messages() {
        let (mut sender, mut receiver) = UnixStream::pair().unwrap();
        sender
            .write_all(b"{\"hello\":1}\n{\"request\":2}\n")
            .unwrap();
        let mut frames = Frames::default();
        assert_eq!(
            frames
                .read(&mut receiver, Instant::now() + TIMEOUT)
                .unwrap(),
            b"{\"hello\":1}\n"
        );
        assert_eq!(
            frames
                .read(&mut receiver, Instant::now() + TIMEOUT)
                .unwrap(),
            b"{\"request\":2}\n"
        );
        assert!(frames.pending.is_empty());
    }

    #[test]
    fn standing_frame_bounds_reject_oversized_buffer_before_waiting() {
        let (_sender, mut receiver) = UnixStream::pair().unwrap();
        let mut frames = Frames {
            pending: vec![b'x'; protocol::MAX_FRAME + 513],
        };
        assert!(
            frames
                .read(&mut receiver, Instant::now() + TIMEOUT)
                .unwrap_err()
                .to_string()
                .contains("network_frame_bound")
        );
    }

    #[test]
    fn source_reply_unknown_fields_are_not_accepted() {
        let reply = json!({"protocol":2,"generation":Uuid::new_v4(),"requestId":Uuid::new_v4(),
            "sourceHost":Uuid::new_v4(),"keyId":Uuid::new_v4(),"response":{"ok":true},"peerUid":0});
        assert!(serde_json::from_value::<SourceReply>(reply).is_err());
    }

    #[test]
    fn duplicate_instance_lock_refuses_before_socket_work() {
        let root = Temporary::new();
        let _one = instance_lock(&root.0).unwrap();
        assert!(
            instance_lock(&root.0)
                .unwrap_err()
                .to_string()
                .contains("network_source_already_running")
        );
    }

    #[test]
    fn stale_socket_requires_dead_incarnation_and_matching_inode() {
        let root = Temporary::new();
        let socket = root.0.join("sock");
        let server = OwnedSocket::bind(&root.0, &socket).unwrap();
        let fd = server.listener.as_raw_fd();
        let mut old: SocketOwner = serde_json::from_slice(
            &service::private_read(&root.0.join("listener.json"), 8192).unwrap(),
        )
        .unwrap();
        old.executable = PathBuf::from("/different-or-deleted-executable");
        assert!(owner_alive(&old).unwrap());
        std::mem::forget(server);
        // SAFETY: ownership was deliberately forgotten above; close the fixture listener once.
        assert_eq!(unsafe { libc::close(fd) }, 0);
        assert!(OwnedSocket::bind(&root.0, &socket).is_err());
        old.boot_id = Uuid::new_v4();
        old.inode += 1;
        let record = root.0.join("listener.json");
        std::fs::write(&record, serde_json::to_vec(&old).unwrap()).unwrap();
        assert!(OwnedSocket::bind(&root.0, &socket).is_err());
        old.inode -= 1;
        std::fs::write(&record, serde_json::to_vec(&old).unwrap()).unwrap();
        let replacement = OwnedSocket::bind(&root.0, &socket).unwrap();
        assert!(socket.exists());
        drop(replacement);
        assert!(!socket.exists());
    }
}
