"""Risk metrics over an equity curve.

Annualization is always explicit: nothing here assumes daily bars unless the
caller says so. The Sharpe definition matches ``honba_analytics::EquityStats``
(sample standard deviation, excess return scaled by ``sqrt(periods_per_year)``),
so Python and Rust folds agree on the same curve.
"""

from __future__ import annotations

import math
import statistics
from collections.abc import Sequence
from itertools import pairwise

__all__ = ["sharpe_ratio"]


def sharpe_ratio(
    equity: Sequence[float],
    *,
    periods_per_year: float = 252.0,
    risk_free_per_period: float = 0.0,
) -> float:
    """Annualized Sharpe ratio of the simple returns implied by ``equity``.

    Returns ``0.0`` — never an exception or NaN — when the curve cannot support a
    ratio: fewer than two returns, or zero dispersion (a flat curve carries no
    evidence of risk-adjusted edge, and a validation gate must fail closed rather
    than crash on such a window).

    Args:
        equity: Equity at each point in time, oldest first (zero or negative
            points are skipped rather than producing infinities).
        periods_per_year: Annualization factor: 252 daily, 52 weekly, 12 monthly.
        risk_free_per_period: Per-period risk-free rate subtracted from each return.
    """
    if periods_per_year <= 0.0 or not math.isfinite(periods_per_year):
        raise ValueError(
            f"periods_per_year must be a positive finite number, got {periods_per_year}"
        )

    curve = list(equity)
    returns = [after / before - 1.0 for before, after in pairwise(curve) if before > 0.0]
    if len(returns) < 2:
        return 0.0

    excess = [r - risk_free_per_period for r in returns]
    std = statistics.stdev(excess)  # sample std (n-1), as in EquityStats::from_returns
    if std == 0.0:
        return 0.0
    return statistics.fmean(excess) / std * math.sqrt(periods_per_year)
