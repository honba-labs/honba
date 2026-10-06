//! Tests for the currency minor-unit table and exponent-generic money maths
//! (ADR 0011).

use crate::instrument::{
    ceil_major_to_minor, floor_major_to_minor, format_minor_amount, minor_to_major,
    round_major_to_minor_exp, round_to_minor_price,
};
use crate::{Currency, Money, MoneyError};

#[test]
fn every_currency_has_a_minor_unit_and_exponent_two() {
    for c in Currency::ALL {
        assert_eq!(c.minor_exponent(), 2, "{c}");
        let u = c.minor_unit();
        assert!(!u.singular.is_empty() && !u.plural.is_empty(), "{c}");
    }
}

#[test]
fn minor_unit_names_per_currency() {
    let names = |c: Currency| (c.minor_unit().singular, c.minor_unit().plural);
    assert_eq!(names(Currency::Inr), ("paisa", "paise"));
    assert_eq!(names(Currency::Usd), ("cent", "cents"));
    assert_eq!(names(Currency::Eur), ("cent", "cents"));
    assert_eq!(names(Currency::Gbp), ("penny", "pence"));
}

#[test]
fn format_minor_uses_singular_only_for_one() {
    let f = |a, c| Money::new(a, c).format_minor();
    assert_eq!(f(1250, Currency::Inr), "1,250 paise");
    assert_eq!(f(1, Currency::Inr), "1 paisa");
    assert_eq!(f(0, Currency::Inr), "0 paise");
    assert_eq!(f(-1250, Currency::Inr), "-1,250 paise");
    assert_eq!(f(1, Currency::Usd), "1 cent");
    assert_eq!(f(300, Currency::Gbp), "300 pence");
    assert_eq!(f(-1, Currency::Gbp), "-1 penny");
    assert_eq!(
        f(i64::MIN, Currency::Usd),
        "-9,223,372,036,854,775,808 cents"
    );
}

#[test]
fn format_minor_amount_groups_thousands() {
    assert_eq!(format_minor_amount(1_234_567, "x", "xs"), "1,234,567 xs");
    assert_eq!(format_minor_amount(999, "x", "xs"), "999 xs");
}

#[test]
fn rounding_is_half_away_from_zero_at_exponent_zero() {
    assert_eq!(round_major_to_minor_exp(2.5, 0), Ok(3));
    assert_eq!(round_major_to_minor_exp(-2.5, 0), Ok(-3));
    assert_eq!(round_major_to_minor_exp(2.4, 0), Ok(2));
}

#[test]
fn rounding_is_half_away_from_zero_at_exponent_three() {
    assert_eq!(round_major_to_minor_exp(1.0005, 3), Ok(1001));
    assert_eq!(round_major_to_minor_exp(-1.0005, 3), Ok(-1001));
    assert_eq!(round_major_to_minor_exp(1.0004, 3), Ok(1000));
}

#[test]
fn payout_floors_and_stake_ceils_at_non_two_exponents() {
    assert_eq!(floor_major_to_minor(10.7, 0), Ok(10));
    assert_eq!(floor_major_to_minor(-10.2, 0), Ok(-11));
    assert_eq!(ceil_major_to_minor(10.2, 0), Ok(11));
    assert_eq!(floor_major_to_minor(1.2349, 3), Ok(1234));
    assert_eq!(floor_major_to_minor(-1.2341, 3), Ok(-1235));
    assert_eq!(ceil_major_to_minor(1.2341, 3), Ok(1235));
    // Float noise below a millionth of a minor unit is exact: 0.1 * 3 = 300 mils.
    assert_eq!(ceil_major_to_minor(0.1 * 3.0, 3), Ok(300));
    assert_eq!(floor_major_to_minor(0.1 * 3.0, 3), Ok(300));
}

#[test]
fn generic_conversions_reject_non_finite_and_overflow() {
    for exp in [0u8, 2, 3] {
        assert_eq!(
            round_major_to_minor_exp(f64::NAN, exp),
            Err(MoneyError::NonFinite)
        );
        assert_eq!(
            floor_major_to_minor(f64::INFINITY, exp),
            Err(MoneyError::NonFinite)
        );
        assert_eq!(ceil_major_to_minor(1e300, exp), Err(MoneyError::Overflow));
    }
}

#[test]
fn minor_to_major_divides_by_the_exponent() {
    assert_eq!(minor_to_major(12_345, 0), 12_345.0);
    assert_eq!(minor_to_major(12_345, 2), 123.45);
    assert_eq!(minor_to_major(12_345, 3), 12.345);
}

#[test]
fn price_rounds_to_the_minor_unit_at_each_exponent() {
    assert_eq!(round_to_minor_price(10.5, 0), 11.0);
    assert_eq!(round_to_minor_price(-10.5, 0), -11.0);
    assert_eq!(round_to_minor_price(10.0005, 3), 10.001);
    assert_eq!(round_to_minor_price(10.005, 2), 10.01);
}

#[test]
fn money_conversions_agree_with_the_generic_functions() {
    let c = Currency::Usd;
    let e = c.minor_exponent();
    for v in [0.0, 10.005, -10.005, 123.456, 0.1 * 3.0] {
        assert_eq!(
            Money::from_major_f64(v, c).unwrap().minor(),
            round_major_to_minor_exp(v, e).unwrap()
        );
        assert_eq!(
            Money::payout_from_major_f64(v, c).unwrap().minor(),
            floor_major_to_minor(v, e).unwrap()
        );
        assert_eq!(
            Money::stake_from_major_f64(v, c).unwrap().minor(),
            ceil_major_to_minor(v, e).unwrap()
        );
    }
}
