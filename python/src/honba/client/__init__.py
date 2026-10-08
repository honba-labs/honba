"""Python SDK client for the REST read API, over two interchangeable transports.

``Client`` exposes typed methods that mirror the served endpoints and return the existing wire
models (bars and quotes with :class:`~honba.wire.wire.UnixNanos` timestamps, ADR 0012 records
that ignore unknown fields). The same calls work against a running server and in process::

    from honba.client import Client, NotFoundApiError

    # A running `honba serve --data-dir ./bars`
    with Client.http("http://127.0.0.1:8080", timeout=5) as api:
        for bar in api.bars("TCS.NSE", tf="1m", from_="2024-01-01", to="2024-01-02"):
            print(bar.ts_event.to_ns(), bar.close)

    # No server: the same Rust router, driven in process (needs the native extension)
    api = Client.inproc("./bars")
    quotes = api.quotes(["TCS"], venue="NSE")
    try:
        api.instrument("NOPE.NSE")
    except NotFoundApiError as err:
        err.code, err.category, err.retryable   # "instrument_not_found", "not_found", False

Failures are typed (:class:`ApiError` and subclasses, built from the ``ErrorDetail`` envelope:
``code``, ``category``, ``retryable``, ``context``); a malformed argument raises
:class:`RequestValidationError` before anything is sent. Retries exist only on
:class:`HttpTransport`, are off by default and bounded (:class:`RetryPolicy`).

Served today: ``health``, ``capabilities``, ``schema``, ``instruments``, ``instrument``, ``bars``,
``quotes``, ``depth``, ``verify_strategy``, ``strategies``, ``compile_strategy`` and
``screener_scan``, plus the run routes (ADR 0017): ``submit_backtest``, ``backtest``,
``backtest_journal``, ``wait`` and ``run_backtest``. A run needs a journals root: pass
``journals_dir=`` to :meth:`Client.inproc` (or serve with ``--journals-dir``). A failed run is a
normal result with a typed ``error``; ``result.assumptions.not_modelled`` lists what the Rust
executor does not model. Every other route answers 501 (``NotImplementedApiError``) and has no
client method yet. Money (ADR 0011) does not appear: these endpoints carry prices as ``f64``.
"""

from honba.client.client import Client
from honba.client.errors import (
    ApiError,
    AuthApiError,
    InternalApiError,
    InvalidResponseError,
    MarketDataUnavailableApiError,
    NotFoundApiError,
    NotImplementedApiError,
    OrderRejectedApiError,
    RateLimitedApiError,
    RequestValidationError,
    RiskApiError,
    RunTimeoutError,
    TransportApiError,
    UnsupportedApiError,
    ValidationApiError,
    error_from_envelope,
)
from honba.client.models import (
    Assumptions,
    BacktestMetrics,
    BacktestResult,
    CapabilityManifest,
    CompiledStrategy,
    Depth,
    DepthLevel,
    ErrorDetail,
    Health,
    InstrumentInfo,
    RunStatus,
    ScreenerResultRow,
)
from honba.client.requests import ApiRequest, TimeLike
from honba.client.transport import HttpTransport, InprocTransport, Response, RetryPolicy, Transport

__all__ = [
    "ApiError",
    "ApiRequest",
    "Assumptions",
    "AuthApiError",
    "BacktestMetrics",
    "BacktestResult",
    "CapabilityManifest",
    "Client",
    "CompiledStrategy",
    "Depth",
    "DepthLevel",
    "ErrorDetail",
    "Health",
    "HttpTransport",
    "InprocTransport",
    "InstrumentInfo",
    "InternalApiError",
    "InvalidResponseError",
    "MarketDataUnavailableApiError",
    "NotFoundApiError",
    "NotImplementedApiError",
    "OrderRejectedApiError",
    "RateLimitedApiError",
    "RequestValidationError",
    "Response",
    "RetryPolicy",
    "RiskApiError",
    "RunStatus",
    "RunTimeoutError",
    "ScreenerResultRow",
    "TimeLike",
    "Transport",
    "TransportApiError",
    "UnsupportedApiError",
    "ValidationApiError",
    "error_from_envelope",
]
