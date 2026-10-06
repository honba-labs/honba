//! The strategy manifest surface exposed to Python (plan.md E0-S8, ADR 0012).
//!
//! `verify_manifest` is the one compiler from a `StrategyManifest` to its
//! `StrategyIr`; Python's `honba.strategies.verify` and `honba verify` wrap it
//! rather than re-implementing the resolution rules.

// PyO3 0.22 `#[pyfunction]` expansion trips this lint on `PyResult` returns.
#![allow(clippy::useless_conversion)]

use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use pyo3::types::PyModule;

use honba_strategy::{StrategyIr, StrategyManifest, STRATEGY_API_VERSION};

/// Why `verify` failed: a stable code (`deserialize` or an `IrError` code) and
/// a human-readable message.
#[derive(Debug, PartialEq, Eq)]
pub struct VerifyFailure {
    pub code: &'static str,
    pub message: String,
}

/// Parses manifest JSON and compiles it; returns the IR as JSON.
pub fn verify(manifest_json: &str) -> Result<String, VerifyFailure> {
    let manifest: StrategyManifest =
        serde_json::from_str(manifest_json).map_err(|e| VerifyFailure {
            code: "deserialize",
            message: e.to_string(),
        })?;
    let ir = StrategyIr::compile(manifest).map_err(|e| VerifyFailure {
        code: e.code(),
        message: e.to_string(),
    })?;
    serde_json::to_string(&ir).map_err(|e| VerifyFailure {
        code: "serialize",
        message: e.to_string(),
    })
}

/// Compile a strategy manifest (JSON) into its IR (JSON).
///
/// Raises `ValueError("<code>: <message>")`, where `code` is `deserialize` or
/// the Rust `IrError::code()`.
#[pyfunction]
pub fn verify_manifest(manifest_json: &str) -> PyResult<String> {
    verify(manifest_json).map_err(|f| PyValueError::new_err(format!("{}: {}", f.code, f.message)))
}

pub fn register(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add("STRATEGY_API_VERSION", STRATEGY_API_VERSION)?;
    m.add_function(wrap_pyfunction!(verify_manifest, m)?)?;
    Ok(())
}
