"""The adapter registry (E1-S1): name to factory, with lazy entry-point discovery.

Mirrors ``honba_market::MarketRegistry`` on the Rust side, so the shape of plugin lookup is
the same in both layers: register explicitly, resolve by name, list what is installed.

Discovery reads the ``honba.adapters`` entry-point group the first time it is needed, not
at import time, so importing :mod:`honba` never touches installed distributions. An adapter
package opts in from its own ``pyproject.toml``::

    [project.entry-points."honba.adapters"]
    dhan = "honba_dhan:DhanAdapter"

Explicit registration always wins over discovery, so a config file or a test can override an
installed adapter without uninstalling it. A broken entry point is recorded in
:func:`AdapterRegistry.discovery_errors` instead of hiding the adapters that do load.
"""

from __future__ import annotations

from collections.abc import Callable
from importlib.metadata import entry_points
from typing import Any

from honba.adapters.base import Adapter, ExecutionClient, MarketDataClient
from honba.adapters.errors import AdapterError, AdapterNotFound

__all__ = [
    "ENTRY_POINT_GROUP",
    "AdapterFactory",
    "AdapterRegistry",
    "available_adapters",
    "default_registry",
    "register_adapter",
    "resolve_adapter",
    "resolve_execution_adapter",
    "resolve_market_data_adapter",
]

#: Entry-point group adapter packages advertise themselves under.
ENTRY_POINT_GROUP = "honba.adapters"

#: Builds an adapter from configuration. Called as ``factory(**config)``.
AdapterFactory = Callable[..., Adapter]


class AdapterRegistry:
    """A name-to-factory map with lazy entry-point discovery.

    Not thread-safe: build and register adapters at startup, then read. Per-adapter state
    (sessions, transports) belongs to the adapter, never to the registry.
    """

    def __init__(self) -> None:
        self._factories: dict[str, AdapterFactory] = {}
        self._explicit: set[str] = set()
        self._discovered_names: set[str] = set()
        self._discovered = False
        self._errors: dict[str, str] = {}

    def register(self, name: str, factory: AdapterFactory, *, replace: bool = False) -> None:
        """Register ``factory`` under ``name``.

        Raises :class:`AdapterError` on a duplicate unless ``replace`` is set, because a
        silent override makes a run's adapter untraceable.
        """
        if not callable(factory):
            raise AdapterError(f"adapter factory for {name!r} is not callable")
        if name in self._factories and not replace:
            raise AdapterError(f"adapter {name!r} is already registered")
        self._factories[name] = factory
        self._explicit.add(name)
        self._explicit.add(name)

    def unregister(self, name: str) -> None:
        """Forget ``name``. Unknown names are ignored, so teardown cannot fail."""
        self._factories.pop(name, None)
        self._explicit.discard(name)
        self._discovered_names.discard(name)
        self._errors.pop(name, None)

    def discover(self, *, refresh: bool = False) -> None:
        """Load entry points once; a second call is a no-op unless ``refresh`` is set.

        On ``refresh`` every name previously *discovered* (not explicitly registered) is
        forgotten before re-scanning, so an uninstalled adapter does not survive a refresh.
        """
        if self._discovered and not refresh:
            return
        self._discovered = True
        if refresh:
            for name in list(self._discovered_names):
                if name not in self._explicit:
                    self._factories.pop(name, None)
                    self._errors.pop(name, None)
            self._discovered_names.clear()
            self._errors.clear()
        for entry in entry_points(group=ENTRY_POINT_GROUP):
            if entry.name in self._factories:
                continue  # explicit registration wins
            try:
                loaded = entry.load()
            except Exception as error:  # noqa: BLE001 - a broken install must not hide the rest
                self._errors[entry.name] = f"{type(error).__name__}: {error}"
                continue
            if not callable(loaded):
                self._errors[entry.name] = f"{entry.value} is not callable"
                continue
            self._factories[entry.name] = loaded

    def discovery_errors(self) -> dict[str, str]:
        """Entry points that failed to load, mapped to the reason."""
        self.discover()
        return dict(self._errors)

    def get(self, name: str) -> AdapterFactory:
        """The factory for ``name``.

        Raises :class:`AdapterNotFound` naming the installed alternatives, which is the
        difference between a typo and a missing install.
        """
        self.discover()
        try:
            return self._factories[name]
        except KeyError:
            installed = ", ".join(sorted(self._factories)) or "none"
            detail = f" (entry point failed: {self._errors[name]})" if name in self._errors else ""
            raise AdapterNotFound(
                f"no adapter registered as {name!r}; installed adapters: {installed}{detail}"
            ) from None

    def create(self, name: str, /, **config: Any) -> Adapter:
        """Build the adapter called ``name``, passing ``config`` to its factory."""
        adapter = self.get(name)(**config)
        if not isinstance(adapter, Adapter):
            raise AdapterError(
                f"factory for adapter {name!r} returned {type(adapter).__name__}, not an Adapter"
            )
        return adapter

    def create_market_data(self, name: str, /, **config: Any) -> MarketDataClient:
        """Like :meth:`create`, typed as an adapter with market data (``instruments``, ``quote``...).

        Raises :class:`AdapterError` if the adapter does not implement market data.
        """
        adapter = self.create(name, **config)
        if not isinstance(adapter, MarketDataClient):
            raise AdapterError(f"adapter {name!r} does not implement market data")
        return adapter

    def create_execution(self, name: str, /, **config: Any) -> ExecutionClient:
        """Like :meth:`create`, typed as an adapter with execution (``place_order``...).

        Raises :class:`AdapterError` if the adapter does not implement execution.
        """
        adapter = self.create(name, **config)
        if not isinstance(adapter, ExecutionClient):
            raise AdapterError(f"adapter {name!r} does not implement execution")
        return adapter

    def available(self) -> list[str]:
        """Sorted names of every registered and discoverable adapter."""
        self.discover()
        return sorted(self._factories)


_DEFAULT_REGISTRY = AdapterRegistry()


def default_registry() -> AdapterRegistry:
    """The process-wide registry used by the helpers below."""
    return _DEFAULT_REGISTRY


def register_adapter(name: str, factory: AdapterFactory, *, replace: bool = False) -> None:
    """Register an adapter in the default registry."""
    _DEFAULT_REGISTRY.register(name, factory, replace=replace)


def resolve_adapter(name: str, /, **config: Any) -> Adapter:
    """Build a registered adapter by name; how a run config's ``adapter.name`` is honoured."""
    return _DEFAULT_REGISTRY.create(name, **config)


def resolve_market_data_adapter(name: str, /, **config: Any) -> MarketDataClient:
    """:func:`resolve_adapter`, typed and checked as a market-data adapter."""
    return _DEFAULT_REGISTRY.create_market_data(name, **config)


def resolve_execution_adapter(name: str, /, **config: Any) -> ExecutionClient:
    """:func:`resolve_adapter`, typed and checked as an execution adapter."""
    return _DEFAULT_REGISTRY.create_execution(name, **config)


def available_adapters() -> list[str]:
    """Sorted names of the adapters this installation can use."""
    return _DEFAULT_REGISTRY.available()
