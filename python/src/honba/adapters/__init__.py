"""Broker adapters: the contract, the canonical value types, the registry and the test double.

Import from here, never from the modules directly::

    from honba.adapters import Adapter, ExecutionAdapter, MarketDataAdapter, resolve_adapter

The contract is three declarations in :mod:`honba.adapters.base` (a lifecycle facade plus a
market-data role and an execution role), the values an adapter may return live in
:mod:`honba.adapters.models`, and :mod:`honba.adapters.capabilities` says up front what an
adapter can do. :mod:`honba.adapters.contract` is the suite every adapter must pass, and
:mod:`honba.adapters.testing.FakeAdapter` is the in-memory reference implementation.

The shared runtime pieces live here too: :mod:`honba.adapters.session` owns token expiry and
refresh, :mod:`honba.adapters.rate_limit` a token-bucket limiter that holds across workers,
and :mod:`honba.adapters.secrets` an encrypted at-rest secret store (E1-S5).

Broker implementations live in the separate ``honba-adapters`` repository, installed as
packages that advertise themselves through the ``honba.adapters`` entry-point group.
"""

from honba.adapters.base import (
    Adapter,
    ExecutionAdapter,
    ExecutionClient,
    FullAdapterClient,
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
from honba.adapters.order_events import ReportReduction, reduce_order_report
from honba.adapters.rate_limit import (
    BucketState,
    FileBackedBucket,
    InMemoryBucket,
    RateLimiter,
    RateLimiterRegistry,
    RateLimitExceeded,
    SharedBucketStore,
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
    resolve_full_adapter,
    resolve_market_data_adapter,
)
from honba.adapters.secrets import FileSecretStore, SecretStore
from honba.adapters.session import (
    DEFAULT_REFRESH_MARGIN_NS,
    Clock,
    SessionProvider,
    Token,
    TokenSession,
)

__all__ = [
    "DEFAULT_REFRESH_MARGIN_NS",
    "ENTRY_POINT_GROUP",
    "Adapter",
    "AdapterCapabilities",
    "AdapterError",
    "AdapterFactory",
    "AdapterFatalError",
    "AdapterNotFound",
    "AdapterRegistry",
    "BoundaryViolation",
    "BucketState",
    "Capability",
    "CapabilityError",
    "Clock",
    "DepthLevel",
    "ExecutionAdapter",
    "ExecutionClient",
    "FileBackedBucket",
    "FileSecretStore",
    "FullAdapterClient",
    "Funds",
    "Holding",
    "InMemoryBucket",
    "MarginReport",
    "MarketDataAdapter",
    "MarketDataClient",
    "MarketDepth",
    "OrderReport",
    "Product",
    "RateLimitExceeded",
    "RateLimiter",
    "RateLimiterRegistry",
    "ReportReduction",
    "RunMode",
    "SecretStore",
    "SessionError",
    "SessionExpired",
    "SessionInfo",
    "SessionProvider",
    "SharedBucketStore",
    "StreamCallback",
    "StreamEvent",
    "StreamMode",
    "Subscription",
    "Token",
    "TokenSession",
    "available_adapters",
    "capability_for_method",
    "default_registry",
    "find_boundary_violations",
    "format_violations",
    "reduce_order_report",
    "register_adapter",
    "resolve_adapter",
    "resolve_execution_adapter",
    "resolve_full_adapter",
    "resolve_market_data_adapter",
]
