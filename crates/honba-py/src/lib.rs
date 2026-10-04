use pyo3::prelude::*;

pub mod pyclasses;

pub const HONBA_BRIDGE: &str = include_str!("honba_bridge.py");

/// Root PyO3 module exposing `honba` (and `honba._honba`).
#[pymodule]
pub fn _honba(m: &Bound<'_, PyModule>) -> PyResult<()> {
    pyclasses::register(m)?;
    m.add_function(wrap_pyfunction!(initialize_runtime, m)?)?;
    m.add_function(wrap_pyfunction!(get_runtime_handle, m)?)?;
    Ok(())
}

/// Convenience initialization for embedded use
pub fn register_embedded(m: &Bound<'_, PyModule>) -> PyResult<()> {
    pyclasses::register(m)?;
    m.add_function(wrap_pyfunction!(initialize_runtime, m)?)?;
    m.add_function(wrap_pyfunction!(get_runtime_handle, m)?)?;
    Ok(())
}

#[cfg(test)]
mod tests;

/// Initialize the Tokio runtime for Python async bridge.
#[pyfunction]
pub fn initialize_runtime() -> PyResult<()> {
    let rt = Box::leak(Box::new(tokio::runtime::Builder::new_multi_thread().enable_all().build().unwrap()));
    pyo3_async_runtimes::tokio::init_with_runtime(rt)
        .map_err(|_| pyo3::exceptions::PyRuntimeError::new_err("failed to initialize tokio runtime"))?;
    Ok(())
}

/// Get a handle to the current runtime if initialized.
#[pyfunction]
pub fn get_runtime_handle() -> PyResult<String> {
    Ok("tokio-multi-thread".to_string())
}
