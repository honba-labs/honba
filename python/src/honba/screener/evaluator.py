"""Bar series metric extraction and predicate evaluation (Design.md Section 7 & 14.3)."""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Any

from honba.entities.bar import Bar
from honba.entities.screener import (
    FilterOp,
    MetricRef,
    ScreenerFilterGroup,
    ScreenerFilterPredicate,
)
from honba.strategies.indicators.momentum import Rsi
from honba.strategies.indicators.moving_average import Sma


def _compute_series(metric_key: str, bars: Sequence[Bar]) -> list[float | None]:
    """Compute series of metric values for each bar in chronological order."""
    if not bars:
        return []

    key_lower = metric_key.lower()

    if key_lower == "close":
        return [b.close for b in bars]
    if key_lower == "open":
        return [b.open for b in bars]
    if key_lower == "high":
        return [b.high for b in bars]
    if key_lower == "low":
        return [b.low for b in bars]
    if key_lower == "volume":
        return [b.volume for b in bars]

    if key_lower == "price_52_week_low":
        res: list[float | None] = []
        for i in range(len(bars)):
            window = bars[max(0, i - 251) : i + 1]
            res.append(min(b.low for b in window))
        return res

    if key_lower == "price_52_week_high":
        res = []
        for i in range(len(bars)):
            window = bars[max(0, i - 251) : i + 1]
            res.append(max(b.high for b in window))
        return res

    sma_match = re.fullmatch(r"sma(\d+)", key_lower)
    if sma_match:
        period = int(sma_match.group(1))
        sma = Sma(period=period)
        res = []
        for b in bars:
            res.append(sma.update(b.close))
        return res

    if key_lower == "rsi":
        rsi = Rsi(period=14)
        res = []
        for b in bars:
            res.append(rsi.update(b.close))
        return res

    # Unknown or unsupported bar-derived metric
    return [None] * len(bars)


def extract_metrics_from_bars(metric_keys: Sequence[str], bars: Sequence[Bar]) -> dict[str, Any]:
    """Extract latest metric values from bar series."""
    values: dict[str, Any] = {}
    for key in metric_keys:
        series = _compute_series(key, bars)
        values[key] = series[-1] if series else None
    return values


def _compare(lhs: Any, op: FilterOp, rhs: Any) -> bool:
    if lhs is None or rhs is None:
        return False

    if op == FilterOp.EQ:
        return bool(lhs == rhs)
    if op == FilterOp.NEQ:
        return bool(lhs != rhs)
    if op == FilterOp.GT:
        return bool(lhs > rhs)
    if op == FilterOp.GTE:
        return bool(lhs >= rhs)
    if op == FilterOp.LT:
        return bool(lhs < rhs)
    if op == FilterOp.LTE:
        return bool(lhs <= rhs)
    if op == FilterOp.BETWEEN:
        if isinstance(rhs, (list, tuple)) and len(rhs) == 2:
            return bool(rhs[0] <= lhs <= rhs[1])
        return False
    if op == FilterOp.IN:
        if isinstance(rhs, (list, tuple, set)):
            return bool(lhs in rhs)
        return False
    if op == FilterOp.NOT_IN:
        if isinstance(rhs, (list, tuple, set)):
            return bool(lhs not in rhs)
        return False
    if op in (FilterOp.LIKE, FilterOp.HAS):
        return str(rhs).lower() in str(lhs).lower()

    return False


def evaluate_predicate_on_bars(
    pred: ScreenerFilterPredicate,
    bars: Sequence[Bar],
) -> bool:
    """Evaluate a single predicate against bar history."""
    if not bars:
        return False

    # Check 52-week lookback invariant: fewer than 252 bars is insufficient data
    if pred.key in ("price_52_week_low", "price_52_week_high") and len(bars) < 252:
        return False

    lhs_series = _compute_series(pred.key, bars)
    if not lhs_series or lhs_series[-1] is None:
        return False

    # Check if RHS is a MetricRef
    rhs_value = pred.value
    if isinstance(rhs_value, MetricRef):
        rhs_series = _compute_series(rhs_value.key, bars)
        if not rhs_series or rhs_series[-1] is None:
            return False
        rhs_curr = rhs_series[-1]
        rhs_prev = rhs_series[-2] if len(rhs_series) >= 2 else None
    else:
        rhs_curr = rhs_value
        rhs_prev = rhs_value

    lhs_curr = lhs_series[-1]
    lhs_prev = lhs_series[-2] if len(lhs_series) >= 2 else None

    if pred.op == FilterOp.CROSSES_ABOVE:
        if lhs_prev is None or rhs_prev is None:
            return False
        return bool(lhs_prev <= rhs_prev and lhs_curr > rhs_curr)

    if pred.op == FilterOp.CROSSES_BELOW:
        if lhs_prev is None or rhs_prev is None:
            return False
        return bool(lhs_prev >= rhs_prev and lhs_curr < rhs_curr)

    return _compare(lhs_curr, pred.op, rhs_curr)


def evaluate_group_on_bars(
    group: ScreenerFilterGroup,
    bars: Sequence[Bar],
) -> bool:
    """Evaluate a boolean group of predicates on bar history."""
    if not group.items:
        return True

    results: list[bool] = []
    for item in group.items:
        if isinstance(item, ScreenerFilterGroup):
            results.append(evaluate_group_on_bars(item, bars))
        elif isinstance(item, ScreenerFilterPredicate):
            results.append(evaluate_predicate_on_bars(item, bars))
        elif isinstance(item, dict):
            if "operator" in item:
                g = ScreenerFilterGroup.model_validate(item)
                results.append(evaluate_group_on_bars(g, bars))
            else:
                p = ScreenerFilterPredicate.model_validate(item)
                results.append(evaluate_predicate_on_bars(p, bars))

    if group.operator == "OR":
        return any(results)
    return all(results)
