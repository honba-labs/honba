"""``StrategyRunner`` semantics and the ``BarCloseFills`` simulator (ADR 008, decision 5)."""

import pytest

from honba.entities.bar import Bar
from honba.entities.instrument import InstrumentId
from honba.entities.order import OrderIntent, OrderSide
from honba.entities.tick import AggressorSide, QuoteTick, TradeTick
from honba.entities.trade import Trade
from honba.strategies.base import Strategy
from honba.strategies.context import LedgerContext
from honba.strategies.runner import StrategyRunner
from honba.strategies.testing import MAX_COST_BPS, MAX_FLAT_COST, BarCloseFills, FillCostsError

X = InstrumentId("X", "NSE")


def bar(close: float, ts: int) -> Bar:
    return Bar(X, ts, close, close, close, close, 1.0)


class Recorder(Strategy):
    """Logs every hook with the context clock; submits what the test scripts."""

    name = "rec"

    def __init__(self, script=None):
        self.log: list[tuple[str, int]] = []
        self.script = script or {}

    def _hook(self, hook: str) -> None:
        self.log.append((hook, self.ctx.now()))
        for intent in self.script.pop(hook, []):
            self.ctx.submit(intent)

    def on_start(self):
        self._hook("on_start")

    def on_bar(self, bar):
        self._hook("on_bar")

    def on_quote(self, quote):
        self._hook("on_quote")

    def on_trade(self, trade):
        self._hook("on_trade")

    def on_fill(self, fill):
        self._hook("on_fill")

    def on_stop(self):
        self._hook("on_stop")


class FakeExecution:
    """Records submissions; fills nothing unless told to."""

    def __init__(self):
        self.orders: list[tuple[str, OrderIntent, int]] = []
        self.pending_fills: list[Trade] = []

    def submit(self, order_id, intent, ts):
        self.orders.append((order_id, intent, ts))

    def drain_fills(self):
        fills, self.pending_fills = self.pending_fills, []
        return fills


def test_hooks_dispatch_by_event_type_with_the_clock_at_ts_init():
    s = Recorder()
    runner = StrategyRunner(s, FakeExecution())
    runner.start()
    runner.on_event(bar(1.0, 10), ts_init=11)
    runner.on_event(QuoteTick(X, 20, 1.0, 1.1, 1.0, 1.0), ts_init=20)
    runner.on_event(TradeTick(X, 30, 1.0, 1.0, AggressorSide.BUYER, "T"), ts_init=30)
    runner.on_event(object(), ts_init=40)  # not market data: no hook
    runner.stop()
    assert s.log == [
        ("on_start", 0),
        ("on_bar", 11),
        ("on_quote", 20),
        ("on_trade", 30),
        ("on_stop", 40),
    ]


def test_runner_binds_its_context_to_the_strategy():
    s = Recorder()
    ctx = LedgerContext(cash=5.0)
    runner = StrategyRunner(s, FakeExecution(), ctx=ctx)
    assert runner.ctx is ctx and s.ctx is ctx


def test_start_intents_go_out_with_the_first_event_and_stop_intents_never():
    buy, sell = OrderIntent.market_buy(X, 1), OrderIntent.market_sell(X, 1)
    s = Recorder({"on_start": [buy], "on_stop": [sell]})
    ex = FakeExecution()
    runner = StrategyRunner(s, ex)
    runner.start()
    assert ex.orders == []
    runner.on_event(bar(1.0, 5), ts_init=5)
    runner.stop()
    assert ex.orders == [("rec-0", buy, 5)]
    assert [(i.ts_init, i.intent) for i in runner.intents] == [(5, buy)]


def test_fills_update_the_context_before_on_fill_and_on_fill_intents_wait_for_the_next_event():
    protect = OrderIntent.stop_sell(X, 2, 9.0)
    s = Recorder({"on_bar": [OrderIntent.market_buy(X, 2)], "on_fill": [protect]})
    ex = FakeExecution()
    runner = StrategyRunner(s, ex)
    runner.start()
    ex.pending_fills = [Trade(X, OrderSide.BUY, 2, 10.0, 5, "rec-0")]
    runner.on_event(bar(10.0, 5), ts_init=5)
    assert s.ctx.position(X) == 2.0 and s.ctx.cash() == -20.0
    assert [o[0] for o in ex.orders] == ["rec-0"]  # protect not yet sent
    runner.on_event(bar(10.0, 6), ts_init=6)
    assert ex.orders[1] == ("rec-1", protect, 6)
    assert runner.fills == [Trade(X, OrderSide.BUY, 2, 10.0, 5, "rec-0")]


