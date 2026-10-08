"""Calendar fold geometry for out-of-sample validation.

Balch pitfall #1 (in-sample backtesting) is first a question of dates: which
part of history is used to calibrate a strategy and which part is held back to
judge it. ``plan_folds`` cuts that geometry deterministically, so a walk-forward
run is fully described by its inputs and never by the calendar of the day it
happens to run on.
"""

from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import date
from typing import Literal

__all__ = ["FoldWindow", "Window", "add_months", "plan_folds"]

Window = Literal["expanding", "rolling"]
"""``expanding`` keeps the training window anchored at ``start``; ``rolling`` slides it."""


def add_months(value: date, months: int) -> date:
    """Shift ``value`` by whole calendar months.

    The day clamps to the last day of the target month (2024-01-31 + 1 month is
    2024-02-29), which keeps month-anniversary fold cuts stable across leap years.
    """
    month_index = value.year * 12 + (value.month - 1) + months
    year, zero_based_month = divmod(month_index, 12)
    month = zero_based_month + 1
    day = min(value.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


@dataclass(frozen=True, slots=True)
class FoldWindow:
    """One train/test cut: train on ``[train_start, train_end)``, test on ``[test_start, test_end)``.

    ``train_end`` always equals ``test_start``, so the out-of-sample window begins
    exactly where the calibration data stops — no gap, no overlap.
    """

    train_start: date
    train_end: date
    test_start: date
    test_end: date


def plan_folds(
    start: date,
    end: date,
    *,
    train_months: int,
    test_months: int,
    n_folds: int,
    window: Window = "expanding",
) -> list[FoldWindow]:
    """Cut ``[start, end)`` into ``n_folds`` contiguous train/test windows.

    ``expanding`` (default, as documented): every fold trains from ``start`` up to
    its own ``train_end``, so the calibration set only grows — fold *i* trains on
    ``train_months + i * test_months`` months. ``rolling`` slides the training
    window forward by ``test_months`` per fold instead.

    The test windows are contiguous and never overlap: fold *i*'s test window ends
    where fold *i+1*'s begins. Data after the last test window is left unused.

    Raises:
        ValueError: on non-positive windows, fewer than 2 folds, an unknown
            ``window``, or a range that ends before the last test window.
    """
    if train_months < 1:
        raise ValueError(f"train_months must be >= 1, got {train_months}")
    if test_months < 1:
        raise ValueError(f"test_months must be >= 1, got {test_months}")
    if n_folds < 2:
        raise ValueError(f"n_folds must be >= 2 so the fold spread is measurable, got {n_folds}")
    if window not in ("expanding", "rolling"):
        raise ValueError(f"window must be 'expanding' or 'rolling', got {window!r}")
    if start >= end:
        raise ValueError(f"start {start} must be before end {end}")

    folds: list[FoldWindow] = []
    for i in range(n_folds):
        if window == "expanding":
            train_start = start
            train_end = add_months(start, train_months + i * test_months)
        else:
            train_start = add_months(start, i * test_months)
            train_end = add_months(train_start, train_months)
        test_start = train_end
        test_end = add_months(test_start, test_months)
        folds.append(
            FoldWindow(
                train_start=train_start,
                train_end=train_end,
                test_start=test_start,
                test_end=test_end,
            )
        )

    last_end = folds[-1].test_end
    if last_end > end:
        raise ValueError(
            f"data range [{start}, {end}) does not cover the planned folds: the last test "
            f"window ends {last_end} (need at least train {train_months}m + "
            f"{n_folds} x test {test_months}m from {start})"
        )
    return folds
