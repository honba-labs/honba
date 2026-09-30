//! Combined performance report.

use serde::{Deserialize, Serialize};

use crate::equity_stats::EquityStats;
use crate::round_trip::RoundTrip;
use crate::trade_stats::TradeStats;
use crate::Result;

/// A complete performance report: trade stats plus equity stats.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct PerformanceReport {
    /// Aggregate trade statistics.
    pub trades: TradeStats,
    /// Equity curve statistics.
    pub equity: EquityStats,
}

impl PerformanceReport {
    /// Builds a report from round trips and a return series.
    pub fn from_returns(
        trades: &[RoundTrip],
        returns: &[f64],
        periods_per_year: f64,
        risk_free_per_period: f64,
    ) -> Result<Self> {
        Ok(Self {
            trades: TradeStats::from_round_trips(trades)?,
            equity: EquityStats::from_returns(returns, periods_per_year, risk_free_per_period)?,
        })
    }

    /// Builds a report from round trips and an equity curve.
    pub fn from_equity_curve(
        trades: &[RoundTrip],
        equity: &[f64],
        periods_per_year: f64,
        risk_free_per_period: f64,
    ) -> Result<Self> {
        Ok(Self {
            trades: TradeStats::from_round_trips(trades)?,
            equity: EquityStats::from_equity_curve(equity, periods_per_year, risk_free_per_period)?,
        })
    }
}
