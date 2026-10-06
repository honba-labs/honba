//! Unit tests for `errors`.

use crate::*;

#[test]
fn as_str_matches_serde_spelling() {
    // The hand-written match and the serde rename_all must agree, or the
    // string a client reads differs from the string JSON carries.
    for code in ErrorCode::ALL {
        let via_serde = serde_json::to_value(code).unwrap();
        assert_eq!(via_serde, json_string(code.as_str()), "{code:?}");
    }
}

#[test]
fn error_detail_derives_retryability_and_keeps_context() {
    let d = ErrorDetail::new(ErrorCode::RiskMaxNotionalExceeded, "too big")
        .with_context(serde_json::json!({"limit": 100000}));
    assert!(!d.retryable);
    assert_eq!(d.context.unwrap()["limit"], serde_json::json!(100000));
}

#[test]
fn context_is_omitted_from_the_wire_when_absent() {
    let d = ErrorDetail::new(ErrorCode::NotFound, "nope");
    let v = serde_json::to_value(&d).unwrap();
    assert!(v.get("context").is_none(), "got {v}");
}

fn json_string(s: &str) -> serde_json::Value {
    serde_json::Value::String(s.to_string())
}
