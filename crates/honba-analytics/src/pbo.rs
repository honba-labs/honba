//! Probability of Backtest Overfitting (PBO) via Combinatorially Symmetric Cross-Validation (CSCV).
//!
//! Implements Bailey, Borwein, López de Prado & Zhu (2017) CSCV methodology.

use schemars::JsonSchema;
use serde::{Deserialize, Serialize};

use crate::equity_stats::EquityStats;

/// Configuration for PBO CSCV evaluation.
#[derive(Clone, Copy, Debug, PartialEq, Serialize, Deserialize, JsonSchema)]
pub struct PboConfig {
    /// Number of sub-period slices $S$ (must be an even integer, default: 8).
    pub num_slices: usize,
    /// Maximum acceptable PBO threshold (default: 0.50 = 50%).
    pub max_acceptable_pbo: f64,
}

impl Default for PboConfig {
    fn default() -> Self {
        Self {
            num_slices: 8,
            max_acceptable_pbo: 0.50,
        }
    }
}

/// Result of Probability of Backtest Overfitting analysis.
#[derive(Clone, Copy, Debug, PartialEq, Serialize, Deserialize, JsonSchema)]
pub struct PboResult {
    /// Probability of Backtest Overfitting ($PBO \in [0.0, 1.0]$).
    pub pbo: f64,
    /// Number of symmetric cross-validation combinations evaluated.
    pub combinations_evaluated: usize,
    /// Median relative rank of the best In-Sample model out-of-sample ($\in [0.0, 1.0]$).
    pub median_oos_percentile: f64,
    /// Whether PBO is below the acceptable threshold.
    pub passed: bool,
}

/// Computes PBO across a set of trial return series ($M$ models $\times$ $T$ periods).
pub fn compute_pbo(trial_returns: &[Vec<f64>], config: PboConfig) -> PboResult {
    let num_models = trial_returns.len();
    if num_models < 2 {
        return PboResult {
            pbo: 0.0,
            combinations_evaluated: 0,
            median_oos_percentile: 1.0,
            passed: true,
        };
    }

    let min_len = trial_returns.iter().map(|r| r.len()).min().unwrap_or(0);
    let s = config.num_slices.max(4);
    let s = if s % 2 != 0 { s + 1 } else { s }; // ensure even

    if min_len < s * 2 {
        return PboResult {
            pbo: 0.0,
            combinations_evaluated: 0,
            median_oos_percentile: 1.0,
            passed: true,
        };
    }

    // Partition time into S equal slices
    let slice_len = min_len / s;

    // Generate combinations of choosing S/2 slices out of S for In-Sample
    let half_s = s / 2;
    let combinations = generate_combinations(s, half_s);
    let total_combs = combinations.len();

    let mut overfit_count = 0;
    let mut oos_percentiles: Vec<f64> = Vec::with_capacity(total_combs);

    for is_indices in &combinations {
        let is_set: std::collections::HashSet<usize> = is_indices.iter().copied().collect();

        // Evaluate IS and OOS performance for each model
        let mut is_sharpes: Vec<(usize, f64)> = Vec::with_capacity(num_models);
        let mut oos_sharpes: Vec<(usize, f64)> = Vec::with_capacity(num_models);

        for (m_idx, returns) in trial_returns.iter().enumerate() {
            let mut is_sub: Vec<f64> = Vec::new();
            let mut oos_sub: Vec<f64> = Vec::new();

            for slice_idx in 0..s {
                let start = slice_idx * slice_len;
                let end = start + slice_len;
                let slice_data = &returns[start..end];

                if is_set.contains(&slice_idx) {
                    is_sub.extend_from_slice(slice_data);
                } else {
                    oos_sub.extend_from_slice(slice_data);
                }
            }

            let is_sh = EquityStats::from_returns(&is_sub, 252.0, 0.0)
                .ok()
                .and_then(|s| s.sharpe)
                .unwrap_or(0.0);
            let oos_sh = EquityStats::from_returns(&oos_sub, 252.0, 0.0)
                .ok()
                .and_then(|s| s.sharpe)
                .unwrap_or(0.0);

            is_sharpes.push((m_idx, is_sh));
            oos_sharpes.push((m_idx, oos_sh));
        }

        // Find best model In-Sample
        is_sharpes.sort_by(|a, b| b.1.partial_cmp(&a.1).unwrap_or(std::cmp::Ordering::Equal));
        let best_model_is = is_sharpes[0].0;

        // Rank models Out-of-Sample
        oos_sharpes.sort_by(|a, b| b.1.partial_cmp(&a.1).unwrap_or(std::cmp::Ordering::Equal));
        let oos_rank = oos_sharpes
            .iter()
            .position(|(m, _)| *m == best_model_is)
            .unwrap_or(num_models - 1);

        // Percentile: 1.0 is best, 0.0 is worst
        let oos_pct = 1.0 - (oos_rank as f64 / (num_models as f64 - 1.0).max(1.0));
        oos_percentiles.push(oos_pct);

        // If best IS model ranks below median OOS (lower 50%), it is overfit
        if oos_pct < 0.50 {
            overfit_count += 1;
        }
    }

    let pbo = if total_combs > 0 {
        overfit_count as f64 / total_combs as f64
    } else {
        0.0
    };

    oos_percentiles.sort_by(|a, b| a.partial_cmp(b).unwrap_or(std::cmp::Ordering::Equal));
    let median_pct = if !oos_percentiles.is_empty() {
        oos_percentiles[oos_percentiles.len() / 2]
    } else {
        1.0
    };

    PboResult {
        pbo,
        combinations_evaluated: total_combs,
        median_oos_percentile: median_pct,
        passed: pbo <= config.max_acceptable_pbo,
    }
}

/// Helper to generate combinations of `k` elements from `0..n`.
fn generate_combinations(n: usize, k: usize) -> Vec<Vec<usize>> {
    let mut results = Vec::new();
    let mut current = Vec::new();

    fn backtrack(
        start: usize,
        n: usize,
        k: usize,
        current: &mut Vec<usize>,
        results: &mut Vec<Vec<usize>>,
    ) {
        if current.len() == k {
            results.push(current.clone());
            return;
        }
        for i in start..n {
            current.push(i);
            backtrack(i + 1, n, k, current, results);
            current.pop();
        }
    }

    backtrack(0, n, k, &mut current, &mut results);
    results
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_combinations_count() {
        let combs = generate_combinations(8, 4);
        // 8 choose 4 = 70
        assert_eq!(combs.len(), 70);
    }

    #[test]
    fn test_pbo_calculation() {
        // Model 1: consistently good
        let m1 = vec![0.01; 160];
        // Model 2: pure noise
        let m2: Vec<f64> = (0..160)
            .map(|i| if i % 2 == 0 { 0.02 } else { -0.02 })
            .collect();

        let trials = vec![m1, m2];
        let res = compute_pbo(
            &trials,
            PboConfig {
                num_slices: 4,
                ..Default::default()
            },
        );
        assert_eq!(res.combinations_evaluated, 6); // 4 choose 2 = 6
        assert!(res.passed);
    }
}
