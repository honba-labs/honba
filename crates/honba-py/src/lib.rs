use pyo3::prelude::*;

pub mod pyclasses;

pub const HONBA_BRIDGE: &str = include_str!("honba_bridge.py");

/// Root PyO3 module exposing `honba` (and `honba._honba`).
#[pymodule]
pub fn _honba(m: &Bound<'_, PyModule>) -> PyResult<()> {
    pyclasses::register(m)?;
    Ok(())
}

/// Convenience initialization for embedded use
pub fn register_embedded(m: &Bound<'_, PyModule>) -> PyResult<()> {
    pyclasses::register(m)?;
    Ok(())
}
