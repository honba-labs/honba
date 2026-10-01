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
from honba.strategies.testing import BarCloseFills

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
