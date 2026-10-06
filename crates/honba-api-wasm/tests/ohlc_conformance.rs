//! Runs `schema/conformance/ohlc_series.json` against the pure OHLC indicator module.
//!
//! The same vectors are executed by `python/tests/integration/test_ohlc_series_conformance.py`
//! (Python `Atr(include_first_bar=True)`, where the expected values were generated) and, in a wasm
//! runtime, by `tests/js/indicator_conformance.mjs` over the `ohlc_indicator_series` export.
//!
//! Tolerance: `null` expects `NaN` (warm-up); integer-valued expectations are exact; otherwise
//! `|actual - expected| <= rel * max(1, |expected|)` with `rel = 1e-12`.

use honba_api_wasm::indicators::ohlc_indicator_series;
use serde_json::Value;

// Hand-computed spot check (atr_1_single): one bar, period 1, TR = high - low = 2.5 - 1.5 = 1.
const FIXTURE: &str = include_str!("../../../schema/conformance/ohlc_series.json");

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
    assert_eq!(fx["type"], "OhlcSeries");
    let rel = fx["tolerance"]["relative"].as_f64().unwrap();
    assert_eq!(rel, 1e-12);
    let cases = fx["cases"].as_array().unwrap();
    assert!(cases.len() >= 10, "fixture lost cases");
    for case in cases {
        let name = case["name"].as_str().unwrap();
        let bars = &fx["inputs"][case["input"].as_str().unwrap()];
        let (h, l, c) = (
            floats(&bars["high"]),
            floats(&bars["low"]),
            floats(&bars["close"]),
        );
        let expected = floats(&case["expected"]);
        let params = case["params"].to_string();
        let out = ohlc_indicator_series(case["indicator"].as_str().unwrap(), &params, &h, &l, &c)
            .unwrap_or_else(|e| panic!("{name}: {e}"));
        assert_eq!(out.len(), c.len(), "{name}: length");
        assert_eq!(out.len(), expected.len(), "{name}: expected length");
        for (i, (a, e)) in out.iter().zip(&expected).enumerate() {
            assert!(matches(*a, *e, rel), "{name}[{i}]: got {a}, want {e}");
        }
    }
}
