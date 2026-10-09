//! Rule 9: maximum notional.

use honba_entities::{Currency, Money};

use super::{refusal, req, stage_with, x};
use crate::{
    OrderRateLimit, RiskCheck, RiskConfigError, RiskDecision, RiskLimits, RiskRefusal, RiskRequest,
    RiskStage,
};

fn limit(max: f64) -> RiskLimits {
    RiskLimits {
        max_notional: Some(max),
        order_rate: None,
        max_participation: None,
        stale_after_ms: None,
    }
}

fn inr(major: f64) -> Money {
    Money::from_major_f64(major, Currency::Inr).unwrap()
}

#[test]
fn max_notional_refused() {
    // 50 @ 100.0 = 5000.0 against a 4999.95 limit.
    let r = RiskRequest {
        quantity: 50.0,
        ..req()
    };
    let got = refusal(stage_with(limit(4999.95)).check(&r));
    assert_eq!(
        got,
        RiskRefusal::MaxNotional {
            notional: inr(5000.0),
            limit: inr(4999.95)
        }
    );
    assert_eq!(
        got.error_code(),
        honba_messages::ErrorCode::RiskMaxNotionalExceeded
    );
    // Exactly at the limit is approved.
    assert_eq!(stage_with(limit(5000.0)).check(&r), RiskDecision::Approved);
}

#[test]
fn max_notional_unset_skips_the_rule() {
    let r = RiskRequest {
        price: None,
        reference_price: None,
        ..req()
    };
    assert_eq!(
        stage_with(RiskLimits::default()).check(&r),
        RiskDecision::Approved
    );
}

#[test]
fn max_notional_uses_trigger_then_reference() {
    let mut stage = stage_with(limit(2600.0));
    // price wins over trigger and reference: 25 * 100 = 2500 <= 2600.
    let r = RiskRequest {
        price: Some(100.0),
        trigger_price: Some(109.0),
        reference_price: Some(109.0),
        ..req()
    };
    assert_eq!(stage.check(&r), RiskDecision::Approved);

    // No price: the trigger is used (25 * 109 = 2725 > 2600).
    let r = RiskRequest {
        price: None,
        trigger_price: Some(109.0),
        reference_price: Some(100.0),
        ..req()
    };
    assert_eq!(
        refusal(stage.check(&r)),
        RiskRefusal::MaxNotional {
            notional: inr(2725.0),
            limit: inr(2600.0)
        }
    );

    // Neither: the reference price is used.
    let r = RiskRequest {
        price: None,
        trigger_price: None,
        reference_price: Some(109.0),
        ..req()
    };
    assert_eq!(
        refusal(stage.check(&r)),
        RiskRefusal::MaxNotional {
            notional: inr(2725.0),
            limit: inr(2600.0)
        }
    );
}

#[test]
fn max_notional_unpriceable_refused() {
    let r = RiskRequest {
        price: None,
        trigger_price: None,
        reference_price: None,
        ..req()
    };
    let got = refusal(stage_with(limit(2600.0)).check(&r));
    assert_eq!(
        got,
        RiskRefusal::MaxNotionalUnpriceable { limit: inr(2600.0) }
    );
    assert_eq!(got.context()["reason"], "unpriceable");
    assert_eq!(
        got.error_code(),
        honba_messages::ErrorCode::RiskMaxNotionalExceeded
    );
}

#[test]
fn invalid_limits_rejected_by_new() {
    for bad in [0.0, -1.0, f64::NAN, f64::INFINITY] {
        let err = RiskStage::new(limit(bad), Currency::Inr, super::rules()).err();
        assert!(
            matches!(err, Some(RiskConfigError::InvalidMaxNotional(_))),
            "{bad}"
        );
    }
    let rate = |max_orders, window_ms| RiskLimits {
        max_notional: None,
        order_rate: Some(OrderRateLimit {
            max_orders,
            window_ms,
        }),
        max_participation: None,
        stale_after_ms: None,
    };
    assert!(matches!(
        RiskStage::new(rate(0, 1000), Currency::Inr, super::rules()).err(),
        Some(RiskConfigError::InvalidOrderRate)
    ));
    assert!(matches!(
        RiskStage::new(rate(1, 0), Currency::Inr, super::rules()).err(),
        Some(RiskConfigError::InvalidOrderRate)
    ));
    assert!(RiskStage::new(rate(1, 1), Currency::Inr, super::rules()).is_ok());
    let _ = x();
}
