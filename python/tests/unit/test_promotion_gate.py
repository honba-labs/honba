"""The Backtest -> Paper -> Live promotion gate (Balch pitfall #10).

A strategy never jumps from a backtest to live capital: paper incubation is
the mandatory middle stage, and the paper -> live hop is refused unless the
incubation report passed. The stages are the existing ``RunMode`` values (the
ubiquitous language shared with the adapter contract), the report is
``tests/unit/test_incubation_report.py``, and a real session's report driving
this gate is ``tests/integration/test_paper_incubation.py``.
"""

from __future__ import annotations

import pytest

from honba.adapters.models import RunMode
from honba.domain.money import Currency, Money
from honba.entities.instrument import InstrumentId
from honba.entities.order import OrderIntent, OrderSide
from honba.entities.trade import Trade
from honba.incubation import (
    IncubationGates,
    PromotionRefused,
    incubation_report,
    require_promotion,
)
from honba.strategies.execution import Accepted, Fill, Submitted

A = InstrumentId("AAA", "NSE")
INR = Currency.INR
T0 = 1_704_153_600 * 10**9
DAY = 86_400 * 10**9


def _intent() -> OrderIntent:
    return OrderIntent.market_buy(A, 10.0)


def report(passed: bool):
    """A minimal report whose verdict is the input under test."""
    if passed:
        fill = Fill(
            Trade(A, OrderSide.BUY, 10.0, 100.0, T0 + DAY, "o1", costs=Money.zero(INR)),
            10.0,
            True,
        )
        events = [
            Submitted("o1", _intent(), T0),
            Accepted("o1", _intent(), T0 + 10**6, venue_order_id="v-o1"),
            fill,
        ]
        final = Money.from_major(100_000.0, INR) - Money.mul_qty(10.0, 100.0, INR)
    else:
        events = [Submitted("o1", _intent(), T0)]  # no acks: fails the latency gate
        final = Money.from_major(100_000.0, INR)
    return incubation_report(
        events,
        initial_cash=Money.from_major(100_000.0, INR),
        final_cash=final,
        gates=IncubationGates(min_days=0.0),  # only the gate under test matters here
    )


def test_the_forward_path_backtest_to_paper_is_open() -> None:
    require_promotion(RunMode.BACKTEST, RunMode.PAPER)  # no incubation needed yet


def test_the_stages_may_be_revisited_in_any_backwards_order() -> None:
    require_promotion(RunMode.LIVE, RunMode.PAPER)  # demote to paper: always allowed
    require_promotion(RunMode.PAPER, RunMode.BACKTEST)
    require_promotion(RunMode.PAPER, RunMode.PAPER)  # staying put is not a promotion


def test_paper_to_live_requires_a_passed_incubation() -> None:
    require_promotion(RunMode.PAPER, RunMode.LIVE, incubation=report(passed=True))

    with pytest.raises(PromotionRefused, match="incubation"):
        require_promotion(RunMode.PAPER, RunMode.LIVE)  # no evidence at all
    with pytest.raises(PromotionRefused, match="failed"):
        require_promotion(RunMode.PAPER, RunMode.LIVE, incubation=report(passed=False))


def test_backtest_to_live_can_never_skip_the_paper_stage() -> None:
    # Even a passed report does not license skipping the mode hop: the strategy
    # must be labelled and run as PAPER before it becomes LIVE.
    with pytest.raises(PromotionRefused, match="PAPER"):
        require_promotion(RunMode.BACKTEST, RunMode.LIVE, incubation=report(passed=True))
    with pytest.raises(PromotionRefused, match="PAPER"):
        require_promotion(RunMode.BACKTEST, RunMode.LIVE)


def test_the_refusal_carries_the_failed_gate_names() -> None:
    failed = report(passed=False)
    with pytest.raises(PromotionRefused) as exc:
        require_promotion(RunMode.PAPER, RunMode.LIVE, incubation=failed)
    assert "ack_latency" in str(exc.value)


def test_non_run_mode_inputs_are_refused() -> None:
    with pytest.raises(TypeError, match="RunMode"):
        require_promotion("paper", RunMode.LIVE)  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="RunMode"):
        require_promotion(RunMode.PAPER, "live")  # type: ignore[arg-type]
