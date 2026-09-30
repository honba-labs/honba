//! Risk and return statistics over a return or equity series.

use serde::{Deserialize, Serialize};

use crate::error::{AnalyticsError, Result};

/// Risk and return statistics for an equity curve or return series.
///
/// Annualization is explicit: the caller supplies `periods_per_year` (e.g.
/// 252 for daily bars, 52 for weekly, 12 for monthly) and the per-period
/// risk-free rate. Nothing is hardcoded.
#[derive(Clone, Copy, Debug, PartialEq, Serialize, Deserialize)]
pub struct EquityStats {
    /// Number of return observations.
    pub n_periods: usize,
    /// Total return over the series, as a fraction.
    pub total_return: f64,
    /// Geometric annualized return, as a fraction.
    pub annualized_return: f64,
    /// Annualized volatility of returns, as a fraction.
    pub annualized_volatility: f64,
    /// Annualized Sharpe ratio. `None` if return variance was zero.
    pub sharpe: Option<f64>,
    /// Annualized Sortino ratio. `None` if downside deviation was zero.
    pub sortino: Option<f64>,
    /// Maximum peak-to-trough decline in currency units.
    pub max_drawdown: f64,
    /// Maximum peak-to-trough decline as a fraction of the peak.
    pub max_drawdown_pct: f64,
    /// `annualized_return / max_drawdown_pct`. `None` if DD was zero.
    pub calmar: Option<f64>,
}

impl EquityStats {
    /// Computes statistics from a series of periodic returns.
    ///
    /// `periods_per_year` annualizes volatility and ratios. `risk_free_per_period`
    /// is subtracted from the mean return before ratios are computed.
    pub fn from_returns(
        returns: &[f64],
        periods_per_year: f64,
        risk_free_per_period: f64,
    ) -> Result<Self> {
        if returns.is_empty() {
            return Err(AnalyticsError::EmptyInput);
        }
        if periods_per_year <= 0.0 || !periods_per_year.is_finite() {
            return Err(AnalyticsError::InsufficientData { needed: 1, got: 0 });
        }

        let n = returns.len();
        let n_f = n as f64;

        // Total return = product(1 + r_i) - 1
        let mut equity_factor = 1.0f64;
        for r in returns {
            equity_factor *= 1.0 + r;
        }
        let total_return = equity_factor - 1.0;

        // Annualized (geometric) return
        let annualized_return = if n > 0 {
            equity_factor.powf(periods_per_year / n_f) - 1.0
        } else {
            0.0
        };

        // Mean and stddev of excess returns
        let excess: Vec<f64> = returns.iter().map(|r| r - risk_free_per_period).collect();
        let mean_excess = excess.iter().sum::<f64>() / n_f;

        let sharpe = if n >= 2 {
            let var = excess
                .iter()
                .map(|r| (r - mean_excess).powi(2))
                .sum::<f64>()
                / (n_f - 1.0);
            let std = var.sqrt();
            if std > 0.0 {
                Some(mean_excess / std * periods_per_year.sqrt())
            } else {
                None
            }
        } else {
            None
        };

        // Downside deviation (target = risk-free)
        let downside_sq: f64 = excess
            .iter()
            .filter(|r| **r < 0.0)
            .map(|r| r * r)
            .sum();
        let downside_dev = (downside_sq / n_f).sqrt();
        let sortino = if downside_dev > 0.0 {
            Some(mean_excess / downside_dev * periods_per_year.sqrt())
        } else {
            None
        };

        // Annualized volatility
        let annualized_volatility = if n >= 2 {
            let mean = returns.iter().sum::<f64>() / n_f;
            let var = returns
                .iter()
                .map(|r| (r - mean).powi(2))
                .sum::<f64>()
                / (n_f - 1.0);
            var.sqrt() * periods_per_year.sqrt()
        } else {
            0.0
        };

        // Max drawdown from the equity curve derived from returns
        let mut equity = 1.0f64;
        let mut peak = 1.0f64;
        let mut max_dd = 0.0f64;
        let mut max_dd_pct = 0.0f64;
        for r in returns {
            equity *= 1.0 + r;
            if equity > peak {
                peak = equity;
            }
            let dd = peak - equity;
            if dd > max_dd {
                max_dd = dd;
                max_dd_pct = if peak != 0.0 { dd / peak } else { 0.0 };
            }
        }

        let calmar = if max_dd_pct > 0.0 {
            Some(annualized_return / max_dd_pct)
        } else {
            None
        };

        Ok(Self {
            n_periods: n,
            total_return,
            annualized_return,
            annualized_volatility,
            sharpe,
            sortino,
            max_drawdown: max_dd,
            max_drawdown_pct: max_dd_pct,
            calmar,
        })
    }

    /// Computes statistics from an equity curve by converting to returns.
    ///
    /// The first equity value is used only as the baseline; the returned
    /// `n_periods` is `equity.len() - 1`. Requires at least two values.
    pub fn from_equity_curve(
        equity: &[f64],
        periods_per_year: f64,
        risk_free_per_period: f64,
    ) -> Result<Self> {
        if equity.len() < 2 {
            return Err(AnalyticsError::InsufficientData {
                needed: 2,
                got: equity.len(),
            });
        }
        let returns: Vec<f64> = equity
            .windows(2)
            .map(|w| {
                if w[0] != 0.0 {
                    (w[1] - w[0]) / w[0]
                } else {
                    0.0
                }
            })
            .collect();
        Self::from_returns(&returns, periods_per_year, risk_free_per_period)
    }
}
