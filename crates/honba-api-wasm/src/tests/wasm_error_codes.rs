//! Stable `reason: message` errors on the wasm compute surface (ADR 0012 rule 4).
//!
//! Every wasm refusal is `reason: message` where `reason` is stable and machine-readable: the
//! [`ErrorCode`] for input validation on the screener surface
//! (`validation_invalid_request`, reusing [`WasmScreenerError::reason`]), the evaluator's own
//! reason (`unsupported_metric`, `invalid_operand`, `invalid_filter`), or a [`ReplayError`]
//! reason on the replay surface. Message text stays human; the reason is what a caller matches.

use crate::replay::ReplayError;
use crate::screener::WasmScreenerError;
use honba_messages::ErrorCode;

fn reason_of(error: String) -> String {
    match error.split_once(": ") {
        Some((reason, _)) => reason.to_string(),
        None => error,
    }
}

#[test]
fn screener_validation_refusals_carry_the_rest_code() {
    let code = ErrorCode::ValidationInvalidRequest;
    assert_eq!(
        code.as_str(),
        WasmScreenerError::InvalidJson {
            what: "predicate",
            message: String::new(),
        }
        .reason()
    );
    for error in [
        WasmScreenerError::LengthMismatch {
            open: 2,
            high: 1,
            low: 2,
            close: 2,
            volume: 2,
        },
        WasmScreenerError::NonFiniteInput {
            series: "close",
            index: 0,
        },
        WasmScreenerError::InvalidBar {
            index: 0,
            message: "low is above high",
        },
    ] {
        assert_eq!(error.reason(), code.as_str());
    }
}

#[test]
fn indicator_reasons_are_all_distinct_and_stable() {
    use crate::indicators::IndicatorError;
    for (error, reason) in [
        (
            IndicatorError::UnknownIndicator("x".into()),
            "unknown_indicator",
        ),
        (IndicatorError::InvalidParams("y".into()), "invalid_params"),
        (IndicatorError::NonFiniteInput(0), "non_finite_input"),
        (IndicatorError::NonFiniteOutput(1), "non_finite_output"),
        (IndicatorError::InvertedRange(2), "inverted_range"),
        (
            IndicatorError::LengthMismatch {
                high: 2,
                low: 1,
                close: 2,
            },
            "length_mismatch",
        ),
    ] {
        assert_eq!(error.reason(), reason);
    }
}

#[test]
fn evaluator_reasons_reach_the_wasm_surface_unchanged() {
    use crate::screener::{evaluate_predicate_json, scan_predicate_json};
    let (o, h, l, c, v) = (
        [10.0, 11.0],
        [12.0, 13.0],
        [9.0, 10.0],
        [10.5, 10.75],
        [1.0, 2.0],
    );
    let err = evaluate_predicate_json(
        r#"{"key":"market_cap","op":"gt","value":1}"#,
        &o,
        &h,
        &l,
        &c,
        &v,
    )
    .unwrap_err();
    assert!(
        err.starts_with("unsupported_metric:"),
        "evaluator reason must lead: {err}"
    );
    let err = scan_predicate_json(
        r#"{"key":"close","op":"gt","value":{"key":"ema20"}}"#,
        &o,
        &h,
        &l,
        &c,
        &v,
    )
    .unwrap_err();
    assert!(
        err.starts_with("unsupported_metric:"),
        "evaluator reason must lead: {err}"
    );
    let err = scan_predicate_json(r#"{"key":"close","op":"gt"}"#, &o, &h, &l, &c, &v).unwrap_err();
    assert!(
        reason_of(err.clone()) == "invalid_filter"
            || reason_of(err) == "validation_invalid_request",
        "a malformed predicate carries a stable reason"
    );
}

#[test]
fn replay_reasons_are_all_distinct_and_stable() {
    let cases = [
        (ReplayError::EmptyJournal, "empty_journal"),
        (ReplayError::InvalidHeaderJson, "invalid_header_json"),
        (ReplayError::MissingHeader, "missing_header"),
        (ReplayError::InvalidHeader, "invalid_header"),
        (
            ReplayError::UnsupportedJournalVersion,
            "unsupported_journal_version",
        ),
        (ReplayError::InvalidRecord, "invalid_record"),
        (ReplayError::HeaderSerialize, "header_serialize"),
    ];
    for (error, reason) in cases {
        assert_eq!(error.reason(), reason);
        assert_eq!(
            error.to_string(),
            format!("{reason}: journal rejected"),
            "{reason} display drifts"
        );
    }
}

#[test]
fn every_replay_reason_is_machine_readable() {
    for error in [
        ReplayError::EmptyJournal,
        ReplayError::InvalidHeaderJson,
        ReplayError::MissingHeader,
        ReplayError::InvalidHeader,
        ReplayError::UnsupportedJournalVersion,
        ReplayError::InvalidRecord,
        ReplayError::HeaderSerialize,
    ] {
        assert!(!error.reason().contains(' '), "{error:?}");
        assert_eq!(
            reason_of(format!("{}: dropped", error.reason())),
            error.reason()
        );
    }
}
