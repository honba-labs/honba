"""Walk-forward validation and out-of-sample gates (Balch pitfall #1).

Tweaking a strategy on the full dataset until it shines is curve-fitting, not
research. The defense is mechanical: cut history into train/test folds
(:mod:`honba.algo_analytics.folds`), replay the *same* strategy over each window
through ``Honba.backtest`` — which fills at the next open with full Indian
costs and settlement — and require the out-of-sample numbers to hold up:

* ``train_test_split``: the single-cut gate. The out-of-sample Sharpe must keep
  at least half of the in-sample Sharpe (``MIN_OOS_IS_RATIO``).
* ``walk_forward``: the rolling gate. The mean out-of-sample Sharpe across folds
  must clear the configured threshold, the fold spread must stay narrow, and at
  least three quarters of the folds must be profitable.

Every fold runs on a fresh strategy instance (a class is instantiated per fold;
an instance is deep-copied), so state from the training window of one fold can
never leak into the next.
"""

from __future__ import annotations

import statistics
from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING, Any

from honba.algo_analytics.folds import Window, plan_folds
from honba.algo_analytics.metrics import sharpe_ratio
from honba.strategies.base import Strategy

if TYPE_CHECKING:
    from honba.session import BacktestResult, DataProvider

__all__ = [
    "MIN_OOS_IS_RATIO",
    "Fold",
    "FoldStats",
    "WalkForwardGates",
    "WalkForwardResult",
    "oos_is_ratio",
    "passes_oos_is_gate",
    "stats_from_result",
    "train_test_split",
    "walk_forward",
]

MIN_OOS_IS_RATIO = 0.5
"""Out-of-sample Sharpe must keep at least half of the in-sample Sharpe."""

BacktestStrategy = Strategy | type[Strategy] | str | Path
"""Anything ``Honba.backtest`` accepts: instance, class, .py path, catalog name."""


@dataclass(frozen=True, slots=True)
class FoldStats:
    """What one window (in-sample or out-of-sample) produced."""

    sharpe: float = 0.0
    total_return_pct: float = 0.0
    max_drawdown_pct: float = 0.0
    final_equity: float = 0.0
    n_fills: int = 0
    n_trades: int = 0


def stats_from_result(result: BacktestResult, *, periods_per_year: float = 252.0) -> FoldStats:
    """Read a fold's statistics off a finished ``BacktestResult``.

    Level metrics come from the session; the Sharpe is read off the equity
    curve, which folds share with Rust ``EquityStats`` (same definition).
    A result without bars has a zero Sharpe — it fails gates, it never crashes them.
    """
    metrics = result.metrics
    return FoldStats(
        sharpe=sharpe_ratio(
            [equity for _, equity in result.equity_curve],
            periods_per_year=periods_per_year,
        ),
        total_return_pct=_metric(metrics, "total_return_pct"),
        max_drawdown_pct=_metric(metrics, "max_drawdown_pct"),
        final_equity=_metric(metrics, "final_equity"),
        n_fills=int(_metric(metrics, "n_fills")),
        n_trades=int(_metric(metrics, "n_trades")),
    )


def _metric(metrics: Mapping[str, float], key: str) -> float:
    value = metrics.get(key, 0.0)
    return float(value)


def oos_is_ratio(in_sample: FoldStats, out_of_sample: FoldStats) -> float:
    """Out-of-sample Sharpe divided by in-sample Sharpe.

    NaN when the in-sample Sharpe is zero: with nothing fitted, a ratio is
    meaningless, and NaN fails every ``>=`` gate by construction.
    """
    if in_sample.sharpe == 0.0:
        return float("nan")
    return out_of_sample.sharpe / in_sample.sharpe


def passes_oos_is_gate(
    in_sample: FoldStats,
    out_of_sample: FoldStats,
    *,
    min_ratio: float = MIN_OOS_IS_RATIO,
) -> bool:
    """The 50% rule: out-of-sample must beat ``min_ratio`` of in-sample."""
    return oos_is_ratio(in_sample, out_of_sample) >= min_ratio


@dataclass(frozen=True, slots=True)
class Fold:
    """One train/test cut with both evaluations attached."""

    index: int
    train_start: date
    train_end: date
    test_start: date
    test_end: date
    in_sample: FoldStats
    out_of_sample: FoldStats

    @property
    def oos_is_ratio(self) -> float:
        """This fold's out-of-sample / in-sample Sharpe ratio."""
        return oos_is_ratio(self.in_sample, self.out_of_sample)


