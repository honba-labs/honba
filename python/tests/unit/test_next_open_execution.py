"""``honba.backtest.simulated.NextOpenExecution``: the multi-instrument next-open simulator.

Port-level rules (no runner): next-session-open timing, sells before buys, integer
``Money`` cash, T+N availability of sale proceeds, funding cuts, long-only caps, and
the reject/cancel path. The runner-level flow is in
``tests/integration/test_backtest_next_open.py``.
"""

from __future__ import annotations

import datetime as dt

import pytest

from honba.backtest.simulated import (
    NextOpenExecution,
    SessionOpen,
    fill_costs_from_model,
    group_sessions,
    make_simulator,
    resolve_fill_costs,
    zero_costs,
)
from honba.domain.money import Currency, Money
from honba.entities.bar import Bar
from honba.entities.instrument import InstrumentId
from honba.entities.order import OrderIntent, OrderSide
from honba.markets.india.costs import (
    nse_equity_delivery_breakdown,
    nse_equity_delivery_fill_cost,
)
from honba.strategies.testing import BarCloseFills

A = InstrumentId("AAA", "NSE")
B = InstrumentId("BBB", "NSE")
INR = Currency.INR


def rupees(x: float) -> Money:
    return Money.from_major(x, INR)


def bar(iid: InstrumentId, ts: int, open_: float, close: float | None = None) -> Bar:
    c = open_ if close is None else close
    return Bar(iid, ts, open_, max(open_, c), min(open_, c), c, 1_000.0)


def port(cash: float = 10_000.0, **kw) -> NextOpenExecution:
    return NextOpenExecution(cash=rupees(cash), **kw)


def test_an_order_fills_at_the_next_session_open_never_the_decision_close() -> None:
    p = port()
    p.open_session(1, [bar(A, 1, 100.0, close=105.0)])
    p.submit("o-0", OrderIntent.market_buy(A, 10), 1)
    assert p.drain_fills() == []  # not at the decision bar
    p.open_session(2, [bar(A, 2, 110.0, close=120.0)])
    (fill,) = p.drain_fills()
    assert (fill.order_id, fill.price, fill.ts, fill.quantity) == ("o-0", 110.0, 2, 10)
    assert p.cash == rupees(10_000.0 - 1_100.0)
    assert p.positions == {A: 10}


def test_an_order_waits_for_its_instrument_to_print() -> None:
    p = port()
    p.open_session(1, [bar(A, 1, 100.0)])
    p.submit("o-0", OrderIntent.market_buy(B, 1), 1)
    p.open_session(2, [bar(A, 2, 100.0)])  # B does not print
    assert p.drain_fills() == []
    p.open_session(3, [bar(B, 3, 50.0)])
    assert [f.price for f in p.drain_fills()] == [50.0]


def test_sells_fill_before_buys_within_a_session() -> None:
    p = port(cash=1_000.0, settlement_days=0)
    p.open_session(1, [bar(A, 1, 100.0), bar(B, 1, 100.0)])
    p.submit("o-0", OrderIntent.market_buy(A, 10), 1)
    p.open_session(2, [bar(A, 2, 100.0), bar(B, 2, 100.0)])
    p.drain_fills()
    # Rotate A -> B with no spare cash: the buy is listed first but the sell funds it.
    p.submit("o-1", OrderIntent.market_buy(B, 10), 2)
    p.submit("o-2", OrderIntent.market_sell(A, 10), 2)
    p.open_session(3, [bar(A, 3, 100.0), bar(B, 3, 100.0)])
    assert [(f.order_id, f.side) for f in p.drain_fills()] == [
        ("o-2", OrderSide.SELL),
        ("o-1", OrderSide.BUY),
    ]
    assert p.positions == {B: 10}


def test_sale_proceeds_settle_after_settlement_days_sessions() -> None:
    p = port(cash=0.0, settlement_days=2)
    p.positions[A] = 10.0  # seeded holding
    p.open_session(1, [bar(A, 1, 100.0)])
    p.submit("s", OrderIntent.market_sell(A, 10), 1)
    p.open_session(2, [bar(A, 2, 100.0)])
    assert p.cash == rupees(1_000.0)
    assert p.available_cash == Money.zero(INR)
    p.open_session(3, [bar(A, 3, 100.0)])
    assert p.available_cash == Money.zero(INR)
    p.open_session(4, [bar(A, 4, 100.0)])
    assert p.available_cash == rupees(1_000.0)


