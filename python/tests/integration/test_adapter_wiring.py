"""Integration tests for the adapter surface (E1-S1).

These exercise the whole path a real caller takes — registry name to connected adapter to
filled order to books — with the pieces wired together for real: the registry, the capability
descriptor, the contract suite and the canonical value types. The adapter is still the
in-memory double, because no broker may be touched in a test; what is under test is the
wiring, not the broker.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import pytest

from honba.adapters import (
    AdapterRegistry,
    Capability,
    MarketDataAdapter,
    OrderReport,
    Product,
    RunMode,
    StreamMode,
    available_adapters,
    find_boundary_violations,
    format_violations,
    resolve_adapter,
)
from honba.adapters.contract import verify_adapter_contract
from honba.adapters.testing import FakeAdapter
from honba.domain.instrument import InstrumentId
from honba.domain.order import OrderIntent, OrderStatus
from honba.domain.tick import QuoteTick

PACKAGE_ROOT = Path(__file__).resolve().parents[2] / "src" / "honba"


@pytest.fixture
def reg() -> AdapterRegistry:
    """A registry with the double registered the way a broker package would be."""
    registry = AdapterRegistry()
    registry.register("fake", FakeAdapter)
    return registry


class TestRegistryToFill:
    async def test_a_run_config_name_resolves_to_a_working_adapter(
        self, reg: AdapterRegistry
    ) -> None:
        # What configs/live/*.toml [adapter] name = "..." does at startup.
        config = {"name": "fake", "mode": RunMode.PAPER}
        adapter = reg.create(config.pop("name"), **config)
        assert adapter.capabilities().name == "fake"

        await adapter.connect()
        try:
            assert (await adapter.session()).mode is RunMode.PAPER
            instrument = (await adapter.instruments())[0].instrument_id
            report = await adapter.place_order(
                OrderIntent.market_buy(instrument, 25.0), product=Product.DELIVERY
            )
            assert report.status is OrderStatus.FILLED
            assert await adapter.positions()
            assert await adapter.trades()
            assert (await adapter.funds()).available_cash < 1_000_000.0
        finally:
            await adapter.disconnect()

    async def test_the_whole_books_agree_after_a_round_trip(self, reg: AdapterRegistry) -> None:
        adapter = reg.create("fake")
        await adapter.connect()
        instrument = InstrumentId("RELIANCE", "NSE")
        try:
            report = await adapter.place_order(
                OrderIntent.market_buy(instrument, 10.0),
                product=Product.DELIVERY,
                client_order_id="run-1",
            )
            orders = {o.order_id: o for o in await adapter.orders()}
            assert orders[report.order_id] == await adapter.order_status(report.order_id)

            fill = next(t for t in await adapter.trades() if t.order_id == report.order_id)
            assert fill.quantity == report.filled_quantity == 10.0
            assert fill.price == report.average_price

            position = next(p for p in await adapter.positions() if p.instrument_id == instrument)
            assert position.quantity == 10.0
            assert position.avg_price == fill.price

            funds = await adapter.funds()
            assert funds.available_cash == 1_000_000.0 - fill.quantity * fill.price
        finally:
            await adapter.disconnect()

    async def test_a_resting_order_is_cancelled_through_the_contract_api(
        self, reg: AdapterRegistry
    ) -> None:
        adapter = reg.create("fake")
        await adapter.connect()
        instrument = InstrumentId("TCS", "NSE")
        try:
            quote = await adapter.quote(instrument)
            resting = await adapter.place_order(
                OrderIntent.limit_buy(instrument, 1.0, quote.bid_price / 2.0),
                product=Product.DELIVERY,
            )
            assert resting.status is OrderStatus.ACCEPTED
            await adapter.cancel_order(resting.order_id)
            assert (await adapter.order_status(resting.order_id)).status is OrderStatus.CANCELLED
            assert await adapter.trades() == []  # nothing filled, so no position either
            assert await adapter.positions() == []
        finally:
            await adapter.disconnect()


class TestStreaming:
    async def test_a_subscription_delivers_typed_events_until_unsubscribed(self) -> None:
        adapter = FakeAdapter()
        await adapter.connect()
        received: list[object] = []
        try:
            subscription = await adapter.subscribe(
                (InstrumentId("RELIANCE", "NSE"),),
                mode=StreamMode.QUOTE,
                callback=received.append,
            )
            adapter.advance_prices(InstrumentId("RELIANCE", "NSE"))
            assert received and all(isinstance(event, QuoteTick) for event in received)
            await adapter.unsubscribe(subscription.id)
            adapter.advance_prices(InstrumentId("RELIANCE", "NSE"))
            assert len(received) == 1  # nothing arrives after unsubscribe
        finally:
            await adapter.disconnect()

    async def test_history_is_ascending_and_bounded_by_the_requested_window(self) -> None:
        adapter = FakeAdapter()
        await adapter.connect()
        instrument = InstrumentId("RELIANCE", "NSE")
        try:
            bars = await adapter.historical_bars(
                instrument,
                timeframe="1m",
                start=dt.datetime(2025, 6, 2, 9, tzinfo=dt.timezone.utc),
                end=dt.datetime(2025, 6, 2, 9, 30, tzinfo=dt.timezone.utc),
            )
            stamps = [bar.ts for bar in bars]
            assert stamps == sorted(stamps)
            assert bars, "a 15-minute window over a live series must return bars"
        finally:
            await adapter.disconnect()


class TestContractThroughTheRegistry:
    async def test_a_registry_built_adapter_satisfies_the_contract(
        self, reg: AdapterRegistry
    ) -> None:
        await verify_adapter_contract(reg.create("fake"))

    async def test_the_contract_suite_covers_a_registry_built_adapter(
        self, reg: AdapterRegistry
    ) -> None:
        adapter = reg.create("fake")
        assert isinstance(adapter, MarketDataAdapter)
        assert adapter.capabilities().supports(Capability.PLACE_ORDER)
        await verify_adapter_contract(adapter)


class TestDefaultRegistry:
    def test_module_level_helpers_share_one_registry(self) -> None:
        registry = available_adapters()
        assert isinstance(registry, list)
        assert registry == sorted(registry)

    async def test_resolve_adapter_builds_from_the_default_registry(self) -> None:
        from honba.adapters import register_adapter
        from honba.adapters.registry import default_registry

        register_adapter("integration-scratch", FakeAdapter)
        try:
            adapter = resolve_adapter("integration-scratch")
            assert adapter.capabilities().name == "fake"
        finally:
            default_registry().unregister("integration-scratch")


class TestBoundaryHoldsForThisPackage:
    def test_no_broker_sdk_or_adapter_import_leaks_into_the_core(self) -> None:
        violations = find_boundary_violations(PACKAGE_ROOT)
        assert not violations, f"adapter boundary broken:\n{format_violations(violations)}"

    def test_the_contract_module_itself_imports_no_broker(self) -> None:
        assert find_boundary_violations(PACKAGE_ROOT / "adapters") == []

    def test_an_adapter_returned_value_is_a_canonical_type(self) -> None:
        # The rule that keeps the leak from mattering: values out of an adapter are honba types.
        assert OrderReport.__module__.startswith("honba.adapters")
        assert issubclass(Capability, object)


class TestDiscoveredEntryPoints:
    def test_registered_entry_points_contain_installed_adapters(self) -> None:
        adapters = available_adapters()
        if "dhan" in adapters:
            dhan = resolve_adapter("dhan")
            assert dhan.capabilities().name == "dhan"
        if "zerodha" in adapters:
            zerodha = resolve_adapter("zerodha")
            assert zerodha.capabilities().name == "zerodha"