def test_bar_close_fills_at_the_latest_close_with_strictly_increasing_times():
    ex = BarCloseFills()
    ex.on_event(bar(101.0, 7), ts_init=7)
    ex.on_event(QuoteTick(X, 8, 1.0, 2.0, 1.0, 1.0), ts_init=8)  # quotes do not move the price
    ex.submit("a-0", OrderIntent.market_buy(X, 3), 8)
    ex.submit("a-1", OrderIntent.limit_sell(X, 1, 500.0), 8)
    assert ex.drain_fills() == [
        Trade(X, OrderSide.BUY, 3, 101.0, 8, "a-0"),
        Trade(X, OrderSide.SELL, 1, 101.0, 9, "a-1"),
    ]
    assert ex.drain_fills() == []


def test_bar_close_fills_refuses_an_order_before_any_bar():
    # Rust BarFillEngine fills at 0.0 here (known gap, ADR 006); the mirror refuses.
    with pytest.raises(RuntimeError):
        BarCloseFills().submit("a-0", OrderIntent.market_buy(X, 1), 1)


def test_bar_close_fills_default_charges_no_costs():
    ex = BarCloseFills()
    ex.on_event(bar(101.0, 1), ts_init=1)
    ex.submit("a-0", OrderIntent.market_buy(X, 3), 1)
    assert ex.drain_fills()[0].costs == 0.0


def test_bar_close_fills_costs_are_flat_plus_bps_of_the_notional():
    # notional 10 * 100 = 1000; 10 bps of it is 1.0; plus the flat 20.
    ex = BarCloseFills(flat_cost=20.0, cost_bps=10.0)
    ex.on_event(bar(100.0, 1), ts_init=1)
    ex.submit("a-0", OrderIntent.market_buy(X, 10), 1)
    ex.submit("a-1", OrderIntent.market_sell(X, 10), 1)
    buy, sell = ex.drain_fills()
    assert (buy.costs, sell.costs) == (21.0, 21.0)  # an amount, not signed by side
    assert buy.price == 100.0


def test_bar_close_fills_flat_only_and_bps_only():
    flat = BarCloseFills(flat_cost=2.5)
    flat.on_event(bar(100.0, 1), ts_init=1)
    flat.submit("a-0", OrderIntent.market_buy(X, 3), 1)
    assert flat.drain_fills()[0].costs == 2.5
    bps = BarCloseFills(cost_bps=625.0)
    bps.on_event(bar(64.0, 1), ts_init=1)
    bps.submit("a-0", OrderIntent.market_sell(X, 1), 1)
    assert bps.drain_fills()[0].costs == 4.0


def test_costs_reach_the_context_cash_through_the_simulator():
    class BuySell(Strategy):
        name = "bs"

        def on_bar(self, bar):
            if not self.ctx.busy(X):
                side = self.ctx.position(X)
                self.ctx.submit(
                    OrderIntent.market_sell(X, 1) if side else OrderIntent.market_buy(X, 1)
                )

    ex = BarCloseFills(flat_cost=0.5, cost_bps=625.0)
    runner = StrategyRunner(BuySell(), ex)
    ex.on_event(bar(64.0, 1), ts_init=1)
    runner.on_event(bar(64.0, 1), ts_init=1)  # buy: debit 64 + (0.5 + 4.0)
    assert runner.ctx.cash() == -68.5
    ex.on_event(bar(64.0, 2), ts_init=2)
    runner.on_event(bar(64.0, 2), ts_init=2)  # sell: credit 64 - 4.5
    assert runner.ctx.cash() == -9.0


@pytest.mark.parametrize(
    ("flat", "bps"),
    [
        (-0.01, 0.0),
        (float("nan"), 0.0),
        (float("inf"), 0.0),
        (MAX_FLAT_COST + 1, 0.0),
        (0.0, -1.0),
        (0.0, float("nan")),
        (0.0, float("inf")),
        (0.0, MAX_COST_BPS + 1),
    ],
)
def test_bar_close_fills_rejects_invalid_costs_with_a_typed_error(flat, bps):
    with pytest.raises(FillCostsError):
        BarCloseFills(flat_cost=flat, cost_bps=bps)
    assert issubclass(FillCostsError, ValueError)


def test_bar_close_fills_accepts_the_cost_caps():
    BarCloseFills(flat_cost=MAX_FLAT_COST, cost_bps=MAX_COST_BPS)


def test_run_feeds_the_simulator_before_the_strategy():
    class BuyOnce(Strategy):
        name = "once"

        def on_bar(self, bar):
            if self.ctx.position(X) == 0 and not self.ctx.busy(X):
                self.ctx.submit(OrderIntent.market_buy(X, 1))

    runner = StrategyRunner(BuyOnce(), BarCloseFills())
    result = runner.run([(bar(10.0, 1), 1), (bar(11.0, 2), 2)])
    assert result.fills == [Trade(X, OrderSide.BUY, 1, 10.0, 1, "once-0")]
    assert result.ctx.position(X) == 1.0


