//! Input-validation and scan-shape tests for the pure screener module.

use crate::screener::{
    evaluate_group_json, evaluate_predicate_json, scan_group_json, scan_predicate_json,
};
use serde_json::Value;

/// Two bars as `[open, high, low, close, volume]`.
fn one_bar() -> [Vec<f64>; 5] {
    [
        vec![10.4, 10.65],
        vec![11.0, 11.25],
        vec![10.0, 10.25],
        vec![10.5, 10.75],
        vec![1000.0, 1002.0],
    ]
}

#[test]
fn scan_predicate_reports_result_and_metrics() {
    let [o, h, l, c, v] = one_bar();
    let json = scan_predicate_json(
        r#"{"key":"close","op":"gt","value":10}"#,
        &o,
        &h,
        &l,
        &c,
        &v,
    )
    .unwrap();
    let scan: Value = serde_json::from_str(&json).unwrap();
    assert_eq!(scan["result"], true);
    assert_eq!(scan["metrics"]["close"], 10.75);
}

#[test]
fn scan_predicate_includes_the_right_side_metric() {
    let [o, h, l, c, v] = one_bar();
    let json = scan_predicate_json(
        r#"{"key":"open","op":"lt","value":{"key":"close"}}"#,
        &o,
        &h,
        &l,
        &c,
        &v,
    )
    .unwrap();
    let scan: Value = serde_json::from_str(&json).unwrap();
    assert_eq!(scan["result"], true);
    assert_eq!(scan["metrics"]["open"], 10.65);
    assert_eq!(scan["metrics"]["close"], 10.75);
}

#[test]
fn scan_group_reports_the_result_and_metric_keys() {
    let [o, h, l, c, v] = one_bar();
    let json = scan_group_json(
        r#"{"operator":"AND","items":[{"key":"close","op":"gt","value":10},{"key":"open","op":"lt","value":11}]}"#,
        &o,
        &h,
        &l,
        &c,
        &v,
    )
    .unwrap();
    let scan: Value = serde_json::from_str(&json).unwrap();
    assert_eq!(scan["result"], true);
    assert_eq!(scan["metrics"]["close"], 10.75);
    assert_eq!(scan["metrics"]["open"], 10.65);
}

#[test]
fn empty_group_is_true() {
    let [o, h, l, c, v] = one_bar();
    let json = scan_group_json(r#"{"operator":"AND","items":[]}"#, &o, &h, &l, &c, &v).unwrap();
    assert_eq!(
        serde_json::from_str::<Value>(&json).unwrap()["result"],
        true
    );
}

#[test]
fn empty_series_gives_false_and_null_metrics() {
    let json = scan_predicate_json(
        r#"{"key":"close","op":"gt","value":10}"#,
        &[],
        &[],
        &[],
        &[],
        &[],
    )
    .unwrap();
    let scan: Value = serde_json::from_str(&json).unwrap();
    assert_eq!(scan["result"], false);
    assert!(scan["metrics"]["close"].is_null());
}

#[test]
fn non_finite_input_is_rejected_with_its_series_and_index() {
    let err = scan_predicate_json(
        r#"{"key":"close","op":"gt","value":10}"#,
        &[10.0, 11.0],
        &[12.0, f64::NAN],
        &[9.0, 10.0],
        &[10.5, 10.75],
        &[1.0, 2.0],
    )
    .unwrap_err();
    assert_eq!(
        err,
        "validation_invalid_request: non-finite high input at index 1"
    );
}

#[test]
fn unequal_lengths_are_refused() {
    let err = scan_predicate_json(
        r#"{"key":"close","op":"gt","value":10}"#,
        &[10.0, 11.0],
        &[12.0],
        &[9.0, 10.0],
        &[10.5, 10.75],
        &[1.0, 2.0],
    )
    .unwrap_err();
    assert!(
        err.contains("validation_invalid_request: series lengths differ"),
        "{err}"
    );
}

#[test]
fn inconsistent_ohlc_rows_are_refused_before_bar_construction() {
    let err = scan_predicate_json(
        r#"{"key":"close","op":"gt","value":10}"#,
        &[10.65],
        &[10.0],
        &[11.0],
        &[10.75],
        &[1.0],
    )
    .unwrap_err();
    assert_eq!(
        err,
        "validation_invalid_request: invalid bar at index 0: low is above high"
    );
}

#[test]
fn malformed_json_is_refused_with_the_parser_message() {
    let [o, h, l, c, v] = one_bar();
    let err = scan_predicate_json("not json", &o, &h, &l, &c, &v).unwrap_err();
    assert!(
        err.starts_with("validation_invalid_request: invalid predicate:"),
        "{err}"
    );
    let err = scan_group_json(r#"{"operator":"XOR","items":[]}"#, &o, &h, &l, &c, &v).unwrap_err();
    assert!(err.contains("not AND or OR"), "{err}");
}

#[test]
fn unsupported_metrics_are_errors_not_false() {
    let [o, h, l, c, v] = one_bar();
    assert!(evaluate_predicate_json(
        r#"{"key":"market_cap","op":"gt","value":1}"#,
        &o,
        &h,
        &l,
        &c,
        &v
    )
    .is_err());
    let err = evaluate_predicate_json(
        r#"{"key":"close","op":"gt","value":{"key":"ema20"}}"#,
        &o,
        &h,
        &l,
        &c,
        &v,
    )
    .unwrap_err();
    assert!(err.contains("ema20"), "{err}");
}

#[test]
fn non_numeric_ordering_operand_is_an_error() {
    let [o, h, l, c, v] = one_bar();
    let err = evaluate_predicate_json(
        r#"{"key":"close","op":"gt","value":"5"}"#,
        &o,
        &h,
        &l,
        &c,
        &v,
    )
    .unwrap_err();
    assert!(err.contains("cannot order a number against"), "{err}");
}

#[test]
fn evaluate_group_does_not_short_circuit_an_unsupported_branch() {
    let [o, h, l, c, v] = one_bar();
    // The first branch is true, yet the unsupported branch must still surface as an error.
    let err = evaluate_group_json(
        r#"{"operator":"OR","items":[{"key":"close","op":"gt","value":1},{"key":"market_cap","op":"gt","value":1}]}"#,
        &o,
        &h,
        &l,
        &c,
        &v,
    )
    .unwrap_err();
    assert!(err.contains("market_cap"), "{err}");
}
