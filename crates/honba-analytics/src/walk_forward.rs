//! Walk-Forward Efficiency (WFE) and rolling out-of-sample validation.

use schemars::JsonSchema;
use serde::{Deserialize, Serialize};

use crate::equity_stats::EquityStats;

/// Configuration for Walk-Forward Efficiency analysis.
#[derive(Clone, Copy, Debug, PartialEq, Serialize, Deserialize, JsonSchema)]
pub struct WalkForwardConfig {
    /// In-sample fraction of total series (default: 0.60 = 60%).
    pub in_sample_fraction: f64,
    /// Minimum acceptable Walk-Forward Efficiency ratio (default: 0.50 = 50%).
    pub min_wfe: f64,
    /// Annualization factor (default: 252.0 for daily trading sessions).
    pub annualization_factor: f64,
}

impl Default for WalkForwardConfig {
    fn default() -> Self {
        Self {
            in_sample_fraction: 0.60,
            min_wfe: 0.50,
            annualization_factor: 252.0,
        }
    }
}

/// Result of Walk-Forward Analysis.
#[derive(Clone, Copy, Debug, PartialEq, Serialize, Deserialize, JsonSchema)]
pub struct WalkForwardResult {
    /// In-sample annualized return.
    pub is_annual_return: f64,
    /// Out-of-sample annualized return.
    pub oos_annual_return: f64,
    /// In-sample Sharpe ratio.
    pub is_sharpe: f64,
    /// Out-of-sample Sharpe ratio.
    pub oos_sharpe: f64,
    /// Walk-Forward Efficiency ratio ($WFE = \text{Return}_{OOS} / \text{Return}_{IS}$).
    pub wfe: f64,
    /// Whether the strategy passes walk-forward stability requirements.
    pub passed: bool,
}

/// Computes Walk-Forward Efficiency by splitting returns into In-Sample and Out-of-Sample segments.
pub fn compute_walk_forward(returns: &[f64], config: WalkForwardConfig) -> WalkForwardResult {
    let n = returns.len();
    if n < 20 {
        return WalkForwardResult {
            is_annual_return: 0.0,
            oos_annual_return: 0.0,
            is_sharpe: 0.0,
            oos_sharpe: 0.0,
            wfe: 0.0,
            passed: false,
        };
    }

    let split = ((n as f64) * config.in_sample_fraction.clamp(0.2, 0.8)).floor() as usize;
    let is_returns = &returns[..split];
    let oos_returns = &returns[split..];

    let is_stats = EquityStats::from_returns(is_returns, config.annualization_factor, 0.0).ok();
    let oos_stats = EquityStats::from_returns(oos_returns, config.annualization_factor, 0.0).ok();

    let is_ret = is_stats.map(|s| s.annualized_return).unwrap_or(0.0);
    let oos_ret = oos_stats.map(|s| s.annualized_return).unwrap_or(0.0);
    let is_sh = is_stats.and_then(|s| s.sharpe).unwrap_or(0.0);
    let oos_sh = oos_stats.and_then(|s| s.sharpe).unwrap_or(0.0);

    let wfe = if is_ret.abs() > 1e-6 {
        oos_ret / is_ret
    } else if oos_ret > 0.0 {
        1.0
    } else {
        0.0
    };

    let passed = wfe >= config.min_wfe && oos_sh > 0.0;

    WalkForwardResult {
        is_annual_return: is_ret,
        oos_annual_return: oos_ret,
        is_sharpe: is_sh,
        oos_sharpe: oos_sh,
        wfe,
        passed,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_walk_forward_consistent_strategy() {
        let returns = vec![0.001; 100];
        let res = compute_walk_forward(&returns, WalkForwardConfig::default());
        assert!(res.passed);
        assert!((res.wfe - 1.0).abs() < 0.1);
    }

    #[test]
    fn test_walk_forward_overfitted_strategy() {
        let mut returns = vec![0.005; 60];
        returns.extend(vec![-0.002; 40]);
        let res = compute_walk_forward(&returns, WalkForwardConfig::default());
        assert!(!res.passed);
        assert!(res.wfe < 0.0);
    }
}
