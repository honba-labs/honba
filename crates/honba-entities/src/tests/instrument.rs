//! Unit tests for `crate::instrument`.

use crate::Currency;

#[test]
fn currency_serializes_as_iso_code() {
    for c in [Currency::Inr, Currency::Usd, Currency::Eur, Currency::Gbp] {
        let json = serde_json::to_value(c).unwrap();
        assert_eq!(json, c.code());
        assert_eq!(serde_json::from_value::<Currency>(json).unwrap(), c);
    }
}

// --- Lot and tick edges where money leaves the model (ADR 0011). ---

use crate::{Instrument, InstrumentKind, MoneyError};

fn nifty() -> Instrument {
    Instrument::new(
        super::any_instrument(),
        InstrumentKind::Future,
        Currency::Inr,
        75.0,
        0.05,
    )
}

#[test]
fn a_stake_rounds_up_to_the_next_lot_multiple() {
    let i = nifty();
    assert_eq!(i.stake_quantity(1.0), Ok(75.0));
    assert_eq!(i.stake_quantity(75.0), Ok(75.0));
    assert_eq!(i.stake_quantity(76.0), Ok(150.0));
    assert_eq!(i.stake_quantity(0.0), Ok(0.0));
}

#[test]
fn a_stake_on_a_lot_multiple_with_float_noise_is_not_bumped_a_lot() {
    // 0.1 lots * 3 = 0.30000000000000004 lots of a 0.1-lot instrument is 3 lots.
    let i = Instrument::new(
        super::any_instrument(),
        InstrumentKind::Fx,
        Currency::Usd,
        0.1,
        0.0001,
    );
    let q = i.stake_quantity(0.1 * 3.0).unwrap();
    assert!((q - 0.3).abs() < 1e-12, "{q}");
}

#[test]
fn a_stake_quantity_rejects_negative_and_non_finite_input() {
    let i = nifty();
    for bad in [-1.0, f64::NAN, f64::INFINITY] {
        assert_eq!(i.stake_quantity(bad), Err(MoneyError::InvalidQuantity));
    }
}

#[test]
fn tick_alignment_tolerates_float_noise_only() {
    let i = nifty();
    assert!(i.is_on_tick(22_000.05));
    assert!(i.is_on_tick(100.0));
    assert!(i.is_on_tick(0.1 + 0.2 - 0.25)); // 0.05000000000000002
    assert!(!i.is_on_tick(100.03));
    assert!(!i.is_on_tick(f64::NAN));
}

#[test]
fn settlement_rejects_an_off_tick_price_instead_of_snapping_it() {
    let i = nifty();
    assert_eq!(i.settle_notional(75.0, 100.03), Err(MoneyError::OffTick));
}

#[test]
fn settlement_on_tick_rounds_the_notional_once_to_minor_units() {
    let i = nifty();
    let n = i.settle_notional(75.0, 22_000.05).unwrap();
    assert_eq!(n.minor(), 165_000_375);
    assert_eq!(n.currency(), Currency::Inr);
    assert_eq!(
        i.settle_notional(f64::NAN, 100.0),
        Err(MoneyError::InvalidQuantity)
    );
}
