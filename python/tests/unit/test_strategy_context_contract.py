"""The E0-S3 strategy contract surface (ADR 008): hooks, ``StrategyContext`` and value types."""

import inspect
import math

import pytest

from honba.entities.instrument import Instrument, InstrumentId, InstrumentKind
from honba.entities.tick import AggressorSide, QuoteTick, TradeTick
from honba.strategies.base import Strategy
from honba.strategies.context import StrategyContext

NIFTY = InstrumentId("NIFTY50", "NSE")

HOOKS = ("on_start", "on_bar", "on_quote", "on_trade", "on_fill", "on_stop")


def test_strategy_is_an_abc_with_the_full_hook_set():
    assert inspect.isabstract(StrategyContext)
    assert issubclass(type(Strategy), type(StrategyContext))  # both use ABCMeta
    for hook in HOOKS:
        assert callable(getattr(Strategy, hook)), hook


def test_new_hooks_default_to_no_ops():
    class OnlyBars(Strategy):
        name = "only_bars"

    s = OnlyBars()
    quote = QuoteTick(NIFTY, 1, 100.0, 100.5, 10.0, 20.0)
    trade = TradeTick(NIFTY, 1, 100.25, 5.0, AggressorSide.BUYER, "T-1")
    assert s.on_quote(quote) is None
    assert s.on_trade(trade) is None


def test_context_port_lists_exactly_the_contract_methods():
    assert StrategyContext.__abstractmethods__ == {
        "now",
        "position",
        "positions",
        "cash",
        "busy",
        "instrument",
        "submit",
    }
    with pytest.raises(TypeError):
        StrategyContext()  # type: ignore[abstract]


def test_quote_tick_invariants():
    q = QuoteTick(NIFTY, 5, 99.5, 100.0, 1.0, 2.0)
    assert (q.bid_price, q.ask_price, q.ts) == (99.5, 100.0, 5)
    with pytest.raises(ValueError):
        QuoteTick(NIFTY, 5, 100.5, 100.0, 1.0, 1.0)  # crossed book
    with pytest.raises(ValueError):
        QuoteTick(NIFTY, 5, 99.0, 100.0, -1.0, 1.0)  # negative size
    with pytest.raises(ValueError):
        QuoteTick(NIFTY, 5, math.nan, 100.0, 1.0, 1.0)


def test_trade_tick_invariants():
    t = TradeTick(NIFTY, 7, 101.0, 3.0, AggressorSide.SELLER, "T-9")
    assert (t.price, t.size, t.aggressor_side, t.trade_id) == (
        101.0,
        3.0,
        AggressorSide.SELLER,
        "T-9",
    )
    with pytest.raises(ValueError):
        TradeTick(NIFTY, 7, 101.0, -3.0, AggressorSide.BUYER, "T-9")
    with pytest.raises(ValueError):
        TradeTick(NIFTY, 7, math.inf, 3.0, AggressorSide.BUYER, "T-9")


def test_aggressor_side_matches_the_wire_enum():
    from honba.entities import wire

    assert wire.AggressorSide is AggressorSide


def test_instrument_metadata_invariants():
    inst = Instrument(NIFTY, InstrumentKind.INDEX, lot_size=75.0, tick_size=0.05)
    assert (inst.lot_size, inst.tick_size, inst.currency) == (75.0, 0.05, "INR")
    assert [k.value for k in InstrumentKind] == [
        "equity",
        "etf",
        "bond",
        "ipo",
        "future",
        "option",
        "fx",
        "index",
        "mutual_fund",
    ]
    with pytest.raises(ValueError):
        Instrument(NIFTY, InstrumentKind.INDEX, lot_size=0.0, tick_size=0.05)
    with pytest.raises(ValueError):
        Instrument(NIFTY, InstrumentKind.INDEX, lot_size=75.0, tick_size=-0.05)