@dataclass(frozen=True, slots=True)
class WalkForwardGates:
    """The out-of-sample thresholds a strategy must clear.

    Defaults are the documented bar: mean out-of-sample Sharpe strictly above
    0.8, fold Sharpe spread (population std) strictly below 0.5, and at least
    three quarters of folds profitable. "Profitable" means OOS Sharpe above zero.
    """

    min_mean_oos_sharpe: float = 0.8
    max_oos_sharpe_std: float = 0.5
    min_profitable_fraction: float = 0.75


@dataclass(frozen=True, slots=True)
class WalkForwardResult:
    """The full walk-forward run: folds, aggregates, gate verdict."""

    folds: tuple[Fold, ...]
    train_months: int
    test_months: int
    window: Window
    gates: WalkForwardGates = field(default_factory=WalkForwardGates)

    def __post_init__(self) -> None:
        if not self.folds:
            raise ValueError("WalkForwardResult needs at least one fold")

    @property
    def oos_sharpes(self) -> list[float]:
        """Out-of-sample Sharpe of each fold, in fold order — the number that matters."""
        return [fold.out_of_sample.sharpe for fold in self.folds]

    @property
    def mean_oos_sharpe(self) -> float:
        """Mean out-of-sample Sharpe across folds."""
        return statistics.fmean(self.oos_sharpes)

    @property
    def oos_sharpe_std(self) -> float:
        """Spread of the fold Sharpes (population std, so a single fold is 0.0)."""
        if len(self.oos_sharpes) < 2:
            return 0.0
        return statistics.pstdev(self.oos_sharpes)

    @property
    def min_oos_sharpe(self) -> float:
        """Worst fold's out-of-sample Sharpe."""
        return min(self.oos_sharpes)

    @property
    def max_oos_sharpe(self) -> float:
        """Best fold's out-of-sample Sharpe."""
        return max(self.oos_sharpes)

    @property
    def profitable_folds(self) -> int:
        """Folds with an out-of-sample Sharpe strictly above zero."""
        return sum(1 for s in self.oos_sharpes if s > 0.0)

    @property
    def profitable_fraction(self) -> float:
        """Folds profitable, as a fraction of all folds."""
        return self.profitable_folds / len(self.folds)

    @property
    def checks(self) -> dict[str, bool]:
        """Each gate by name. Equality sits on the failing side (fail closed)."""
        gates = self.gates
        return {
            "mean_oos_sharpe": self.mean_oos_sharpe > gates.min_mean_oos_sharpe,
            "oos_sharpe_std": self.oos_sharpe_std < gates.max_oos_sharpe_std,
            "profitable_folds": self.profitable_fraction >= gates.min_profitable_fraction,
        }

    @property
    def passed(self) -> bool:
        """True only when every gate passes."""
        return all(self.checks.values())

    def summary(self) -> str:
        """A human- and log-friendly one-block rendering of the run."""
        gates = self.gates
        checks = self.checks
        verdicts = " ".join(
            f"[{name}: {'OK' if checks[name] else 'FAIL'}]"
            for name in ("mean_oos_sharpe", "oos_sharpe_std", "profitable_folds")
        )
        header = (
            f"Walk-forward: {len(self.folds)} folds "
            f"({self.window}, train {self.train_months}m, test {self.test_months}m)"
        )
        spread = (
            f"Mean: {self.mean_oos_sharpe:.2f}   Std: {self.oos_sharpe_std:.2f}   "
            f"Min: {self.min_oos_sharpe:.2f}   Max: {self.max_oos_sharpe:.2f}"
        )
        checks_line = (
            f"Checks: mean OOS Sharpe {self.mean_oos_sharpe:.2f} > "
            f"{gates.min_mean_oos_sharpe:.2f}, "
            f"OOS Sharpe std {self.oos_sharpe_std:.2f} < {gates.max_oos_sharpe_std:.2f}, "
            f"profitable folds {self.profitable_folds}/{len(self.folds)} >= "
            f"{gates.min_profitable_fraction:.0%} {verdicts}"
        )
        lines = [
            header,
            f"OOS Sharpe per fold: {self.oos_sharpes}",
            spread,
            f"Fraction of folds profitable: {self.profitable_folds}/{len(self.folds)}",
            checks_line,
        ]
        if self.passed:
            lines.append("RESULT: PASSED")
        else:
            failed = sum(1 for ok in checks.values() if not ok)
            lines.append(f"RESULT: FAILED ({failed} of {len(checks)} checks failed)")
        return "\n".join(lines)


