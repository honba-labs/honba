"""Parameter-surface plateau validation (Balch pitfall #9: trusting complex models).

A dense algorithm whose performance collapses when one parameter moves by one
step did not find structure — it memorized the sample. The defense judges the
*shape* of the parameter surface: run a grid, score every point, and measure
how flat the scores are across adjacent parameter values.

The step metric is scale-free and symmetric::

    step_flatness(a, b) = 1 - |a - b| / max(|a|, |b|)   (clamped to [0, 1])

so multiplying every score by a constant (any Sharpe scaling) cannot change the
verdict, a sign flip scores 0, and two equal scores score 1. The **plateau
score is the least flat adjacent step** — fail closed: one cliff anywhere (the
doc's ``(20, 50) -> (21, 50)`` plunge from Sharpe 1.8 to 0.3 scores ~0.17)
rejects the whole surface even when the mean looks healthy, because a cliff is
exactly the signature of parameter memorization. The gate is
:data:`PlateauGates.min_flatness_score` = 0.4 by default ("flatness score >= 0.4").

Two seams, mirroring :mod:`honba.algo_analytics.monte_carlo`:

* :func:`summarise_plateau` — pure: a mapping of parameter coordinates to
  scores becomes a :class:`PlateauResult` verdict (checks / passed / summary).
* :func:`plateau` — the driver: the Cartesian product of ``params`` runs
  through full ``Honba.backtest`` sessions (fresh strategy copy per point,
  equity-curve Sharpe by default) and is summarised.

:func:`heatmap` renders a 2-D grid as a row-major matrix (``None`` holes) for
notebooks, agents and CI logs. Pure: no I/O, no wall clock.
"""

from __future__ import annotations

import itertools
import math
import statistics
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from honba.session import BacktestResult, DataProvider

__all__ = [
    "PlateauGates",
    "PlateauResult",
    "heatmap",
    "plateau",
    "plateau_score",
    "step_flatness",
    "summarise_plateau",
]

BacktestStrategy = Any
Coord = tuple[float, ...]
"""One point of the parameter surface: the parameter values, in ``params`` order."""

Grid = Mapping[Coord, float]
"""Parameter coordinates to scores (Sharpe by convention, any finite number)."""


@dataclass(frozen=True, slots=True)
class PlateauGates:
    """The flatness threshold a parameter surface must clear.

    ``min_flatness_score`` is applied to the plateau score (the least flat
    adjacent step), inclusively: a score of exactly 0.4 passes, matching the
    documented "flatness score >= 0.4".
    """

    min_flatness_score: float = 0.4

    def __post_init__(self) -> None:
        if not (math.isfinite(self.min_flatness_score) and 0.0 <= self.min_flatness_score <= 1.0):
            raise ValueError(
                "min_flatness_score must be a finite number in [0, 1], "
                f"got {self.min_flatness_score}"
            )


def step_flatness(a: float, b: float) -> float:
    """Flatness of one adjacent step between scores ``a`` and ``b``, in [0, 1].

    ``1 - |a - b| / max(|a|, |b|)``: 1 for equal scores (including two zeros),
    0 when the scores flip sign, and scale-free — rescaling every score leaves
    it unchanged. The taker's cliff from 1.8 to 0.3 scores ~0.167.
    """
    peak = max(abs(a), abs(b))
    if peak == 0.0:
        return 1.0
    return max(0.0, 1.0 - abs(a - b) / peak)


def plateau_score(grid: Grid) -> float:
    """The least flat adjacent step of ``grid`` — the plateau score, in [0, 1].

    Raises ``ValueError`` for an empty grid, ragged coordinates, non-finite
    scores, or a grid with no adjacent parameter pair to measure.
    """
    return min(flatness for _, _, flatness in _steps(_normalise(grid)))


def summarise_plateau(grid: Grid, gates: PlateauGates | None = None) -> PlateauResult:
    """Build the gate verdict from one score per parameter coordinate."""
    scores = _normalise(grid)
    steps = _steps(scores)
    flatness = min(f for _, _, f in steps)
    worst = min(steps, key=lambda t: t[2])
    return PlateauResult(
        points=len(scores),
        steps=len(steps),
        flatness=flatness,
        mean_flatness=statistics.fmean(f for _, _, f in steps),
        worst_step=(worst[0], worst[1]),
        scores=scores,
        gates=PlateauGates() if gates is None else gates,
    )


