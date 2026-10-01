//! `run_strategy` (the `honba._honba.run_strategy` binding) agrees with the
//! shared conformance fixture `schema/conformance/strategy_contract.json`
//! (ADR 008). Runs through the Rust API only; no Python interpreter.

use std::path::PathBuf;

use honba::pyclasses::run::run_strategy_json;
use serde_json::Value;

#[test]
fn run_strategy_matches_every_conformance_scenario() {
    let path = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("../../schema/conformance/strategy_contract.json");
    let doc: Value = serde_json::from_str(&std::fs::read_to_string(path).unwrap()).unwrap();
    for s in doc["scenarios"].as_array().unwrap() {
        let name = s["name"].as_str().unwrap();
        let out = run_strategy_json(
            s["strategy"].as_str().unwrap(),
            &s["params"].to_string(),
            &s["events"].to_string(),
            &s["instruments"].to_string(),
            s["initial_cash"].as_f64().unwrap(),
        )
        .unwrap_or_else(|e| panic!("{name}: {e}"));
        let got: Value = serde_json::from_str(&out).unwrap();
        assert_eq!(got, s["expected"], "{name}");
    }
}
