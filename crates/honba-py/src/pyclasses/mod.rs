//! Rust-backed Python classes exposed to the embedded interpreter.
//!
//! Strategy: keep the SimObject tree and event loop in Python, but back the
//! leaf domain types with real Rust structs so they carry honba-messages data.

use pyo3::prelude::*;
use pyo3::types::PyModule;

pub mod api;
pub mod codegen;
pub mod domain;
pub mod manifest;
pub mod next_open;
pub mod risk;
pub mod run;
pub mod strategy;
pub mod wire;

pub fn register(m: &Bound<'_, PyModule>) -> PyResult<()> {
    api::register(m)?;
    codegen::register(m)?;
    domain::register(m)?;
    manifest::register(m)?;
    next_open::register(m)?;
    risk::register(m)?;
    run::register(m)?;
    strategy::register(m)?;
    wire::register(m)?;
    Ok(())
}
