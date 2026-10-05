//! Tests for the stable error-code taxonomy (ADR 0011, plan.md E0-S5).

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
    // The plan (plan.md 4.2) requires `retryable` to be set for anything a
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
