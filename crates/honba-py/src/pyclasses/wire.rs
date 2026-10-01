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

use std::collections::BTreeMap;

use honba_entities::{Currency, Position, PositionSide, Trade};
use honba_messages::{
    AggressorSide, Bar, BarAggregation, Event, InstrumentId, Message, Order, OrderSide,
    OrderStatus, OrderType, PriceType, TimeInForce, SCHEMA_VERSION,
};
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

/// Wire enum names accepted by [`canonical`] and listed by [`enum_values`].
pub const ENUM_KINDS: [&str; 9] = [
    "OrderSide",
    "OrderType",
    "OrderStatus",
    "TimeInForce",
    "BarAggregation",
    "PriceType",
    "AggressorSide",
    "PositionSide",
    "Currency",
];

fn wire_strings<T: Serialize>(all: &[T]) -> Vec<String> {
    all.iter()
        .map(|v| match serde_json::to_value(v) {
            Ok(serde_json::Value::String(s)) => s,
            other => unreachable!("wire enum serialized as {other:?}"),
        })
        .collect()
}

/// Every variant of every wire enum, as its JSON string, keyed by enum name.
///
/// Generated from the enums' `ALL` constants, so a variant added in Rust
/// shows up here (and fails the Python parity test until Python has it).
pub fn enum_values() -> BTreeMap<&'static str, Vec<String>> {
    BTreeMap::from([
        ("OrderSide", wire_strings(OrderSide::ALL)),
        ("OrderType", wire_strings(OrderType::ALL)),
        ("OrderStatus", wire_strings(OrderStatus::ALL)),
        ("TimeInForce", wire_strings(TimeInForce::ALL)),
        ("BarAggregation", wire_strings(BarAggregation::ALL)),
        ("PriceType", wire_strings(PriceType::ALL)),
        ("AggressorSide", wire_strings(AggressorSide::ALL)),
        ("PositionSide", wire_strings(PositionSide::ALL)),
        ("Currency", wire_strings(Currency::ALL)),
    ])
}

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
        "OrderSide" => reserialize::<OrderSide>(payload),
        "OrderType" => reserialize::<OrderType>(payload),
        "OrderStatus" => reserialize::<OrderStatus>(payload),
        "TimeInForce" => reserialize::<TimeInForce>(payload),
        "BarAggregation" => reserialize::<BarAggregation>(payload),
        "PriceType" => reserialize::<PriceType>(payload),
        "AggressorSide" => reserialize::<AggressorSide>(payload),
        "PositionSide" => reserialize::<PositionSide>(payload),
        "Currency" => reserialize::<Currency>(payload),
        other => Err(format!(
            "unknown kind '{other}'; expected one of {KINDS:?} or {ENUM_KINDS:?}"
        )),
    }
}

/// Parse a JSON payload with the Rust type `kind` and return its canonical JSON.
///
/// `kind` is a wire type (`KINDS`) or a wire enum (`ENUM_KINDS`).
///
/// Raises `ValueError` for an unknown kind or a payload Rust rejects.
#[pyfunction]
pub fn canonical_json(kind: &str, payload: &str) -> PyResult<String> {
    canonical(kind, payload).map_err(PyValueError::new_err)
}

/// Every variant of every wire enum, as its JSON string, keyed by enum name.
#[pyfunction]
pub fn wire_enum_values() -> BTreeMap<&'static str, Vec<String>> {
    enum_values()
}

pub fn register(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add("SCHEMA_VERSION", SCHEMA_VERSION)?;
    m.add_function(wrap_pyfunction!(canonical_json, m)?)?;
    m.add_function(wrap_pyfunction!(wire_enum_values, m)?)?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn enum_values_list_every_variant_as_its_wire_string() {
        let values = enum_values();
        assert_eq!(values.len(), ENUM_KINDS.len());
        assert_eq!(values["OrderSide"], ["buy", "sell", "no_order_side"]);
        assert_eq!(values["Currency"], ["INR", "USD", "EUR", "GBP"]);
        for (kind, variants) in &values {
            for v in variants {
                let json = serde_json::to_string(v).unwrap();
                assert_eq!(canonical(kind, &json).unwrap(), json, "{kind}");
            }
        }
        assert!(canonical("OrderSide", "\"sideways\"").is_err());
    }

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
