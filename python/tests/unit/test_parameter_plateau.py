"""Parameter-surface plateau validation (Balch pitfall #9: trusting complex models).

A parameter surface that is not a plateau — one step from Sharpe 1.8 to 0.3 —
is a strategy that memorized history, so Honba gates the *flatness* of scores
across adjacent parameter values. The flatness of one step is scale-free and
symmetric (``1 - |a-b| / max(|a|, |b|)``); the plateau score is the least flat
adjacent step, so a single cliff anywhere rejects the surface. The driver that
runs a real grid through ``Honba.backtest`` is
``tests/integration/test_backtest_parameter_plateau.py``.
"""

from __future__ import annotations

import pytest

from honba.algo_analytics import (
    PlateauGates,
    PlateauResult,
    heatmap,
    plateau_score,
    step_flatness,
    summarise_plateau,
)

# The doc's example: (20,50)->(21,50) plunges 1.8 -> 0.3.
CLIFF = {
    (20, 50): 1.8,
    (21, 50): 0.3,
    (20, 51): 1.7,
    (21, 51): 0.4,
}
SMOOTH = {
    (20, 50): 1.8,
    (21, 50): 1.7,
    (20, 51): 1.6,
    (21, 51): 1.5,
}


def test_identical_scores_are_a_perfect_step() -> None:
    assert step_flatness(1.8, 1.8) == 1.0
    assert step_flatness(0.0, 0.0) == 1.0  # two zeros: nothing moved


def test_the_docs_cliff_step_is_far_below_the_gate() -> None:
    # 1.8 -> 0.3 is a drop of 1.5 out of a peak of 1.8: mostly gone.
    assert step_flatness(1.8, 0.3) == pytest.approx(1.0 - 1.5 / 1.8)
    assert step_flatness(1.8, 0.3) < 0.4


def test_flatness_is_scale_free_and_sign_aware() -> None:
    assert step_flatness(1.8, 1.7) == pytest.approx(step_flatness(0.18, 0.17))
    assert step_flatness(1.0, -1.0) == 0.0  # a sign flip is not a plateau
    assert step_flatness(0.2, 0.4) == pytest.approx(0.5)  # small absolute, big relative move


SMOOTH_STEPS = [(1.8, 1.7), (1.8, 1.6), (1.7, 1.5), (1.6, 1.5)]  # the grid's adjacent pairs
CLIFF_STEPS = [(1.8, 0.3), (1.8, 1.7), (0.3, 0.4), (1.7, 0.4)]


def test_the_plateau_score_is_the_least_flat_adjacent_step() -> None:
    assert plateau_score(SMOOTH) == pytest.approx(min(step_flatness(a, b) for a, b in SMOOTH_STEPS))
    assert plateau_score(CLIFF) == pytest.approx(min(step_flatness(a, b) for a, b in CLIFF_STEPS))
    assert plateau_score(SMOOTH) > 0.4
    assert plateau_score(CLIFF) < 0.4


def test_a_smooth_surface_passes_the_default_gate() -> None:
    verdict = summarise_plateau(SMOOTH)
    assert isinstance(verdict, PlateauResult)
    assert verdict.points == 4 and verdict.steps == 4
    assert verdict.flatness == pytest.approx(min(step_flatness(a, b) for a, b in SMOOTH_STEPS))
    assert verdict.mean_flatness >= verdict.flatness
    assert verdict.checks == {"flatness": True}
    assert verdict.passed
    assert verdict.summary().endswith("RESULT: PASSED")


def test_a_single_cliff_rejects_the_surface_and_names_the_step() -> None:
    verdict = summarise_plateau(CLIFF)
    assert verdict.checks == {"flatness": False}
    assert not verdict.passed
    assert verdict.worst_step[0] == (20, 50) and verdict.worst_step[1] == (21, 50)
    assert verdict.mean_flatness > 0.4  # the mean looks fine; the least flat step does not
    summary = verdict.summary()
    assert "[20, 50]" in summary and "[21, 50]" in summary
    assert summary.endswith("RESULT: FAILED (1 of 1 checks failed)")


def test_the_gate_threshold_is_configurable_and_inclusive() -> None:
    # A step of 1.0 -> 0.4 scores exactly 0.4: the doc's ">= 0.4" is inclusive.
    grid = {(0,): 1.0, (1,): 0.4}
    assert plateau_score(grid) == pytest.approx(0.4)
    assert summarise_plateau(grid).passed
    assert not summarise_plateau(grid, PlateauGates(min_flatness_score=0.5)).passed


def test_gates_are_validated() -> None:
    with pytest.raises(ValueError, match="min_flatness_score"):
        PlateauGates(min_flatness_score=-0.1)
    with pytest.raises(ValueError, match="min_flatness_score"):
        PlateauGates(min_flatness_score=float("nan"))


def test_a_grid_without_an_adjacent_pair_cannot_be_judged() -> None:
    with pytest.raises(ValueError, match="adjacent"):
        plateau_score({(0,): 1.0})
    with pytest.raises(ValueError, match="adjacent"):
        summarise_plateau({(0, 0): 1.0, (1, 1): 1.0})  # diagonal only: no shared axis step


def test_malformed_grids_are_refused() -> None:
    with pytest.raises(ValueError, match="empty"):
        plateau_score({})
    with pytest.raises(ValueError, match="dimension"):
        plateau_score({(0,): 1.0, (0, 1): 2.0})
    with pytest.raises(ValueError, match="finite"):
        plateau_score({(0,): 1.0, (1,): float("nan")})


def test_heatmap_orders_the_axes_and_marks_holes() -> None:
    grid = {
        (21, 51): 0.4,
        (20, 50): 1.8,
        (21, 50): 0.3,
        # (20, 51) missing: a hole, not a zero
    }
    rows = heatmap(grid)
    assert rows == [[1.8, None], [0.3, 0.4]]  # rows: axis-0 asc, cols: axis-1 asc


def test_heatmap_needs_a_two_dimensional_grid() -> None:
    with pytest.raises(ValueError, match="two-dimensional"):
        heatmap({(0,): 1.0, (1,): 2.0})
    with pytest.raises(ValueError, match="two-dimensional"):
        heatmap({(0, 0, 0): 1.0, (1, 1, 1): 2.0})


def test_results_carry_the_scores_for_machine_consumption() -> None:
    verdict = summarise_plateau(CLIFF)
    assert dict(verdict.scores) == CLIFF  # the grid is kept so an agent can redraw the heatmap
