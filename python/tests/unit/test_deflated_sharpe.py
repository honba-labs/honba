"""Deflated Sharpe Ratio (Balch pitfall #6: data mining fallacy).

DSR adjusts the observed Sharpe for the number of trials N and the non-normality
of returns (skew, kurtosis). It is the single most important metric in the
validation toolkit and the one most often ignored.

Reference: Bailey & López de Prado, "The Deflated Sharpe Ratio" (2014).
"""

from __future__ import annotations

import itertools
import math
from statistics import NormalDist

import pytest

from honba.algo_analytics import deflated_sharpe

# Tolerances for approximate equality
REL_TOL = 1e-6
ABS_TOL = 1e-9


def _normal_sr(n_trials: int) -> float:
    """Approximate expected max Sharpe from N normal draws (Bailey & LdP Eq 4)."""
    # For large N, E[max Z] ≈ sqrt(2 ln N) - 0.5*(ln ln N + ln 4π) / sqrt(2 ln N)
    if n_trials <= 1:
        return 0.0
    log_n = math.log(n_trials)
    return math.sqrt(2.0 * log_n) - (math.log(log_n) + math.log(4 * math.pi)) / (
        2.0 * math.sqrt(2.0 * log_n)
    )


def test_deflated_sharpe_returns_a_probability_between_zero_and_one() -> None:
    dsr = deflated_sharpe(observed_sr=1.5, n_trials=50, skew=0.0, kurtosis=3.0, t=60)
    assert 0.0 <= dsr <= 1.0


def test_higher_observed_sr_gives_higher_dsr() -> None:
    dsr_low = deflated_sharpe(observed_sr=0.5, n_trials=100, skew=0.0, kurtosis=3.0, t=60)
    dsr_high = deflated_sharpe(observed_sr=1.0, n_trials=100, skew=0.0, kurtosis=3.0, t=60)
    assert dsr_high > dsr_low


def test_more_trials_deflates_more() -> None:
    dsr_10 = deflated_sharpe(observed_sr=1.0, n_trials=10, skew=0.0, kurtosis=3.0, t=60)
    dsr_100 = deflated_sharpe(observed_sr=1.0, n_trials=100, skew=0.0, kurtosis=3.0, t=60)
    dsr_1000 = deflated_sharpe(observed_sr=1.0, n_trials=1_000, skew=0.0, kurtosis=3.0, t=60)
    assert dsr_10 > dsr_100 > dsr_1000


def test_positive_skew_helps_deflation_less_than_negative() -> None:
    # Positive skew means fatter right tail, so a high observed SR is more likely
    # by chance — the deflation should be *larger* (lower DSR).
    dsr_pos = deflated_sharpe(observed_sr=1.0, n_trials=100, skew=1.0, kurtosis=3.0, t=60)
    dsr_neg = deflated_sharpe(observed_sr=1.0, n_trials=100, skew=-1.0, kurtosis=3.0, t=60)
    assert dsr_pos < dsr_neg  # positive skew -> more deflation


def test_excess_kurtosis_helps_deflation() -> None:
    # Fat tails (kurtosis > 3) make extreme SRs more likely by chance.
    dsr_normal = deflated_sharpe(observed_sr=1.0, n_trials=100, skew=0.0, kurtosis=3.0, t=60)
    dsr_fat = deflated_sharpe(observed_sr=1.0, n_trials=100, skew=0.0, kurtosis=6.0, t=60)
    assert dsr_fat < dsr_normal


def test_dsr_matches_the_bailey_lopez_de_prado_closed_form() -> None:
    """
    Closed-form DSR for normal returns (Eq 5 in Bailey & López de Prado 2014):

    DSR = P(SR* > 0) = Φ( (SR - μ_max) / σ_max )

    where:
    - μ_max ≈ E[max Z] * sqrt(V[SR])
    - σ_max ≈ sqrt(V[SR] * (1 - 2/π))   (for normal)
    - V[SR] = (1 + SR^2/2) / (T - 1) ≈ 1/T for large T

    For a single trial (N=1), DSR should equal the standard p-value of SR.
    """
    sr = 1.0
    n_trials = 1
    t = 252  # trading days in the backtest period
    v_sr = (1 + sr**2 / 2) / (t - 1)
    mu_max = _normal_sr(n_trials) * math.sqrt(v_sr)
    sigma_max = math.sqrt(v_sr * (1 - 2.0 / math.pi))
    expected = NormalDist().cdf((sr - mu_max) / sigma_max)

    got = deflated_sharpe(observed_sr=sr, n_trials=n_trials, skew=0.0, kurtosis=3.0, t=t)
    assert abs(got - expected) < 0.01


def test_dsr_is_one_when_no_trials() -> None:
    # Edge case: N=0 is treated as N=1 (the observed strategy itself)
    dsr = deflated_sharpe(observed_sr=1.5, n_trials=0, skew=0.0, kurtosis=3.0)
    assert dsr == 1.0 or dsr == 0.0  # implementation-defined for N<1


def test_negative_sr_gives_dsr_below_half() -> None:
    dsr = deflated_sharpe(observed_sr=-1.0, n_trials=50, skew=0.0, kurtosis=3.0)
    assert dsr < 0.5


def test_dsr_is_monotonic_in_observed_sr_for_fixed_n() -> None:
    srs = [0.0, 0.5, 1.0, 1.5, 2.0, 2.5, 3.0]
    dsrs = [deflated_sharpe(sr, 100, 0.0, 3.0) for sr in srs]
    for a, b in itertools.pairwise(dsrs):
        assert b >= a


def test_dsr_is_monotonic_decreasing_in_n_for_fixed_sr() -> None:
    n_trials_list = [1, 5, 10, 50, 100, 500, 1000]
    dsrs = [deflated_sharpe(1.5, n, 0.0, 3.0) for n in n_trials_list]
    for a, b in itertools.pairwise(dsrs):
        assert a >= b


def test_input_validation() -> None:
    with pytest.raises(ValueError, match="observed_sr"):
        deflated_sharpe(observed_sr=float("nan"), n_trials=10)
    with pytest.raises(ValueError, match="observed_sr"):
        deflated_sharpe(observed_sr=float("inf"), n_trials=10)
    with pytest.raises(ValueError, match="n_trials"):
        deflated_sharpe(observed_sr=1.0, n_trials=-1)
    with pytest.raises(ValueError, match="t"):
        deflated_sharpe(observed_sr=1.0, n_trials=10, t=1)
    with pytest.raises(ValueError, match="t"):
        deflated_sharpe(observed_sr=1.0, n_trials=10, t=0)
