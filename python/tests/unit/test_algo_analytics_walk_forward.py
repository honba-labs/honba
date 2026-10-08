"""Unit tests for ``honba.algo_analytics``: fold geometry, Sharpe and the OOS gates.

Balch pitfall #1 (in-sample backtesting) is defended by pure decisions: how the
train/test windows are cut, how a fold's Sharpe is read off an equity curve, and
which out-of-sample thresholds a strategy must clear. All of that is tested here
without running a backtest; the real backtest loop is covered by
``tests/integration/test_walk_forward_validation.py``.
"""

from __future__ import annotations

import math
import random
import statistics
from datetime import date
from itertools import pairwise

import pytest

from honba.algo_analytics import (
    MIN_OOS_IS_RATIO,
    Fold,
    FoldStats,
    WalkForwardGates,
    WalkForwardResult,
    add_months,
    oos_is_ratio,
    passes_oos_is_gate,
    plan_folds,
    sharpe_ratio,
    stats_from_result,
)
from honba.session import BacktestConfig, BacktestResult

# ---------------------------------------------------------------------------
# add_months
# ---------------------------------------------------------------------------


def test_add_months_clamps_to_the_last_day_of_the_target_month() -> None:
    assert add_months(date(2024, 1, 31), 1) == date(2024, 2, 29)
    assert add_months(date(2023, 1, 31), 1) == date(2023, 2, 28)
    assert add_months(date(2024, 3, 31), -1) == date(2024, 2, 29)
    assert add_months(date(2024, 1, 15), 12) == date(2025, 1, 15)
    assert add_months(date(2024, 5, 31), 0) == date(2024, 5, 31)


# ---------------------------------------------------------------------------
# Fold planning
# ---------------------------------------------------------------------------


def test_expanding_folds_anchor_the_training_window_at_the_start() -> None:
    windows = plan_folds(
        start=date(2020, 1, 1),
        end=date(2023, 1, 1),
        train_months=24,
        test_months=6,
        n_folds=2,
    )
    assert [(w.train_start, w.train_end, w.test_start, w.test_end) for w in windows] == [
        (date(2020, 1, 1), date(2022, 1, 1), date(2022, 1, 1), date(2022, 7, 1)),
        (date(2020, 1, 1), date(2022, 7, 1), date(2022, 7, 1), date(2023, 1, 1)),
    ]


def test_rolling_folds_slide_the_training_window_forward() -> None:
    windows = plan_folds(
        start=date(2020, 1, 1),
        end=date(2023, 1, 1),
        train_months=24,
        test_months=6,
        n_folds=2,
        window="rolling",
    )
    assert [(w.train_start, w.train_end, w.test_start, w.test_end) for w in windows] == [
        (date(2020, 1, 1), date(2022, 1, 1), date(2022, 1, 1), date(2022, 7, 1)),
        (date(2020, 7, 1), date(2022, 7, 1), date(2022, 7, 1), date(2023, 1, 1)),
    ]


def test_test_windows_are_contiguous_and_never_overlap() -> None:
    start, end = date(2020, 1, 1), date(2024, 1, 1)
    windows = plan_folds(
        start=start, end=end, train_months=6, test_months=3, n_folds=8, window="rolling"
    )
    for i, w in enumerate(windows):
        assert w.train_start <= w.train_end < w.test_end
        assert w.train_end == w.test_start
        assert start <= w.test_start
        assert w.test_end <= end
        if i:
            assert windows[i - 1].test_end == w.test_start


def test_plan_folds_rejects_a_range_that_ends_before_the_last_test_window() -> None:
    with pytest.raises(ValueError, match="data range"):
        plan_folds(
            start=date(2020, 1, 1),
            end=date(2022, 12, 1),
            train_months=24,
            test_months=6,
            n_folds=2,
        )


@pytest.mark.parametrize(
    ("train_months", "test_months", "n_folds", "window"),
    [
        (0, 6, 2, "expanding"),
        (24, 0, 2, "expanding"),
        (24, 6, 1, "expanding"),
        (24, 6, 2, "sideways"),
    ],
)
def test_plan_folds_validates_its_arguments(
    train_months: int, test_months: int, n_folds: int, window: str
) -> None:
    with pytest.raises(ValueError):
        plan_folds(
            start=date(2020, 1, 1),
            end=date(2030, 1, 1),
            train_months=train_months,
            test_months=test_months,
            n_folds=n_folds,
            window=window,  # type: ignore[arg-type]
        )


