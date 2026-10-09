"""Trailing stop is opt-in per adapter, enforced through the real adapter flow."""

from __future__ import annotations

import pytest

from honba.adapters.capabilities import AdapterCapabilities
from honba.adapters.contract import ContractViolation, verify_adapter_contract
from honba.adapters.errors import CapabilityError
from honba.adapters.models import Product
from honba.adapters.testing import FakeAdapter
from honba.domain.instrument import InstrumentId
from honba.domain.order import OrderIntent, OrderType

RELIANCE = InstrumentId("RELIANCE", "NSE")


class TrailingFake(FakeAdapter):
    """A fake that declares the trailing stop (it cannot execute it)."""

    def capabilities(self) -> AdapterCapabilities:
        base = super().capabilities()
        return AdapterCapabilities(
            name=base.name,
            exchanges=base.exchanges,
            products=base.products,
            order_types=base.order_types | {OrderType.TRAILING_STOP},
            stream_modes=base.stream_modes,
            price_types=base.price_types,
            features=base.features,
        )


class LyingFake(FakeAdapter):
    """Does not declare the type but accepts it silently: the contract must catch this."""

    def __init__(self) -> None:
        super().__init__()
        self.placed = 0

    async def place_order(self, intent, *, product, client_order_id=None):  # type: ignore[override]
        if intent.order_type is OrderType.TRAILING_STOP:
            self.placed += 1
            return await super().place_order(
                OrderIntent.market_buy(intent.instrument_id, intent.quantity),
                product=product,
                client_order_id=client_order_id,
            )
        return await super().place_order(intent, product=product, client_order_id=client_order_id)


async def test_adapter_without_the_capability_refuses_and_sends_nothing() -> None:
    adapter = FakeAdapter()
    await adapter.connect()
    try:
        with pytest.raises(CapabilityError, match="trailing_stop"):
            await adapter.place_order(
                OrderIntent.trailing_stop_sell(RELIANCE, 1, trail_percent=2.0),
                product=Product.DELIVERY,
            )
        assert await adapter.orders() == []
    finally:
        await adapter.disconnect()


async def test_contract_passes_for_adapter_without_the_capability() -> None:
    await verify_adapter_contract(FakeAdapter())


async def test_adapter_listing_the_type_passes_capability_checks_and_contract() -> None:
    adapter = TrailingFake()
    caps = adapter.capabilities()
    caps.require_order_type(OrderType.TRAILING_STOP)
    await verify_adapter_contract(adapter)


async def test_contract_catches_an_adapter_that_accepts_an_undeclared_type() -> None:
    adapter = LyingFake()
    with pytest.raises(ContractViolation, match="trailing_stop"):
        await verify_adapter_contract(adapter)
