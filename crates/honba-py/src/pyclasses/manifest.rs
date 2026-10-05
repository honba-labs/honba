//! The strategy manifest surface exposed to Python (plan.md E0-S8, ADR 0012).

use pyo3::prelude::*;
use pyo3::types::PyModule;

use honba_strategy::STRATEGY_API_VERSION;

pub fn register(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add("STRATEGY_API_VERSION", STRATEGY_API_VERSION)?;
    Ok(())
}
