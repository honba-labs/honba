use crate::indicators::{list_indicators_json, ohlc_indicator_series, IndicatorError};

fn nan_mask(v: &[f64]) -> Vec<bool> {
    v.iter().map(|x| x.is_nan()).collect()
}

#[test]
fn atr_first_value_is_mean_true_range_then_wilder_smoothed() {
    // period 2; TR = [2 (high-low), 3 (gap: |12-9|), 1].
    let high = [10.0, 12.0, 11.0];
    let low = [8.0, 9.0, 10.0];
    let close = [9.0, 11.0, 10.5];
    let out = ohlc_indicator_series("atr", r#"{"period":2}"#, &high, &low, &close).unwrap();
    assert_eq!(nan_mask(&out), [true, false, false]);
    assert_eq!(out[1], 2.5); // (2 + 3) / 2
    assert_eq!(out[2], 1.75); // (2.5 * 1 + 1) / 2
}

#[test]
fn atr_period_one_is_the_true_range() {
    let out = ohlc_indicator_series(
        "atr",
        r#"{"period":1}"#,
        &[3.0, 5.0],
        &[1.0, 4.0],
        &[2.0, 4.5],
    )
    .unwrap();
    assert_eq!(out, [2.0, 3.0]); // second bar gaps up: |5 - 2| = 3
}

#[test]
fn empty_input_gives_empty_output() {
    let out = ohlc_indicator_series("atr", r#"{"period":3}"#, &[], &[], &[]).unwrap();
    assert!(out.is_empty());
}

#[test]
fn period_longer_than_input_is_all_warmup() {
    let out = ohlc_indicator_series("atr", r#"{"period":5}"#, &[2.0], &[1.0], &[1.5]).unwrap();
    assert_eq!(nan_mask(&out), [true]);
}

#[test]
fn unequal_lengths_are_refused() {
    let err = ohlc_indicator_series("atr", r#"{"period":2}"#, &[2.0, 3.0], &[1.0], &[1.5, 2.0])
        .unwrap_err();
    assert_eq!(
        err,
        IndicatorError::LengthMismatch {
            high: 2,
            low: 1,
            close: 2
        }
    );
    assert!(err.to_string().contains("high=2"), "{err}");
}

#[test]
fn bad_names_and_params_are_errors_not_panics() {
    let (h, l, c) = ([2.0, 3.0], [1.0, 2.0], [1.5, 2.5]);
    // Close-only indicators are not OHLC indicators.
    assert!(matches!(
        ohlc_indicator_series("sma", r#"{"period":2}"#, &h, &l, &c),
        Err(IndicatorError::UnknownIndicator(_))
    ));
    for params in [
        r#"{"period":0}"#,
        r#"{"period":2.5}"#,
        r#"{"period":-1}"#,
        r#"{"period":1000001}"#,
        r#"{"period":18446744073709551615}"#,
        r#"{}"#,
        r#"{"period":2,"bogus":1}"#,
        "not json",
    ] {
        assert!(
            matches!(
                ohlc_indicator_series("atr", params, &h, &l, &c),
                Err(IndicatorError::InvalidParams(_))
            ),
            "{params}"
        );
    }
}

#[test]
fn params_are_validated_before_data() {
    // Bad params win over unequal lengths and non-finite input.
    assert!(matches!(
        ohlc_indicator_series("atr", r#"{"period":0}"#, &[f64::NAN], &[], &[]),
        Err(IndicatorError::InvalidParams(_))
    ));
}

#[test]
fn non_finite_input_is_rejected_in_any_series() {
    let ok = [2.0, 3.0, 4.0];
    let bad = [2.0, f64::INFINITY, 4.0];
    let p = r#"{"period":2}"#;
    for (h, l, c) in [(&bad, &ok, &ok), (&ok, &bad, &ok), (&ok, &ok, &bad)] {
        assert_eq!(
            ohlc_indicator_series("atr", p, h, l, c).unwrap_err(),
            IndicatorError::NonFiniteInput(1)
        );
    }
}

#[test]
fn finite_input_that_overflows_is_an_error_not_infinity() {
    // high - low = 2e308 overflows to infinity.
    let err = ohlc_indicator_series(
        "atr",
        r#"{"period":1}"#,
        &[1.7e308, 1.7e308],
        &[-1.7e308, -1.7e308],
        &[0.0, 0.0],
    )
    .unwrap_err();
    assert_eq!(err, IndicatorError::NonFiniteOutput(0));
}

#[test]
fn catalog_lists_atr_as_an_ohlc_indicator() {
    let v: serde_json::Value = serde_json::from_str(&list_indicators_json()).unwrap();
    let atr = v["indicators"]
        .as_array()
        .unwrap()
        .iter()
        .find(|i| i["name"] == "atr")
        .expect("atr in catalog");
    assert_eq!(atr["input"], "ohlc");
    assert_eq!(atr["export"], "ohlc_indicator_series");
    assert_eq!(atr["params"][0]["name"], "period");
    assert_eq!(atr["warmup"], "period - 1");
}