def train_test_split(
    strategy: BacktestStrategy,
    *,
    start: date | str,
    end: date | str,
    train_end: date | str,
    symbol: str,
    exchange: str = "NSE",
    data: DataProvider | None = None,
    periods_per_year: float = 252.0,
    **backtest_kwargs: Any,
) -> tuple[FoldStats, FoldStats]:
    """Calibrate on ``[start, train_end)``, judge on ``[train_end, end)``.

    Returns ``(in_sample, out_of_sample)``. The caller applies the 50% rule via
    :func:`passes_oos_is_gate`: a ratio below :data:`MIN_OOS_IS_RATIO` means the
    edge found in calibration did not survive contact with held-back data.
    """
    start_d = _as_date(start, "start")
    train_end_d = _as_date(train_end, "train_end")
    end_d = _as_date(end, "end")
    if not start_d < train_end_d < end_d:
        raise ValueError(f"need start < train_end < end, got {start_d} / {train_end_d} / {end_d}")
    run = _run_factory(symbol, exchange, data, periods_per_year, backtest_kwargs)
    return (
        run(strategy, start_d, train_end_d),
        run(strategy, train_end_d, end_d),
    )


def walk_forward(
    strategy: BacktestStrategy,
    *,
    start: date | str,
    end: date | str,
    symbol: str,
    exchange: str = "NSE",
    data: DataProvider | None = None,
    train_months: int = 24,
    test_months: int = 6,
    n_folds: int = 8,
    window: Window = "expanding",
    periods_per_year: float = 252.0,
    gates: WalkForwardGates | None = None,
    **backtest_kwargs: Any,
) -> WalkForwardResult:
    """Roll a train/test split through history and apply the out-of-sample gates.

    Each fold runs two full ``Honba.backtest`` sessions (train window, then test
    window) with the caller's backtest settings (``timeframe``, ``cash``,
    ``costs``, ``fill``, ...). Test windows are contiguous and out-of-sample:
    fold *i*'s test window is never part of fold *i*'s calibration data, and
    ``expanding`` folds only ever add history before it.

    Args:
        start / end: Full data range the folds are cut from (``end`` must cover
            ``train_months + n_folds * test_months`` from ``start``).
        train_months / test_months / n_folds: Fold geometry; see
            :func:`honba.algo_analytics.plan_folds`.
        window: ``expanding`` (anchored at ``start``, default) or ``rolling``.
        gates: Thresholds to judge the folds against; ``WalkForwardGates()`` defaults.

    Returns:
        A :class:`WalkForwardResult` whose ``passed`` verdict is the CI gate.
    """
    start_d = _as_date(start, "start")
    end_d = _as_date(end, "end")
    windows = plan_folds(
        start_d,
        end_d,
        train_months=train_months,
        test_months=test_months,
        n_folds=n_folds,
        window=window,
    )
    run = _run_factory(symbol, exchange, data, periods_per_year, backtest_kwargs)

    folds = tuple(
        Fold(
            index=i,
            train_start=w.train_start,
            train_end=w.train_end,
            test_start=w.test_start,
            test_end=w.test_end,
            in_sample=run(strategy, w.train_start, w.train_end),
            out_of_sample=run(strategy, w.test_start, w.test_end),
        )
        for i, w in enumerate(windows)
    )
    return WalkForwardResult(
        folds=folds,
        train_months=train_months,
        test_months=test_months,
        window=window,
        gates=gates or WalkForwardGates(),
    )


def _run_factory(
    symbol: str,
    exchange: str,
    data: DataProvider | None,
    periods_per_year: float,
    backtest_kwargs: Mapping[str, Any],
):
    """Build the per-window ``run(strategy, start, end) -> FoldStats`` closure."""
    reserved = {"start", "end"} & backtest_kwargs.keys()
    if reserved:
        raise TypeError(
            f"start/end are owned by the fold geometry; drop {sorted(reserved)} from "
            "backtest kwargs"
        )

    def run(strategy: BacktestStrategy, start: date, end: date) -> FoldStats:
        from honba.session import Honba

        result = Honba.backtest(
            _fresh_strategy(strategy),
            symbol=symbol,
            exchange=exchange,
            start=start.isoformat(),
            end=end.isoformat(),
            data=data,
            **backtest_kwargs,
        ).run()
        return stats_from_result(result, periods_per_year=periods_per_year)

    return run


def _fresh_strategy(strategy: BacktestStrategy) -> BacktestStrategy:
    """An instance is deep-copied so one fold's state never reaches the next.

    Classes, .py paths and catalog names resolve to a new instance inside
    ``Honba.backtest`` on every call, so they are passed through unchanged.
    """
    if isinstance(strategy, Strategy):
        return deepcopy(strategy)
    return strategy


def _as_date(value: date | str, name: str) -> date:
    """Parse an ISO date string (``YYYY-MM-DD``) or pass a ``date`` through."""
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value)
        except ValueError as e:
            raise ValueError(f"{name} must be an ISO date (YYYY-MM-DD), got {value!r}") from e
    raise TypeError(f"{name} must be a date or ISO date string, got {type(value).__name__}")
