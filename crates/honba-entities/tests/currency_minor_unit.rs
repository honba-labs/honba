//! Golden-vector contract for the currency minor-unit table (ADR 0011).
//!
//! Reads `schema/conformance/currency_minor_units.json`; the Python tests read the same file
//! and also pin `honba._honba` to it.

use std::path::PathBuf;

use honba_entities::{Currency, Money};
use honba_messages::SCHEMA_VERSION;
use serde_json::Value;

fn load() -> Value {
    let path = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("../../schema/conformance/currency_minor_units.json");
    let doc: Value = serde_json::from_str(&std::fs::read_to_string(path).unwrap()).unwrap();
    assert_eq!(doc["schema_version"], u64::from(SCHEMA_VERSION));
    assert_eq!(doc["type"], "CurrencyMinorUnits");
    doc
}

fn currency(code: &str) -> Currency {
    serde_json::from_value(Value::String(code.to_owned())).unwrap()
}

#[test]
fn minor_unit_table_matches_golden() {
    let doc = load();
    let cases = doc["cases"].as_array().unwrap();
    assert_eq!(cases.len(), Currency::ALL.len(), "one case per currency");
    for case in cases {
        let v = &case["value"];
        let c = currency(v["currency"].as_str().unwrap());
        assert_eq!(u64::from(c.minor_exponent()), v["minor_exponent"], "{c}");
        assert_eq!(c.minor_unit().singular, v["singular"], "{c}");
        assert_eq!(c.minor_unit().plural, v["plural"], "{c}");
    }
}

#[test]
fn format_minor_matches_golden() {
    let doc = load();
    for case in doc["format_minor"].as_array().unwrap() {
        let c = currency(case["currency"].as_str().unwrap());
        let m = Money::new(case["amount"].as_i64().unwrap(), c);
        assert_eq!(
            m.format_minor(),
            case["text"].as_str().unwrap(),
            "{}",
            case["name"]
        );
    }
}
