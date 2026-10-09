"""Unit tests for the Jesse-style DeclarativeStrategy facade."""

import math

import pytest

from honba.entities.bar import Bar
from honba.entities.instrument import InstrumentId
from honba.entities.order import OrderIntent, OrderType
from honba.entities.trade import Trade
from honba.strategies import DeclarativeStrategy, Entry
from honba.strategies.base import Strategy

IID = InstrumentId("NIFTY50", "NSE")


def bar(close: float = 100.0, ts: int = 1) -> Bar:
    return Bar(IID, ts, close, close, close, close, 1000.0)


class Rules(DeclarativeStrategy):
    name = "rules"

    def __init__(self, long=False, short=False, exit_=False, entry=None):
        self.long, self.short, self.exit_ = long, short, exit_
        self.entry = entry or Entry(10)
        self.calls: list[str] = []

    def should_long(self, bar):
        return self.long

    def should_short(self, bar):
        return self.short

    def should_exit(self, bar):
        return self.exit_

    def go_long(self, bar):
        self.calls.append("go_long")
        return self.entry

    def go_short(self, bar):
        self.calls.append("go_short")
        return self.entry


def fill(side_buy: bool, qty: float, price: float = 100.0) -> Trade:
    intents = (OrderIntent.market_buy if side_buy else OrderIntent.market_sell)(IID, qty)
    return Trade(IID, intents.side, qty, price, 1)


def test_is_strategy_subclass():
    assert issubclass(DeclarativeStrategy, Strategy)


# -- Entry ------------------------------------------------------------------
@pytest.mark.parametrize("qty", [0, -1, math.nan, math.inf])
def test_entry_rejects_bad_quantity(qty):
    with pytest.raises(ValueError):
        Entry(qty)


@pytest.mark.parametrize("field", ["stop_loss", "take_profit"])
@pytest.mark.parametrize("value", [0, -1.0, math.nan, math.inf])
def test_entry_rejects_bad_levels(field, value):
    with pytest.raises(ValueError):
        Entry(1, **{field: value})


def test_entry_accepts_valid_and_is_frozen():
    e = Entry(2.5, stop_loss=90, take_profit=110)
    assert (e.quantity, e.stop_loss, e.take_profit) == (2.5, 90, 110)
    with pytest.raises(AttributeError):
        e.quantity = 3  # type: ignore[misc]


# -- defaults -----------------------------------------------------------------
def test_defaults_do_nothing():
    s = DeclarativeStrategy.__new__(type("Plain", (DeclarativeStrategy,), {"name": "plain"}))
    s.on_bar(bar())
    assert s.drain_intents() == []


def test_default_go_long_and_short_raise():
    s = type("Plain", (DeclarativeStrategy,), {"name": "plain"})()
    with pytest.raises(NotImplementedError, match="go_long"):
        s.go_long(bar())
    with pytest.raises(NotImplementedError, match="go_short"):
        s.go_short(bar())


# -- entries -------------------------------------------------------------------
def test_long_entry():
    s = Rules(long=True)
    s.on_bar(bar())
    assert s.drain_intents() == [OrderIntent.market_buy(IID, 10)]
    assert s.calls == ["go_long"]


def test_short_entry():
    s = Rules(short=True)
    s.on_bar(bar())
    assert s.drain_intents() == [OrderIntent.market_sell(IID, 10)]
    assert s.calls == ["go_short"]


def test_long_wins_ties():
    s = Rules(long=True, short=True)
    s.on_bar(bar())
    assert s.drain_intents() == [OrderIntent.market_buy(IID, 10)]
    assert s.calls == ["go_long"]


def test_no_entry_when_busy():
    s = Rules(long=True)
    s.on_bar(bar())
    s.on_bar(bar(ts=2))  # first entry unfilled -> busy
    assert len(s.drain_intents()) == 1
    assert s.calls == ["go_long"]


def test_no_entry_when_in_position():
    s = Rules(long=True)
    s.handle_fill(fill(True, 5))
    s.on_bar(bar())
    assert s.drain_intents() == []
    assert s.calls == []


# -- exits ---------------------------------------------------------------------
def test_exit_long_sells_position():
    s = Rules(exit_=True)
    s.handle_fill(fill(True, 7))
    s.on_bar(bar())
    assert s.drain_intents() == [OrderIntent.market_sell(IID, 7)]


def test_exit_short_buys_position():
    s = Rules(exit_=True)
    s.handle_fill(fill(False, 7))
    s.on_bar(bar())
    assert s.drain_intents() == [OrderIntent.market_buy(IID, 7)]


