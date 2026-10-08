"""Staggered start-date Monte Carlo (Balch pitfall #7: stateful strategy luck).

A path-dependent strategy (trailing stops, regime flags, rebalancing calendars)
can look brilliant purely because of the chosen start date. The defense reruns
the same strategy over staggered start offsets and requires the dispersion of
final equity and drawdown to stay small.
"""

from __future__ import annotations

from datetime import date

import pytest

from honba.algo_analytics.monte_carlo import (
    StartDateConfig,
    offsets_to_ranges,
    summarise_start_dates,
)


def _gates(**overrides) -> StartDateConfig:
    base = {
        "symbol": "XYZ",
        "start": date(2020, 1, 1),
        "end": date(2021, 1, 1),
        "offsets": (0, 5, 10, 20),
        "max_final_equity_cv": 0.25,
        "max_drawdown_spread_pct": 10.0,
    }
    base.update(overrides)
    return StartDateConfig(**base)


def test_start_date_config_rejects_bad_windows_and_offsets() -> None:
    with pytest.raises(ValueError, match="start"):
        _gates(start=date(2021, 1, 1), end=date(2021, 1, 1))
    with pytest.raises(ValueError, match="start"):
        _gates(start=date(2021, 1, 2), end=date(2021, 1, 1))
    with pytest.raises(ValueError, match="offsets"):
        _gates(offsets=())
    with pytest.raises(ValueError, match="non-negative"):
        _gates(offsets=(-1, 5))
    with pytest.raises(ValueError, match="symbol"):
        _gates(symbol="")


def test_offsets_shift_the_whole_window_forward() -> None:
    ranges = offsets_to_ranges(start=date(2020, 1, 1), end=date(2020, 2, 1), offsets=(0, 7, 14))
    assert ranges == [
        (date(2020, 1, 1), date(2020, 2, 1)),
        (date(2020, 1, 8), date(2020, 2, 8)),
        (date(2020, 1, 15), date(2020, 2, 15)),
    ]


def test_identical_outcomes_have_zero_dispersion_and_pass() -> None:
    result = summarise_start_dates(
        final_equities=(100_000.0, 100_000.0, 100_000.0, 100_000.0),
        max_drawdown_pcts=(5.0, 5.0, 5.0, 5.0),
        gates=_gates(),
    )
    assert result.mean_final_equity == pytest.approx(100_000.0)
    assert result.cv_final_equity == pytest.approx(0.0)
    assert result.drawdown_spread == pytest.approx(0.0)
    assert result.checks == {"final_equity_cv": True, "drawdown_spread": True}
    assert result.passed is True
    assert "RESULT: PASSED" in result.summary()


def test_state_dependent_outcomes_fail_both_gates() -> None:
    # Same mean as perfect above, but start dates halve/double the outcome.
    result = summarise_start_dates(
        final_equities=(50_000.0, 100_000.0, 100_000.0, 150_000.0),
        max_drawdown_pcts=(2.0, 5.0, 5.0, 30.0),
        gates=_gates(),
    )
    assert result.cv_final_equity == pytest.approx(0.354, abs=0.01)
    assert result.drawdown_spread == pytest.approx(28.0)
    assert result.checks == {"final_equity_cv": False, "drawdown_spread": False}
    assert result.passed is False
    assert "RESULT: FAILED (2 of 2 checks failed)" in result.summary()


def test_gates_accept_exactly_at_threshold() -> None:
    # offsets (0,10): CV and spread sit exactly on the thresholds -> pass (>=).
    result = summarise_start_dates(
        final_equities=(100.0, 150.0),
        max_drawdown_pcts=(5.0, 15.0),
        gates=_gates(
            offsets=(0, 10),
            max_final_equity_cv=0.2,
            max_drawdown_spread_pct=10.0,
        ),
    )
    assert result.cv_final_equity == pytest.approx(0.2)
    assert result.drawdown_spread == pytest.approx(10.0)
    assert result.passed is True


def test_misaligned_outcomes_are_refused() -> None:
    with pytest.raises(ValueError, match="align"):
        summarise_start_dates(
            final_equities=(100_000.0, 100_000.0),
            max_drawdown_pcts=(5.0,),
            gates=_gates(offsets=(0, 5)),
        )
