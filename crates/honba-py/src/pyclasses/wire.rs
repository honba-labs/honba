//! The JSON wire contract exposed to Python (ADR 006).
//!
//! `canonical_json` parses a payload with the Rust serde types and serializes
//! it back, so Python can check that its models agree with Rust exactly.

// PyO3 0.22 `#[pyfunction]` expansion trips this lint on `PyResult` returns
// (same allowance as `domain.rs`).
#![allow(clippy::useless_conversion)]

use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use pyo3::types::PyModule;
use serde::de::DeserializeOwned;
use serde::Serialize;

use honba_entities::{Position, Trade};
use honba_messages::{Bar, Event, InstrumentId, Message, Order, SCHEMA_VERSION};
use honba_strategy::OrderIntent;

/// Wire-contract type names accepted by [`canonical_json`].
pub const KINDS: [&str; 8] = [
    "InstrumentId",
    "Bar",
    "Order",
    "OrderIntent",
    "Trade",
    "Position",
    "Event",
    "Message",
];

fn reserialize<T: Serialize + DeserializeOwned>(payload: &str) -> Result<String, String> {
    let value: T = serde_json::from_str(payload).map_err(|e| e.to_string())?;
    serde_json::to_string(&value).map_err(|e| e.to_string())
}

/// Parses `payload` as the Rust type named `kind` and returns its canonical JSON.
pub fn canonical(kind: &str, payload: &str) -> Result<String, String> {
    match kind {
        "InstrumentId" => reserialize::<InstrumentId>(payload),
        "Bar" => reserialize::<Bar>(payload),
        "Order" => reserialize::<Order>(payload),
        "OrderIntent" => reserialize::<OrderIntent>(payload),
        "Trade" => reserialize::<Trade>(payload),
        "Position" => reserialize::<Position>(payload),
        "Event" => reserialize::<Event>(payload),
        "Message" => reserialize::<Message>(payload),
        other => Err(format!("unknown kind '{other}'; expected one of {KINDS:?}")),
    }
}

/// Parse a JSON payload with the Rust type `kind` and return its canonical JSON.
///
/// Raises `ValueError` for an unknown kind or a payload Rust rejects.
#[pyfunction]
pub fn canonical_json(kind: &str, payload: &str) -> PyResult<String> {
    canonical(kind, payload).map_err(PyValueError::new_err)
}

pub fn register(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add("SCHEMA_VERSION", SCHEMA_VERSION)?;
    m.add_function(wrap_pyfunction!(canonical_json, m)?)?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn canonical_roundtrips_and_rejects() {
        let id = r#"{"symbol":"X","venue":"NSE"}"#;
        assert_eq!(canonical("InstrumentId", id).unwrap(), id);
        assert!(canonical("Nope", "{}")
            .unwrap_err()
            .contains("unknown kind"));
        assert!(canonical("Order", "{}").is_err());
    }
}