def test_an_unfunded_buy_waits_for_settlement_then_is_cut_and_the_rest_rejected() -> None:
    p = port(cash=250.0, settlement_days=1)
    p.positions[B] = 1.0
    p.open_session(1, [bar(A, 1, 100.0), bar(B, 1, 10.0)])
    p.submit("b", OrderIntent.market_buy(A, 5), 1)
    p.submit("s", OrderIntent.market_sell(B, 1), 1)
    # The sell fills (proceeds pending until session 3), so the unfunded buy waits.
    p.open_session(2, [bar(A, 2, 100.0), bar(B, 2, 10.0)])
    assert [f.order_id for f in p.drain_fills()] == ["s"] and p.drain_rejections() == []
    p.open_session(3, [bar(A, 3, 100.0), bar(B, 3, 10.0)])  # settled: 260 buys 2, cut
    (fill,) = p.drain_fills()
    (rej,) = p.drain_rejections()
    assert fill.quantity == 2
    assert (rej.order_id, rej.intent.quantity, rej.reason, rej.cancelled) == (
        "b",
        3,
        "insufficient_funds",
        False,
    )
    assert p.cash == rupees(60.0)


def test_with_t0_settlement_an_unfunded_buy_is_cut_at_once() -> None:
    p = port(cash=150.0, settlement_days=0)
    p.open_session(1, [bar(A, 1, 100.0)])
    p.submit("b", OrderIntent.market_buy(A, 3), 1)
    p.open_session(2, [bar(A, 2, 100.0)])
    assert [f.quantity for f in p.drain_fills()] == [1]
    assert [r.intent.quantity for r in p.drain_rejections()] == [2]


def test_long_only_caps_a_sell_at_the_position_held() -> None:
    p = port()
    p.positions[A] = 4.0
    p.open_session(1, [bar(A, 1, 10.0)])
    p.submit("s", OrderIntent.market_sell(A, 6), 1)
    p.open_session(2, [bar(A, 2, 10.0)])
    assert [f.quantity for f in p.drain_fills()] == [4]
    assert [(r.intent.quantity, r.reason) for r in p.drain_rejections()] == [(2, "no_position")]
    assert A not in p.positions


def test_shorting_is_allowed_when_not_long_only() -> None:
    p = port(long_only=False)
    p.open_session(1, [bar(A, 1, 10.0)])
    p.submit("s", OrderIntent.market_sell(A, 3), 1)
    p.open_session(2, [bar(A, 2, 10.0)])
    assert p.positions == {A: -3}


def test_cancel_releases_a_working_order_and_ignores_unknown_ids() -> None:
    p = port()
    p.open_session(1, [bar(A, 1, 10.0)])
    p.submit("o", OrderIntent.market_buy(A, 1), 1)
    p.cancel("nope")
    p.cancel("o")
    (rej,) = p.drain_rejections()
    assert rej.cancelled and rej.order_id == "o"
    p.open_session(2, [bar(A, 2, 10.0)])
    assert p.drain_fills() == []


def test_non_market_orders_are_rejected() -> None:
    p = port()
    p.submit("l", OrderIntent.limit_buy(A, 1, 9.0), 0)
    (rej,) = p.drain_rejections()
    assert rej.reason == "unsupported_order_type"


def test_sessions_must_move_forward() -> None:
    p = port()
    p.open_session(5, [bar(A, 5, 10.0)])
    with pytest.raises(ValueError):
        p.open_session(5, [bar(A, 5, 10.0)])


def test_plain_bar_events_open_sessions_by_timestamp() -> None:
    p = port()
    p.on_event(bar(A, 1, 10.0), 1)
    p.on_event(bar(B, 1, 20.0), 1)
    p.submit("a", OrderIntent.market_buy(A, 1), 1)
    p.submit("b", OrderIntent.market_buy(B, 1), 1)
    p.on_event(bar(A, 2, 11.0), 2)  # opens session 2 with A only
    assert [f.order_id for f in p.drain_fills()] == ["a"]
    p.on_event(bar(B, 2, 21.0), 2)  # B prints later in the same session
    assert [(f.order_id, f.price) for f in p.drain_fills()] == [("b", 21.0)]
    with pytest.raises(ValueError, match="duplicate"):  # a repeated bar is an error now
        p.on_event(bar(B, 2, 99.0), 2)
    assert p.drain_fills() == []


