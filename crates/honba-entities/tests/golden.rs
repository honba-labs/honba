//! Golden-vector contract tests for `Trade` and `Position` (ADR 006).
//!
//! Reads the shared vectors in `schema/golden/`; the Python tests read the
//! same files.

use std::collections::BTreeMap;
use std::path::PathBuf;

use honba_entities::{Currency, Position, PositionSide, Trade};
use honba_messages::{InstrumentId, OrderId, OrderSide, UnixNanos, Venue, SCHEMA_VERSION};
use serde::de::DeserializeOwned;
use serde::Serialize;
use serde_json::Value;

const TS: u64 = 1_700_000_060_000_000_000;

fn load_cases(file: &str, type_name: &str) -> BTreeMap<String, Value> {
    load(file, type_name, "cases")
}

/// Loads the `invalid` cases; files without that section have none.
fn load_invalid(file: &str, type_name: &str) -> BTreeMap<String, Value> {
    load(file, type_name, "invalid")
}

/// Loads the `invalid_text` cases: raw JSON text (e.g. with duplicate keys).
fn load_invalid_text(file: &str) -> Vec<(String, String)> {
    let path = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("../../schema/golden")
        .join(file);
    let doc: Value = serde_json::from_str(&std::fs::read_to_string(path).unwrap()).unwrap();
    doc.get("invalid_text")
        .and_then(Value::as_array)
        .into_iter()
        .flatten()
        .map(|c| {
            let name = c["name"].as_str().unwrap().to_owned();
            (name, c["text"].as_str().unwrap().to_owned())
        })
        .collect()
}

fn load(file: &str, type_name: &str, key: &str) -> BTreeMap<String, Value> {
    let path = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("../../schema/golden")
        .join(file);
    let text = std::fs::read_to_string(&path).unwrap_or_else(|e| panic!("{path:?}: {e}"));
    let doc: Value = serde_json::from_str(&text).unwrap();
    assert_eq!(doc["schema_version"], u64::from(SCHEMA_VERSION), "{file}");
    assert_eq!(doc["type"], type_name, "{file}");
    doc.get(key)
        .and_then(Value::as_array)
        .map(|cases| {
            cases
                .iter()
                .map(|c| (c["name"].as_str().unwrap().to_owned(), c["value"].clone()))
                .collect()
        })
        .unwrap_or_default()
}

fn check<T>(file: &str, type_name: &str, expected: Vec<(&str, T)>)
where
    T: Serialize + DeserializeOwned + PartialEq + std::fmt::Debug,
{
    let cases = load_cases(file, type_name);
    let expected: BTreeMap<&str, T> = expected.into_iter().collect();
    assert_eq!(
        cases.keys().map(String::as_str).collect::<Vec<_>>(),
        expected.keys().copied().collect::<Vec<_>>(),
        "{file}: case names differ"
    );
    for (name, json) in &cases {
        let want = &expected[name.as_str()];
        let got: T = serde_json::from_value(json.clone())
            .unwrap_or_else(|e| panic!("{file}/{name}: deserialize failed: {e}"));
        assert_eq!(&got, want, "{file}/{name}: deserialized value");
        assert_eq!(
            &serde_json::to_value(want).unwrap(),
            json,
            "{file}/{name}: JSON"
        );
        let again: T = serde_json::from_str(&serde_json::to_string(&got).unwrap()).unwrap();
        assert_eq!(&again, want, "{file}/{name}: text round trip");
    }
    for (name, text) in load_invalid_text(file) {
        let res: Result<T, _> = serde_json::from_str(&text);
        assert!(res.is_err(), "{file}/{name}: invalid text was accepted");
    }
    let invalid = load_invalid(file, type_name);
    assert!(!invalid.is_empty(), "{file}: expected invalid cases");
    for (name, json) in &invalid {
        let res: Result<T, _> = serde_json::from_value(json.clone());
        assert!(res.is_err(), "{file}/{name}: invalid case was accepted");
    }
}

fn nse(sym: &str) -> InstrumentId {
    InstrumentId::new(sym, Venue::new("NSE"))
}

#[test]
fn trade_golden_has_order_id_and_costs() {
    let buy = Trade::new(
        OrderId::new("O-1"),
        nse("NIFTY50"),
        OrderSide::Buy,
        75.0,
        22_000.0,
        UnixNanos::from_u64(TS),
        UnixNanos::from_u64(TS + 5),
    )
    .with_costs(45.67);
    let sell = Trade::new(
        OrderId::new("O-2"),
        nse("RELIANCE"),
        OrderSide::Sell,
        10.0,
        2_950.05,
        UnixNanos::from_u64(TS),
        UnixNanos::from_u64(TS),
    );
    check(
        "trade.json",
        "Trade",
        vec![("buy_with_costs", buy), ("sell_zero_costs", sell)],
    );
}

#[test]
fn position_golden() {
    let flat = Position::flat(nse("NIFTY50"), Currency::Inr);
    let mut short = Position::flat(nse("RELIANCE"), Currency::Inr);
    short.apply_fill(PositionSide::Short, 100.0, 2_950.5);
    short.apply_fill(PositionSide::Long, 40.0, 2_952.5);
    check(
        "position.json",
        "Position",
        vec![("flat", flat), ("short_with_realized_pnl", short)],
    );
}
