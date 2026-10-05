//! `run_strategy` validates strategy parameters before constructing anything:
//! out-of-range, negative or non-integer params give a typed `Err` (a
//! `ValueError` through the binding), never a panic or an allocation abort.
//! Runs through the Rust API only; no Python interpreter.

use honba::pyclasses::run::{run_strategy_json, MAX_SMA_PERIOD};

const INSTRUMENT: &str = r#"{"symbol": "RELIANCE", "exchange": "NSE"}"#;

fn run_sma(fast: &str, slow: &str, quantity: &str) -> Result<String, String> {
    let params = format!(
        r#"{{"instrument_id": {INSTRUMENT}, "fast": {fast}, "slow": {slow}, "quantity": {quantity}}}"#
    );
    run_strategy_json("sma_crossover", &params, "[]", "[]", 0.0)
}

#[test]
fn valid_params_still_run() {
    assert!(run_sma("2", "3", "1.0").is_ok());
    let max = MAX_SMA_PERIOD.to_string();
    assert!(run_sma("2", &max, "1.0").is_ok());
}

#[test]
fn huge_periods_are_refused_not_allocated() {
    for (fast, slow) in [
        ("1099511627776", "1099511627777"),       // 2**40: used to abort
        ("2", "18446744073709551614"),            // ~2**64-2: used to panic
        ("2", "18446744073709551615"),            // u64::MAX
        ("2", &(MAX_SMA_PERIOD + 1).to_string()), // just over the cap
    ] {
        let err = run_sma(fast, slow, "1.0").unwrap_err();
        assert!(err.contains("at most"), "fast={fast} slow={slow}: {err}");
    }
}

#[test]
fn malformed_periods_are_typed_errors() {
    for (fast, slow) in [
        ("-1", "5"),
        ("2", "-5"),
        ("2.5", "5"),
        ("2", "5.5"),
        (r#""2""#, "5"),
        ("null", "5"),
        ("18446744073709551616", "18446744073709551617"), // beyond u64
        ("1e30", "1e31"),
        ("0", "5"),
        ("5", "5"),
        ("6", "5"),
    ] {
        let err = run_sma(fast, slow, "1.0").unwrap_err();
        assert!(!err.is_empty(), "fast={fast} slow={slow}");
    }
}

#[test]
fn malformed_quantity_is_a_typed_error() {
    for q in ["null", r#""x""#, "1e999"] {
        assert!(run_sma("2", "3", q).is_err(), "quantity {q}");
    }
}

#[test]
fn a_non_finite_or_overflowing_initial_cash_is_a_typed_error_not_a_panic() {
    // ADR 0011: initial cash crosses into integer Money at the boundary; a NaN
    // used to `unwrap()` a MoneyError and panic across the FFI.
    let params = format!(r#"{{"instrument_id": {INSTRUMENT}, "quantity": 1.0}}"#);
    for bad in [f64::NAN, f64::INFINITY, f64::NEG_INFINITY, 1e30] {
        let err = run_strategy_json("buy_and_hold", &params, "[]", "[]", bad).unwrap_err();
        assert!(err.contains("initial_cash"), "{bad}: {err}");
    }
}
