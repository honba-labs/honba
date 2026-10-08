"""Staggered start-date Monte Carlo (Balch pitfall #7: stateful strategy luck).

A path-dependent strategy (trailing stops, regime flags, rebalancing calendars)
can look brilliant purely because of the chosen start date. The defense reruns
the same strategy over staggered start offsets and requires the dispersion of
final equity and drawdown to stay small.

`offsets_to_ranges` shifts the whole window forward by each offset in days
(never extending the end), so every offset run covers the same length of
history. `summarise_start_dates` turns the per-offset outcomes into the gate
verdict; `monte_carlo` drives the real `Honba.backtest` sessions.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from honba.session import DataProvider

__all__ = [
    "MonteCarloResult",
    "StartDateConfig",
    "StartDateResult",
    "monte_carlo",
    "offsets_to_ranges",
    "summarise_start_dates",
]

BacktestStrategy = Any


@dataclass(frozen=True, slots=True)
class StartDateConfig:
    """The offsets to rerun and the dispersion gates they must clear.

    `offsets` are whole-day shifts of the backtest window (day 0 is the original
    window). Gates: the coefficient of variation of final equity across offsets
    must stay under `max_final_equity_cv`, and the max-minus-min spread of
    max-drawdown-% under `max_drawdown_spread_pct`.
    """

    symbol: str
    start: date
    end: date
    offsets: tuple[int, ...] = (0, 5, 10, 20)
    periods_per_year: float = 252.0
    max_final_equity_cv: float = 0.25
    max_drawdown_spread_pct: float = 10.0

    def __post_init__(self) -> None:
        if not self.symbol:
            raise ValueError("StartDateConfig.symbol is required")
        if not self.start < self.end:
            raise ValueError(f"start {self.start} must be before end {self.end}")
        if not self.offsets:
            raise ValueError("offsets needs at least one start-date offset")
        if any(o < 0 for o in self.offsets):
            raise ValueError(f"offsets must be non-negative day counts, got {self.offsets}")
        if self.max_final_equity_cv < 0:
            raise ValueError("max_final_equity_cv must be >= 0")
        if self.max_drawdown_spread_pct < 0:
            raise ValueError("max_drawdown_spread_pct must be >= 0")


def offsets_to_ranges(start: date, end: date, offsets: tuple[int, ...]) -> list[tuple[date, date]]:
    """Shift the whole `[start, end)` window forward by each offset (in days).

    Every offset covers the same span of history; the end moves with the start
    so a long backtest window is never extended into unseen tail data.
    """
    return [(start + timedelta(days=o), end + timedelta(days=o)) for o in offsets]


@dataclass(frozen=True, slots=True)
class StartDateResult:
    """Per-offset outcomes and the dispersion gate verdict."""

    offsets: tuple[int, ...]
    final_equities: tuple[float, ...]
    max_drawdown_pcts: tuple[float, ...]
    gates: StartDateConfig = field(repr=False)

    def __post_init__(self) -> None:
        if not (len(self.offsets) == len(self.final_equities) == len(self.max_drawdown_pcts)):
            raise ValueError("offsets, final_equities and max_drawdown_pcts must align")

    @property
    def mean_final_equity(self) -> float:
        """Mean final equity across start offsets."""
        return statistics.fmean(self.final_equities)

    @property
    def cv_final_equity(self) -> float:
        """Coefficient of variation of final equity (0 for a single offset)."""
        if len(self.final_equities) < 2 or self.mean_final_equity == 0.0:
            return 0.0
        return statistics.pstdev(self.final_equities) / abs(self.mean_final_equity)

    @property
    def drawdown_spread(self) -> float:
        """Max minus min of max-drawdown-% across offsets (points, not a ratio)."""
        if not self.max_drawdown_pcts:
            return 0.0
        return max(self.max_drawdown_pcts) - min(self.max_drawdown_pcts)

    @property
    def checks(self) -> dict[str, bool]:
        """Each gate by name. Equality sits on the failing side (fail closed)."""
        return {
            "final_equity_cv": self.cv_final_equity <= self.gates.max_final_equity_cv,
            "drawdown_spread": self.drawdown_spread <= self.gates.max_drawdown_spread_pct,
        }

    @property
    def passed(self) -> bool:
        """True only when every gate passes."""
        return all(self.checks.values())

    def summary(self) -> str:
        """A human- and log-friendly one-block rendering of the run."""
        gates = self.gates
        equity_line = (
            f"Final equity: mean {self.mean_final_equity:,.2f}, "
            f"CV {self.cv_final_equity:.3f} (<= {gates.max_final_equity_cv:.3f})"
        )
        dd_line = (
            f"Max DD spread: {self.drawdown_spread:.2f} pts "
            f"(<= {gates.max_drawdown_spread_pct:.2f})"
        )
        lines = [
            f"Start-date Monte Carlo: {len(self.offsets)} offsets {list(self.offsets)}",
            equity_line,
            dd_line,
        ]
        if self.passed:
            lines.append("RESULT: PASSED")
        else:
            failed = sum(1 for ok in self.checks.values() if not ok)
            lines.append(f"RESULT: FAILED ({failed} of {len(self.checks)} checks failed)")
        return "\n".join(lines)


def summarise_start_dates(
    *,
    final_equities: tuple[float, ...] | list[float],
    max_drawdown_pcts: tuple[float, ...] | list[float],
    gates: StartDateConfig,
) -> StartDateResult:
    """Build the verdict from one `(equity, drawdown)` pair per offset, in order."""
    return StartDateResult(
        offsets=tuple(gates.offsets),
        final_equities=tuple(final_equities),
        max_drawdown_pcts=tuple(max_drawdown_pcts),
        gates=gates,
    )


MonteCarloResult = StartDateResult
"""Alias kept stable while block-bootstrap methods land later."""


def monte_carlo(
    strategy: BacktestStrategy,
    *,
    symbol: str,
    exchange: str = "NSE",
    start: date | str,
    end: date | str,
    offsets: tuple[int, ...] = (0, 5, 10, 20),
    data: DataProvider | None = None,
    periods_per_year: float = 252.0,
    max_final_equity_cv: float = 0.25,
    max_drawdown_spread_pct: float = 10.0,
    **backtest_kwargs: Any,
) -> StartDateResult:
    """Rerun one strategy over staggered start-date offsets and gate the dispersion.

    Each offset shifts the whole backtest window forward by that many days and
    runs a full `Honba.backtest` session (fresh strategy instance per run, same
    as walk-forward). A strategy whose edge survives only from one lucky
    initialization shows up as a high CV of final equity or a wide drawdown
    spread and fails the verdict.
    """
    from honba.session import Honba

    config = StartDateConfig(
        symbol=symbol,
        start=_as_date(start, "start"),
        end=_as_date(end, "end"),
        offsets=offsets,
        periods_per_year=periods_per_year,
        max_final_equity_cv=max_final_equity_cv,
        max_drawdown_spread_pct=max_drawdown_spread_pct,
    )
    reserved = {"start", "end"} & backtest_kwargs.keys()
    if reserved:
        raise TypeError(
            "start/end are owned by the offset geometry; drop "
            f"{sorted(reserved)} from backtest kwargs"
        )

    equities: list[float] = []
    drawdowns: list[float] = []
    for run_start, run_end in offsets_to_ranges(config.start, config.end, config.offsets):
        result = Honba.backtest(
            _fresh_strategy(strategy),
            symbol=symbol,
            exchange=exchange,
            start=run_start.isoformat(),
            end=run_end.isoformat(),
            data=data,
            **backtest_kwargs,
        ).run()
        equities.append(float(result.metrics.get("final_equity", 0.0)))
        drawdowns.append(float(result.metrics.get("max_drawdown_pct", 0.0)))
    return StartDateResult(
        offsets=config.offsets,
        final_equities=tuple(equities),
        max_drawdown_pcts=tuple(drawdowns),
        gates=config,
    )


def _fresh_strategy(strategy: BacktestStrategy) -> BacktestStrategy:
    """An instance is deep-copied so one offset's state never reaches the next."""
    from copy import deepcopy

    from honba.strategies.base import Strategy

    if isinstance(strategy, Strategy):
        return deepcopy(strategy)
    return strategy


def _as_date(value: date | str, name: str) -> date:
    """Parse an ISO date string (`YYYY-MM-DD`) or pass a `date` through."""
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value)
        except ValueError as e:
            raise ValueError(f"{name} must be an ISO date (YYYY-MM-DD), got {value!r}") from e
    raise TypeError(f"{name} must be a date or ISO date string, got {type(value).__name__}")
