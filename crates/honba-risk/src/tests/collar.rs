//! Market-order price collar (ADR 0018 addendum): `price_collar_bps`.

use honba_messages::{ErrorCode, OrderSide};

use super::{refusal, req, stage_with};
use crate::{RiskCheck, RiskDecision, RiskLimits, RiskRefusal, RiskRequest};

fn collar(bps: u32) -> RiskLimits {
    RiskLimits {
        price_collar_bps: Some(bps),
        ..Default::default()
    }
}

/// A market buy against reference 100 with the ask at `expected`.
fn market(side: OrderSide, expected: Option<f64>, reference: Option<f64>) -> RiskRequest {
    let mut r = req();
    r.side = side;
    r.price = None;
    r.reference_price = reference;
    r.expected_price = expected;
    r
}

#[test]
fn off_by_default_even_for_a_wild_expected_price() {
    let mut stage = stage_with(RiskLimits::default());
    let r = market(OrderSide::Buy, Some(500.0), Some(100.0));
    assert_eq!(stage.check(&r), RiskDecision::Approved);
}

#[test]
fn within_collar_passes_and_boundary_is_inclusive() {
    let mut stage = stage_with(collar(100)); // 1%
    for expected in [100.0, 100.5, 101.0, 99.0] {
        let r = market(OrderSide::Buy, Some(expected), Some(100.0));
        assert_eq!(stage.check(&r), RiskDecision::Approved, "{expected}");
    }
}

#[test]
fn beyond_collar_refuses_buy_and_sell() {
    let mut stage = stage_with(collar(100));
    let buy = refusal(stage.check(&market(OrderSide::Buy, Some(101.5), Some(100.0))));
    match &buy {
        RiskRefusal::PriceCollar {
            side,
            expected_price,
            reference_price,
            collar_bps,
            deviation_bps,
        } => {
            assert_eq!(*side, OrderSide::Buy);
            assert_eq!(*expected_price, 101.5);
            assert_eq!(*reference_price, 100.0);
            assert_eq!(*collar_bps, 100);
            assert!((deviation_bps - 150.0).abs() < 1e-9);
        }
        other => panic!("expected PriceCollar, got {other:?}"),
    }
    assert_eq!(buy.error_code(), ErrorCode::RiskPriceCollar);
    assert_eq!(buy.error_code().as_str(), "risk_price_collar");
    assert_eq!(buy.rule(), "price_collar");
    assert_eq!(buy.context()["rule"], "price_collar");

    let sell = refusal(stage.check(&market(OrderSide::Sell, Some(98.0), Some(100.0))));
    assert!(matches!(
        sell,
        RiskRefusal::PriceCollar {
            side: OrderSide::Sell,
            ..
        }
    ));
}

#[test]
fn limit_orders_are_unaffected() {
    let mut stage = stage_with(collar(1));
    let mut r = market(OrderSide::Buy, Some(150.0), Some(100.0));
    r.price = Some(100.0);
    assert_eq!(stage.check(&r), RiskDecision::Approved);
}

#[test]
fn stop_market_leg_is_checked() {
    let mut stage = stage_with(collar(100));
    let mut r = market(OrderSide::Buy, Some(105.0), Some(100.0));
    r.trigger_price = Some(104.0);
    assert!(matches!(
        refusal(stage.check(&r)),
        RiskRefusal::PriceCollar { .. }
    ));
}

#[test]
fn missing_expected_or_reference_skips_the_check() {
    let mut stage = stage_with(collar(1));
    for r in [
        market(OrderSide::Buy, None, Some(100.0)),
        market(OrderSide::Buy, Some(150.0), None),
        market(OrderSide::Buy, Some(f64::NAN), Some(100.0)),
        market(OrderSide::Buy, Some(150.0), Some(0.0)),
    ] {
        assert_eq!(stage.check(&r), RiskDecision::Approved);
    }
}

#[test]
fn zero_collar_is_invalid() {
    let err = collar(0).validate().unwrap_err();
    assert_eq!(err, crate::RiskConfigError::InvalidPriceCollar(0));
}
