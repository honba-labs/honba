//! Runs `schema/conformance/indicator_series.json` against the pure indicator module.
//!
//! The same vectors are executed by the Python test
//! `python/tests/integration/test_indicator_series_conformance.py` and, in a wasm runtime, by any
//! JS harness over the `indicator_series` export, so a number cannot drift between surfaces.
//!
//! Tolerance policy (stated in the fixture): `null` expects `NaN` (warm-up); an expected value
//! that is integer-valued must match exactly; anything else must satisfy
//! `|actual - expected| <= rel * max(1, |expected|)` with `rel = 1e-12`.

use honba_api_wasm::indicators::indicator_series;
use serde_json::Value;

// Hand-computed spot checks of the vectors (the numbers themselves come from honba-indicators):
//   sma_3_ramp:  the first full window is (1, 2, 3), mean 2; each step slides by 1, so 2, 3, 4, ...
//   ema_3_ramp:  alpha = 2/(3+1) = 0.5; seed = SMA(1, 2, 3) = 2; next = 0.5*4 + 0.5*2 = 3, then 4, ...
//   rsi_3_ramp:  only gains, so avg_loss = 0 and RSI = 100 from the 4th close (period + 1 inputs).
const FIXTURE: &str = include_str!("../../../schema/conformance/indicator_series.json");

fn floats(v: &Value) -> Vec<f64> {
    v.as_array()
        .unwrap()
        .iter()
        .map(|x| x.as_f64().unwrap_or(f64::NAN))
        .collect()
}

fn matches(actual: f64, expected: f64, rel: f64) -> bool {
    if expected.is_nan() {
        return actual.is_nan();
    }
    if expected.fract() == 0.0 {
        return actual == expected;
    }
    (actual - expected).abs() <= rel * expected.abs().max(1.0)
}

#[test]
fn every_vector_matches_the_pure_module() {
    let fx: Value = serde_json::from_str(FIXTURE).unwrap();
    assert_eq!(fx["type"], "IndicatorSeries");
    let rel = fx["tolerance"]["relative"].as_f64().unwrap();
    assert_eq!(rel, 1e-12);
    let cases = fx["cases"].as_array().unwrap();
    assert!(cases.len() >= 10, "fixture lost cases");
    for case in cases {
        let name = case["name"].as_str().unwrap();
        let closes = floats(&fx["inputs"][case["input"].as_str().unwrap()]);
        let expected = floats(&case["expected"]);
        let params = case["params"].to_string();
        let out = indicator_series(case["indicator"].as_str().unwrap(), &params, &closes)
            .unwrap_or_else(|e| panic!("{name}: {e}"));
        assert_eq!(out.len(), closes.len(), "{name}: length");
        assert_eq!(out.len(), expected.len(), "{name}: expected length");
        for (i, (a, e)) in out.iter().zip(&expected).enumerate() {
            assert!(matches(*a, *e, rel), "{name}[{i}]: got {a}, want {e}");
        }
    }
}
