use crate::indicators::{indicator_series, list_indicators_json, IndicatorError};

fn nan_mask(v: &[f64]) -> Vec<bool> {
    v.iter().map(|x| x.is_nan()).collect()
}

#[test]
fn sma_series_is_full_length_with_nan_warmup() {
    // Hand-computed: windows of 3 over 1..5 average to 2, 3, 4.
    let out = indicator_series("sma", r#"{"period":3}"#, &[1.0, 2.0, 3.0, 4.0, 5.0]).unwrap();
    assert_eq!(nan_mask(&out), [true, true, false, false, false]);
    assert_eq!(&out[2..], [2.0, 3.0, 4.0]);
}

#[test]
fn ema_is_seeded_with_the_sma_then_smoothed() {
    // period 3: alpha = 0.5, seed = mean(1,2,3) = 2, then 0.5*4+0.5*2 = 3, 0.5*5+0.5*3 = 4.
    let out = indicator_series("ema", r#"{"period":3}"#, &[1.0, 2.0, 3.0, 4.0, 5.0]).unwrap();
    assert_eq!(nan_mask(&out), [true, true, false, false, false]);
    assert_eq!(&out[2..], [2.0, 3.0, 4.0]);
}

#[test]
fn rsi_all_gains_is_100_after_period_changes() {
    let out = indicator_series("rsi", r#"{"period":3}"#, &[1.0, 2.0, 3.0, 4.0, 5.0]).unwrap();
    assert_eq!(nan_mask(&out), [true, true, true, false, false]);
    assert_eq!(&out[3..], [100.0, 100.0]);
}

#[test]
fn bollinger_selects_the_requested_band() {
    let closes = [1.0, 2.0, 3.0, 4.0, 5.0];
    let mid = indicator_series("bollinger", r#"{"period":5}"#, &closes).unwrap();
    let up = indicator_series(
        "bollinger",
        r#"{"period":5,"k":2.0,"output":"upper"}"#,
        &closes,
    )
    .unwrap();
    let lo = indicator_series("bollinger", r#"{"period":5,"output":"lower"}"#, &closes).unwrap();
    assert_eq!(mid[4], 3.0);
    // population stddev of 1..5 is sqrt(2)
    assert!((up[4] - (3.0 + 2.0 * 2f64.sqrt())).abs() < 1e-12);
    assert!((lo[4] - (3.0 - 2.0 * 2f64.sqrt())).abs() < 1e-12);
    assert_eq!(nan_mask(&mid), [true, true, true, true, false]);
}

#[test]
fn macd_outputs_are_consistent_and_default_params_apply() {
    let closes: Vec<f64> = (0..60)
        .map(|i| 100.0 + f64::from(i % 7) + f64::from(i) * 0.5)
        .collect();
    let macd = indicator_series("macd", "{}", &closes).unwrap();
    let sig = indicator_series("macd", r#"{"output":"signal"}"#, &closes).unwrap();
    let hist = indicator_series("macd", r#"{"output":"histogram"}"#, &closes).unwrap();
    // slow 26 + signal 9 - 2 warm-up bars.
    let first = macd.iter().position(|x| !x.is_nan()).unwrap();
    assert_eq!(first, 26 + 9 - 2);
    assert_eq!(nan_mask(&macd), nan_mask(&sig));
    assert!((hist[59] - (macd[59] - sig[59])).abs() < 1e-12);
}

#[test]
fn empty_input_gives_empty_output() {
    assert!(indicator_series("sma", r#"{"period":3}"#, &[])
        .unwrap()
        .is_empty());
}

#[test]
fn blank_params_mean_defaults() {
    assert!(indicator_series("macd", "", &[1.0]).is_ok());
}

#[test]
fn invalid_requests_are_errors_not_panics() {
    let c = [1.0, 2.0, 3.0];
    assert!(matches!(
        indicator_series("nope", "{}", &c),
        Err(IndicatorError::UnknownIndicator(_))
    ));
    assert!(matches!(
        indicator_series("sma", r#"{"period":0}"#, &c),
        Err(IndicatorError::InvalidParams(_))
    ));
    assert!(matches!(
        indicator_series("sma", "{}", &c),
        Err(IndicatorError::InvalidParams(_))
    ));
    assert!(matches!(
        indicator_series("sma", r#"{"period":3,"bogus":1}"#, &c),
        Err(IndicatorError::InvalidParams(_))
    ));
    assert!(matches!(
        indicator_series("sma", "not json", &c),
        Err(IndicatorError::InvalidParams(_))
    ));
    assert!(matches!(
        indicator_series("sma", r#"{"period":18446744073709551615}"#, &c),
        Err(IndicatorError::InvalidParams(_))
    ));
    assert!(matches!(
        indicator_series("macd", r#"{"fast":26,"slow":12}"#, &c),
        Err(IndicatorError::InvalidParams(_))
    ));
    assert!(matches!(
        indicator_series("bollinger", r#"{"period":3,"output":"x"}"#, &c),
        Err(IndicatorError::InvalidParams(_))
    ));
    assert!(matches!(
        indicator_series("bollinger", r#"{"period":3,"k":-1}"#, &c),
        Err(IndicatorError::InvalidParams(_))
    ));
}

#[test]
fn non_finite_input_is_rejected_with_its_index() {
    let err = indicator_series("sma", r#"{"period":2}"#, &[1.0, f64::NAN, 3.0]).unwrap_err();
    assert_eq!(err, IndicatorError::NonFiniteInput(1));
}

#[test]
fn catalog_lists_every_indicator_with_params_and_warmup() {
    let v: serde_json::Value = serde_json::from_str(&list_indicators_json()).unwrap();
    let names: Vec<&str> = v["indicators"]
        .as_array()
        .unwrap()
        .iter()
        .map(|i| i["name"].as_str().unwrap())
        .collect();
    assert_eq!(names, ["sma", "ema", "rsi", "macd", "bollinger", "atr"]);
    let sma = &v["indicators"][0];
    assert_eq!(sma["input"], "close");
    assert_eq!(sma["params"][0]["name"], "period");
    assert_eq!(sma["params"][0]["required"], true);
    assert_eq!(sma["warmup"], "period - 1");
    assert_eq!(v["warmup_value"], "NaN");
}

#[test]
fn finite_input_that_overflows_is_an_error_not_infinity() {
    let big = [1e308, 1e308, 1e308];
    let err = indicator_series("sma", r#"{"period":2}"#, &big).unwrap_err();
    assert_eq!(err, IndicatorError::NonFiniteOutput(1));
    assert!(err.to_string().contains("index 1"));
}

#[test]
fn leading_warmup_nan_is_not_an_overflow_error() {
    let out = indicator_series("sma", r#"{"period":2}"#, &[1e300, 1e300]).unwrap();
    assert!(out[0].is_nan());
    assert_eq!(out[1], 1e300);
}
