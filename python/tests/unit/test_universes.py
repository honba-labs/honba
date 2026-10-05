from honba.entities.instrument import InstrumentId
from honba.markets.india.universes import (
    NIFTY_200_ALPHA_30_SYMBOLS,
    resolve_universe,
)


def test_nifty_200_alpha_30_symbols():
    assert len(NIFTY_200_ALPHA_30_SYMBOLS) == 30
    assert len(set(NIFTY_200_ALPHA_30_SYMBOLS)) == 30
    assert "RELIANCE" not in NIFTY_200_ALPHA_30_SYMBOLS
    assert "HINDALCO" in NIFTY_200_ALPHA_30_SYMBOLS
    assert "POWERINDIA" in NIFTY_200_ALPHA_30_SYMBOLS


def test_resolve_universe_alpha30():
    insts = resolve_universe("nifty200_alpha_30")
    assert len(insts) == 30
    assert all(isinstance(i, InstrumentId) for i in insts)
    assert all(i.exchange == "NSE" for i in insts)

    # Alternate naming aliases
    assert resolve_universe("nifty200_alpha30") == insts
    assert resolve_universe("nifty200-alpha-30") == insts
    assert resolve_universe("alpha30") == insts
