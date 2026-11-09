//! Operator recovery. Archives contain facts, never executable authority.
use super::*;

#[derive(Deserialize, Serialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct Epoch {
    schema: u32,
    boot: Uuid,
    recovery: Uuid,
}

#[derive(Deserialize, Serialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct History {
    schema: u32,
    source_host: Uuid,
    created_at: u64,
    seats: Vec<protocol::Grant>,
    mail: BTreeMap<Uuid, Mail>,
    recording: Vec<crate::recording::ArchiveEntry>,
    observation: crate::observation::Archive,
}

#[derive(Deserialize, Serialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct Archive {
    schema: u32,
    sha256: String,
    history: History,
}

fn boot() -> Result<Uuid> {
    Ok(Uuid::parse_str(
        std::fs::read_to_string("/proc/sys/kernel/random/boot_id")?.trim(),
    )?)
}

fn epoch(state: &Path) -> Result<Option<Epoch>> {
    if !state.join("lifecycle.json").try_exists()? {
        return Ok(None);
    }
    let epoch: Epoch = serde_json::from_slice(&private_read(&state.join("lifecycle.json"), 1024)?)?;
    ensure!(
        epoch.schema == 1 && !epoch.recovery.is_nil(),
        "recovery_epoch_invalid"
    );
    Ok(Some(epoch))
}

fn replace(state: &Path, name: &str, bytes: &[u8]) -> Result<()> {
    let temporary = state.join(format!(".recovery-{}", Uuid::new_v4()));
    let result = (|| {
        exclusive_write(&temporary, bytes)?;
        std::fs::rename(&temporary, state.join(name))?;
        File::open(state)?.sync_all()?;
        Ok(())
    })();
    if temporary.exists() {
        let _ = std::fs::remove_file(temporary);
    }
    result
}

fn fence(registry: &mut Registry) -> (usize, usize) {
    registry.bootstraps.clear();
    for seat in registry.seats.values_mut() {
        seat.revoked = true;
        seat.previous = None;
        seat.nonce.clear();
    }
    for host in registry.remote_hosts.values_mut() {
        host.revoked = true;
        host.previous_key = None;
    }
    let mut pending = 0;
    let mut active = 0;
    for mail in registry.mail.values_mut() {
        if mail.state == "service_accepted" {
            mail.state = "quarantined".into();
            pending += 1;
        } else if mail.state == "delivered" || mail.state == "consumed" {
            mail.reconcile_required = true;
            active += 1;
        }
    }
    (pending, active)
}

pub(crate) fn check_boot(state: &Path) -> Result<()> {
    if let Some(epoch) = epoch(state)? {
        ensure!(epoch.boot == boot()?, "reboot_requires_service_recovery");
    }
    Ok(())
}

/// Caller holds the registry lock. No request can race the durable authority fence.
pub(crate) fn prepare_locked(state: &Path) -> Result<()> {
    let current = boot()?;
    if let Some(epoch) = epoch(state)? {
        if epoch.boot == current {
            return Ok(());
        }
        let mut registry = load(state)?;
        let (pending, active) = fence(&mut registry);
        audit(
            &mut registry,
            json!({"kind":"boot_recovery","at":now()?,
            "previousBoot":epoch.boot,"boot":current,"pendingQuarantined":pending,
            "activeRequiresReconciliation":active,"completedEffects":"preserved"}),
        );
        // Persist revocation first. A crash before the epoch write repeats only safe fencing.
        save(state, &registry)?;
        fixture_fault(state, "after_recovery_fence")?;
        replace(state, "service.key", &random()?)?;
        crate::observation::recover(&state.join("observation"), "host_reboot")?;
        fixture_fault(state, "before_recovery_epoch")?;
    }
    replace(
        state,
        "lifecycle.json",
        &serde_json::to_vec(&Epoch {
            schema: 1,
            boot: current,
            recovery: Uuid::new_v4(),
        })?,
    )
}

/// A consistent, bounded JSON archive. Its checksum detects corruption, not a trusted issuer.
pub fn backup(state: &Path, output: &Path) -> Result<Value> {
    let _lock = lock(state)?;
    check_boot(state)?;
    let registry = load(state)?;
    let mut history = History {
        schema: 1,
        source_host: registry.host.context("registry_uninitialized")?,
        created_at: now()?,
        seats: registry
            .seats
            .into_values()
            .map(|seat| seat.grant)
            .collect(),
        mail: registry.mail,
        recording: crate::recording::archive(&state.join("recording"))?,
        observation: crate::observation::archive(&state.join("observation"))?,
    };
    if state.join("recovery-history.json").try_exists()? {
        let earlier: Archive = serde_json::from_slice(&private_read(
            &state.join("recovery-history.json"),
            8 * 1024 * 1024,
        )?)?;
        let known: std::collections::BTreeSet<Uuid> = history
            .recording
            .iter()
            .map(|entry| entry.event_id)
            .collect();
        history.recording.extend(
            earlier
                .history
                .recording
                .into_iter()
                .filter(|entry| !known.contains(&entry.event_id)),
        );
        ensure!(
            history.recording.len() <= 1024,
            "archive_recording_capacity"
        );
        history.observation.merge(earlier.history.observation)?;
    }
    let checksum = hex(&Sha256::digest(serde_json::to_vec(&history)?));
    let bytes = serde_json::to_vec(&Archive {
        schema: 1,
        sha256: checksum.clone(),
        history,
    })?;
    ensure!(bytes.len() <= 8 * 1024 * 1024, "backup_capacity");
    exclusive_write(output, &bytes)?;
    File::open(output.parent().context("output_parent_missing")?)?.sync_all()?;
    Ok(json!({"ok":true,"archive":output,"sha256":checksum,
        "secretsIncluded":false,"restorePolicy":"new_host_revoked_seats_no_replay"}))
}

