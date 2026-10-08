//! Rules 4-6: quantity bounds and lot multiple.

use super::{refusal, req, stage};
use crate::{RiskCheck, RiskDecision, RiskRefusal};

fn with_qty(q: f64) -> crate::RiskRequest {
    crate::RiskRequest {
        quantity: q,
        ..req()
    }
}

#[test]
fn quantity_below_min() {
    assert_eq!(
        refusal(stage().check(&with_qty(10.0))),
        RiskRefusal::QuantityBelowMin {
            quantity: 10.0,
            min: 25.0
        }
    );
}

#[test]
fn quantity_over_freeze() {
    assert_eq!(
        refusal(stage().check(&with_qty(125.0))),
        RiskRefusal::QuantityOverFreeze {
            quantity: 125.0,
            max: 100.0
        }
    );
}

#[test]
fn lot_multiple_refused() {
    assert_eq!(
        refusal(stage().check(&with_qty(30.0))),
        RiskRefusal::LotMultiple {
            quantity: 30.0,
            lot: 25.0
        }
    );
}

#[test]
fn quantity_boundaries_are_approved() {
    // min and freeze max are inclusive; lot multiples in between pass.
    for q in [25.0, 50.0, 100.0] {
        assert_eq!(stage().check(&with_qty(q)), RiskDecision::Approved, "{q}");
    }
}

#[test]
fn quantity_rules_precede_price_rules() {
    let mut r = with_qty(30.0);
    r.price = Some(-5.0);
    assert!(matches!(
        refusal(stage().check(&r)),
        RiskRefusal::LotMultiple { .. }
    ));
}
