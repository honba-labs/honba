//! Tests for integer minor-unit money (ADR 0011, plan.md E0-S6).

use crate::{Currency, Money, MoneyError};

#[test]
fn whole_major_amounts_construct_exactly_from_minor() {
    let m = Money::new(10_000, Currency::Inr);
    assert_eq!(m.minor(), 10_000);
    assert_eq!(m.currency(), Currency::Inr);
}

#[test]
fn from_major_rounds_half_away_from_zero() {
    // Half away from zero is symmetric: buys and sells bias identically.
    assert_eq!(
        Money::from_major_f64(10.005, Currency::Inr)
            .unwrap()
            .minor(),
        1001
    );
    assert_eq!(
        Money::from_major_f64(-10.005, Currency::Inr)
            .unwrap()
            .minor(),
        -1001
    );
    assert_eq!(
        Money::from_major_f64(10.004, Currency::Inr)
            .unwrap()
            .minor(),
        1000
    );
}

#[test]
fn from_major_rejects_non_finite_values() {
    for bad in [f64::NAN, f64::INFINITY, f64::NEG_INFINITY] {
        assert!(
            Money::from_major_f64(bad, Currency::Inr).is_err(),
            "accepted {bad}"
        );
    }
}

#[test]
fn from_major_rejects_overflow() {
    // i64::MAX paise is roughly 9.2e16 INR; anything above cannot be exact.
    assert!(Money::from_major_f64(1e18, Currency::Inr).is_err());
}

#[test]
fn to_major_is_exact_division() {
    let m = Money::new(12_345, Currency::Inr);
    assert_eq!(m.to_major_f64(), 123.45);
}

#[test]
fn addition_is_exact_in_minor_units() {
    // The defect this fixes: 0.1 + 0.2 != 0.3 in f64.
    let a = Money::from_major_f64(0.1, Currency::Inr).unwrap();
    let b = Money::from_major_f64(0.2, Currency::Inr).unwrap();
    let total = (a + b).unwrap();
    assert_eq!(total, Money::new(30, Currency::Inr));
}

#[test]
fn repeated_addition_does_not_drift() {
    // 10,000 fills of 95 paise must be exactly 950,000 paise, not a float
    // that is off by a fraction that compounds.
    let leg = Money::from_major_f64(0.95, Currency::Inr).unwrap();
    let mut total = Money::zero(Currency::Inr);
    for _ in 0..10_000 {
        total = (total + leg).unwrap();
    }
    assert_eq!(total.minor(), 950_000);
}

#[test]
fn quantity_times_price_rounds_to_the_nearest_minor_unit() {
    // 75 lots at 22,000.25 rounds to the paise it settles at.
    let notional = Money::mul_qty(75.0, 22_000.25, Currency::Inr).unwrap();
    assert_eq!(notional.minor(), 165_001_875);
}

#[test]
fn mul_qty_rejects_a_non_finite_quantity() {
    assert!(Money::mul_qty(f64::NAN, 100.0, Currency::Inr).is_err());
}

#[test]
fn multiplication_is_symmetric_for_buys_and_sells() {
    let buy = Money::mul_qty(10.0, 100.005, Currency::Inr).unwrap();
    let sell = Money::mul_qty(-10.0, 100.005, Currency::Inr).unwrap();
    assert_eq!(buy.minor(), -sell.minor());
}

#[test]
fn subtraction_is_exact_and_checked_for_currency() {
    let a = Money::new(10_000, Currency::Inr);
    let b = Money::new(2_500, Currency::Inr);
    assert_eq!((a - b).unwrap().minor(), 7_500);
}

#[test]
fn a_negative_balance_is_representable() {
    // Short margin and overdrafts are negative balances, not errors.
    assert_eq!(Money::new(-5_000, Currency::Inr).neg().minor(), 5_000);
}

#[test]
fn a_money_value_round_trips_through_json() {
    // Serialization emits the integer — never a JSON float — so the wire is exact.
    let m = Money::from_major_f64(123.45, Currency::Inr).unwrap();
    let v = serde_json::to_value(m).unwrap();
    assert_eq!(v["amount"], serde_json::json!(12345));
    let back: Money = serde_json::from_value(v).unwrap();
    assert_eq!(back, m);
}

