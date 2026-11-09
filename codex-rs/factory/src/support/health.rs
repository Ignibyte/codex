//! Fixed read-only probes. A missing reading is never a clean zero.
use crate::service;
use anyhow::Result;
use anyhow::ensure;
use serde::Deserialize;
use serde::Serialize;
use std::collections::BTreeSet;
use std::ffi::CString;
use std::io::Read;
use std::os::unix::ffi::OsStrExt;
use std::path::PathBuf;

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct Thresholds {
    pub warning: u16,
    pub critical: u16,
}
impl Thresholds {
    fn validate(&self) -> Result<()> {
        ensure!(
            self.warning > 0 && self.warning < self.critical && self.critical <= 10000,
            "support_thresholds_invalid"
        );
        Ok(())
    }
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct Disk {
    pub label: String,
    pub path: PathBuf,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct Config {
    pub disks: Vec<Disk>,
    pub usage: Thresholds,
    pub pressure: Thresholds,
}
impl Config {
    pub fn validate(&self) -> Result<()> {
        self.usage.validate()?;
        self.pressure.validate()?;
        let mut labels = BTreeSet::new();
        ensure!(self.disks.len() <= 8, "support_probe_bound");
        for disk in &self.disks {
            ensure!(
                !disk.label.is_empty()
                    && disk.label.len() <= 48
                    && disk
                        .label
                        .bytes()
                        .all(|c| c.is_ascii_alphanumeric() || c == b'_')
                    && labels.insert(&disk.label)
                    && disk.path.is_absolute(),
                "support_disk_scope_invalid"
            );
        }
        Ok(())
    }
}

#[derive(Clone, Copy, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum State {
    Ok,
    Warning,
    Critical,
    Unavailable,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct Metric {
    pub key: String,
    pub basis_points: Option<u16>,
    pub state: State,
}
fn metric(key: String, value: Option<u16>, thresholds: &Thresholds) -> Metric {
    let state = match value {
        None => State::Unavailable,
        Some(v) if v >= thresholds.critical => State::Critical,
        Some(v) if v >= thresholds.warning => State::Warning,
        Some(_) => State::Ok,
    };
    Metric {
        key,
        basis_points: value,
        state,
    }
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct Snapshot {
    pub read_at: u64,
    pub metrics: Vec<Metric>,
}

fn used(total: u64, available: u64) -> Option<u16> {
    if total == 0 || available > total {
        return None;
    }
    Some((((total - available) as u128 * 10000) / total as u128) as u16)
}

fn fixed_read(path: &str) -> Option<String> {
    let mut bytes = Vec::new();
    std::fs::File::open(path)
        .ok()?
        .take(65537)
        .read_to_end(&mut bytes)
        .ok()?;
    if bytes.len() > 65536 {
        return None;
    }
    String::from_utf8(bytes).ok()
}

/// Reads mount statistics and fixed kernel files. No commands, host mutation or inference.
pub fn read(config: &Config) -> Result<Snapshot> {
    config.validate()?;
    let mut metrics = Vec::new();
    for disk in &config.disks {
        let data = CString::new(disk.path.as_os_str().as_bytes())
            .ok()
            .and_then(|path| {
                let mut data = std::mem::MaybeUninit::<libc::statvfs>::uninit();
                // SAFETY: path is NUL-terminated and data points to writable statvfs storage.
                if unsafe { libc::statvfs(path.as_ptr(), data.as_mut_ptr()) } != 0 {
                    return None;
                }
                // SAFETY: statvfs initialized the complete structure on success.
                Some(unsafe { data.assume_init() })
            });
        metrics.push(metric(
            format!("disk.{}.blocks", disk.label),
            data.as_ref().and_then(|v| used(v.f_blocks, v.f_bavail)),
            &config.usage,
        ));
        metrics.push(metric(
            format!("disk.{}.inodes", disk.label),
            data.as_ref().and_then(|v| used(v.f_files, v.f_favail)),
            &config.usage,
        ));
    }
    let memory = fixed_read("/proc/meminfo");
    let number = |key: &str| {
        memory.as_ref().and_then(|text| {
            text.lines().find_map(|line| {
                let mut fields = line.split_whitespace();
                (fields.next()? == key)
                    .then(|| fields.next()?.parse::<u64>().ok())
                    .flatten()
            })
        })
    };
    metrics.push(metric(
        "memory.used".into(),
        number("MemTotal:")
            .zip(number("MemAvailable:"))
            .and_then(|(total, available)| used(total, available)),
        &config.usage,
    ));
    let pressure = fixed_read("/proc/pressure/memory").and_then(|text| {
        let line = text.lines().find(|line| line.starts_with("full "))?;
        let value = line
            .split_whitespace()
            .find_map(|part| part.strip_prefix("avg300="))?
            .parse::<f64>()
            .ok()?;
        (value.is_finite() && (0.0..=100.0).contains(&value)).then_some((value * 100.0) as u16)
    });
    metrics.push(metric(
        "memory.pressure_full_avg300".into(),
        pressure,
        &config.pressure,
    ));
    Ok(Snapshot {
        read_at: service::now()?,
        metrics,
    })
}
