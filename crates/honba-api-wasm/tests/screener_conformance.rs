//! Runs `schema/conformance/screener_scan.json` against the pure wasm screener module.
//!
//! The vectors were generated once from the Python evaluator
//! (`scripts/gen_screener_scan_vectors.py`); `crates/honba-indicators/tests/screener_conformance.rs`
//! and `python/tests/integration/test_screener_scan_conformance.py` run the same file against the
//! native evaluator and Python. This test pins the wasm surface's column-oriented adapter to the
//! same numbers: it builds the input arrays, parses the JSON returned by [`scan_predicate_json`] /
//! [`scan_group_json`], and checks `result` exactly and `metrics` within `tolerance.relative`.
//!
//! `unsupported` cases (metrics Python cannot compute from bars) must be refused, not answered
//! `false`. `divergences` pin a case where Rust deliberately differs from Python.

use honba_api_wasm::screener::{evaluate_predicate_json, scan_group_json, scan_predicate_json};
use serde_json::Value;

const FIXTURE: &str = include_str!("../../../schema/conformance/screener_scan.json");

fn floats(v: &Value) -> Vec<f64> {
    v.as_array()
        .expect("array")
        .iter()
        .map(|x| x.as_f64().expect("number"))
        .collect()
}

/// The five column arrays of input `name` in the fixture.
struct Inputs {
    open: Vec<f64>,
    high: Vec<f64>,
    low: Vec<f64>,
    close: Vec<f64>,
    volume: Vec<f64>,
}

impl Inputs {
    fn of(fx: &Value, case: &Value) -> Self {
        let input = &fx["inputs"][case["input"].as_str().unwrap()];
        Self {
            open: floats(&input["open"]),
            high: floats(&input["high"]),
            low: floats(&input["low"]),
            close: floats(&input["close"]),
            volume: floats(&input["volume"]),
        }
    }

    fn scan_predicate(&self, predicate: &Value) -> Result<Value, String> {
        scan_predicate_json(
            &predicate.to_string(),
            &self.open,
            &self.high,
            &self.low,
            &self.close,
            &self.volume,
        )
        .map(|json| serde_json::from_str(&json).expect("scan JSON"))
    }

    fn scan_group(&self, group: &Value) -> Result<Value, String> {
        scan_group_json(
            &group.to_string(),
            &self.open,
            &self.high,
            &self.low,
            &self.close,
            &self.volume,
        )
        .map(|json| serde_json::from_str(&json).expect("scan JSON"))
    }
}

fn close_enough(actual: f64, expected: f64, rel: f64) -> bool {
    if expected.fract() == 0.0 {
        return actual == expected;
    }
    (actual - expected).abs() <= rel * expected.abs().max(1.0)
}

#[test]
fn every_predicate_case_matches_python() {
    let fx: Value = serde_json::from_str(FIXTURE).unwrap();
    assert_eq!(fx["type"], "ScreenerScan");
    let rel = fx["tolerance"]["relative"].as_f64().unwrap();
    let cases = fx["cases"].as_array().unwrap();
    assert!(cases.len() >= 60);
    for case in cases {
        let name = case["name"].as_str().unwrap();
        let inputs = Inputs::of(&fx, case);
        let expected = &case["expected"];
        let scan = inputs.scan_predicate(&case["predicate"]);
        if let Some(py_error) = expected.get("error") {
            // Python raised (TypeError/ValueError): the wasm surface must refuse, not answer.
            assert!(
                scan.is_err(),
                "{name}: python raised {py_error}, wasm gave {scan:?}"
            );
            continue;
        }
        let scan = scan.unwrap_or_else(|e| panic!("{name}: {e}"));
        assert_eq!(
            scan["result"], expected["result"],
            "{name}: predicate result"
        );
        for (key, want) in expected["metrics"].as_object().unwrap() {
            let have = &scan["metrics"][key.as_str()];
            match want.as_f64() {
                None => assert_eq!(have, &Value::Null, "{name}: metric {key}"),
                Some(w) => {
                    let h = have.as_f64();
                    assert!(
                        h.is_some_and(|h| close_enough(h, w, rel)),
                        "{name}: metric {key}: got {have}, want {w}"
                    );
                }
            }
        }
    }
}

#[test]
fn every_group_case_matches_python() {
    let fx: Value = serde_json::from_str(FIXTURE).unwrap();
    for case in fx["groups"].as_array().unwrap() {
        let name = case["name"].as_str().unwrap();
        let inputs = Inputs::of(&fx, case);
        let scan = inputs
            .scan_group(&case["group"])
            .unwrap_or_else(|e| panic!("{name}: {e}"));
        assert_eq!(scan["result"], case["expected"]["result"], "{name}");
    }
}

#[test]
fn metrics_python_cannot_compute_from_bars_are_rejected_not_false() {
    let fx: Value = serde_json::from_str(FIXTURE).unwrap();
    for case in fx["unsupported"].as_array().unwrap() {
        let name = case["name"].as_str().unwrap();
        let inputs = Inputs::of(&fx, case);
        let err = inputs
            .scan_predicate(&case["predicate"])
            .expect_err(&format!("{name}: must not be answered"));
        assert!(
            err.contains("cannot be computed from bars"),
            "{name}: {err}"
        );
        assert_eq!(case["rust"], "unsupported_metric", "{name}");
    }
}

#[test]
fn pinned_divergences_hold_for_the_wasm_surface() {
    let fx: Value = serde_json::from_str(FIXTURE).unwrap();
    let divergences = fx["divergences"].as_array().unwrap();
    assert!(!divergences.is_empty());
    for case in divergences {
        let name = case["name"].as_str().unwrap();
        let inputs = Inputs::of(&fx, case);
        let scan = inputs
            .scan_predicate(&case["predicate"])
            .unwrap_or_else(|e| panic!("{name}: {e}"));
        assert_eq!(scan["result"], case["rust"]["result"], "{name}");
        assert_ne!(case["python"]["result"], case["rust"]["result"], "{name}");
    }
}

#[test]
fn evaluate_predicate_json_agrees_with_the_scan_result() {
    let fx: Value = serde_json::from_str(FIXTURE).unwrap();
    let case = &fx["cases"][0];
    let inputs = Inputs::of(&fx, case);
    let direct = evaluate_predicate_json(
        &case["predicate"].to_string(),
        &inputs.open,
        &inputs.high,
        &inputs.low,
        &inputs.close,
        &inputs.volume,
    )
    .unwrap();
    assert_eq!(direct, case["expected"]["result"].as_bool().unwrap());
}