def test_no_exit_when_should_exit_false():
    s = Rules()
    s.handle_fill(fill(True, 7))
    s.on_bar(bar())
    assert s.drain_intents() == []


def test_no_exit_when_busy():
    s = Rules(exit_=True)
    s.handle_fill(fill(True, 7))
    s.on_bar(bar())
    s.on_bar(bar(ts=2))
    assert len(s.drain_intents()) == 1


def test_exit_not_evaluated_when_flat():
    s = Rules(exit_=True)
    s.on_bar(bar())
    assert s.drain_intents() == []


# -- stop / take validation ---------------------------------------------------------
@pytest.mark.parametrize(
    "entry",
    [
        Entry(1, stop_loss=100.0),
        Entry(1, stop_loss=101.0),
        Entry(1, take_profit=100.0),
        Entry(1, take_profit=99.0),
    ],
)
def test_long_levels_must_straddle_close(entry):
    s = Rules(long=True, entry=entry)
    with pytest.raises(ValueError, match="long"):
        s.on_bar(bar(100.0))
    assert s.drain_intents() == []


@pytest.mark.parametrize(
    "entry",
    [
        Entry(1, stop_loss=100.0),
        Entry(1, stop_loss=99.0),
        Entry(1, take_profit=100.0),
        Entry(1, take_profit=101.0),
    ],
)
def test_short_levels_must_straddle_close(entry):
    s = Rules(short=True, entry=entry)
    with pytest.raises(ValueError, match="short"):
        s.on_bar(bar(100.0))
    assert s.drain_intents() == []


# -- protective orders ----------------------------------------------------------------
def test_long_protective_orders_after_entry_fill():
    s = Rules(long=True, entry=Entry(10, stop_loss=95, take_profit=110))
    s.on_bar(bar())
    assert s.drain_intents() == [OrderIntent.market_buy(IID, 10)]  # no protectives yet
    s.handle_fill(fill(True, 10))
    stop, take = s.drain_intents()
    assert (stop.side.value, stop.order_type, stop.quantity, stop.trigger_price) == (
        "sell",
        OrderType.STOP_MARKET,
        10,
        95,
    )
    assert (take.side.value, take.order_type, take.quantity, take.price) == (
        "sell",
        OrderType.LIMIT,
        10,
        110,
    )


def test_short_protective_orders_after_entry_fill():
    s = Rules(short=True, entry=Entry(4, stop_loss=105, take_profit=90))
    s.on_bar(bar())
    s.drain_intents()
    s.handle_fill(fill(False, 4))
    assert s.drain_intents() == [
        OrderIntent.stop_buy(IID, 4, 105),
        OrderIntent.limit_buy(IID, 4, 90),
    ]


def test_protective_uses_fill_quantity_for_partials():
    s = Rules(long=True, entry=Entry(10, stop_loss=95))
    s.on_bar(bar())
    s.drain_intents()
    s.handle_fill(fill(True, 6))
    assert s.drain_intents() == [OrderIntent.stop_sell(IID, 6, 95)]


def test_only_given_levels_are_placed():
    s = Rules(long=True, entry=Entry(10, take_profit=110))
    s.on_bar(bar())
    s.drain_intents()
    s.handle_fill(fill(True, 10))
    assert s.drain_intents() == [OrderIntent.limit_sell(IID, 10, 110)]


def test_no_protectives_without_levels():
    s = Rules(long=True)
    s.on_bar(bar())
    s.drain_intents()
    s.handle_fill(fill(True, 10))
    assert s.drain_intents() == []


def test_state_cleared_when_flat_and_not_reused():
    s = Rules(long=True, entry=Entry(10, stop_loss=95))
    s.on_bar(bar())
    s.drain_intents()
    s.handle_fill(fill(True, 10))
    s.drain_intents()
    assert IID in s._entries
    s.handle_fill(fill(False, 10, 95.0))  # stop hit
    assert IID not in s._entries
    # a later manual buy must not resurrect old protectives
    s.handle_fill(fill(True, 3))
    assert s.drain_intents() == []


def test_subclass_on_fill_can_call_super():
    seen = []

    class Sub(Rules):
        def on_fill(self, fill):
            seen.append(fill)
            super().on_fill(fill)

    s = Sub(long=True, entry=Entry(10, stop_loss=95))
    s.on_bar(bar())
    s.drain_intents()
    s.handle_fill(fill(True, 10))
    assert len(seen) == 1
    assert s.drain_intents() == [OrderIntent.stop_sell(IID, 10, 95)]
