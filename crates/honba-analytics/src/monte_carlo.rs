//! Block-bootstrap Monte Carlo simulation for equity drawdown and ruin estimation.
//!
//! Uses stationary moving-block bootstrap (resampling contiguous return blocks)
//! to preserve serial autocorrelation and volatility clustering without parametric assumptions.

use schemars::JsonSchema;
use serde::{Deserialize, Serialize};

use crate::math::SplitMix64;

/// Configuration for block-bootstrap Monte Carlo simulation.
#[derive(Clone, Copy, Debug, PartialEq, Serialize, Deserialize, JsonSchema)]
pub struct MonteCarloConfig {
    /// Number of resampled paths to generate (default: 500).
    pub num_simulations: usize,
    /// Block length in bars. If 0, defaults to $\max(2, \lfloor T^{1/3} \rfloor)$.
    pub block_size: usize,
    /// Deterministic seed for reproducible simulation.
    pub seed: u64,
    /// Maximum acceptable 95% drawdown threshold (default: 0.25 = 25%).
    pub max_acceptable_drawdown_95: f64,
    /// Ruin drawdown threshold (default: 0.50 = 50%).
    pub ruin_drawdown_threshold: f64,
}

impl Default for MonteCarloConfig {
    fn default() -> Self {
        Self {
            num_simulations: 500,
            block_size: 0,
            seed: 42,
            max_acceptable_drawdown_95: 0.30,
            ruin_drawdown_threshold: 0.50,
        }
    }
}

/// Results from block-bootstrap Monte Carlo analysis.
#[derive(Clone, Copy, Debug, PartialEq, Serialize, Deserialize, JsonSchema)]
pub struct MonteCarloResult {
    /// Number of simulated paths evaluated.
    pub simulations_run: usize,
    /// Block size used for resampling.
    pub block_size: usize,
    /// Median maximum peak-to-trough drawdown across all simulated paths.
    pub median_max_drawdown: f64,
    /// 95th percentile worst-case maximum drawdown (VaR 95%).
    pub drawdown_95: f64,
    /// 99th percentile worst-case maximum drawdown (VaR 99%).
    pub drawdown_99: f64,
    /// Probability of ending with negative return (loss probability).
    pub prob_loss: f64,
    /// Probability of hitting or exceeding the ruin drawdown threshold.
    pub prob_ruin: f64,
    /// Whether the strategy passed the Monte Carlo stability gate.
    pub passed: bool,
}

/// Runs a deterministic stationary moving-block bootstrap over a return series.
pub fn run_block_bootstrap(returns: &[f64], config: MonteCarloConfig) -> MonteCarloResult {
    let t = returns.len();
    if t < 5 {
        return MonteCarloResult {
            simulations_run: 0,
            block_size: 0,
            median_max_drawdown: 0.0,
            drawdown_95: 0.0,
            drawdown_99: 0.0,
            prob_loss: 0.0,
            prob_ruin: 0.0,
            passed: false,
        };
    }

    let b = if config.block_size > 0 {
        config.block_size.min(t)
    } else {
        (t as f64).cbrt().floor() as usize
    }
    .max(2);

    let num_sims = config.num_simulations.max(10);
    let mut rng = SplitMix64::new(config.seed);

    let mut max_drawdowns: Vec<f64> = Vec::with_capacity(num_sims);
    let mut loss_count = 0;
    let mut ruin_count = 0;

    for _ in 0..num_sims {
        // Construct a synthetic path of length t by drawing contiguous blocks
        let mut equity = 1.0;
        let mut peak = 1.0;
        let mut max_dd = 0.0;
        let mut bars_generated = 0;

        while bars_generated < t {
            // Pick a random block start index
            let start = rng.next_usize(t);
            let take = b.min(t - bars_generated);

            for j in 0..take {
                let idx = (start + j) % t;
                let r = returns[idx];
                equity *= 1.0 + r;
                if equity > peak {
                    peak = equity;
                }
                let dd = (peak - equity) / peak;
                if dd > max_dd {
                    max_dd = dd;
                }
            }
            bars_generated += take;
        }

        max_drawdowns.push(max_dd);
        if equity < 1.0 {
            loss_count += 1;
        }
        if max_dd >= config.ruin_drawdown_threshold {
            ruin_count += 1;
        }
    }

    max_drawdowns.sort_by(|a, b| a.partial_cmp(b).unwrap_or(std::cmp::Ordering::Equal));

    let idx_50 = (num_sims as f64 * 0.50).floor() as usize;
    let idx_95 = (num_sims as f64 * 0.95).floor() as usize;
    let idx_99 = (num_sims as f64 * 0.99).floor() as usize;

    let median_dd = max_drawdowns[idx_50.min(num_sims - 1)];
    let dd_95 = max_drawdowns[idx_95.min(num_sims - 1)];
    let dd_99 = max_drawdowns[idx_99.min(num_sims - 1)];

    let prob_loss = loss_count as f64 / num_sims as f64;
    let prob_ruin = ruin_count as f64 / num_sims as f64;

    let passed = dd_95 <= config.max_acceptable_drawdown_95 && prob_ruin <= 0.05;

    MonteCarloResult {
        simulations_run: num_sims,
        block_size: b,
        median_max_drawdown: median_dd,
        drawdown_95: dd_95,
        drawdown_99: dd_99,
        prob_loss,
        prob_ruin,
        passed,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_bootstrap_seeded() {
        let returns: Vec<f64> = (0..200)
            .map(|i| if i % 2 == 0 { 0.015 } else { -0.010 })
            .collect();

        let cfg = MonteCarloConfig {
            num_simulations: 200,
            seed: 12345,
            ..Default::default()
        };

        let res1 = run_block_bootstrap(&returns, cfg);
        let res2 = run_block_bootstrap(&returns, cfg);

        // Identical seed must yield byte-for-byte identical outcomes
        assert_eq!(res1.median_max_drawdown, res2.median_max_drawdown);
        assert_eq!(res1.drawdown_95, res2.drawdown_95);
        assert_eq!(res1.drawdown_99, res2.drawdown_99);
        assert_eq!(res1.prob_loss, res2.prob_loss);
        assert_eq!(res1.prob_ruin, res2.prob_ruin);
        assert_eq!(res1.passed, res2.passed);
    }
}
