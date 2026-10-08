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
        max_participation: None,
    };
    let back: RiskLimits = serde_json::from_str(&serde_json::to_string(&l).unwrap()).unwrap();
    assert_eq!(back, l);
}

#[test]
fn validate_accepts_default_and_valid_limits() {
    assert_eq!(RiskLimits::default().validate(), Ok(()));
    let l = RiskLimits {
        max_notional: Some(1.0),
        order_rate: Some(OrderRateLimit {
            max_orders: 1,
            window_ms: 1,
        }),
        max_participation: None,
    };
    assert_eq!(l.validate(), Ok(()));
}

#[test]
fn validate_rejects_bad_notional_and_rate() {
    use crate::RiskConfigError;
    for v in [0.0, -1.0, f64::NAN, f64::INFINITY, f64::NEG_INFINITY] {
        let l = RiskLimits {
            max_notional: Some(v),
            order_rate: None,
            max_participation: None,
        };
        assert!(
            matches!(l.validate(), Err(RiskConfigError::InvalidMaxNotional(_))),
            "{v}"
        );
    }
    for (m, w) in [(0, 1), (1, 0), (0, 0)] {
        let l = RiskLimits {
            max_notional: None,
            order_rate: Some(OrderRateLimit {
                max_orders: m,
                window_ms: w,
            }),
            max_participation: None,
        };
        assert_eq!(l.validate(), Err(RiskConfigError::InvalidOrderRate));
    }
}

#[test]
fn live_run_without_limits_refused() {
    use crate::RiskConfigError;
    let rate = OrderRateLimit {
        max_orders: 30,
        window_ms: 1000,
    };
    for l in [
        RiskLimits::default(),
        RiskLimits {
            max_notional: Some(5.0),
            order_rate: None,
            max_participation: None,
        },
        RiskLimits {
            max_notional: None,
            order_rate: Some(rate),
            max_participation: None,
        },
    ] {
        assert_eq!(l.require_live(), Err(RiskConfigError::LiveRunWithoutLimit));
    }
    let both = RiskLimits {
        max_notional: Some(5.0),
        order_rate: Some(rate),
        max_participation: None,
    };
    assert_eq!(both.require_live(), Ok(()));
}

#[test]
fn live_run_rejects_invalid_limits_with_the_validation_error() {
    use crate::RiskConfigError;
    let l = RiskLimits {
        max_notional: Some(-1.0),
        order_rate: Some(OrderRateLimit {
            max_orders: 1,
            window_ms: 1,
        }),
        max_participation: None,
    };
    assert!(matches!(
        l.require_live(),
        Err(RiskConfigError::InvalidMaxNotional(_))
    ));
}
