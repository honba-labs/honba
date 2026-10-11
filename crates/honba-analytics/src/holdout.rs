//! One-shot temporal holdout validation.
//!
//! Evaluates strategy returns on a pristine terminal holdout partition.

use schemars::JsonSchema;
use serde::{Deserialize, Serialize};

use crate::equity_stats::EquityStats;

/// Configuration for one-shot holdout evaluation.
#[derive(Clone, Copy, Debug, PartialEq, Serialize, Deserialize, JsonSchema)]
pub struct HoldoutConfig {
    /// Fraction of return series reserved for terminal holdout (default: 0.20 = 20%).
    pub holdout_fraction: f64,
    /// Maximum allowed degradation from training Sharpe (default: 0.50 = 50% retention).
    pub min_retention_ratio: f64,
    /// Total trials tested prior to holdout evaluation.
    pub trial_count: usize,
    /// Annualization factor (default: 252.0).
    pub annualization_factor: f64,
}

impl Default for HoldoutConfig {
    fn default() -> Self {
        Self {
            holdout_fraction: 0.20,
            min_retention_ratio: 0.50,
            trial_count: 1,
            annualization_factor: 252.0,
        }
    }
}

/// Result of one-shot holdout validation.
#[derive(Clone, Copy, Debug, PartialEq, Serialize, Deserialize, JsonSchema)]
pub struct HoldoutResult {
    /// In-sample training Sharpe ratio.
    pub train_sharpe: f64,
    /// Out-of-sample holdout Sharpe ratio.
    pub holdout_sharpe: f64,
    /// Retention ratio $\text{Sharpe}_{\text{Holdout}} / \text{Sharpe}_{\text{Train}}$.
    pub retention_ratio: f64,
    /// Whether the holdout validation passed.
    pub passed: bool,
}

/// Evaluates one-shot holdout performance on a pristine terminal block.
pub fn compute_holdout(returns: &[f64], config: HoldoutConfig) -> HoldoutResult {
    let n = returns.len();
    if n < 10 {
        return HoldoutResult {
            train_sharpe: 0.0,
            holdout_sharpe: 0.0,
            retention_ratio: 0.0,
            passed: false,
        };
    }

    let holdout_len = ((n as f64) * config.holdout_fraction.clamp(0.05, 0.5)).floor() as usize;
    let train_len = n - holdout_len;

    let train_returns = &returns[..train_len];
    let holdout_returns = &returns[train_len..];

    let train_sh = EquityStats::from_returns(train_returns, config.annualization_factor, 0.0)
        .ok()
        .and_then(|s| s.sharpe)
        .unwrap_or(0.0);
    let holdout_sh = EquityStats::from_returns(holdout_returns, config.annualization_factor, 0.0)
        .ok()
        .and_then(|s| s.sharpe)
        .unwrap_or(0.0);

    let retention = if train_sh > 0.0 {
        holdout_sh / train_sh
    } else if holdout_sh > 0.0 {
        1.0
    } else {
        0.0
    };

    let passed = holdout_sh > 0.0 && retention >= config.min_retention_ratio;

    HoldoutResult {
        train_sharpe: train_sh,
        holdout_sharpe: holdout_sh,
        retention_ratio: retention,
        passed,
    }
}
