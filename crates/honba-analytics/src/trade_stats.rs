//! Aggregate statistics over a set of round trips.

use serde::{Deserialize, Serialize};

use crate::error::{AnalyticsError, Result};
use crate::round_trip::RoundTrip;

/// Aggregate statistics over closed trades.
///
/// Fields whose value depends on the presence of wins or losses are
/// `Option`, so an all-wins run reports `profit_factor: None` rather than a
/// misleading infinity.
#[derive(Clone, Copy, Debug, PartialEq, Serialize, Deserialize)]
pub struct TradeStats {
    /// Number of round trips.
    pub n_trades: usize,
    /// Number of profitable trips.
    pub n_wins: usize,
    /// Number of losing trips.
    pub n_losses: usize,
    /// Number of break-even trips.
    pub n_flat: usize,
    /// `n_wins / n_trades`.
    pub win_rate: f64,
    /// Sum of positive net PnL.
    pub gross_profit: f64,
    /// Absolute sum of negative net PnL.
    pub gross_loss: f64,
    /// `gross_profit / gross_loss`. `None` if `gross_loss == 0`.
    pub profit_factor: Option<f64>,
    /// Average net PnL of winning trips. `None` if no wins.
    pub avg_win: Option<f64>,
    /// Average net PnL of losing trips (negative). `None` if no losses.
    pub avg_loss: Option<f64>,
    /// Sum of net PnL.
    pub total_pnl: f64,
    /// Average net PnL per trip.
    pub expectancy: f64,
    /// Sum of all fees.
    pub total_fees: f64,
}

impl TradeStats {
    /// Computes statistics from a slice of round trips.
    ///
    /// Returns [`AnalyticsError::EmptyInput`] if `trades` is empty.
    pub fn from_round_trips(trades: &[RoundTrip]) -> Result<Self> {
        if trades.is_empty() {
            return Err(AnalyticsError::EmptyInput);
        }

        let n_trades = trades.len();
        let mut n_wins = 0usize;
        let mut n_losses = 0usize;
        let mut n_flat = 0usize;
        let mut gross_profit = 0.0f64;
        let mut gross_loss = 0.0f64;
        let mut total_pnl = 0.0f64;
        let mut total_fees = 0.0f64;

        for t in trades {
            total_pnl += t.net_pnl;
            total_fees += t.fees;
            if t.net_pnl > 0.0 {
                n_wins += 1;
                gross_profit += t.net_pnl;
            } else if t.net_pnl < 0.0 {
                n_losses += 1;
                gross_loss += -t.net_pnl;
            } else {
                n_flat += 1;
            }
        }

        let win_rate = n_wins as f64 / n_trades as f64;
        let profit_factor = if gross_loss > 0.0 {
            Some(gross_profit / gross_loss)
        } else {
            None
        };
        let avg_win = if n_wins > 0 {
            Some(gross_profit / n_wins as f64)
        } else {
            None
        };
        let avg_loss = if n_losses > 0 {
            Some(-gross_loss / n_losses as f64)
        } else {
            None
        };
        let expectancy = total_pnl / n_trades as f64;

        Ok(Self {
            n_trades,
            n_wins,
            n_losses,
            n_flat,
            win_rate,
            gross_profit,
            gross_loss,
            profit_factor,
            avg_win,
            avg_loss,
            total_pnl,
            expectancy,
            total_fees,
        })
    }
}
