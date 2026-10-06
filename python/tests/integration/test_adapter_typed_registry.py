"""Integration: a typed registry helper feeds a real fetch through the in-memory adapter."""

from __future__ import annotations

from honba.adapters import AdapterRegistry
from honba.adapters.testing import FakeAdapter


async def test_create_market_data_to_instrument_search() -> None:
    reg = AdapterRegistry()
    reg.register("fake", FakeAdapter)
    adapter = reg.create_market_data("fake")
    await adapter.connect()
    everything = await adapter.instruments()
    assert everything
    symbol = everything[0].instrument_id.symbol
    found = await adapter.search_instruments(symbol)
    assert everything[0].instrument_id in {i.instrument_id for i in found}
    await adapter.disconnect()


async def test_create_execution_to_books() -> None:
    reg = AdapterRegistry()
    reg.register("fake", FakeAdapter)
    adapter = reg.create_execution("fake")
    await adapter.connect()
    assert await adapter.positions() == []
    await adapter.disconnect()
