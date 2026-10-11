//! Deflated Sharpe Ratio (DSR) implementation (Bailey & López de Prado, 2014).
//!
//! Adjusts the observed Sharpe ratio for:
//! 1. Non-normality of returns (negative skewness and fat-tailed excess kurtosis).
//! 2. Selection bias / multiple testing across $N$ trial evaluations.

use schemars::JsonSchema;
use serde::{Deserialize, Serialize};

use crate::math::{kurtosis, norm_cdf, norm_inv, skewness};

/// Euler-Mascheroni constant $\gamma$.
const EULER_MASCHERONI: f64 = 0.577_215_664_901_532_9;

/// Configuration parameters for Deflated Sharpe Ratio calculation.
#[derive(Clone, Copy, Debug, PartialEq, Serialize, Deserialize, JsonSchema)]
pub struct DsrConfig {
    /// Number of independent or tested strategy trials $N \ge 1$.
    pub num_trials: usize,
    /// Variance of Sharpe ratios across all tested trials.
    pub trial_sharpe_variance: f64,
    /// Significance threshold (default: 0.95, corresponding to 95% confidence).
    pub threshold: f64,
}

impl Default for DsrConfig {
    fn default() -> Self {
        Self {
            num_trials: 1,
            trial_sharpe_variance: 1.0,
            threshold: 0.95,
        }
    }
}

/// Result of Deflated Sharpe Ratio validation.
#[derive(Clone, Copy, Debug, PartialEq, Serialize, Deserialize, JsonSchema)]
pub struct DsrResult {
    /// The unadjusted observed Sharpe ratio.
    pub observed_sharpe: f64,
    /// Expected maximum Sharpe ratio under the null hypothesis ($SR_0$).
    pub expected_max_sharpe: f64,
    /// Standard error of the observed Sharpe ratio accounting for skewness & kurtosis.
    pub standard_error: f64,
    /// Test statistic $z = (\widehat{SR} - SR_0) / \text{SE}$.
    pub z_stat: f64,
    /// Deflated Sharpe Ratio value ($\Phi(z) \in [0.0, 1.0]$).
    pub dsr: f64,
    /// Whether DSR exceeds the required significance threshold.
    pub passed: bool,
}

/// Computes the expected maximum Sharpe ratio under the null hypothesis across $N$ trials.
///
/// $$E[\max_N] \approx \sqrt{V} \cdot \left((1 - \gamma)\Phi^{-1}\left(1 - \frac{1}{N}\right) + \gamma \Phi^{-1}\left(1 - \frac{1}{N e}\right)\right)$$
pub fn expected_max_sharpe(num_trials: usize, trial_variance: f64) -> f64 {
    if num_trials <= 1 {
        return 0.0;
    }
    let n = num_trials as f64;
    let var_sqrt = trial_variance.max(0.0).sqrt();

    let p1 = (1.0 - 1.0 / n).clamp(1e-12, 1.0 - 1e-12);
    let p2 = (1.0 - 1.0 / (n * std::f64::consts::E)).clamp(1e-12, 1.0 - 1e-12);

    let z1 = norm_inv(p1);
    let z2 = norm_inv(p2);

    var_sqrt * ((1.0 - EULER_MASCHERONI) * z1 + EULER_MASCHERONI * z2)
}

/// Computes the Deflated Sharpe Ratio from returns and trial parameters.
pub fn compute_dsr(observed_sharpe: f64, returns: &[f64], config: DsrConfig) -> DsrResult {
    let t = returns.len() as f64;
    if t < 3.0 || !observed_sharpe.is_finite() {
        return DsrResult {
            observed_sharpe,
            expected_max_sharpe: 0.0,
            standard_error: 1.0,
            z_stat: 0.0,
            dsr: 0.5,
            passed: false,
        };
    }

    let skew = skewness(returns);
    let kurt = kurtosis(returns);

    // Standard error formula (Mertens, 2002 / Lo, 2002 / Bailey & López de Prado, 2014)
    // $\sigma_{SR}^2 = \frac{1}{T - 1} \left(1 - \gamma_3 \widehat{SR} + \frac{\gamma_4 - 1}{4} \widehat{SR}^2\right)$
    let var_sr =
        (1.0 - skew * observed_sharpe + ((kurt - 1.0) / 4.0) * observed_sharpe.powi(2)) / (t - 1.0);
    let se = var_sr.max(1e-12).sqrt();

    let sr0 = expected_max_sharpe(config.num_trials, config.trial_sharpe_variance);
    let z = (observed_sharpe - sr0) / se;
    let dsr = norm_cdf(z);

    DsrResult {
        observed_sharpe,
        expected_max_sharpe: sr0,
        standard_error: se,
        z_stat: z,
        dsr,
        passed: dsr >= config.threshold,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_expected_max_sharpe_single_trial() {
        assert_eq!(expected_max_sharpe(1, 1.0), 0.0);
    }

    #[test]
    fn test_dsr_known_values() {
        // N=100 trials, trial variance = 1.0
        let sr0 = expected_max_sharpe(100, 1.0);
        // Approximately 2.50 to 2.55 per Bailey & López de Prado table 1
        assert!(sr0 > 2.40 && sr0 < 2.65, "Expected ~2.5, got {sr0}");

        // Normal returns with length 250
        let returns = vec![0.01; 250];
        let cfg = DsrConfig {
            num_trials: 100,
            trial_sharpe_variance: 1.0,
            threshold: 0.95,
        };

        // If observed Sharpe is well above expected maximum (e.g. 3.5), DSR passes
        let res_high = compute_dsr(3.5, &returns, cfg);
        assert!(res_high.passed);
        assert!(res_high.dsr > 0.99);

        // If observed Sharpe is below expected maximum (e.g. 1.5 < 2.5), DSR fails
        let res_low = compute_dsr(1.5, &returns, cfg);
        assert!(!res_low.passed);
        assert!(res_low.dsr < 0.01);
    }
}
