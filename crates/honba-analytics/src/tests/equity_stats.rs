//! Unit tests for `crate::equity_stats`.

use super::assert_close;
use crate::{AnalyticsError, EquityStats};

#[test]
fn rejects_non_positive_or_non_finite_periods_per_year() {
    for ppy in [0.0, -1.0, f64::NAN, f64::INFINITY] {
        assert!(
            matches!(
                EquityStats::from_returns(&[0.01], ppy, 0.0),
                Err(AnalyticsError::InsufficientData { .. })
            ),
            "{ppy}"
        );
    }
}

#[test]
fn single_return_has_no_sharpe_and_zero_volatility() {
    let s = EquityStats::from_returns(&[0.05], 252.0, 0.0).unwrap();
    assert_eq!(s.n_periods, 1);
    assert_close(s.total_return, 0.05);
    assert_eq!(s.sharpe, None);
    assert_eq!(s.annualized_volatility, 0.0);
}

#[test]
fn compounds_total_and_annualized_return() {
    let s = EquityStats::from_returns(&[0.1, 0.1], 2.0, 0.0).unwrap();
    assert_close(s.total_return, 0.21);
    // Two periods at two periods/year is exactly one year.
    assert_close(s.annualized_return, 0.21);
}

#[test]
fn drawdown_is_measured_from_the_running_peak() {
    // Equity: 1.0 -> 1.5 -> 0.75 -> 1.5
    let s = EquityStats::from_returns(&[0.5, -0.5, 1.0], 252.0, 0.0).unwrap();
    assert_close(s.max_drawdown, 0.75);
    assert_close(s.max_drawdown_pct, 0.5);
    assert_close(s.calmar.unwrap(), s.annualized_return / 0.5);
}

#[test]
fn no_losses_means_no_sortino_and_no_calmar() {
    let s = EquityStats::from_returns(&[0.01, 0.02, 0.03], 252.0, 0.0).unwrap();
    assert_eq!(s.sortino, None);
    assert_eq!(s.calmar, None);
    assert_eq!(s.max_drawdown, 0.0);
    assert!(s.sharpe.unwrap() > 0.0);
}

#[test]
fn risk_free_rate_shifts_sharpe_sign() {
    let r = [0.01, 0.02, 0.015];
    let above = EquityStats::from_returns(&r, 252.0, 0.0).unwrap();
    let below = EquityStats::from_returns(&r, 252.0, 0.05).unwrap();
    assert!(above.sharpe.unwrap() > 0.0);
    assert!(below.sharpe.unwrap() < 0.0);
}

#[test]
fn equity_curve_with_zero_baseline_treats_that_step_as_flat() {
    let s = EquityStats::from_equity_curve(&[0.0, 100.0, 110.0], 252.0, 0.0).unwrap();
    assert_eq!(s.n_periods, 2);
    assert_close(s.total_return, 0.1);
}

#[test]
fn equity_curve_needs_two_points() {
    assert_eq!(
        EquityStats::from_equity_curve(&[100.0], 252.0, 0.0),
        Err(AnalyticsError::InsufficientData { needed: 2, got: 1 })
    );
}