def test_a_session_open_event_opens_every_instrument_at_once() -> None:
    p = port(cash=0.0, settlement_days=0)
    p.positions[B] = 1.0
    p.on_event(SessionOpen(1, (bar(A, 1, 10.0), bar(B, 1, 10.0))), 1)
    p.submit("buy-a", OrderIntent.market_buy(A, 1), 1)
    p.submit("sell-b", OrderIntent.market_sell(B, 1), 1)
    p.on_event(SessionOpen(2, (bar(A, 2, 10.0), bar(B, 2, 10.0))), 2)
    assert [f.order_id for f in p.drain_fills()] == ["sell-b", "buy-a"]
    p.on_event(bar(A, 2, 10.0), 2)  # the bars that follow change nothing
    assert p.drain_fills() == []


def test_costs_are_charged_in_money_and_reach_the_fill() -> None:
    def flat(side: OrderSide, qty: float, px: float) -> Money:
        return rupees(20.0)

    p = port(costs=flat)
    p.open_session(1, [bar(A, 1, 100.0)])
    p.submit("o", OrderIntent.market_buy(A, 1), 1)
    p.open_session(2, [bar(A, 2, 100.0)])
    (fill,) = p.drain_fills()
    assert fill.costs == rupees(20.0)
    assert p.cash == rupees(10_000.0 - 120.0)
    assert p.fees == rupees(20.0) and p.traded_notional == rupees(100.0)


def test_india_delivery_fill_cost_rounds_each_leg_once() -> None:
    legs = nse_equity_delivery_breakdown(OrderSide.SELL, 37, 1234.55)
    want = sum(
        Money.from_major(v, INR).amount
        for v in (
            legs.brokerage,
            legs.stt,
            legs.exchange,
            legs.sebi,
            legs.ipft,
            legs.stamp_duty,
            legs.gst,
        )
    )
    assert nse_equity_delivery_fill_cost(OrderSide.SELL, 37, 1234.55) == Money(want, INR)


def test_resolve_fill_costs_names() -> None:
    assert resolve_fill_costs("none") is zero_costs
    assert resolve_fill_costs("india.equity") is nse_equity_delivery_fill_cost
    with pytest.raises(ValueError):
        resolve_fill_costs("mars.equity")


def test_make_simulator_settlement_follows_the_as_of_date() -> None:
    def days(**kw) -> int:
        return make_simulator(fill="next_open", cash=rupees(1.0), **kw).settlement_days

    assert days(as_of=dt.date(2023, 1, 26)) == 2
    assert days(as_of=dt.date(2023, 1, 27)) == 1
    assert days(as_of=dt.date(2023, 1, 26), exchange="BSE") == 2
    assert days(as_of=dt.date(2019, 1, 1), exchange="NYSE") == 1
    # An explicit override always wins over the dated default.
    assert days(as_of=dt.date(2019, 1, 1), settlement_days=0) == 0
    assert days(as_of=dt.date(2025, 1, 1), settlement_days=3) == 3


@pytest.mark.parametrize("timeframe", ["1m", "5m", "15min", "1h", "60s", "30m"])
def test_make_simulator_requires_explicit_settlement_for_intraday(timeframe: str) -> None:
    with pytest.raises(ValueError, match="settlement_days"):
        make_simulator(fill="next_open", cash=rupees(1.0), timeframe=timeframe)
    sim = make_simulator(fill="next_open", cash=rupees(1.0), timeframe=timeframe, settlement_days=0)
    assert sim.settlement_days == 0


@pytest.mark.parametrize("timeframe", ["1d", "1D", "d", "1w", "day", "daily"])
def test_make_simulator_daily_timeframes_use_the_market_default(timeframe: str) -> None:
    sim = make_simulator(fill="next_open", cash=rupees(1.0), timeframe=timeframe)
    assert sim.settlement_days == 1


