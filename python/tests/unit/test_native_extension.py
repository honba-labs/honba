"""Tests ensuring honba._honba native module is properly built and matches stubs."""
import importlib
import pytest


def test_honba_native_module_imports():
    _honba = importlib.import_module("honba._honba")
    assert hasattr(_honba, "Bar")
    assert hasattr(_honba, "Fill")
    assert hasattr(_honba, "InstrumentId")
    assert hasattr(_honba, "OrderIntent")
    assert hasattr(_honba, "QuoteTick")
    assert hasattr(_honba, "RustSmaCrossover")


def test_bar_roundtrip():
    _honba = importlib.import_module("honba._honba")
    bar = _honba.Bar("RELIANCE", 100, 10.0, 15.0, 9.0, 12.0, 500.0, "NSE")
    assert bar.symbol == "RELIANCE"
    assert bar.venue == "NSE"
    assert bar.open == 10.0
    assert bar.close == 12.0
    assert repr(bar) == "<Bar RELIANCE ts=100 close=12>"


def test_quote_tick_mid_price():
    _honba = importlib.import_module("honba._honba")
    tick = _honba.QuoteTick("NIFTY50", 100.0, 102.0, 10.0, 10.0, 12345, "NSE")
    assert tick.mid_price == pytest.approx(101.0)
    assert tick.symbol == "NIFTY50"


def test_rust_sma_crossover():
    _honba = importlib.import_module("honba._honba")
    sma = _honba.RustSmaCrossover(fast=2, slow=3, qty=5.0)
    assert sma.position == 0.0
    assert sma.intent_count == 0
