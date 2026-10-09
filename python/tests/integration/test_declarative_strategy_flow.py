"""strategy -> context -> intents -> fills -> protective intents, via the replay harness."""

from honba.entities.bar import Bar
from honba.entities.instrument import InstrumentId
from honba.entities.order import OrderIntent, OrderSide, OrderType
from honba.strategies import DeclarativeStrategy, Entry
from honba.strategies.testing import replay

IID = InstrumentId("NIFTY50", "NSE")


def bars(closes):
    return [Bar(IID, i + 1, c, c, c, c, 1000.0) for i, c in enumerate(closes)]


class Breakout(DeclarativeStrategy):
    name = "breakout_flow"

    def __init__(self, direction: str):
        self.direction = direction

    def should_long(self, bar):
        return self.direction == "long" and bar.close >= 101

    def should_short(self, bar):
        return self.direction == "short" and bar.close <= 99

    def go_long(self, bar):
        return Entry(5, stop_loss=bar.close - 5, take_profit=bar.close + 10)

    def go_short(self, bar):
        return Entry(5, stop_loss=bar.close + 5, take_profit=bar.close - 10)


def test_long_flow_entry_fill_then_protectives():
    s = Breakout("long")
    result = replay(s, bars([100, 101, 102]))
    assert result.intents[0] == OrderIntent.market_buy(IID, 5)
    stop, take = result.intents[1:3]
    assert (stop.side, stop.order_type, stop.trigger_price) == (
        OrderSide.SELL,
        OrderType.STOP_MARKET,
        96,
    )
    assert (take.side, take.order_type, take.price) == (OrderSide.SELL, OrderType.LIMIT, 111)
    # replay fills every intent at the bar close (it has no resting-order model)
    assert result.fills[0].side is OrderSide.BUY and result.fills[0].price == 101


def test_short_flow_entry_fill_then_protectives():
    s = Breakout("short")
    result = replay(s, bars([100, 99, 98]))
    assert result.intents[0] == OrderIntent.market_sell(IID, 5)
    assert result.intents[1] == OrderIntent.stop_buy(IID, 5, 104)
    assert result.intents[2] == OrderIntent.limit_buy(IID, 5, 89)
    assert result.fills[0].side is OrderSide.SELL


def test_delayed_fill_places_protectives_only_after_fill():
    s = Breakout("long")
    result = replay(s, bars([100, 101, 102, 103]), fill_delay=1)
    # bar 2 emits entry; fills at bar 3 open; protectives emitted at that fill (before bar 3 on_bar)
    assert result.intents[0].order_type is OrderType.MARKET
    assert [i.order_type for i in result.intents[1:3]] == [OrderType.STOP_MARKET, OrderType.LIMIT]