@dataclass(frozen=True, slots=True)
class PlateauResult:
    """The parameter surface and the flatness gate verdict."""

    points: int
    steps: int
    flatness: float
    mean_flatness: float
    worst_step: tuple[Coord, Coord]
    scores: Mapping[Coord, float] = field(repr=False)
    gates: PlateauGates = field(repr=False)

    @property
    def checks(self) -> dict[str, bool]:
        """Each gate by name. The documented rule is inclusive: flatness >= 0.4."""
        return {"flatness": self.flatness >= self.gates.min_flatness_score}

    @property
    def passed(self) -> bool:
        """True only when every gate passes."""
        return all(self.checks.values())

    def summary(self) -> str:
        """A human- and log-friendly one-block rendering of the verdict."""
        lo, hi = self.worst_step
        lines = [
            f"Parameter plateau: {self.points} points, {self.steps} adjacent steps",
            (
                f"Flatness: {self.flatness:.3f} (worst step {list(lo)} -> {list(hi)}), "
                f"mean {self.mean_flatness:.3f} "
                f"(>= {self.gates.min_flatness_score:.3f})"
            ),
        ]
        if self.passed:
            lines.append("RESULT: PASSED")
        else:
            failed = sum(1 for ok in self.checks.values() if not ok)
            lines.append(f"RESULT: FAILED ({failed} of {len(self.checks)} checks failed)")
        return "\n".join(lines)


def heatmap(grid: Grid) -> list[list[float | None]]:
    """Row-major score matrix of a 2-D grid: axis-0 ascending by row, axis-1 by column.

    A coordinate the grid does not contain becomes ``None`` (a hole, never a
    silent 0), so the matrix is JSON-serialisable for notebooks and agents.
    Raises ``ValueError`` unless every coordinate is 2-D.
    """
    scores = _normalise(grid)
    dims = {len(c) for c in scores}
    if dims != {2}:
        raise ValueError(f"heatmap needs a two-dimensional grid, got dimensions {sorted(dims)}")
    rows = sorted({c[0] for c in scores})
    cols = sorted({c[1] for c in scores})
    return [[scores.get((r, c)) for c in cols] for r in rows]


def plateau(
    strategy: BacktestStrategy,
    *,
    params: Mapping[str, Sequence[float]],
    symbol: str,
    start: date | str,
    end: date | str,
    exchange: str = "NSE",
    data: DataProvider | None = None,
    periods_per_year: float = 252.0,
    score: Callable[[BacktestResult], float] | None = None,
    min_flatness_score: float = 0.4,
    **backtest_kwargs: Any,
) -> PlateauResult:
    """Run every point of a Cartesian parameter grid and gate the surface's flatness.

    Each axis is one strategy attribute (``params={"fast": [2, 3, 4], ...}``); a
    fresh copy of ``strategy`` gets the point's values set on it and runs a full
    ``Honba.backtest`` session with the caller's backtest settings. The default
    score is the equity-curve Sharpe (``honba.algo_analytics.stats_from_result``,
    the same definition walk-forward gates use); pass ``score=`` for anything else.

    The grid, the strategy's parameters and the gate geometry are validated
    *before* the first run, so an unusable surface costs nothing.

    Args:
        strategy: A ``Strategy`` *instance* exposing every parameter as an attribute.
        params: Parameter name -> axis values (>= 2 values on at least one axis).
        min_flatness_score: Inclusive flatness threshold for the least flat step.

    Returns:
        A :class:`PlateauResult` whose ``passed`` verdict is the CI gate.
    """
    from honba.session import Honba
    from honba.strategies.base import Strategy

    if not isinstance(strategy, Strategy):
        raise TypeError(
            "plateau() drives a strategy instance (it sets each parameter on a fresh "
            f"copy per grid point), got {type(strategy).__name__}"
        )
    axes = _validate_params(params)
    missing = [name for name in params if not hasattr(strategy, name)]
    if missing:
        raise ValueError(
            f"strategy {getattr(strategy, 'name', type(strategy).__name__)!r} does not expose "
            f"parameter(s) {missing}: every grid axis must be an attribute of the strategy"
        )
    coords = list(itertools.product(*axes))
    _steps({c: 0.0 for c in coords})  # refuse a surface with no adjacent step, before running

    gates = PlateauGates(min_flatness_score=min_flatness_score)
    grid: dict[Coord, float] = {}
    for coord in coords:
        fresh = _fresh_strategy(strategy)
        for name, value in zip(params, coord, strict=True):
            setattr(fresh, name, value)
        result = Honba.backtest(
            fresh,
            symbol=symbol,
            exchange=exchange,
            start=start,
            end=end,
            data=data,
            **backtest_kwargs,
        ).run()
        if score is None:
            from honba.algo_analytics.walk_forward import stats_from_result

            value = stats_from_result(result, periods_per_year=periods_per_year).sharpe
        else:
            value = float(score(result))
        grid[coord] = float(value)
    return summarise_plateau(grid, gates)


