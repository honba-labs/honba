import datetime as dt
import pytest
from honba.screener.coverage import (
    DateInterval,
    CoverageStatus,
    CoverageRecord,
    subtract_intervals,
    merge_intervals,
    merge_close_intervals,
    plan_gaps,
)


def test_date_interval_basics():
    iv = DateInterval(dt.date(2025, 1, 1), dt.date(2025, 1, 5))
    assert iv.start == dt.date(2025, 1, 1)
    assert iv.end == dt.date(2025, 1, 5)
    assert not iv.is_empty()
    assert dt.date(2025, 1, 1) in iv
    assert dt.date(2025, 1, 4) in iv
    assert dt.date(2025, 1, 5) not in iv  # half-open [start, end)

    empty = DateInterval(dt.date(2025, 1, 5), dt.date(2025, 1, 5))
    assert empty.is_empty()


def test_merge_intervals():
    iv1 = DateInterval(dt.date(2025, 1, 1), dt.date(2025, 1, 10))
    iv2 = DateInterval(dt.date(2025, 1, 10), dt.date(2025, 1, 20))
    iv3 = DateInterval(dt.date(2025, 2, 1), dt.date(2025, 2, 10))

    merged = merge_intervals([iv1, iv2, iv3])
    assert len(merged) == 2
    assert merged[0] == DateInterval(dt.date(2025, 1, 1), dt.date(2025, 1, 20))
    assert merged[1] == DateInterval(dt.date(2025, 2, 1), dt.date(2025, 2, 10))


def test_subtract_intervals():
    # Required: [2021-06-01, 2025-07-01)
    # Covered:  [2022-01-01, 2025-01-01)
    # Result: [2021-06-01, 2022-01-01) and [2025-01-01, 2025-07-01) (as in Design.md Section 12.3)
    req = DateInterval(dt.date(2021, 6, 1), dt.date(2025, 7, 1))
    cov = [DateInterval(dt.date(2022, 1, 1), dt.date(2025, 1, 1))]

    gaps = subtract_intervals(req, cov)
    assert len(gaps) == 2
    assert gaps[0] == DateInterval(dt.date(2021, 6, 1), dt.date(2022, 1, 1))
    assert gaps[1] == DateInterval(dt.date(2025, 1, 1), dt.date(2025, 7, 1))


def test_subtract_intervals_fully_covered():
    req = DateInterval(dt.date(2022, 6, 1), dt.date(2023, 1, 1))
    cov = [DateInterval(dt.date(2022, 1, 1), dt.date(2025, 1, 1))]
    gaps = subtract_intervals(req, cov)
    assert gaps == []


def test_subtract_intervals_interior_holes():
    req = DateInterval(dt.date(2025, 1, 1), dt.date(2025, 1, 20))
    cov = [
        DateInterval(dt.date(2025, 1, 2), dt.date(2025, 1, 5)),
        DateInterval(dt.date(2025, 1, 10), dt.date(2025, 1, 15)),
    ]
    gaps = subtract_intervals(req, cov)
    assert len(gaps) == 3
    assert gaps[0] == DateInterval(dt.date(2025, 1, 1), dt.date(2025, 1, 2))
    assert gaps[1] == DateInterval(dt.date(2025, 1, 5), dt.date(2025, 1, 10))
    assert gaps[2] == DateInterval(dt.date(2025, 1, 15), dt.date(2025, 1, 20))


def test_merge_close_intervals():
    # Gap 1: [2025-01-01, 2025-01-05), Gap 2: [2025-01-07, 2025-01-10)
    # Difference is 2 days. If max_gap_days >= 2, they should merge.
    g1 = DateInterval(dt.date(2025, 1, 1), dt.date(2025, 1, 5))
    g2 = DateInterval(dt.date(2025, 1, 7), dt.date(2025, 1, 10))

    merged = merge_close_intervals([g1, g2], max_gap_days=2)
    assert len(merged) == 1
    assert merged[0] == DateInterval(dt.date(2025, 1, 1), dt.date(2025, 1, 10))

    unmerged = merge_close_intervals([g1, g2], max_gap_days=1)
    assert len(unmerged) == 2


def test_plan_gaps():
    from honba.india.calendar import NseCalendar
    cal = NseCalendar()

    # Suppose instrument requires 5 trading sessions ending on 2025-01-10
    # Covered range is [2025-01-08, 2025-01-11)
    # The required interval is [2025-01-06, 2025-01-11)
    req_interval = DateInterval(dt.date(2025, 1, 6), dt.date(2025, 1, 11))
    covered = [DateInterval(dt.date(2025, 1, 8), dt.date(2025, 1, 11))]

    gaps = plan_gaps(req_interval, covered)
    assert len(gaps) == 1
    assert gaps[0] == DateInterval(dt.date(2025, 1, 6), dt.date(2025, 1, 8))
