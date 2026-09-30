//! Embeds a Python interpreter and exposes the honba simulation module
//! (`honba_bridge.py`) under the import name `honba`.

use anyhow::{Context, Result};
use pyo3::prelude::*;
use pyo3::types::{PyDict, PyModule};
use std::fs;
use std::path::Path;

const HONBA_BRIDGE: &str = include_str!("honba_bridge.py");

pub fn run_script(script: &Path, max_events: Option<u64>) -> Result<()> {
    let source = fs::read_to_string(script)
        .with_context(|| format!("reading {}", script.display()))?;

    Python::with_gil(|py| -> PyResult<()> {
        let module = PyModule::from_code_bound(py, HONBA_BRIDGE, "honba_bridge.py", "honba")?;

        // Register under `honba` so user scripts can `from honba import ...`.
        let sys = py.import_bound("sys")?;
        let modules: Bound<PyDict> = sys.getattr("modules")?.downcast_into()?;
        modules.set_item("honba", &module)?;

        let max_events_obj: PyObject = match max_events {
            Some(n) => n.into_py(py),
            None => py.None(),
        };

        let result = module.call_method1(
            "run_script",
            (source.as_str(), script.to_string_lossy().as_ref(), max_events_obj),
        )?;

        let exit_code: i32 = result.get_item("exit_code")?.extract()?;
        if exit_code != 0 {
            let err: String = result.get_item("error")?.extract()?;
            eprintln!("{err}");
            std::process::exit(exit_code);
        }
        Ok(())
    })?;

    Ok(())
}
