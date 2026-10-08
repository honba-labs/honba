"""Paper-incubation verification (Balch pitfall #10: failing to forward test).

Before live capital, a strategy must spend a mandatory incubation period in
paper mode whose execution record proves three things: orders actually fill,
the venue acknowledges them promptly, and the books balance against the event
record (margin accounting). The gate is pure: it reads one ordered
``ExecutionPort`` event stream (ADR 0019) plus the cash either side. The same
gate over a real runner-driven session is
``tests/integration/test_paper_incubation.py``; the Backtest -> Paper -> Live
promotion state machine is ``tests/unit/test_promotion_gate.py``.
"""

from __future__ import annotations

import pytest

from honba.domain.money import Currency, Money
from honba.entities.instrument import InstrumentId
from honba.entities.order import OrderIntent, OrderSide
from honba.entities.trade import Trade
from honba.incubation import IncubationGates, IncubationResult, incubation_report
from honba.strategies.execution import Accepted, Fill, Rejected, Submitted

A = InstrumentId("AAA", "NSE")
INR = Currency.INR
NS = 10**9
MINUTE = 60 * NS
DAY = 86_400 * NS
T0 = 1_704_153_600 * NS  # 2024-01-02, epoch nanoseconds
QTY = 10.0


def money(x: float) -> Money:
    return Money.from_major(x, INR)


def intent() -> OrderIntent:
    return OrderIntent.market_buy(A, QTY)


def submitted(oid: str, ts: int):
    return Submitted(oid, intent(), ts)


def accepted(oid: str, ts: int):
    return Accepted(oid, intent(), ts, venue_order_id=f"v-{oid}")


def fill(oid: str, ts: int, price: float, costs: float = 0.0) -> Fill:
    trade = Trade(A, OrderSide.BUY, QTY, price, ts, oid, costs=money(costs))
    return Fill(trade, QTY, True)


def healthy_stream(ack_ms: int = 50) -> list:
    """Two orders over two days: fast acks, both filled, no rejects."""
    events = []
    for i, (oid, day, price) in enumerate((("o1", 0, 100.0), ("o2", 2, 101.0))):
        t = T0 + day * DAY
        events += [
            submitted(oid, t),
            accepted(oid, t + ack_ms * 10**6),
            fill(oid, t + ack_ms * 10**6 + MINUTE, price, costs=1.0),
        ]
    return events


def cash_after(events, initial: float) -> Money:
    """What the books must hold after ``events`` (buys only: cash leaves)."""
    total = money(initial)
    for ev in events:
        if isinstance(ev, Fill):
            notional = Money.mul_qty(ev.trade.quantity, ev.trade.price, INR)
            total = total - (notional + ev.trade.costs)
    return total


def test_a_healthy_paper_stream_passes_every_gate() -> None:
    events = healthy_stream()
    initial = money(100_000.0)
    report = incubation_report(
        events, initial_cash=initial, final_cash=cash_after(events, 100_000.0)
    )

    assert isinstance(report, IncubationResult)
    assert report.orders == 2 and report.filled == 2
    assert report.fill_rate == 1.0
    assert report.median_ack_ms == pytest.approx(50.0)
    assert report.span_days == pytest.approx(2.0, abs=0.001)
    assert report.accounting_drift == 0.0
    assert report.checks == {
        "period": True,
        "fill_rate": True,
        "ack_latency": True,
        "accounting": True,
    }
    assert report.passed
    assert report.summary().endswith("RESULT: PASSED")


def test_rejected_orders_count_against_the_fill_rate() -> None:
    events = healthy_stream() + [
        Rejected("o3", intent(), "insufficient_funds", T0 + 3 * DAY),
    ]
    report = incubation_report(
        events, initial_cash=money(100_000.0), final_cash=cash_after(events, 100_000.0)
    )
    assert report.orders == 3 and report.filled == 2
    assert report.fill_rate == pytest.approx(2.0 / 3.0)
    assert report.checks["fill_rate"] is False
    assert not report.passed


