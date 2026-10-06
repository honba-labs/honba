//! Flat series, minimal lengths and non-finite input, for every exposed indicator.

use crate::indicators::{indicator_series, ohlc_indicator_series, IndicatorError};

const CLOSE_ONLY: [(&str, &str); 5] = [
    ("sma", r#"{"period":3}"#),
    ("ema", r#"{"period":3}"#),
    ("rsi", r#"{"period":3}"#),
    ("bollinger", r#"{"period":3}"#),
    ("macd", r#"{"fast":2,"slow":3,"signal":2}"#),
];

fn warm(v: &[f64]) -> Vec<f64> {
    v.iter().copied().filter(|x| !x.is_nan()).collect()
}

#[test]
fn flat_series_never_overflows_and_has_defined_values() {
    let flat = [5.0; 20];
    for (name, params) in CLOSE_ONLY {
        let out = indicator_series(name, params, &flat)
            .unwrap_or_else(|e| panic!("{name} on a flat series: {e}"));
        assert_eq!(out.len(), flat.len(), "{name}");
        assert!(out.iter().all(|x| !x.is_infinite()), "{name}");
        assert!(!warm(&out).is_empty(), "{name} never leaves warm-up");
    }
}

#[test]
fn flat_series_values() {
    let flat = [5.0; 20];
    for name in ["sma", "ema"] {
        let out = indicator_series(name, r#"{"period":3}"#, &flat).unwrap();
        assert!(warm(&out).iter().all(|&x| x == 5.0), "{name}");
    }
    let hist = indicator_series(
        "macd",
        r#"{"fast":2,"slow":3,"signal":2,"output":"histogram"}"#,
        &flat,
    )
    .unwrap();
    assert!(warm(&hist).iter().all(|&x| x == 0.0));
    for output in ["upper", "lower", "middle"] {
        let p = format!(r#"{{"period":3,"output":"{output}"}}"#);
        let out = indicator_series("bollinger", &p, &flat).unwrap();
        assert!(warm(&out).iter().all(|&x| x == 5.0), "{output}");
    }
    let rsi = indicator_series("rsi", r#"{"period":3}"#, &flat).unwrap();
    assert!(warm(&rsi).iter().all(|x| (0.0..=100.0).contains(x)));
}

#[test]
fn flat_ohlc_atr_is_zero() {
    let f = [5.0; 10];
    let out = ohlc_indicator_series("atr", r#"{"period":3}"#, &f, &f, &f).unwrap();
    assert_eq!(warm(&out), [0.0; 8]);
}

#[test]
fn period_one_is_accepted_for_every_period_indicator() {
    let c = [1.0, 3.0, 2.0, 5.0];
    for name in ["sma", "ema", "rsi", "bollinger"] {
        let out = indicator_series(name, r#"{"period":1}"#, &c)
            .unwrap_or_else(|e| panic!("{name} period 1: {e}"));
        assert_eq!(out.len(), c.len(), "{name}");
    }
    assert_eq!(
        indicator_series("sma", r#"{"period":1}"#, &c).unwrap(),
        c.to_vec()
    );
    assert_eq!(
        indicator_series("ema", r#"{"period":1}"#, &c).unwrap(),
        c.to_vec()
    );
}

#[test]
fn length_equal_to_period_yields_first_value_at_the_end() {
    let c = [1.0, 2.0, 4.0];
    for name in ["sma", "ema", "bollinger"] {
        let out = indicator_series(name, r#"{"period":3}"#, &c).unwrap();
        assert_eq!(warm(&out).len(), 1, "{name}");
        assert!(!out[2].is_nan(), "{name}");
    }
    // RSI needs `period` changes, i.e. period + 1 closes.
    let rsi = indicator_series("rsi", r#"{"period":3}"#, &c).unwrap();
    assert!(rsi.iter().all(|x| x.is_nan()));
    let out =
        ohlc_indicator_series("atr", r#"{"period":3}"#, &[3.0; 3], &[1.0; 3], &[2.0; 3]).unwrap();
    assert_eq!(warm(&out), [2.0]);
}

#[test]
fn length_shorter_than_period_is_all_warmup_not_an_error() {
    let c = [1.0, 2.0];
    for (name, params) in CLOSE_ONLY {
        let out = indicator_series(name, params, &c).unwrap();
        assert_eq!(out.len(), 2, "{name}");
        assert!(out.iter().all(|x| x.is_nan()), "{name}");
    }
}

#[test]
fn every_close_indicator_rejects_nan_and_infinity_with_index() {
    for (name, params) in CLOSE_ONLY {
        for bad in [f64::NAN, f64::INFINITY, f64::NEG_INFINITY] {
            let err = indicator_series(name, params, &[1.0, 2.0, bad, 4.0]).unwrap_err();
            assert_eq!(err, IndicatorError::NonFiniteInput(2), "{name} {bad}");
        }
    }
}

#[test]
fn atr_rejects_non_finite_in_any_series() {
    let ok = [2.0, 3.0, 4.0];
    for bad in [f64::NAN, f64::INFINITY, f64::NEG_INFINITY] {
        let b = [2.0, bad, 4.0];
        let low = [1.0, 1.0, 1.0];
        for (h, l, c) in [(&b, &low, &ok), (&ok, &b, &ok), (&ok, &low, &b)] {
            let err = ohlc_indicator_series("atr", r#"{"period":2}"#, h, l, c).unwrap_err();
            assert_eq!(err, IndicatorError::NonFiniteInput(1), "{bad}");
        }
    }
}

#[test]
fn atr_rejects_high_below_low_with_index() {
    let err = ohlc_indicator_series(
        "atr",
        r#"{"period":2}"#,
        &[3.0, 2.0, 4.0],
        &[1.0, 2.5, 1.0],
        &[2.0, 2.2, 2.0],
    )
    .unwrap_err();
    assert_eq!(err, IndicatorError::InvertedRange(1));
    assert!(err.to_string().contains("index 1"));
}

#[test]
fn atr_accepts_high_equal_to_low() {
    let out = ohlc_indicator_series(
        "atr",
        r#"{"period":1}"#,
        &[2.0, 2.0],
        &[2.0, 2.0],
        &[2.0, 2.0],
    )
    .unwrap();
    assert_eq!(out, [0.0, 0.0]);
}

#[test]
fn non_finite_is_reported_before_inverted_range() {
    let err = ohlc_indicator_series(
        "atr",
        r#"{"period":1}"#,
        &[1.0, 3.0],
        &[2.0, f64::NAN],
        &[1.5, 2.0],
    )
    .unwrap_err();
    assert_eq!(err, IndicatorError::NonFiniteInput(1));
}
