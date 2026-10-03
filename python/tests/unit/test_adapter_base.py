"""Unit tests for the adapter contract itself: the facade ABC, the two role protocols and
the method-to-capability table that keeps them honest (E1-S1).
"""

from __future__ import annotations

import inspect

import pytest

from honba.adapters.base import Adapter, ExecutionAdapter, MarketDataAdapter
from honba.adapters.capabilities import _METHOD_CAPABILITIES, Capability
from honba.adapters.errors import AdapterError, CapabilityError
from honba.adapters.testing import FakeAdapter

_ROLE_PROTOCOLS = (Adapter, MarketDataAdapter, ExecutionAdapter)


def _public_methods(protocol: type) -> set[str]:
    return {
        name
        for name, value in vars(protocol).items()
        if not name.startswith("_") and (callable(value) or isinstance(value, property))
    }


class TestAdapterFacade:
    def test_is_abstract(self) -> None:
        assert inspect.isabstract(Adapter)
        with pytest.raises(TypeError):
            Adapter()  # type: ignore[abstract]

    def test_role_protocols_are_runtime_checkable(self) -> None:
        adapter = FakeAdapter()
        assert isinstance(adapter, MarketDataAdapter)
        assert isinstance(adapter, ExecutionAdapter)

    async def test_require_connected_raises_before_connect_and_passes_after(self) -> None:
        adapter = FakeAdapter()
        with pytest.raises(AdapterError, match="not connected"):
            adapter.require_connected()
        await adapter.connect()
        try:
            assert adapter.require_connected() is None
        finally:
            await adapter.disconnect()

    def test_require_capabilities_delegates_to_the_descriptor(self) -> None:
        adapter = FakeAdapter()
        caps = adapter.capabilities()
        adapter.require_capabilities(*sorted(caps.features, key=lambda c: c.value))
        missing = next(c for c in Capability if c not in caps.features)
        with pytest.raises(CapabilityError, match=missing.value):
            adapter.require_capabilities(missing)

    def test_capabilities_name_matches_the_registry_key(self) -> None:
        assert FakeAdapter(name="zerodha").capabilities().name == "zerodha"

    async def test_session_is_the_fact_connect_returned(self) -> None:
        adapter = FakeAdapter()
        returned = await adapter.connect()
        try:
            assert await adapter.session() == returned
        finally:
            await adapter.disconnect()

    async def test_connecting_twice_is_a_programming_error(self) -> None:
        adapter = FakeAdapter()
        await adapter.connect()
        try:
            with pytest.raises(AdapterError, match="already connected"):
                await adapter.connect()
        finally:
            await adapter.disconnect()

    async def test_disconnecting_twice_is_a_programming_error(self) -> None:
        adapter = FakeAdapter()
        await adapter.connect()
        await adapter.disconnect()
        with pytest.raises(AdapterError, match="not connected"):
            await adapter.disconnect()


def _declared_methods() -> set[str]:
    return {name for protocol in _ROLE_PROTOCOLS for name in _public_methods(protocol)}


class TestMethodCapabilityTable:
    def test_every_public_method_of_every_protocol_has_an_entry(self) -> None:
        missing = _declared_methods() - set(_METHOD_CAPABILITIES)
        assert not missing, f"contract methods missing from the table: {missing}"

    def test_no_orphan_entries_in_the_table(self) -> None:
        stale = set(_METHOD_CAPABILITIES) - _declared_methods()
        assert not stale, f"table names methods no protocol declares: {stale}"

    def test_the_two_roles_cover_the_whole_broker_function_set(self) -> None:
        market_data = _public_methods(MarketDataAdapter)
        execution = _public_methods(ExecutionAdapter)
        assert {"quote", "depth", "historical_bars", "instruments"} <= market_data
        assert {
            "place_order",
            "modify_order",
            "cancel_order",
            "cancel_all",
            "order_status",
            "orders",
            "trades",
            "positions",
            "holdings",
            "funds",
            "margin",
        } <= execution

    def test_lifecycle_methods_need_no_capability(self) -> None:
        for name in (
            "connect",
            "disconnect",
            "session",
            "is_connected",
            "capabilities",
            "require_connected",
            "require_capabilities",
        ):
            assert _METHOD_CAPABILITIES[name] is None