def test_from_wire_messages_parses_market_data_strictly():
    import json

    from honba.strategies.testing import from_wire_messages

    iid = {"symbol": "X", "venue": "NSE"}
    quote = {
        "type": "quote",
        "instrument_id": iid,
        "bid_price": 1.0,
        "ask_price": 1.5,
        "bid_size": 2.0,
        "ask_size": 3.0,
        "ts_event": 4,
        "ts_init": 5,
    }
    msgs = [{"schema_version": 1, "event": quote, "ts_init": 6}]
    assert from_wire_messages(json.dumps(msgs)) == [(QuoteTick(X, 4, 1.0, 1.5, 2.0, 3.0), 6)]

    accepted = {"type": "order_accepted", "order_id": "O-1", "ts_event": 1}
    with pytest.raises(ValueError, match="unsupported event type"):
        from_wire_messages(json.dumps([{"schema_version": 1, "event": accepted, "ts_init": 1}]))
    duplicate = '[{"schema_version": 1, "schema_version": 1, "event": {}, "ts_init": 1}]'
    with pytest.raises(ValueError, match="duplicate key"):
        from_wire_messages(duplicate)
    with pytest.raises(TypeError):
        from_wire_messages(json.dumps({"not": "a list"}))


def _invalid(intent: OrderIntent, **fields) -> OrderIntent:
    """Break an ``OrderIntent`` after construction (it validates on construction)."""
    for name, value in fields.items():
        object.__setattr__(intent, name, value)
    return intent


def test_runner_rejects_an_invalid_intent_without_submitting_it():
    good = OrderIntent.market_buy(X, 3)
    bad = _invalid(OrderIntent.market_buy(X, 1), quantity=float("nan"))
    rec = Recorder({"on_bar": [good, bad]})
    execution = FakeExecution()
    runner = StrategyRunner(rec, execution)
    runner.start()
    runner.on_event(bar(10.0, 1), 7)
    assert [o[1] for o in execution.orders] == [good]
    assert [(r.ts_init, r.intent) for r in runner.rejections] == [(7, bad)]
    assert "quantity must be positive" in runner.rejections[0].error
    assert runner.ctx.busy(X)  # the valid order is still pending: the NaN did not wipe it
    # Order ids count only submitted orders.
    assert [o[0] for o in execution.orders] == ["rec-0"]


def test_runner_releases_a_rejected_intent_from_the_context_by_default():
    bad = _invalid(OrderIntent.market_buy(X, 2), quantity=-2.0)
    runner = StrategyRunner(Recorder({"on_bar": [bad]}), FakeExecution())
    runner.start()
    runner.on_event(bar(10.0, 1), 1)
    assert len(runner.rejections) == 1
    assert not runner.ctx.busy(X)


def test_runner_honours_a_handle_rejected_override():
    import warnings

    seen: list[OrderIntent] = []
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)

        class Handles(Recorder):
            name = "handles"

            def handle_rejected(self, intent):
                seen.append(intent)
                super().handle_rejected(intent)

    bad = _invalid(OrderIntent.market_sell(X, 2), quantity=0.0)
    runner = StrategyRunner(Handles({"on_bar": [bad]}), FakeExecution())
    runner.start()
    runner.on_event(bar(10.0, 1), 1)
    assert seen == [bad]
    assert runner.rejections[0].intent is bad
    assert not runner.ctx.busy(X)


def test_fill_costs_reach_the_context_cash_through_the_runner():
    # The fixture's bar_close model charges no costs; this covers the cost signs end to end.
    s = Recorder(
        {"on_bar": [OrderIntent.market_buy(X, 2)], "on_quote": [OrderIntent.market_sell(X, 2)]}
    )
    ex = FakeExecution()
    runner = StrategyRunner(s, ex)
    runner.start()
    ex.pending_fills = [Trade(X, OrderSide.BUY, 2, 10.0, 5, "rec-0", costs=1.5)]
    runner.on_event(bar(10.0, 5), ts_init=5)
    assert runner.ctx.cash() == -(2 * 10.0 + 1.5)  # a buy debits cost on top of the notional
    ex.pending_fills = [Trade(X, OrderSide.SELL, 2, 11.0, 6, "rec-1", costs=2.0)]
    runner.on_event(QuoteTick(X, 6, 10.9, 11.1, 1.0, 1.0), ts_init=6)
    assert runner.ctx.position(X) == 0.0
    assert runner.ctx.cash() == -21.5 + (2 * 11.0 - 2.0)  # a sell credits notional minus cost
