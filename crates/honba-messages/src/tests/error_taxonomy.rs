//! Tests for the stable error-code taxonomy (ADR 0011, docs/archive/plan.md E0-S5).

use serde_json::json;

use crate::{ErrorCategory, ErrorCode};

#[test]
fn every_code_round_trips_through_snake_case_json() {
    for code in ErrorCode::ALL {
        let text = serde_json::to_string(code).expect("serialize code");
        let parsed: ErrorCode = serde_json::from_str(&text).expect("deserialize code");
        assert_eq!(*code, parsed, "round trip failed for {}", text);
    }
}

#[test]
fn codes_are_stable_snake_case_strings() {
    assert_eq!(
        serde_json::to_value(ErrorCode::RiskMaxNotionalExceeded).unwrap(),
        json!("risk_max_notional_exceeded")
    );
    assert_eq!(
        serde_json::to_value(ErrorCode::ValidationInvalidRequest).unwrap(),
        json!("validation_invalid_request")
    );
}

#[test]
fn every_code_declares_a_category() {
    for code in ErrorCode::ALL {
        assert!(
            !code.category().as_str().is_empty(),
            "{code:?} has no category"
        );
    }
}

#[test]
fn retryable_flags_are_set_only_for_transport_level_codes() {
    // The plan (docs/archive/plan.md 4.2) requires `retryable` to be set for anything a
    // caller may safely repeat. Domain/risk/validation refusals must never
    // claim to be retryable, or a caller would loop forever.
    for code in ErrorCode::ALL {
        match code.category() {
            ErrorCategory::Validation | ErrorCategory::Risk | ErrorCategory::Order => {
                assert!(!code.is_retryable(), "{code:?} must not be retryable");
            }
            _ => {}
        }
    }
    assert!(ErrorCode::Timeout.is_retryable());
    assert!(ErrorCode::TransportError.is_retryable());
    assert!(ErrorCode::RateLimited.is_retryable());
}

#[test]
fn category_serializes_as_snake_case() {
    assert_eq!(
        serde_json::to_value(ErrorCategory::MarketData).unwrap(),
        json!("market_data")
    );
    assert_eq!(
        serde_json::from_value::<ErrorCategory>(json!("market_data")).unwrap(),
        ErrorCategory::MarketData
    );
}

#[test]
fn an_unknown_code_is_rejected_rather_than_silently_defaulted() {
    // Silent defaulting would turn a newer server's error into a wrong-but-
    // plausible one. Readers must reject what they do not know.
    let err = serde_json::from_value::<ErrorCode>(json!("totally_new_code"));
    assert!(err.is_err(), "unknown error code must not deserialize");
}

#[test]
fn not_implemented_is_an_unsupported_category_code_that_is_not_retryable() {
    assert_eq!(
        serde_json::to_value(ErrorCode::NotImplemented).unwrap(),
        json!("not_implemented")
    );
    assert_eq!(ErrorCode::NotImplemented.as_str(), "not_implemented");
    assert_eq!(
        ErrorCode::NotImplemented.category(),
        ErrorCategory::Unsupported
    );
    assert!(!ErrorCode::NotImplemented.is_retryable());
}

#[test]
fn pre_gate_refusal_codes_have_their_adr_0018_spelling_and_category() {
    // ADR 0018 decision 5 / ADR 0019 decision 5: the pre-gate `Rejected.reason`.
    for (code, wire, category) in [
        (
            ErrorCode::RiskTradingHalted,
            "risk_trading_halted",
            ErrorCategory::Risk,
        ),
        (
            ErrorCode::OrderExecutionUnavailable,
            "order_execution_unavailable",
            ErrorCategory::Order,
        ),
    ] {
        assert_eq!(serde_json::to_value(code).unwrap(), json!(wire));
        assert_eq!(code.as_str(), wire);
        assert_eq!(code.category(), category);
        assert!(!code.is_retryable(), "{wire} must not be retryable");
    }
}

#[test]
fn risk_stage_refusal_codes_have_their_adr_0018_spelling_and_category() {
    // ADR 0018 decision 5: one wire code per risk rule refusal.
    for (code, wire) in [
        (ErrorCode::RiskOrderRateExceeded, "risk_order_rate_exceeded"),
        (ErrorCode::RiskQuantityBelowMin, "risk_quantity_below_min"),
        (
            ErrorCode::RiskQuantityOverFreeze,
            "risk_quantity_over_freeze",
        ),
        (
            ErrorCode::RiskLotMultipleViolation,
            "risk_lot_multiple_violation",
        ),
        (ErrorCode::RiskTickSizeViolation, "risk_tick_size_violation"),
        (ErrorCode::RiskPriceBandExceeded, "risk_price_band_exceeded"),
        (
            ErrorCode::RiskReduceOnlyViolation,
            "risk_reduce_only_violation",
        ),
        (ErrorCode::RiskInstrumentUnknown, "risk_instrument_unknown"),
    ] {
        assert_eq!(serde_json::to_value(code).unwrap(), json!(wire));
        assert_eq!(code.as_str(), wire);
        assert_eq!(code.category(), ErrorCategory::Risk);
        assert!(!code.is_retryable(), "{wire} must not be retryable");
        assert!(ErrorCode::ALL.contains(&code));
    }
}
