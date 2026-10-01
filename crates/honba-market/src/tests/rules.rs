//! Unit tests for `crate::rules`.

use crate::{InstrumentRules, MarketError, NullSymbolGrammar, PriceBand, SymbolGrammar};

fn violation(r: crate::Result<()>) -> String {
    match r {
        Err(MarketError::RuleViolation(msg)) => msg,
        other => panic!("expected RuleViolation, got {other:?}"),
    }
}

#[test]
fn new_rules_default_min_quantity_to_one_lot() {
    let r = InstrumentRules::new(25.0, 0.05);
    assert_eq!(r.min_order_quantity, 25.0);
    assert_eq!(r.max_order_quantity, None);
    assert_eq!(r.with_max_quantity(1800.0).max_order_quantity, Some(1800.0));
}

#[test]
fn quantity_must_be_a_lot_multiple_within_bounds() {
    let r = InstrumentRules::new(25.0, 0.05).with_max_quantity(100.0);
    assert_eq!(r.validate_quantity(25.0), Ok(()));
    assert_eq!(r.validate_quantity(100.0), Ok(()));
    assert!(violation(r.validate_quantity(10.0)).contains("below minimum"));
    assert!(violation(r.validate_quantity(125.0)).contains("freeze limit"));
    assert!(violation(r.validate_quantity(30.0)).contains("multiple of lot size"));
}

#[test]
fn price_must_be_positive_and_on_tick() {
    let r = InstrumentRules::new(1.0, 0.05);
    assert_eq!(r.validate_price(100.05), Ok(()));
    assert!(violation(r.validate_price(0.0)).contains("must be positive"));
    assert!(violation(r.validate_price(100.03)).contains("tick size"));
}

#[test]
fn price_band_is_inclusive() {
    let b = PriceBand::new(90.0, 110.0);
    assert!(b.contains(90.0) && b.contains(110.0));
    assert!(!b.contains(89.99) && !b.contains(110.01));
}

#[test]
fn default_symbol_grammar_trims_uppercases_and_rejects_blank() {
    let g = NullSymbolGrammar;
    assert_eq!(g.normalize("  reliance "), "RELIANCE");
    assert_eq!(g.validate("TCS"), Ok(()));
    assert!(matches!(
        g.validate("   "),
        Err(MarketError::InvalidSymbol { .. })
    ));
}
