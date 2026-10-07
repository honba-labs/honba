"""``NextOpenExecution`` speaks the one ordered event stream (ADR 0019 decision 4).

Both backends: ``drain_events()`` returns a fill before the rejection of its remainder,
``cancel(order_id, now)`` stamps the caller's ``now``, and the legacy drains are the
buffered split of the same stream.
"""

from __future__ import annotations

import pytest

from honba.backtest.simulated import NextOpenExecution, resolve_backend
from honba.domain.money import Currency, Money
from honba.entities.bar import Bar
from honba.entities.instrument import InstrumentId
from honba.entities.order import OrderIntent
from honba.strategies.execution import Cancelled, Fill, Rejected

A = InstrumentId("AAA", "NSE")
BACKENDS = ["python"] + (["native"] if resolve_backend("auto") == "native" else [])


def bar(ts: int, open_: float) -> Bar:
    return Bar(A, ts, open_, open_, open_, open_, 1_000.0)


def sim(backend: str, cash: float = 1_000.0) -> NextOpenExecution:
    return NextOpenExecution(cash=Money.from_major(cash, Currency.INR), backend=backend)  # type: ignore[arg-type]


@pytest.mark.parametrize("backend", BACKENDS)
def test_a_partial_fill_precedes_the_rejection_of_its_remainder(backend: str) -> None:
    p = sim(backend)
    p.open_session(1, [bar(1, 100.0)])
    p.submit("o-0", OrderIntent.market_buy(A, 20), 1)
    p.open_session(2, [bar(2, 100.0)])
    fill, rejected = p.drain_events()
    assert isinstance(fill, Fill) and (fill.trade.quantity, fill.cum_qty) == (10.0, 10.0)
    assert fill.complete is False
    assert isinstance(rejected, Rejected)
    assert (rejected.reason, rejected.intent.quantity, rejected.ts) == ("insufficient_funds", 10, 2)
    assert p.drain_events() == []


@pytest.mark.parametrize("backend", BACKENDS)
def test_cancel_stamps_the_caller_now(backend: str) -> None:
    p = sim(backend)
    p.open_session(1, [bar(1, 100.0)])
    p.submit("o-0", OrderIntent.market_buy(A, 2), 1)
    p.cancel("o-0", 777)
    (ev,) = p.drain_events()
    assert isinstance(ev, Cancelled)
    assert (ev.order_id, ev.ts, ev.intent.quantity) == ("o-0", 777, 2)
    p.cancel("o-0", 800)  # finished: a no-op
    p.cancel("nope", 800)
    assert p.drain_events() == []


@pytest.mark.parametrize("backend", BACKENDS)
def test_the_event_keeps_the_original_intent(backend: str) -> None:
    p = sim(backend)
    p.open_session(1, [bar(1, 100.0)])
    limit = OrderIntent.limit_buy(A, 3, 90.0)
    p.submit("o-0", limit, 1)
    (ev,) = p.drain_events()
    assert isinstance(ev, Rejected) and ev.reason == "unsupported_order_type"
    assert ev.intent == limit


@pytest.mark.parametrize("backend", BACKENDS)
def test_legacy_drains_are_the_buffered_split_of_the_stream(backend: str) -> None:
    p = sim(backend)
    p.open_session(1, [bar(1, 100.0)])
    p.submit("o-0", OrderIntent.market_buy(A, 20), 1)
    p.submit("o-1", OrderIntent.market_buy(A, 1), 1)
    p.cancel("o-1", 5)
    p.open_session(2, [bar(2, 100.0)])
    rejections = p.drain_rejections()  # called first: the fills are buffered, not lost
    assert [(r.order_id, r.cancelled) for r in rejections] == [("o-1", True), ("o-0", False)]
    assert [(f.order_id, f.quantity) for f in p.drain_fills()] == [("o-0", 10.0)]
    assert p.drain_fills() == [] and p.drain_rejections() == []