def test_make_simulator_selects_by_fill_model_and_india_settlement() -> None:
    sim = make_simulator(fill="next_open", cash=rupees(1.0), exchange="NSE")
    assert isinstance(sim, NextOpenExecution) and sim.settlement_days == 1  # T+1 today
    assert (
        make_simulator(fill="next_open", cash=rupees(1.0), settlement_days=0).settlement_days == 0
    )
    assert isinstance(make_simulator(fill="bar_close", cash=rupees(1.0)), BarCloseFills)
    with pytest.raises(ValueError):
        make_simulator(fill="vwap", cash=rupees(1.0))  # type: ignore[arg-type]


def test_invalid_construction_is_refused() -> None:
    with pytest.raises(ValueError):
        port(settlement_days=-1)
    with pytest.raises(ValueError):
        port(cash=-1.0)


def test_zero_costs_work_in_any_currency() -> None:
    p = NextOpenExecution(cash=Money.from_major(100.0, Currency.USD))
    p.open_session(1, [bar(A, 1, 10.0)])
    p.submit("o", OrderIntent.market_buy(A, 1), 1)
    p.open_session(2, [bar(A, 2, 10.0)])
    assert p.drain_fills()[0].costs == Money.zero(Currency.USD)


def test_group_sessions_emits_a_session_open_before_each_sessions_bars() -> None:
    bars = [bar(B, 2, 1.0), bar(A, 1, 1.0), bar(A, 2, 1.0), bar(B, 1, 1.0)]
    events = group_sessions(bars)
    kinds = [(type(e).__name__, ts) for e, ts in events]
    assert kinds == [
        ("SessionOpen", 1),
        ("Bar", 1),
        ("Bar", 1),
        ("SessionOpen", 2),
        ("Bar", 2),
        ("Bar", 2),
    ]
    assert [b.instrument_id for b in events[0][0].bars] == [A, B]