#[test]
fn a_float_amount_on_the_wire_is_accepted_then_rounded() {
    // Older writers emitted floats; readers must keep parsing them rather than
    // failing a whole stream. The value is rounded to minor units once, at the door.
    let m: Money = serde_json::from_str(r#"{"amount": 123.45, "currency": "INR"}"#).unwrap();
    assert_eq!(m.minor(), 12345);
}

#[test]
fn a_non_finite_wire_amount_is_rejected() {
    // JSON has no NaN; a string smuggling one through is not a value.
    let err = serde_json::from_str::<Money>(r#"{"amount": "NaN", "currency": "INR"}"#);
    assert!(err.is_err());
}

#[test]
fn an_unknown_currency_on_the_wire_is_rejected() {
    let err = serde_json::from_str::<Money>(r#"{"amount": 100, "currency": "inr"}"#);
    assert!(err.is_err(), "lowercase currency must not parse");
}

#[test]
fn an_unknown_field_on_the_wire_is_rejected() {
    let err = serde_json::from_str::<Money>(r#"{"amount": 100, "currency": "INR", "extra": 1}"#);
    assert!(err.is_err());
}

#[test]
fn display_reads_in_major_units() {
    assert_eq!(Money::new(12_345, Currency::Inr).to_string(), "INR 123.45");
    assert_eq!(Money::new(-50, Currency::Usd).to_string(), "USD -0.50");
}

// --- Conservative edges (ADR 0011): payouts floor, stakes round up. ---

#[test]
fn a_payout_floors_to_the_minor_unit() {
    // 10.019 is 1001.9 paise: a payout never rounds up in the portfolio's favour.
    let p = Money::payout_from_major_f64(10.019, Currency::Inr).unwrap();
    assert_eq!(p.minor(), 1001);
    // Half a paisa still floors (nearest would round it up).
    assert_eq!(
        Money::payout_from_major_f64(10.005, Currency::Inr)
            .unwrap()
            .minor(),
        1000
    );
}

#[test]
fn a_negative_payout_floors_towards_negative_infinity() {
    // A loss rounds to the larger loss: equity is understated, never overstated.
    assert_eq!(
        Money::payout_from_major_f64(-10.011, Currency::Inr)
            .unwrap()
            .minor(),
        -1002
    );
}

#[test]
fn a_stake_rounds_up_to_the_minor_unit() {
    assert_eq!(
        Money::stake_from_major_f64(10.011, Currency::Inr)
            .unwrap()
            .minor(),
        1002
    );
    assert_eq!(
        Money::stake_from_major_f64(-10.019, Currency::Inr)
            .unwrap()
            .minor(),
        -1001
    );
}

#[test]
fn float_noise_on_an_exact_amount_is_not_rounded_against_anyone() {
    // 0.1 * 3 is 0.30000000000000004 in f64: a stake of 30 paise, not 31,
    // and a payout of 30, not 29 when the noise is below the paisa.
    let noisy_up = 0.1 * 3.0;
    let noisy_down = 0.7 * 3.0; // 2.0999999999999996
    assert_eq!(
        Money::stake_from_major_f64(noisy_up, Currency::Inr)
            .unwrap()
            .minor(),
        30
    );
    assert_eq!(
        Money::payout_from_major_f64(noisy_down, Currency::Inr)
            .unwrap()
            .minor(),
        210
    );
}

#[test]
fn payout_and_stake_reject_non_finite_and_overflow() {
    for bad in [f64::NAN, f64::INFINITY, f64::NEG_INFINITY] {
        assert_eq!(
            Money::payout_from_major_f64(bad, Currency::Inr),
            Err(MoneyError::NonFinite)
        );
        assert_eq!(
            Money::stake_from_major_f64(bad, Currency::Inr),
            Err(MoneyError::NonFinite)
        );
    }
    assert_eq!(
        Money::stake_from_major_f64(1e30, Currency::Inr),
        Err(MoneyError::Overflow)
    );
}
