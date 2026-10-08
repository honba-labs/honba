"""Paper incubation over a real session's event stream (Balch pitfall #10).

The gate itself (``tests/unit/test_incubation_report.py``) and the promotion
state machine (``tests/unit/test_promotion_gate.py``) are unit-tested; this
proves the whole story through the unified ``ExecutionPortLike`` contract: the
same strategy and the same ``Honba.backtest`` session run against a
venue-style port that emits submitter ``Submitted`` and venue ``Accepted``
events around the simulator's fills, and that recorded stream is exactly what
the incubation gate consumes before ``require_promotion`` allows paper ->
live. A venue that acks slowly, or a strategy whose orders get rejected, fails
the very same promotion.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Sequence

import pytest

from honba.adapters.models import RunMode
from honba.backtest.simulated import NextOpenExecution
from honba.domain.instrument import InstrumentKind
from honba.domain.money import Currency, Money
from honba.entities.bar import Bar
from honba.entities.instrument import Instrument, InstrumentId
from honba.entities.order import OrderIntent
from honba.incubation import PromotionRefused, incubation_report, require_promotion
from honba.session import Honba
from honba.strategies.base import Strategy
from honba.strategies.execution import Accepted, ExecutionEvent, Submitted

X = InstrumentId("XYZ", "NSE")
INR = Currency.INR
DAY_NS = 86_400 * 10**9
T0 = int(dt.datetime(2024, 1, 1, tzinfo=dt.timezone.utc).timestamp()) * 10**9
N_BARS = 12
QUANTITY = 10.0
FAST_ACK_NS = 50 * 10**6  # 50 ms venue acknowledgement
SLOW_ACK_NS = 5 * 10**9  # 5 s: an unacceptable venue


def money(x: float) -> Money:
    return Money.from_major(x, INR)


def _bars() -> list[Bar]:
    # Flat-ish prices with a small uptrend: every fill is affordable and deterministic.
    return [
        Bar(
            X,
            T0 + i * DAY_NS,
            100.0 + i,
            101.0 + i,
            99.0 + i,
            100.5 + i,
            1_000.0,
        )
        for i in range(N_BARS)
    ]


def _to_ns(value: dt.datetime) -> int:
    return int(value.replace(tzinfo=dt.timezone.utc).timestamp() * 10**9)


class Provider:
    def __init__(self, bars: list[Bar]) -> None:
        self._bars = bars

    def bars(self, instrument_id, *, timeframe, start, end) -> Sequence[Bar]:
        lo, hi = _to_ns(start), _to_ns(end)
        return [b for b in self._bars if b.instrument_id == instrument_id and lo <= b.ts < hi]

    def instrument(self, instrument_id) -> Instrument:
        return Instrument(instrument_id, InstrumentKind.EQUITY, 1.0, 0.05)


class VenuePort:
    """Venue-style execution: submitter ``Submitted`` + venue ``Accepted`` around a sim.

    A paper adapter owes the ordered stream ADR 0019 describes; this wrapper gives
    the backtest simulator those venue semantics so one session can be judged as a
    paper incubation. It is the shape ``E3-S7``'s sandbox adapter must grow into.
    """

    def __init__(self, inner: NextOpenExecution, *, ack_ns: int = FAST_ACK_NS) -> None:
        self.inner = inner
        self.ack_ns = ack_ns
        self._pending: list[ExecutionEvent] = []
        self.log: list[ExecutionEvent] = []  # everything the runner ever drained

    def submit(self, order_id: str, intent: OrderIntent, ts: int) -> None:
        self._pending.append(Submitted(order_id, intent, ts))
        self._pending.append(
            Accepted(order_id, intent, ts + self.ack_ns, venue_order_id=f"v-{order_id}")
        )
        self.inner.submit(order_id, intent, ts)

    def cancel(self, order_id: str, now: int) -> None:
        self.inner.cancel(order_id, now)

    def drain_events(self) -> list[ExecutionEvent]:
        out, self._pending = self._pending, []
        out.extend(self.inner.drain_events())
        self.log.extend(out)
        return out

    def on_event(self, event, ts_init: int) -> None:
        self.inner.on_event(event, ts_init)

    def __getattr__(self, name: str):
        return getattr(self.__dict__["inner"], name)


class RoundTrip(Strategy):
    """Two full round trips: every order it places fills (fill rate 1.0)."""

    name = "round_trip"

    def __init__(self) -> None:
        self.seen = 0

    def on_bar(self, bar: Bar) -> None:
        self.seen += 1
        if self.ctx.busy(X):
            return
        if self.seen in (1, 5):
            self.ctx.submit(OrderIntent.market_buy(X, QUANTITY))
        elif self.seen in (3, 7):
            self.ctx.submit(OrderIntent.market_sell(X, QUANTITY))


class Rejecting(Strategy):
    """Buys, sells flat, then tries to sell again: the last order gets rejected."""

    name = "rejecting"

    def __init__(self) -> None:
        self.seen = 0

    def on_bar(self, bar: Bar) -> None:
        self.seen += 1
        if self.ctx.busy(X):
            return
        if self.seen == 1:
            self.ctx.submit(OrderIntent.market_buy(X, QUANTITY))
        elif self.seen in (3, 5):
            self.ctx.submit(OrderIntent.market_sell(X, QUANTITY))


def _run(strategy, *, ack_ns: int = FAST_ACK_NS, cash: float = 100_000.0) -> VenuePort:
    venue = VenuePort(
        NextOpenExecution(cash=money(cash), settlement_days=0, backend="python"),
        ack_ns=ack_ns,
    )
    Honba.backtest(
        strategy,
        symbol="XYZ",
        start="2024-01-01",
        end="2024-02-01",
        data=Provider(_bars()),
        cash=cash,
        costs="none",
        execution=venue,
    ).run()
    return venue


def _report(venue: VenuePort, cash: float = 100_000.0):
    return incubation_report(venue.log, initial_cash=money(cash), final_cash=venue.inner.cash)


def test_one_session_yields_the_full_venue_event_stream() -> None:
    venue = _run(RoundTrip())

    kinds = [type(ev).__name__ for ev in venue.log]
    assert kinds.count("Submitted") == 4 and kinds.count("Accepted") == 4
    assert kinds.count("Fill") == 4
    assert "Rejected" not in kinds


def test_a_healthy_paper_session_passes_and_promotes_to_live() -> None:
    venue = _run(RoundTrip())
    report = _report(venue)

    assert report.orders == 4 and report.fill_rate == 1.0
    assert report.median_ack_ms == pytest.approx(50.0)
    assert report.span_days >= 1.0
    assert report.accounting_drift == 0.0
    assert report.passed
    assert report.summary().endswith("RESULT: PASSED")

    require_promotion(RunMode.PAPER, RunMode.LIVE, incubation=report)  # the gate opens


def test_a_venue_that_acks_too_slowly_is_not_promotable() -> None:
    venue = _run(RoundTrip(), ack_ns=SLOW_ACK_NS)
    report = _report(venue)

    assert report.median_ack_ms == pytest.approx(5_000.0)
    assert not report.passed
    with pytest.raises(PromotionRefused, match="ack_latency"):
        require_promotion(RunMode.PAPER, RunMode.LIVE, incubation=report)


def test_rejected_orders_keep_the_strategy_out_of_live_capital() -> None:
    venue = _run(Rejecting())
    report = _report(venue)

    assert report.fill_rate == pytest.approx(2.0 / 3.0)
    with pytest.raises(PromotionRefused, match="fill_rate"):
        require_promotion(RunMode.PAPER, RunMode.LIVE, incubation=report)