def test_group_sessions_accepts_a_session_key() -> None:
    day = 86_400 * 10**9
    bars = [bar(A, day + 5, 1.0), bar(B, day + 9, 1.0), bar(A, 2 * day + 5, 1.0)]
    events = group_sessions(bars, key=lambda b: b.ts // day)
    assert [(type(e).__name__, ts) for e, ts in events] == [
        ("SessionOpen", day + 5),
        ("Bar", day + 5),
        ("Bar", day + 9),
        ("SessionOpen", 2 * day + 5),
        ("Bar", 2 * day + 5),
    ]


# -- review fixes ------------------------------------------------------------------


def _feed(p: NextOpenExecution, *bars: Bar) -> None:
    for b in bars:
        p.on_event(b, b.ts)


def test_session_open_with_non_ts_keys_ignores_the_following_bars() -> None:
    day = 86_400 * 10**9
    t0 = 1_700_000_000 * 10**9
    bars = [bar(i, t0 + d * day, 100.0 + d) for d in range(3) for i in (A, B)]
    p = port()
    for event, ts in group_sessions(bars, key=lambda b: b.ts // day):
        p.on_event(event, ts)  # must not raise "does not follow session"
    assert p._session == 2


def test_ordinal_session_keys_keep_sells_before_buys_across_instruments() -> None:
    day = 86_400 * 10**9
    t0 = 1_700_000_000 * 10**9
    p = port(cash=1_000.0)
    p.positions[B] = 10
    bars = [bar(i, t0 + d * day, 100.0) for d in range(3) for i in (A, B)]
    events = group_sessions(bars, key=lambda b: b.ts // day)
    p.on_event(*events[0])
    p.submit("buy-a", OrderIntent.market_buy(A, 10), t0)
    p.submit("sell-b", OrderIntent.market_sell(B, 10), t0)
    for event, ts in events[1:]:
        p.on_event(event, ts)
    assert [f.order_id for f in p.drain_fills()] == ["sell-b", "buy-a"]


def test_an_unfundable_buy_does_not_wait_when_nothing_is_unsettled() -> None:
    p = port(cash=1_000.0, settlement_days=2)
    _feed(p, bar(A, 1, 100.0))
    p.submit("o", OrderIntent.market_buy(A, 20), 1)
    _feed(p, bar(A, 2, 100.0))
    (fill,) = p.drain_fills()
    assert (fill.quantity, fill.ts) == (10, 2)
    (rej,) = p.drain_rejections()
    assert (rej.reason, rej.intent.quantity) == ("insufficient_funds", 10)


def test_an_unfundable_buy_waits_while_proceeds_are_pending_then_is_cut() -> None:
    p = port(cash=0.0, settlement_days=2)
    p.positions[B] = 10
    _feed(p, bar(A, 1, 100.0), bar(B, 1, 100.0))
    p.submit("sell", OrderIntent.market_sell(B, 10), 1)
    _feed(p, bar(A, 2, 100.0), bar(B, 2, 100.0))  # sell fills; proceeds due session 4
    p.submit("buy", OrderIntent.market_buy(A, 20), 2)
    _feed(p, bar(A, 3, 100.0), bar(B, 3, 100.0))  # proceeds still unsettled: it waits
    assert [f.order_id for f in p.drain_fills()] == ["sell"]
    _feed(p, bar(A, 4, 100.0), bar(B, 4, 100.0))  # settled: 1000 buys 10, cut
    (fill,) = p.drain_fills()
    assert (fill.order_id, fill.quantity, fill.ts) == ("buy", 10, 4)


@pytest.mark.parametrize("bad", [0.0, -10.0, float("nan"), float("inf")])
def test_bars_with_unusable_opens_never_fill_and_the_order_keeps_waiting(bad: float) -> None:
    p = port(cash=1_000.0)
    _feed(p, bar(A, 1, 100.0))
    p.submit("o", OrderIntent.market_buy(A, 5), 1)
    _feed(p, Bar(A, 2, bad, 100.0, 100.0, 100.0, 1.0))
    assert p.drain_fills() == [] and p.cash == rupees(1_000.0)
    assert p.working_orders == ["o"] and p.drain_rejections() == []
    _feed(p, bar(A, 3, 100.0))
    (fill,) = p.drain_fills()
    assert (fill.price, fill.ts) == (100.0, 3)


def test_a_sell_on_an_unusable_open_keeps_waiting_and_the_position_intact() -> None:
    p = port(cash=0.0)
    p.positions[A] = 5
    _feed(p, bar(A, 1, 100.0))
    p.submit("s", OrderIntent.market_sell(A, 5), 1)
    _feed(p, bar(A, 2, 0.0, close=100.0))
    assert p.drain_fills() == [] and p.positions == {A: 5} and p.working_orders == ["s"]


def test_a_failing_cost_function_does_not_lose_the_order() -> None:
    def boom(side: OrderSide, qty: float, px: float) -> Money:
        raise RuntimeError("cost")

    p = port(cash=1_000.0, costs=boom)
    _feed(p, bar(A, 1, 100.0))
    p.submit("o", OrderIntent.market_sell(A, 1), 1)
    p.positions[A] = 1
    with pytest.raises(RuntimeError):
        _feed(p, bar(A, 2, 100.0))
    assert p.working_orders == ["o"] and p.positions == {A: 1}


def test_plain_bar_mode_rejects_out_of_order_and_duplicate_bars() -> None:
    p = port()
    _feed(p, bar(A, 5, 100.0))
    with pytest.raises(ValueError, match="non-monotonic"):
        _feed(p, bar(A, 3, 100.0))
    with pytest.raises(ValueError, match="duplicate"):
        _feed(p, bar(A, 5, 100.0))
    _feed(p, bar(B, 5, 100.0), bar(A, 6, 100.0))  # same ts, other instrument: legal


def test_fill_costs_from_model_charges_the_models_cost_per_fill() -> None:
    class PerFill:
        def apply(self, trade):
            import dataclasses

            return dataclasses.replace(trade, costs=rupees(trade.quantity * 0.5))

    fn = fill_costs_from_model(PerFill())
    assert fn(OrderSide.BUY, 10, 100.0) == rupees(5.0)
    p = port(cash=1_000.0, settlement_days=0, costs=fn)
    p.open_session(1, [bar(A, 1, 100.0)])
    p.submit("o", OrderIntent.market_buy(A, 4), 1)
    p.open_session(2, [bar(A, 2, 100.0)])
    assert p.cash == rupees(1_000.0 - 400.0 - 2.0)
    assert p.fees == rupees(2.0)
