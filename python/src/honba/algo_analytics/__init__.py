"""Statistical validation for backtests: out-of-sample gates, not in-sample glory.

Implements Balch's *10 Ways Backtests Lie* pitfall #1 (in-sample backtesting)
and the machinery the rest of the gates build on:

* :func:`train_test_split` — one cut; out-of-sample Sharpe must keep at least
  half of in-sample (:data:`MIN_OOS_IS_RATIO`).
* :func:`walk_forward` — rolling train/test folds with the documented gates:
  mean out-of-sample Sharpe > 0.8, fold Sharpe std < 0.5, >= 75% of folds
  profitable.
* :func:`sharpe_ratio`, :func:`plan_folds` — the shared definitions both use.
* :func:`deflated_sharpe` — pitfall #6: selection-adjusted Sharpe over N trials.
* :func:`monte_carlo` — pitfall #7: staggered start-date dispersion gates.
* :func:`plateau` — pitfall #9: the least flat adjacent step of a parameter
  surface must stay >= 0.4, or the grid is a cliff, not a plateau.

Example::

    from honba.algo_analytics import WalkForwardGates, walk_forward

    wf = walk_forward(
        SmaCross(),
        symbol="NIFTY50",
        start="2020-01-01",
        end="2024-01-01",
        data=provider,
        train_months=24,
        test_months=6,
        n_folds=8,
    )
    print(wf.summary())
    assert wf.passed  # CI gate: no promotion to paper trading otherwise
"""

from honba.algo_analytics.deflated_sharpe import deflated_sharpe
from honba.algo_analytics.folds import FoldWindow, Window, add_months, plan_folds
from honba.algo_analytics.metrics import sharpe_ratio
from honba.algo_analytics.monte_carlo import (
    MonteCarloResult,
    StartDateConfig,
    StartDateResult,
    monte_carlo,
    offsets_to_ranges,
    summarise_start_dates,
)
from honba.algo_analytics.plateau import (
    PlateauGates,
    PlateauResult,
    heatmap,
    plateau,
    plateau_score,
    step_flatness,
    summarise_plateau,
)
from honba.algo_analytics.walk_forward import (
    MIN_OOS_IS_RATIO,
    BacktestStrategy,
    Fold,
    FoldStats,
    WalkForwardGates,
    WalkForwardResult,
    oos_is_ratio,
    passes_oos_is_gate,
    stats_from_result,
    train_test_split,
    walk_forward,
)

__all__ = [
    "MIN_OOS_IS_RATIO",
    "BacktestStrategy",
    "Fold",
    "FoldStats",
    "FoldWindow",
    "MonteCarloResult",
    "PlateauGates",
    "PlateauResult",
    "StartDateConfig",
    "StartDateResult",
    "WalkForwardGates",
    "WalkForwardResult",
    "Window",
    "add_months",
    "deflated_sharpe",
    "heatmap",
    "monte_carlo",
    "offsets_to_ranges",
    "oos_is_ratio",
    "passes_oos_is_gate",
    "plan_folds",
    "plateau",
    "plateau_score",
    "sharpe_ratio",
    "stats_from_result",
    "step_flatness",
    "summarise_plateau",
    "summarise_start_dates",
    "train_test_split",
    "walk_forward",
]
