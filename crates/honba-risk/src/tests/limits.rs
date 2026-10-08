//! `RiskLimits` serde contract (ADR 0018 decision 8).

use crate::{OrderRateLimit, RiskLimits};

#[test]
fn default_has_no_limits() {
    let l = RiskLimits::default();
    assert_eq!(l.max_notional, None);
    assert_eq!(l.order_rate, None);
}

#[test]
fn parses_from_json_and_toml_shaped_values() {
    let l: RiskLimits = serde_json::from_str(
        r#"{"max_notional": 500000.0, "order_rate": {"max_orders": 30, "window_ms": 1000}}"#,
    )
    .unwrap();
    assert_eq!(l.max_notional, Some(500000.0));
    assert_eq!(
        l.order_rate,
        Some(OrderRateLimit {
            max_orders: 30,
            window_ms: 1000
        })
    );
    let empty: RiskLimits = serde_json::from_str("{}").unwrap();
    assert_eq!(empty, RiskLimits::default());
}

#[test]
fn unknown_fields_are_rejected() {
    assert!(serde_json::from_str::<RiskLimits>(r#"{"max_notinal": 1.0}"#).is_err());
    assert!(serde_json::from_str::<RiskLimits>(
        r#"{"order_rate": {"max_orders": 1, "window_ms": 1, "burst": 2}}"#
    )
    .is_err());
}

#[test]
fn round_trips() {
    let l = RiskLimits {
        max_notional: Some(1.5),
        order_rate: Some(OrderRateLimit {
            max_orders: 2,
            window_ms: 3,
        }),
    };
    let back: RiskLimits = serde_json::from_str(&serde_json::to_string(&l).unwrap()).unwrap();
    assert_eq!(back, l);
}