/// Explicit replacement: a fresh destination, host and signing key. Never merge live state.
pub fn restore(input: &Path, destination: &Path) -> Result<Value> {
    ensure!(destination.is_absolute(), "restore_path_absolute_required");
    ensure!(
        std::fs::symlink_metadata(destination)
            .is_err_and(|error| error.kind() == std::io::ErrorKind::NotFound),
        "restore_destination_exists"
    );
    let parent = destination.parent().context("restore_parent_missing")?;
    let info = std::fs::symlink_metadata(parent)?;
    ensure!(
        info.is_dir() && info.uid() == uid() && info.mode() & 0o077 == 0,
        "restore_parent_permissions"
    );
    let archive: Archive = serde_json::from_slice(&private_read(input, 8 * 1024 * 1024)?)?;
    ensure!(
        archive.schema == 1 && archive.history.schema == 1,
        "backup_schema_unsupported"
    );
    ensure!(
        archive.sha256 == hex(&Sha256::digest(serde_json::to_vec(&archive.history)?)),
        "backup_checksum_mismatch"
    );
    ensure!(
        archive.history.seats.len() <= 128 && archive.history.mail.len() <= 512,
        "backup_capacity"
    );
    for grant in &archive.history.seats {
        grant.validate()?;
    }
    for (id, mail) in &archive.history.mail {
        ensure!(
            *id == mail.envelope.message_id && mail.receipts.len() <= 16,
            "backup_mail_invalid"
        );
    }
    let temporary = parent.join(format!(".restore-{}", Uuid::new_v4()));
    let host = Uuid::new_v4();
    let result = (|| {
        private_state(&temporary)?;
        let mut registry = load(&temporary)?;
        registry.host = Some(host);
        registry.mail = archive.history.mail.clone();
        for grant in &archive.history.seats {
            ensure!(
                !registry.seats.contains_key(&grant.seat),
                "backup_seat_duplicate"
            );
            registry.seats.insert(
                grant.seat,
                Seat {
                    grant: grant.clone(),
                    nonce: String::new(),
                    expires_at: 0,
                    last_sequence: 0,
                    previous: None,
                    revoked: true,
                },
            );
        }
        let (pending, active) = fence(&mut registry);
        audit(
            &mut registry,
            json!({"kind":"restored_archive","at":now()?,
            "sourceHost":archive.history.source_host,"host":host,"archiveSha256":archive.sha256,
            "pendingQuarantined":pending,"activeRequiresReconciliation":active,
            "historyAuthority":"archive_only","completedEffects":"preserved"}),
        );
        save(&temporary, &registry)?;
        exclusive_write(&temporary.join("service.key"), &random()?)?;
        // Archival recording/observation facts never enter an executable courier or presence lease.
        exclusive_write(
            &temporary.join("recovery-history.json"),
            &serde_json::to_vec(&archive)?,
        )?;
        prepare_locked(&temporary)?;
        // renameat2 NOREPLACE prevents a concurrent installer from replacing a fresh destination.
        let source = std::ffi::CString::new(temporary.as_os_str().as_encoded_bytes())?;
        let target = std::ffi::CString::new(destination.as_os_str().as_encoded_bytes())?;
        // SAFETY: both strings remain valid for this syscall, and no raw descriptor is borrowed.
        ensure!(
            unsafe {
                libc::renameat2(
                    libc::AT_FDCWD,
                    source.as_ptr(),
                    libc::AT_FDCWD,
                    target.as_ptr(),
                    libc::RENAME_NOREPLACE,
                )
            } == 0,
            "restore_destination_race"
        );
        File::open(parent)?.sync_all()?;
        Ok(
            json!({"ok":true,"host":host,"sourceHost":archive.history.source_host,
            "state":destination,"seatsRevoked":registry.seats.len(),"pendingQuarantined":pending,
            "activeRequiresReconciliation":active,"automaticReplay":false}),
        )
    })();
    if temporary.exists() {
        let _ = std::fs::remove_dir_all(temporary);
    }
    result
}

pub fn status(state: &Path) -> Result<Value> {
    let _lock = lock(state)?;
    let epoch = epoch(state)?;
    let archive = if state.join("recovery-history.json").try_exists()? {
        let archive: Archive = serde_json::from_slice(&private_read(
            &state.join("recovery-history.json"),
            8 * 1024 * 1024,
        )?)?;
        Some(
            json!({"sourceHost":archive.history.source_host,"createdAt":archive.history.created_at,
            "mail":archive.history.mail.len(),"recording":archive.history.recording,
            "observation":archive.history.observation,"authority":"archive_only"}),
        )
    } else {
        None
    };
    Ok(json!({"ok":true,"epoch":epoch,"currentBoot":boot()?,"archive":archive}))
}
