#![allow(clippy::useless_conversion)]

use pyo3::prelude::*;

pub mod pyclasses;
pub mod runtime;

pub const HONBA_BRIDGE: &str = include_str!("honba_bridge.py");

/// Root PyO3 module exposing `honba` (and `honba._honba`).
#[pymodule]
pub fn _honba(m: &Bound<'_, PyModule>) -> PyResult<()> {
    pyclasses::register(m)?;
    m.add_function(wrap_pyfunction!(initialize_runtime, m)?)?;
    m.add_function(wrap_pyfunction!(get_runtime_handle, m)?)?;
    m.add_function(wrap_pyfunction!(runtime_start, m)?)?;
    m.add_function(wrap_pyfunction!(runtime_stop, m)?)?;
    m.add_function(wrap_pyfunction!(runtime_info, m)?)?;
    Ok(())
}

/// Convenience initialization for embedded use
pub fn register_embedded(m: &Bound<'_, PyModule>) -> PyResult<()> {
    pyclasses::register(m)?;
    m.add_function(wrap_pyfunction!(initialize_runtime, m)?)?;
    m.add_function(wrap_pyfunction!(get_runtime_handle, m)?)?;
    m.add_function(wrap_pyfunction!(runtime_start, m)?)?;
    m.add_function(wrap_pyfunction!(runtime_stop, m)?)?;
    m.add_function(wrap_pyfunction!(runtime_info, m)?)?;
    Ok(())
}

#[cfg(test)]
mod tests;

/// Start the one async runtime of this interpreter: `(flavor, worker_threads, generation)`.
///
/// Raises `RuntimeError` if a runtime is already running, `ValueError` for 0 worker threads.
/// Python code should go through `honba.event_loop`, which owns the lifecycle.
#[pyfunction]
#[pyo3(signature = (worker_threads=None))]
pub fn runtime_start(worker_threads: Option<usize>) -> PyResult<(String, usize, u64)> {
    match runtime::start(worker_threads) {
        Ok(i) => Ok((i.flavor.to_owned(), i.worker_threads, i.generation)),
        Err(e @ runtime::RuntimeError::InvalidWorkerThreads) => {
            Err(pyo3::exceptions::PyValueError::new_err(e.to_string()))
        }
        Err(e) => Err(pyo3::exceptions::PyRuntimeError::new_err(e.to_string())),
    }
}

/// Stop the runtime and join its threads; `True` if one was running. Idempotent.
#[pyfunction]
pub fn runtime_stop(py: Python<'_>) -> bool {
    py.allow_threads(runtime::stop)
}

/// `(flavor, worker_threads, generation)` of the running runtime, or `None`.
#[pyfunction]
pub fn runtime_info() -> Option<(String, usize, u64)> {
    runtime::info().map(|i| (i.flavor.to_owned(), i.worker_threads, i.generation))
}

/// Deprecated: use `honba.event_loop.start()`. Starts the runtime if none is running.
#[pyfunction]
pub fn initialize_runtime() -> PyResult<()> {
    match runtime_start(None) {
        // Already running (or lost a start race): the runtime is up, which is all this promised.
        Err(_) if runtime::is_running() => Ok(()),
        other => other.map(|_| ()),
    }
}

/// Deprecated: use `honba.event_loop.info()`. The running runtime's flavour, e.g.
/// `"tokio-multi-thread"`; raises `RuntimeError` when none is running.
#[pyfunction]
pub fn get_runtime_handle() -> PyResult<String> {
    runtime::info()
        .map(|i| format!("tokio-{}", i.flavor))
        .ok_or_else(|| {
            pyo3::exceptions::PyRuntimeError::new_err("the async runtime is not running")
        })
}
