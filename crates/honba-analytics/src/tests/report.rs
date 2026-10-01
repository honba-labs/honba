//! Unit tests for `crate::report`.

use super::long_trip;
use crate::{AnalyticsError, EquityStats, PerformanceReport, TradeStats};

#[test]
fn from_equity_curve_combines_trade_and_equity_stats() {
    let trips = [long_trip(100.0, 110.0, 1.0, 0.0)];
    let curve = [100.0, 105.0, 110.0];
    let r = PerformanceReport::from_equity_curve(&trips, &curve, 252.0, 0.0).unwrap();
    assert_eq!(r.trades, TradeStats::from_round_trips(&trips).unwrap());
    assert_eq!(
        r.equity,
        EquityStats::from_equity_curve(&curve, 252.0, 0.0).unwrap()
    );
}

#[test]
fn trade_errors_take_precedence() {
    assert_eq!(
        PerformanceReport::from_returns(&[], &[], 252.0, 0.0),
        Err(AnalyticsError::EmptyInput)
    );
}

#[test]
fn equity_errors_propagate() {
    let trips = [long_trip(100.0, 110.0, 1.0, 0.0)];
    assert_eq!(
        PerformanceReport::from_equity_curve(&trips, &[1.0], 252.0, 0.0),
        Err(AnalyticsError::InsufficientData { needed: 2, got: 1 })
    );
}
