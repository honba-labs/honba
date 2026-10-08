"""Deflated Sharpe Ratio (Balch pitfall #6: data mining fallacy).

Reference: Bailey, D. H., & López de Prado, M. (2014). The Deflated Sharpe Ratio:
Correcting for Selection Bias, Backtest Overfitting and Non-Normality.
Journal of Portfolio Management, 40(5), 94–107.

The DSR adjusts the observed Sharpe ratio for:
- Multiple testing (N trials)
- Non-normality of returns (skewness, kurtosis)
- Sample length (T periods)

DSR = P(SR* > 0 | SR_hat, N, skew, kurtosis, T)
"""

from __future__ import annotations

import math
from statistics import NormalDist

_NORMAL = NormalDist()

__all__ = ["deflated_sharpe"]


def _normal_max_z(n_trials: int) -> float:
    """
    Approximate E[max Z] for n standard normal draws (Bailey & López de Prado, Eq 4).
    For large N: sqrt(2 ln N) - 0.5 * (ln ln N + ln 4π) / sqrt(2 ln N)
    """
    if n_trials <= 1:
        return 0.0
    log_n = math.log(n_trials)
    sqrt_2log = math.sqrt(2.0 * log_n)
    correction = (math.log(log_n) + math.log(4.0 * math.pi)) / (2.0 * sqrt_2log)
    return sqrt_2log - correction


def _variance_of_sharpe(sr: float, t: int) -> float:
    """
    Variance of the Sharpe ratio estimator (Mertens, 2002):
    V[SR] = (1 + SR²/2) / (T - 1)
    For large T, this is approximately 1/T.
    """
    if t <= 1:
        return float("inf")
    return (1.0 + sr**2 / 2.0) / (t - 1)


def _adjust_for_non_normality(skew: float, kurtosis: float) -> tuple[float, float]:
    """
    Adjust mean and std of max Sharpe for non-normality (Bailey & López de Prado, Eq 8-9).
    Returns (mu_adjustment, sigma_adjustment).
    """
    # Excess kurtosis
    excess_kurt = kurtosis - 3.0

    # For the expected maximum:
    # μ_max_adj = μ_max * (1 + skew * 0.5 + excess_kurt * 0.125)
    mu_adj = 1.0 + 0.5 * skew + 0.125 * excess_kurt

    # For the standard deviation:
    # σ_max_adj = σ_max * sqrt(1 + skew² + 0.5 * excess_kurt)
    # Note: 1 - 2/π ≈ 0.363 is the variance fraction for normal max
    sigma_adj = math.sqrt(1.0 + skew**2 + 0.5 * excess_kurt)

    return mu_adj, sigma_adj


def deflated_sharpe(
    observed_sr: float,
    n_trials: int,
    skew: float = 0.0,
    kurtosis: float = 3.0,
    t: int | None = None,
) -> float:
    """
    Compute the Deflated Sharpe Ratio.

    Args:
        observed_sr: The observed Sharpe ratio (annualized).
        n_trials: Number of independent trials (strategies tested).
        skew: Skewness of the return distribution (0 for normal).
        kurtosis: Kurtosis of the return distribution (3 for normal).
        t: Number of return periods in the backtest (e.g., 252 for daily data
           over one year). If None, defaults to 252.

    Returns:
        Probability that the true Sharpe ratio is positive, after correcting
        for selection bias over n_trials and non-normality.

    Raises:
        ValueError: If observed_sr is NaN/inf, n_trials < 0, or t <= 1.
    """
    if not math.isfinite(observed_sr):
        raise ValueError("observed_sr must be a finite number")
    if n_trials < 0:
        raise ValueError("n_trials must be non-negative")
    if n_trials == 0:
        n_trials = 1
    if t is None:
        t = 252
    if t <= 1:
        raise ValueError("t must be > 1 (at least two return periods)")

    # Variance of the Sharpe estimator
    v_sr = _variance_of_sharpe(observed_sr, t)

    # Expected maximum Sharpe under the null (SR = 0)
    mu_max = _normal_max_z(n_trials) * math.sqrt(v_sr)

    # Standard deviation of the maximum Sharpe
    sigma_max = math.sqrt(v_sr * (1.0 - 2.0 / math.pi))

    # Adjust for non-normality
    mu_adj, sigma_adj = _adjust_for_non_normality(skew, kurtosis)
    mu_max *= mu_adj
    sigma_max *= sigma_adj

    # DSR = P(SR* > 0) = Φ((SR - μ_max) / σ_max)
    if sigma_max <= 0.0:
        return 1.0 if observed_sr > mu_max else 0.0

    z = (observed_sr - mu_max) / sigma_max

    return _NORMAL.cdf(z)