# ---------------------------------------------------------------------------
# Sharpe of an equity curve
# ---------------------------------------------------------------------------


def test_sharpe_ratio_matches_the_sample_formula() -> None:
    equity = [100.0, 102.0, 99.0, 103.0, 105.0, 104.0, 108.0]
    returns = [b / a - 1.0 for a, b in pairwise(equity)]
    expected = statistics.fmean(returns) / statistics.stdev(returns) * (252.0**0.5)
    assert sharpe_ratio(equity) == pytest.approx(expected)
    assert sharpe_ratio(equity, periods_per_year=12.0) == pytest.approx(
        statistics.fmean(returns) / statistics.stdev(returns) * (12.0**0.5)
    )


def test_sharpe_ratio_sign_follows_the_drift_of_the_curve() -> None:
    rng = random.Random(7)
    noise = [rng.uniform(-0.001, 0.001) for _ in range(60)]

    def curve(drift: float) -> list[float]:
        equity, level = [100.0], 100.0
        for step in noise:
            level *= 1.0 + drift + step
            equity.append(level)
        return equity

    assert sharpe_ratio(curve(0.003)) > 0.0
    assert sharpe_ratio(curve(-0.003)) < 0.0


def test_sharpe_ratio_is_zero_when_the_curve_has_no_dispersion_or_no_points() -> None:
    assert sharpe_ratio([]) == 0.0
    assert sharpe_ratio([100.0]) == 0.0
    assert sharpe_ratio([100.0, 100.0, 100.0, 100.0]) == 0.0


# ---------------------------------------------------------------------------
# stats_from_result
# ---------------------------------------------------------------------------


def test_stats_from_result_reads_the_session_metrics_and_curve() -> None:
    result = BacktestResult(
        strategy_name="s",
        config=BacktestConfig(symbol="XYZ", start="2024-01-01", end="2024-06-01"),
        metrics={
            "total_return_pct": 12.5,
            "max_drawdown_pct": 4.2,
            "final_equity": 112_500.0,
            "n_fills": 6.0,
            "n_trades": 3.0,
        },
        equity_curve=[(1, 100_000.0), (2, 106_000.0), (3, 104_000.0), (4, 112_500.0)],
    )
    stats = stats_from_result(result)
    assert stats.total_return_pct == 12.5
    assert stats.max_drawdown_pct == 4.2
    assert stats.final_equity == 112_500.0
    assert stats.n_fills == 6
    assert stats.n_trades == 3
    returns = [106_000 / 100_000 - 1.0, 104_000 / 106_000 - 1.0, 112_500 / 104_000 - 1.0]
    assert stats.sharpe == pytest.approx(
        statistics.fmean(returns) / statistics.stdev(returns) * (252.0**0.5)
    )


def test_stats_from_result_of_a_run_without_bars_has_zero_sharpe() -> None:
    result = BacktestResult(
        strategy_name="s",
        config=BacktestConfig(symbol="XYZ", start="2024-01-01", end="2024-06-01"),
    )
    stats = stats_from_result(result)
    assert stats == FoldStats()
    assert stats.sharpe == 0.0


# ---------------------------------------------------------------------------
# Walk-forward gates
# ---------------------------------------------------------------------------


def _fold(index: int, in_sample: float, out_of_sample: float) -> Fold:
    return Fold(
        index=index,
        train_start=date(2020, 1, 1),
        train_end=date(2022, 1, 1),
        test_start=date(2022, 1, 1),
        test_end=date(2022, 7, 1),
        in_sample=FoldStats(sharpe=in_sample),
        out_of_sample=FoldStats(sharpe=out_of_sample),
    )


def _result(*sharpes: float, gates: WalkForwardGates | None = None) -> WalkForwardResult:
    return WalkForwardResult(
        folds=tuple(_fold(i, 1.0, s) for i, s in enumerate(sharpes)),
        train_months=24,
        test_months=6,
        window="expanding",
        gates=gates or WalkForwardGates(),
    )


def test_walk_forward_passes_when_every_fold_is_profitable_and_stable() -> None:
    wf = _result(1.1, 0.9, 1.0, 1.2)
    assert wf.checks == {
        "mean_oos_sharpe": True,
        "oos_sharpe_std": True,
        "profitable_folds": True,
    }
    assert wf.passed is True
    assert wf.profitable_folds == 4
    assert wf.profitable_fraction == pytest.approx(1.0)
    assert wf.mean_oos_sharpe == pytest.approx(1.05)
    assert wf.min_oos_sharpe == pytest.approx(0.9)
    assert wf.max_oos_sharpe == pytest.approx(1.2)
    assert wf.oos_sharpes == [1.1, 0.9, 1.0, 1.2]


