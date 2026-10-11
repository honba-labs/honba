//! Unified overfitting validation block and statistical gate evaluation (E4-S6).

use schemars::JsonSchema;
use serde::{Deserialize, Serialize};

use crate::cost_stress::{compute_cost_stress, CostStressConfig, CostStressResult};
use crate::dsr::{compute_dsr, DsrConfig, DsrResult};
use crate::holdout::{compute_holdout, HoldoutConfig, HoldoutResult};
use crate::monte_carlo::{run_block_bootstrap, MonteCarloConfig, MonteCarloResult};
use crate::pbo::{compute_pbo, PboConfig, PboResult};
use crate::walk_forward::{compute_walk_forward, WalkForwardConfig, WalkForwardResult};

/// Comprehensive statistical validation block for a backtest or parameter sweep.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize, JsonSchema)]
pub struct ValidationBlock {
    /// Deflated Sharpe Ratio validation result.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub dsr: Option<DsrResult>,
    /// Probability of Backtest Overfitting validation result (for multi-trial sweeps).
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub pbo: Option<PboResult>,
    /// Walk-Forward Efficiency validation result.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub walk_forward: Option<WalkForwardResult>,
    /// One-shot holdout validation result.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub holdout: Option<HoldoutResult>,
    /// Block-bootstrap Monte Carlo drawdown and ruin simulation result.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub monte_carlo: Option<MonteCarloResult>,
    /// Cost-stress friction sensitivity result.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub cost_stress: Option<CostStressResult>,
    /// Overall verdict: true only if all evaluated gates passed.
    pub passed: bool,
    /// Human-readable warnings and failure reasons.
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub warnings: Vec<String>,
}

impl ValidationBlock {
    /// Evaluates all overfitting gates on a single backtest return series.
    pub fn evaluate_backtest(returns: &[f64], observed_sharpe: f64, trial_count: usize) -> Self {
        let mut warnings = Vec::new();

        // 1. DSR
        let dsr_cfg = DsrConfig {
            num_trials: trial_count.max(1),
            ..Default::default()
        };
        let dsr = Some(compute_dsr(observed_sharpe, returns, dsr_cfg));
        if let Some(d) = &dsr {
            if !d.passed {
                warnings.push(format!(
                    "DSR failed: observed Sharpe {:.2} deflated to {:.1}% (threshold: 95.0%, SR0: {:.2})",
                    observed_sharpe,
                    d.dsr * 100.0,
                    d.expected_max_sharpe
                ));
            }
        }

        // 2. Walk-Forward
        let wf = Some(compute_walk_forward(returns, WalkForwardConfig::default()));
        if let Some(w) = &wf {
            if !w.passed {
                warnings.push(format!(
                    "Walk-Forward Efficiency failed: WFE {:.1}% (min required: 50.0%)",
                    w.wfe * 100.0
                ));
            }
        }

        // 3. One-shot Holdout
        let ho_cfg = HoldoutConfig {
            trial_count: trial_count.max(1),
            ..Default::default()
        };
        let holdout = Some(compute_holdout(returns, ho_cfg));
        if let Some(h) = &holdout {
            if !h.passed {
                warnings.push(format!(
                    "Holdout validation failed: retention {:.1}% (min: 50.0%, holdout Sharpe: {:.2})",
                    h.retention_ratio * 100.0,
                    h.holdout_sharpe
                ));
            }
        }

        // 4. Block-Bootstrap Monte Carlo
        let mc = Some(run_block_bootstrap(returns, MonteCarloConfig::default()));
        if let Some(m) = &mc {
            if !m.passed {
                warnings.push(format!(
                    "Monte Carlo bootstrap failed: 95% worst DD {:.1}% (threshold: 30.0%, ruin prob: {:.1}%)",
                    m.drawdown_95 * 100.0,
                    m.prob_ruin * 100.0
                ));
            }
        }

        // 5. Cost-Stress Testing
        let cs = Some(compute_cost_stress(returns, CostStressConfig::default()));
        if let Some(c) = &cs {
            if !c.passed {
                warnings.push(format!(
                    "Cost-stress failed: 2x friction Sharpe {:.2} collapsed below 0.0",
                    c.stress_2x_sharpe
                ));
            }
        }

        let passed = warnings.is_empty();

        Self {
            dsr,
            pbo: None, // PBO is evaluated across multi-trial sweeps
            walk_forward: wf,
            holdout,
            monte_carlo: mc,
            cost_stress: cs,
            passed,
            warnings,
        }
    }

    /// Evaluates overfitting gates across a multi-trial sweep.
    pub fn evaluate_sweep(
        best_returns: &[f64],
        best_sharpe: f64,
        trial_returns: &[Vec<f64>],
    ) -> Self {
        let mut block = Self::evaluate_backtest(best_returns, best_sharpe, trial_returns.len());

        // Add PBO across all sweep trials
        if trial_returns.len() >= 2 {
            let pbo_res = compute_pbo(trial_returns, PboConfig::default());
            if !pbo_res.passed {
                block.warnings.push(format!(
                    "PBO failed: Probability of Backtest Overfitting {:.1}% exceeds 50.0%",
                    pbo_res.pbo * 100.0
                ));
                block.passed = false;
            }
            block.pbo = Some(pbo_res);
        }

        block
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_result_carries_validation_block() {
        let returns = vec![0.002; 150];
        let block = ValidationBlock::evaluate_backtest(&returns, 2.5, 1);
        assert!(block.dsr.is_some());
        assert!(block.walk_forward.is_some());
        assert!(block.holdout.is_some());
        assert!(block.monte_carlo.is_some());
        assert!(block.cost_stress.is_some());
        assert!(block.passed);
        assert!(block.warnings.is_empty());
    }
}
