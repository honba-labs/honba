"""Contract tests for the Python Strategy interface (mirrors honba-algo-strategies)."""
import pytest

from honba.entities.bar import Bar
from honba.entities.instrument import InstrumentId
from honba.entities.order import OrderIntent, OrderSide, OrderType
from honba.strategies.base import Strategy
from honba.strategies.config import StrategyConfig
from honba.strategies.testing import replay

NIFTY = InstrumentId("NIFTY50", "NSE")


def bar(close: float, ts: int = 0) -> Bar:
    return Bar(NIFTY, ts, close, close, close, close, 1000.0)


class BuyFirstBar(Strategy):
    name = "buy_first_bar"

    def on_bar(self, bar: Bar) -> None:
        if self.position(bar.instrument_id) == 0:
            self.buy(bar.instrument_id, 10)


def test_intents_are_drained_once():
    s = BuyFirstBar()
    s.on_bar(bar(100.0))
    intents = s.drain_intents()
    assert intents == [OrderIntent.market_buy(NIFTY, 10)]
    assert s.drain_intents() == []


def test_market_intent_defaults():
    i = OrderIntent.market_sell(NIFTY, 5)
    assert (i.side, i.order_type, i.price) == (OrderSide.SELL, OrderType.MARKET, None)


def test_non_positive_quantity_rejected():
    with pytest.raises(ValueError):
        OrderIntent.market_buy(NIFTY, 0)


def test_replay_fills_intents_and_tracks_position():
    s = BuyFirstBar()
    result = replay(s, [bar(100.0, 1), bar(101.0, 2)])
    assert s.position(NIFTY) == 10  # second bar must not buy again
    assert len(result.fills) == 1
    assert result.fills[0].price == 100.0


def test_sell_reduces_position():
    class RoundTrip(Strategy):
        name = "round_trip"

        def on_bar(self, bar: Bar) -> None:
            if self.position(bar.instrument_id) == 0:
                self.buy(bar.instrument_id, 4)
            else:
                self.sell(bar.instrument_id, 4)

    s = RoundTrip()
    replay(s, [bar(100.0, 1), bar(110.0, 2)])
    assert s.position(NIFTY) == 0


def test_strategy_requires_name():
    class Nameless(Strategy):
        pass

    with pytest.raises(TypeError):
        Nameless()


def test_config_from_toml_and_validation(tmp_path):
    p = tmp_path / "config.toml"
    p.write_text('name = "sma_crossover"\nsymbol = "RELIANCE"\n\n[params]\nfast = 20\nslow = 50\n')
    cfg = StrategyConfig.from_toml(p)
    assert cfg.venue == "NSE"
    assert cfg.instrument_id == InstrumentId("RELIANCE", "NSE")
    assert cfg.params == {"fast": 20, "slow": 50}
    with pytest.raises(ValueError):
        StrategyConfig(name="x", symbol="", params={})
