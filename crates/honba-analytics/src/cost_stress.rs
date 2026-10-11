//! Cost-stress analysis under elevated execution frictions ($2\times$ and $3\times$ costs).

use schemars::JsonSchema;
use serde::{Deserialize, Serialize};

use crate::equity_stats::EquityStats;

/// Configuration for cost-stress testing.
#[derive(Clone, Copy, Debug, PartialEq, Serialize, Deserialize, JsonSchema)]
pub struct CostStressConfig {
    /// Baseline transaction cost drag per bar/period (as a fraction of capital).
    pub cost_per_period: f64,
    /// Annualization factor (default: 252.0).
    pub annualization_factor: f64,
}

impl Default for CostStressConfig {
    fn default() -> Self {
        Self {
            cost_per_period: 0.0002, // 2 bps per period default friction
            annualization_factor: 252.0,
        }
    }
}

/// Result of cost-stress testing.
#[derive(Clone, Copy, Debug, PartialEq, Serialize, Deserialize, JsonSchema)]
pub struct CostStressResult {
    /// Baseline ($1\times$ cost) annualized Sharpe ratio.
    pub baseline_sharpe: f64,
    /// Stressed ($2\times$ cost) annualized Sharpe ratio.
    pub stress_2x_sharpe: f64,
    /// Severe stressed ($3\times$ cost) annualized Sharpe ratio.
    pub stress_3x_sharpe: f64,
    /// Ratio of $2\times$ Sharpe to baseline Sharpe ($\text{Sharpe}_{2\times} / \text{Sharpe}_{1\times}$).
    pub retention_2x: f64,
    /// Whether alpha remains positive under $2\times$ cost stress.
    pub passed: bool,
}

/// Computes cost-stress sensitivity by applying $1\times, 2\times, 3\times$ friction penalties.
pub fn compute_cost_stress(returns: &[f64], config: CostStressConfig) -> CostStressResult {
    if returns.is_empty() {
        return CostStressResult {
            baseline_sharpe: 0.0,
            stress_2x_sharpe: 0.0,
            stress_3x_sharpe: 0.0,
            retention_2x: 0.0,
            passed: false,
        };
    }

    let sh_1x = EquityStats::from_returns(returns, config.annualization_factor, 0.0)
        .ok()
        .and_then(|s| s.sharpe)
        .unwrap_or(0.0);

    let returns_2x: Vec<f64> = returns
        .iter()
        .map(|&r| r - config.cost_per_period)
        .collect();
    let sh_2x = EquityStats::from_returns(&returns_2x, config.annualization_factor, 0.0)
        .ok()
        .and_then(|s| s.sharpe)
        .unwrap_or(0.0);

    let returns_3x: Vec<f64> = returns
        .iter()
        .map(|&r| r - 2.0 * config.cost_per_period)
        .collect();
    let sh_3x = EquityStats::from_returns(&returns_3x, config.annualization_factor, 0.0)
        .ok()
        .and_then(|s| s.sharpe)
        .unwrap_or(0.0);

    let retention = if sh_1x > 0.0 { sh_2x / sh_1x } else { 0.0 };

    let passed = sh_2x > 0.0;

    CostStressResult {
        baseline_sharpe: sh_1x,
        stress_2x_sharpe: sh_2x,
        stress_3x_sharpe: sh_3x,
        retention_2x: retention,
        passed,
    }
}
