//! `run_strategy` (the `honba._honba.run_strategy` binding) agrees with the
//! shared conformance fixture `schema/conformance/strategy_contract.json`
//! (ADR 008). Runs through the Rust API only; no Python interpreter.

use std::path::PathBuf;

use honba::pyclasses::run::{run_strategy_costed_json, run_strategy_json};
use serde_json::Value;

#[test]
fn run_strategy_matches_every_conformance_scenario() {
    let path = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("../../schema/conformance/strategy_contract.json");
    let doc: Value = serde_json::from_str(&std::fs::read_to_string(path).unwrap()).unwrap();
    for s in doc["scenarios"].as_array().unwrap() {
        let name = s["name"].as_str().unwrap();
        let costs = &s["fill_costs"];
        let out = run_strategy_costed_json(
            s["strategy"].as_str().unwrap(),
            &s["params"].to_string(),
            &s["events"].to_string(),
            &s["instruments"].to_string(),
            s["initial_cash"].as_f64().unwrap(),
            costs["flat"].as_f64().unwrap_or(0.0),
            costs["bps"].as_f64().unwrap_or(0.0),
        )
        .unwrap_or_else(|e| panic!("{name}: {e}"));
        let mut got: Value = serde_json::from_str(&out).unwrap();
        // `rejections` is Rust-reported only (typed errors); the fixture
        // scenarios are all valid runs, so it must be empty.
        let rejections = got.as_object_mut().unwrap().remove("rejections");
        assert_eq!(rejections, Some(serde_json::json!([])), "{name}");
        assert_eq!(got, s["expected"], "{name}");
    }
}

const BAR: &str = r#"[{"schema_version": 3, "event": {"type": "bar", "bar_type": {"instrument_id": {"symbol": "RELIANCE", "exchange": "NSE"}, "spec": {"step": 1, "aggregation": "minute", "price_type": "last"}}, "open": 2945.0, "high": 2955.0, "low": 2940.0, "close": 2950.0, "volume": 1000.0, "ts_event": {"iso": "1970-01-01T00:00:00.000001000Z", "unix_nanos": "1000"}, "ts_init": {"iso": "1970-01-01T00:00:00.000001000Z", "unix_nanos": "1000"}}, "ts_init": {"iso": "1970-01-01T00:00:00.000001000Z", "unix_nanos": "1000"}}]"#;
const BUY_AND_HOLD: &str =
    r#"{"instrument_id": {"symbol": "RELIANCE", "exchange": "NSE"}, "quantity": QTY}"#;

fn run_buy_and_hold(quantity: &str) -> Value {
    let params = BUY_AND_HOLD.replace("QTY", quantity);
    let out = run_strategy_json("buy_and_hold", &params, BAR, "[]", 0.0).unwrap();
    serde_json::from_str(&out).unwrap()
}

#[test]
fn run_strategy_reports_rejected_intents() {
    let got = run_buy_and_hold("-1.0");
    assert_eq!(got["intents"], serde_json::json!([]));
    let rejections = got["rejections"].as_array().expect("rejections array");
    assert_eq!(rejections.len(), 1);
    assert_eq!(rejections[0]["ts_init"]["unix_nanos"], "1000");
    assert_eq!(rejections[0]["intent"]["quantity"], -1.0);
    assert_eq!(rejections[0]["error"]["kind"], "non_positive_quantity");
    assert_eq!(
        rejections[0]["error"]["message"],
        "quantity must be positive, got -1"
    );
}

#[test]
fn run_strategy_has_an_empty_rejections_array_for_valid_runs() {
    let got = run_buy_and_hold("10.0");
    assert_eq!(got["rejections"], serde_json::json!([]));
    assert_eq!(got["intents"].as_array().unwrap().len(), 1);
}

#[test]
fn run_strategy_costed_charges_costs_and_the_plain_entry_point_charges_none() {
    let params = BUY_AND_HOLD.replace("QTY", "10.0");
    // 10 * 2950 = 29500; 100 bps of it is 295.0; plus a flat 5.
    let out =
        run_strategy_costed_json("buy_and_hold", &params, BAR, "[]", 0.0, 5.0, 100.0).unwrap();
    let got: Value = serde_json::from_str(&out).unwrap();
    assert_eq!(got["fills"][0]["costs"]["amount"], 30000);
    assert_eq!(got["cash"]["amount"], -2980000);
    assert_eq!(run_buy_and_hold("10.0")["fills"][0]["costs"]["amount"], 0);
}

#[test]
fn run_strategy_costed_rejects_invalid_costs_with_an_error_not_a_panic() {
    let params = BUY_AND_HOLD.replace("QTY", "1.0");
    for (flat, bps) in [
        (-1.0, 0.0),
        (f64::NAN, 0.0),
        (f64::INFINITY, 0.0),
        (1e12, 0.0),
        (0.0, -1.0),
        (0.0, f64::NAN),
        (0.0, 10_001.0),
    ] {
        let err = run_strategy_costed_json("buy_and_hold", &params, BAR, "[]", 0.0, flat, bps)
            .unwrap_err();
        assert!(err.contains("fill cost"), "{err}");
    }
}
