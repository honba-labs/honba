//! Rust-backed Python classes exposed to the embedded interpreter.
//!
//! Strategy: keep the SimObject tree and event loop in Python, but back the
//! leaf domain types with real Rust structs so they carry honba-messages data.

use pyo3::prelude::*;
use pyo3::types::PyModule;

pub mod domain;
pub mod strategy;
pub mod wire;

pub fn register(m: &Bound<'_, PyModule>) -> PyResult<()> {
    domain::register(m)?;
    strategy::register(m)?;
    wire::register(m)?;
    Ok(())
}
