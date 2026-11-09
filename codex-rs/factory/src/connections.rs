//! Bounded local request owners. External I/O cannot hold the single accept loop.
use anyhow::{Context, Result, bail, ensure};
use std::io::Write;
use std::os::fd::AsRawFd;
use std::os::unix::net::{UnixListener, UnixStream};
use std::sync::{Mutex, mpsc};
use std::time::Duration;

const WORKERS: usize = 16;
const QUEUED: usize = 16;

pub(crate) fn serve(
    listener: &UnixListener,
    handler: impl Fn(UnixStream) -> Result<()> + Sync,
) -> Result<()> {
    listener.set_nonblocking(true)?;
    let (sender, receiver) = mpsc::sync_channel::<UnixStream>(QUEUED);
    let receiver = Mutex::new(receiver);
    let failure: Mutex<Option<String>> = Mutex::new(None);
    std::thread::scope(|scope| {
        for index in 0..WORKERS {
            let (receiver, failure, handler) = (&receiver, &failure, &handler);
            std::thread::Builder::new()
                .name(format!("factory-request-{index}"))
                .spawn_scoped(scope, move || {
                    let result = (|| -> Result<()> {
                        loop {
                            let stream = receiver
                                .lock()
                                .map_err(|_| anyhow::anyhow!("connection_queue_poisoned"))?
                                .recv();
                            let Ok(stream) = stream else {
                                break;
                            };
                            if failure
                                .lock()
                                .map_err(|_| anyhow::anyhow!("connection_owner_poisoned"))?
                                .is_some()
                            {
                                break;
                            }
                            handler(stream)?;
                        }
                        Ok(())
                    })();
                    if let Err(error) = result
                        && let Ok(mut slot) = failure.lock()
                    {
                        slot.get_or_insert_with(|| error.to_string());
                    }
                })?;
        }
        let result = (|| -> Result<()> {
            loop {
                if let Some(error) = failure
                    .lock()
                    .map_err(|_| anyhow::anyhow!("connection_owner_poisoned"))?
                    .as_ref()
                {
                    bail!("connection_worker_failed: {error}");
                }
                let mut poll = libc::pollfd {
                    fd: listener.as_raw_fd(),
                    events: libc::POLLIN,
                    revents: 0,
                };
                // SAFETY: poll references one writable descriptor entry for this bounded call.
                let ready = unsafe { libc::poll(&mut poll, 1, 50) };
                if ready < 0 {
                    let error = std::io::Error::last_os_error();
                    if error.kind() == std::io::ErrorKind::Interrupted {
                        continue;
                    }
                    return Err(error).context("connection_listener_poll_failed");
                }
                if ready == 0 {
                    continue;
                }
                ensure!(
                    poll.revents & libc::POLLIN != 0,
                    "connection_listener_unavailable"
                );
                let stream = match listener.accept() {
                    Ok((stream, _)) => stream,
                    Err(error) if error.kind() == std::io::ErrorKind::WouldBlock => continue,
                    Err(error) => return Err(error).context("connection_accept_failed"),
                };
                match sender.try_send(stream) {
                    Ok(()) => {}
                    Err(mpsc::TrySendError::Full(mut stream)) => {
                        let _ = stream.set_write_timeout(Some(Duration::from_millis(250)));
                        let _ = stream.write_all(b"{\"ok\":false,\"error\":\"service_busy\"}\n");
                    }
                    Err(mpsc::TrySendError::Disconnected(_)) => {
                        bail!("connection_workers_unavailable")
                    }
                }
            }
        })();
        // Closing the queue wakes idle owners. The scope joins every request owner.
        drop(sender);
        result
    })
}