def test_a_venue_that_never_acknowledges_cannot_prove_latency() -> None:
    # Backtest simulators emit fills but no venue acks: that is fine for a backtest
    # and disqualifying for a paper incubation, which must show a live-ish ack path.
    events = [ev for ev in healthy_stream() if not isinstance(ev, Accepted)]
    report = incubation_report(
        events, initial_cash=money(100_000.0), final_cash=cash_after(events, 100_000.0)
    )
    assert report.median_ack_ms is None
    assert report.checks["ack_latency"] is False
    assert not report.passed


def test_a_slow_median_ack_fails_the_latency_gate() -> None:
    events = healthy_stream(ack_ms=2_000)  # median 2000 ms > the 1000 ms default
    report = incubation_report(
        events, initial_cash=money(100_000.0), final_cash=cash_after(events, 100_000.0)
    )
    assert report.median_ack_ms == pytest.approx(2_000.0)
    assert report.checks["ack_latency"] is False


def test_books_that_do_not_balance_fail_the_accounting_gate() -> None:
    events = healthy_stream()
    honest = cash_after(events, 100_000.0)
    report = incubation_report(events, initial_cash=money(100_000.0), final_cash=honest)
    assert report.accounting_drift == 0.0 and report.checks["accounting"] is True

    # One rupee of margin vanish: drift 1.0 > the 0.0 default.
    sloppy = incubation_report(
        events, initial_cash=money(100_000.0), final_cash=honest + money(1.0)
    )
    assert sloppy.accounting_drift == pytest.approx(1.0)
    assert sloppy.checks["accounting"] is False


def test_costs_are_part_of_the_accounting_recomputation() -> None:
    events = healthy_stream()
    # Books that deducted the notionals but forgot the 1.0 of costs on each fill.
    ignoring_costs = (
        money(100_000.0) - Money.mul_qty(QTY, 100.0, INR) - Money.mul_qty(QTY, 101.0, INR)
    )
    report = incubation_report(events, initial_cash=money(100_000.0), final_cash=ignoring_costs)
    assert report.accounting_drift == pytest.approx(2.0)  # 1.0 of costs per fill
    assert report.checks["accounting"] is False


def test_an_incubation_shorter_than_the_required_period_fails() -> None:
    events = [submitted("o1", T0), accepted("o1", T0 + 10**6), fill("o1", T0 + MINUTE, 100.0)]
    report = incubation_report(
        events,
        initial_cash=money(100_000.0),
        final_cash=cash_after(events, 100_000.0),
        gates=IncubationGates(min_days=1.0),
    )
    assert report.span_days < 1.0
    assert report.checks["period"] is False


def test_an_empty_record_fails_closed_rather_than_crashing() -> None:
    report = incubation_report([], initial_cash=money(100_000.0), final_cash=money(100_000.0))
    assert report.orders == 0
    assert report.fill_rate == 0.0
    assert report.median_ack_ms is None
    assert report.checks["period"] is False
    assert report.checks["fill_rate"] is False
    assert report.checks["ack_latency"] is False
    assert report.checks["accounting"] is True  # nothing happened, nothing is missing
    assert not report.passed
    assert report.summary().endswith("RESULT: FAILED (3 of 4 checks failed)")


def test_gates_are_validated() -> None:
    with pytest.raises(ValueError, match="min_fill_rate"):
        IncubationGates(min_fill_rate=1.5)
    with pytest.raises(ValueError, match="min_days"):
        IncubationGates(min_days=-1.0)
    with pytest.raises(ValueError, match="max_median_ack_ms"):
        IncubationGates(max_median_ack_ms=float("inf"))


def test_an_ack_before_its_submission_is_refused() -> None:
    events = [
        Submitted("o1", intent(), T0 + MINUTE),
        Accepted("o1", intent(), T0, venue_order_id="v-o1"),
    ]
    with pytest.raises(ValueError, match="non-monotonic"):
        incubation_report(events, initial_cash=money(1.0), final_cash=money(1.0))