# -- internals ------------------------------------------------------------------
def _validate_params(params: Mapping[str, Sequence[float]]) -> list[tuple[float, ...]]:
    """Check ``params`` and return its axes as value tuples, in mapping order."""
    if not params:
        raise ValueError("params is empty: name at least one strategy parameter")
    axes: list[tuple[float, ...]] = []
    for name, values in params.items():
        if not isinstance(name, str) or not name:
            raise ValueError(f"parameter names must be non-empty strings, got {name!r}")
        try:
            axis = tuple(values)
        except TypeError as e:
            raise ValueError(f"parameter {name!r} needs a sequence of values") from e
        if not axis:
            raise ValueError(f"parameter {name!r} has no values")
        if len(set(axis)) != len(axis):
            raise ValueError(f"parameter {name!r} has duplicate values: {list(axis)}")
        for v in axis:
            if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
                raise ValueError(f"parameter {name!r} values must be finite numbers, got {v!r}")
        axes.append(axis)
    return axes


def _normalise(grid: Grid) -> dict[Coord, float]:
    """Copy ``grid`` into a dict after checking shape and score validity."""
    if not grid:
        raise ValueError("parameter grid is empty")
    dims = set()
    out: dict[Coord, float] = {}
    for coord, value in grid.items():
        try:
            key = tuple(coord)
        except TypeError as e:
            raise ValueError(f"grid coordinates must be sequences, got {coord!r}") from e
        if not key:
            raise ValueError("grid coordinates must not be empty")
        dims.add(len(key))
        for v in key:
            if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
                raise ValueError(f"grid coordinate {key} must hold finite numbers, got {v!r}")
        score = float(value)
        if not math.isfinite(score):
            raise ValueError(f"score at {list(key)} must be finite, got {value!r}")
        out[key] = score
    if len(dims) != 1:
        raise ValueError(f"grid coordinates disagree on dimension: {sorted(dims)}")
    return out


def _steps(grid: dict[Coord, float]) -> list[tuple[Coord, Coord, float]]:
    """Every adjacent step ``(a, b, flatness)`` where ``b`` is ``a`` plus one step on one axis.

    Adjacency is one step along one axis to the *next present value* of that
    axis (values are sorted per axis, so float axes and non-uniform lattices
    work); a missing neighbour (a hole) is not a step. Raises ``ValueError``
    when the grid holds no adjacent pair — a lone point proves nothing.
    """
    axes = [sorted({c[i] for c in grid}) for i in range(len(next(iter(grid))))]
    nxt = [{value: axis[i + 1] for i, value in enumerate(axis[:-1])} for axis in axes]
    steps: list[tuple[Coord, Coord, float]] = []
    for coord, score in grid.items():
        for i, value in enumerate(coord):
            following = nxt[i].get(value)
            if following is None:
                continue
            other = coord[:i] + (following,) + coord[i + 1 :]
            other_score = grid.get(other)
            if other_score is not None:
                steps.append((coord, other, step_flatness(score, other_score)))
    if not steps:
        raise ValueError(
            "parameter grid has no adjacent parameter pair to measure: give at least one "
            "axis two values that both appear in the grid"
        )
    return steps


def _fresh_strategy(strategy: BacktestStrategy) -> BacktestStrategy:
    """An instance is deep-copied so one grid point's state never reaches the next."""
    from copy import deepcopy

    return deepcopy(strategy)
