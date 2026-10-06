"""Broker adapters: the contract, the canonical value types, the registry and the test double.

Import from here, never from the modules directly::

    from honba.adapters import Adapter, ExecutionAdapter, MarketDataAdapter, resolve_adapter

The contract is three declarations in :mod:`honba.adapters.base` (a lifecycle facade plus a
market-data role and an execution role), the values an adapter may return live in
:mod:`honba.adapters.models`, and :mod:`honba.adapters.capabilities` says up front what an
adapter can do. :mod:`honba.adapters.contract` is the suite every adapter must pass, and
:mod:`honba.adapters.testing.FakeAdapter` is the in-memory reference implementation.

Broker implementations live in the separate ``honba-adapters`` repository, installed as
packages that advertise themselves through the ``honba.adapters`` entry-point group.
"""

from honba.adapters.base import (
    Adapter,
    ExecutionAdapter,
    ExecutionClient,
    MarketDataAdapter,
    MarketDataClient,
)
from honba.adapters.boundary import (
    BoundaryViolation,
    find_boundary_violations,
    format_violations,
)
from honba.adapters.capabilities import (
    AdapterCapabilities,
    Capability,
    capability_for_method,
)
from honba.adapters.errors import (
    AdapterError,
    AdapterFatalError,
    AdapterNotFound,
    CapabilityError,
    SessionError,
    SessionExpired,
)
from honba.adapters.models import (
    DepthLevel,
    Funds,
    Holding,
    MarginReport,
    MarketDepth,
    OrderReport,
    Product,
    RunMode,
    SessionInfo,
    StreamCallback,
    StreamEvent,
    StreamMode,
    Subscription,
)
from honba.adapters.registry import (
    ENTRY_POINT_GROUP,
    AdapterFactory,
    AdapterRegistry,
    available_adapters,
    default_registry,
    register_adapter,
    resolve_adapter,
    resolve_execution_adapter,
    resolve_market_data_adapter,
)

__all__ = [
    "ENTRY_POINT_GROUP",
    "Adapter",
    "AdapterCapabilities",
    "AdapterError",
    "AdapterFactory",
    "AdapterFatalError",
    "AdapterNotFound",
    "AdapterRegistry",
    "BoundaryViolation",
    "Capability",
    "CapabilityError",
    "DepthLevel",
    "ExecutionAdapter",
    "ExecutionClient",
    "Funds",
    "Holding",
    "MarginReport",
    "MarketDataAdapter",
    "MarketDataClient",
    "MarketDepth",
    "OrderReport",
    "Product",
    "RunMode",
    "SessionError",
    "SessionExpired",
    "SessionInfo",
    "StreamCallback",
    "StreamEvent",
    "StreamMode",
    "Subscription",
    "available_adapters",
    "capability_for_method",
    "default_registry",
    "find_boundary_violations",
    "format_violations",
    "register_adapter",
    "resolve_adapter",
    "resolve_execution_adapter",
    "resolve_market_data_adapter",
]
