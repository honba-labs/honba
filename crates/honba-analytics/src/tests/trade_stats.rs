//! Unit tests for `crate::trade_stats`.

use super::{assert_close, long_trip};
use crate::{AnalyticsError, TradeStats};

#[test]
fn empty_input_is_an_error() {
    assert_eq!(
        TradeStats::from_round_trips(&[]),
        Err(AnalyticsError::EmptyInput)
    );
}

#[test]
fn break_even_trips_count_as_flat_not_wins_or_losses() {
    let s = TradeStats::from_round_trips(&[long_trip(10.0, 10.0, 1.0, 0.0)]).unwrap();
    assert_eq!((s.n_wins, s.n_losses, s.n_flat), (0, 0, 1));
    assert_eq!(s.win_rate, 0.0);
    assert_eq!(s.profit_factor, None);
    assert_eq!((s.avg_win, s.avg_loss), (None, None));
}

#[test]
fn mixed_trips_aggregate_pnl_fees_and_ratios() {
    let trips = [
        long_trip(100.0, 110.0, 1.0, 1.0), // net +9
        long_trip(100.0, 95.0, 1.0, 1.0),  // net -6
        long_trip(100.0, 104.0, 1.0, 1.0), // net +3
    ];
    let s = TradeStats::from_round_trips(&trips).unwrap();
    assert_eq!((s.n_trades, s.n_wins, s.n_losses), (3, 2, 1));
    assert_close(s.win_rate, 2.0 / 3.0);
    assert_close(s.gross_profit, 12.0);
    assert_close(s.gross_loss, 6.0);
    assert_close(s.profit_factor.unwrap(), 2.0);
    assert_close(s.avg_win.unwrap(), 6.0);
    assert_close(s.avg_loss.unwrap(), -6.0);
    assert_close(s.total_pnl, 6.0);
    assert_close(s.expectancy, 2.0);
    assert_close(s.total_fees, 3.0);
}

#[test]
fn all_losses_give_zero_profit_factor_and_no_avg_win() {
    let s = TradeStats::from_round_trips(&[long_trip(10.0, 9.0, 1.0, 0.0)]).unwrap();
    assert_eq!(s.profit_factor, Some(0.0));
    assert_eq!(s.avg_win, None);
}
