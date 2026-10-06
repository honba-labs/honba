//! Codegen exposed to Python: `honba._honba.codegen_render`.
//!
//! Rust (`honba-codegen`) is the single owner of every generated artifact.
//! The Python `honba schema export` command writes what this returns rather
//! than building schemas from Python models, so it works from an installed
//! wheel (no cargo, no npx) and produces the same bytes as the Rust CLI.

// PyO3 0.22 `#[pyfunction]` expansion trips this lint on `PyResult` returns
// (same allowance as `wire.rs`).
#![allow(clippy::useless_conversion)]

use honba_codegen::{Artifact, Codegen};
use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use pyo3::types::PyModule;

/// The artifact names `render` accepts, in generation order.
pub fn artifact_names() -> Vec<&'static str> {
    Artifact::ALL.iter().map(|a| a.name()).collect()
}

/// Renders the artifact named `name` as `(file_name, content)`.
pub fn render(name: &str) -> Result<(&'static str, String), String> {
    let artifact = Artifact::from_name(name).ok_or_else(|| {
        format!(
            "unknown artifact {name:?}; expected one of {:?}",
            artifact_names()
        )
    })?;
    Ok((artifact.file_name(), Codegen::new().render(artifact)))
}

/// Python: `codegen_render(kind) -> (file_name, content)`.
#[pyfunction]
pub fn codegen_render(kind: &str) -> PyResult<(&'static str, String)> {
    render(kind).map_err(PyValueError::new_err)
}

/// Python: `codegen_artifacts() -> list[str]`.
#[pyfunction]
pub fn codegen_artifacts() -> Vec<&'static str> {
    artifact_names()
}

pub fn register(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(codegen_render, m)?)?;
    m.add_function(wrap_pyfunction!(codegen_artifacts, m)?)?;
    Ok(())
}
