"""Unit tests for the adapter registry and its lazy entry-point discovery (E1-S1)."""

from __future__ import annotations

from importlib.metadata import EntryPoint

import pytest

from honba.adapters import registry as registry_module
from honba.adapters.base import Adapter
from honba.adapters.errors import AdapterError, AdapterNotFound
from honba.adapters.registry import (
    ENTRY_POINT_GROUP,
    AdapterRegistry,
    available_adapters,
    default_registry,
    register_adapter,
    resolve_adapter,
)
from honba.adapters.testing import FakeAdapter


@pytest.fixture
def reg() -> AdapterRegistry:
    return AdapterRegistry()


@pytest.fixture
def no_entry_points(monkeypatch: pytest.MonkeyPatch) -> None:
    """Isolate every test from adapters installed in the environment."""
    monkeypatch.setattr(registry_module, "entry_points", lambda group: ())


class TestExplicitRegistration:
    def test_register_then_get_returns_the_factory(self, reg: AdapterRegistry) -> None:
        reg.register("fake", FakeAdapter)
        assert reg.get("fake") is FakeAdapter

    def test_create_passes_configuration_to_the_factory(
        self, reg: AdapterRegistry, no_entry_points: None
    ) -> None:
        reg.register("fake", FakeAdapter)
        adapter = reg.create("fake", name="dhan")
        assert isinstance(adapter, Adapter)
        assert adapter.capabilities().name == "dhan"

    def test_available_is_sorted(self, reg: AdapterRegistry, no_entry_points: None) -> None:
        reg.register("zerodha", FakeAdapter)
        reg.register("dhan", FakeAdapter)
        assert reg.available() == ["dhan", "zerodha"]

    def test_unknown_name_raises_and_lists_what_is_available(
        self, reg: AdapterRegistry, no_entry_points: None
    ) -> None:
        reg.register("dhan", FakeAdapter)
        with pytest.raises(AdapterNotFound) as excinfo:
            reg.get("upstox")
        message = str(excinfo.value)
        assert "upstox" in message and "dhan" in message

    def test_duplicate_registration_is_refused_by_default(self, reg: AdapterRegistry) -> None:
        reg.register("fake", FakeAdapter)
        with pytest.raises(AdapterError, match="already registered"):
            reg.register("fake", FakeAdapter)
        reg.register("fake", FakeAdapter, replace=True)
        assert reg.get("fake") is FakeAdapter

    def test_unregister_forgets_an_adapter(
        self, reg: AdapterRegistry, no_entry_points: None
    ) -> None:
        reg.register("fake", FakeAdapter)
        reg.unregister("fake")
        assert reg.available() == []
        with pytest.raises(AdapterNotFound):
            reg.get("fake")

    def test_a_factory_that_returns_the_wrong_type_is_rejected(
        self, reg: AdapterRegistry, no_entry_points: None
    ) -> None:
        reg.register("broken", lambda: "not an adapter")  # type: ignore[arg-type]
        with pytest.raises(AdapterError, match="not an Adapter"):
            reg.create("broken")


class TestEntryPointDiscovery:
    def test_discovers_installed_adapters_lazily(self, monkeypatch: pytest.MonkeyPatch) -> None:
        calls: list[str] = []

        def fake_entry_points(group: str) -> tuple[EntryPoint, ...]:
            calls.append(group)
            return (EntryPoint("fake", "honba.adapters.testing:FakeAdapter", group),)

        monkeypatch.setattr(registry_module, "entry_points", fake_entry_points)
        reg = AdapterRegistry()
        assert calls == [], "entry points must not be read before they are needed"
        assert reg.available() == ["fake"]
        assert calls == [ENTRY_POINT_GROUP]

    def test_discovery_happens_once(self, monkeypatch: pytest.MonkeyPatch) -> None:
        calls: list[str] = []
        monkeypatch.setattr(
            registry_module,
            "entry_points",
            lambda group: (calls.append(group), ())[1],
        )
        reg = AdapterRegistry()
        reg.available()
        reg.available()
        with pytest.raises(AdapterNotFound):
            reg.get("missing")
        assert calls == [ENTRY_POINT_GROUP]

    def test_explicit_registration_wins_over_an_entry_point(
        self, monkeypatch: pytest.MonkeyPatch, no_entry_points: None
    ) -> None:
        monkeypatch.setattr(
            registry_module,
            "entry_points",
            lambda group: (EntryPoint("fake", "honba.adapters.testing:FakeAdapter", group),),
        )

        def replacement() -> Adapter:
            return FakeAdapter(name="explicit")

        reg = AdapterRegistry()
        reg.register("fake", replacement)
        assert reg.create("fake").capabilities().name == "explicit"

    def test_a_broken_entry_point_does_not_hide_the_working_ones(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            registry_module,
            "entry_points",
            lambda group: (
                EntryPoint("broken", "honba.adapters.testing:DoesNotExist", group),
                EntryPoint("fake", "honba.adapters.testing:FakeAdapter", group),
            ),
        )
        reg = AdapterRegistry()
        assert reg.available() == ["fake"]
        assert "broken" in reg.discovery_errors()

    def test_discovery_errors_are_reported_for_a_named_adapter(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            registry_module,
            "entry_points",
            lambda group: (EntryPoint("broken", "honba.adapters.testing:DoesNotExist", group),),
        )
        reg = AdapterRegistry()
        with pytest.raises(AdapterNotFound, match="broken"):
            reg.get("broken")


class TestModuleLevelHelpers:
    def test_default_registry_is_the_process_wide_one(self) -> None:
        assert default_registry() is default_registry()

    def test_helpers_operate_on_the_default_registry(self, no_entry_points: None) -> None:
        default_registry().unregister("scratch")
        register_adapter("scratch", FakeAdapter)
        try:
            assert "scratch" in available_adapters()
            assert resolve_adapter("scratch").capabilities().name == "fake"
        finally:
            default_registry().unregister("scratch")