def test_mean_oos_sharpe_gate_is_strict() -> None:
    # mean is exactly the 0.8 threshold: not strictly greater, so it fails.
    wf = _result(0.4, 0.4, 1.2, 1.2)
    assert wf.mean_oos_sharpe == pytest.approx(0.8)
    assert wf.checks["mean_oos_sharpe"] is False
    assert wf.checks["oos_sharpe_std"] is True
    assert wf.passed is False


def test_oos_sharpe_std_gate_is_strict() -> None:
    # mean 1.0 and population std 0.5 are both exact in binary here: not strictly
    # less than the 0.5 threshold, so the spread gate fails.
    wf = _result(0.5, 1.5)
    assert wf.mean_oos_sharpe == pytest.approx(1.0)
    assert wf.oos_sharpe_std == pytest.approx(0.5)
    assert wf.checks["oos_sharpe_std"] is False
    assert wf.checks["mean_oos_sharpe"] is True
    assert wf.passed is False


def test_profitable_fraction_gate_accepts_exactly_three_quarters() -> None:
    # 3 of 4 folds above water: 0.75 is the inclusive default threshold.
    wf = _result(1.07, 1.07, 1.07, 0.0)
    assert wf.profitable_fraction == pytest.approx(0.75)
    assert wf.checks == {
        "mean_oos_sharpe": True,
        "oos_sharpe_std": True,
        "profitable_folds": True,
    }
    assert wf.passed is True


def test_walk_forward_gates_are_configurable() -> None:
    wf = _result(0.5, 0.6, 0.7, 0.4, gates=WalkForwardGates(min_mean_oos_sharpe=0.5))
    assert wf.checks["mean_oos_sharpe"] is True
    assert wf.gates.min_mean_oos_sharpe == 0.5


def test_walk_forward_summary_lists_every_fold_sharpe_and_the_result() -> None:
    passed = _result(1.1, 0.9).summary()
    assert "Walk-forward: 2 folds" in passed
    assert "OOS Sharpe per fold: [1.1, 0.9]" in passed
    assert "Fraction of folds profitable: 2/2" in passed
    assert "RESULT: PASSED" in passed

    failed = _result(0.1, -0.2).summary()
    assert "RESULT: FAILED" in failed
    assert "of 3 checks failed" in failed


def test_walk_forward_rejects_an_empty_fold_list() -> None:
    with pytest.raises(ValueError, match="fold"):
        WalkForwardResult(
            folds=(),
            train_months=24,
            test_months=6,
            window="expanding",
            gates=WalkForwardGates(),
        )


# ---------------------------------------------------------------------------
# In-sample / out-of-sample ratio (the 50% rule)
# ---------------------------------------------------------------------------


def test_oos_is_ratio_divides_out_of_sample_by_in_sample_sharpe() -> None:
    assert oos_is_ratio(FoldStats(sharpe=1.2), FoldStats(sharpe=0.9)) == pytest.approx(0.75)
    assert MIN_OOS_IS_RATIO == 0.5


def test_oos_is_ratio_is_not_a_number_when_the_in_sample_sharpe_is_zero() -> None:
    ratio = oos_is_ratio(FoldStats(sharpe=0.0), FoldStats(sharpe=1.0))
    assert math.isnan(ratio)  # a meaningless ratio never opens a gate


def test_passes_oos_is_gate_requires_half_of_the_in_sample_sharpe() -> None:
    assert passes_oos_is_gate(FoldStats(sharpe=1.0), FoldStats(sharpe=0.5)) is True
    assert passes_oos_is_gate(FoldStats(sharpe=1.0), FoldStats(sharpe=0.49)) is False
    assert passes_oos_is_gate(FoldStats(sharpe=0.0), FoldStats(sharpe=0.9)) is False
    assert passes_oos_is_gate(FoldStats(sharpe=1.0), FoldStats(sharpe=0.4), min_ratio=0.3) is True


def test_fold_exposes_its_own_out_of_sample_ratio() -> None:
    fold = _fold(0, in_sample=2.0, out_of_sample=1.0)
    assert fold.oos_is_ratio == pytest.approx(0.5)
