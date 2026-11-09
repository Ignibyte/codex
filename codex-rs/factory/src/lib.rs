//! Factory authority contracts, independent of model login and local child ownership.

pub mod protocol;
#[cfg(target_os = "linux")]
pub mod service;

#[cfg(target_os = "linux")]
pub mod client;

#[cfg(target_os = "linux")]
pub mod recording;

#[cfg(target_os = "linux")]
pub mod observation;

#[cfg(target_os = "linux")]
pub mod observation_mcp;

#[cfg(target_os = "linux")]
pub mod network;

#[cfg(target_os = "linux")]
pub mod foreman;

#[cfg(target_os = "linux")]
pub mod workflow_mcp;

#[cfg(target_os = "linux")]
pub mod manager;

#[cfg(target_os = "linux")]
pub mod manager_mcp;

#[cfg(target_os = "linux")]
mod connections;

#[cfg(target_os = "linux")]
pub mod support;
