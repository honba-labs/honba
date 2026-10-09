"""Typed errors of the REST client, built from the ``ErrorDetail`` envelope.

A failed call carries ``{"error": {"code", "message", "retryable", "context"}}`` (the Rust
``honba_messages::ErrorDetail``). :func:`error_from_envelope` turns it into an :class:`ApiError`
subclass chosen by ``code``; ``category`` is derived from the code with the same fixed table as
Rust's ``ErrorCode::category`` (the envelope does not carry it), and ``retryable`` is the
server's own flag, never inferred from the class.

An unknown code (a newer server) stays a plain :class:`ApiError` that keeps the raw code
instead of being mapped onto a known one. A failure whose body is not an envelope at all is an
:class:`InvalidResponseError`.

:class:`RequestValidationError` is different: it is raised on the client, before anything is
sent, and mirrors the server's ``validation_invalid_request`` detail (``field``, ``reason``).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

__all__ = [
    "CATEGORY_OF_CODE",
    "ERROR_CLASS_OF_CODE",
    "ApiError",
    "AuthApiError",
    "InternalApiError",
    "InvalidResponseError",
    "MarketDataUnavailableApiError",
    "NotFoundApiError",
    "NotImplementedApiError",
    "OrderRejectedApiError",
    "RateLimitedApiError",
    "RequestValidationError",
    "RiskApiError",
    "RunTimeoutError",
    "TransportApiError",
    "UnsupportedApiError",
    "ValidationApiError",
    "error_from_envelope",
]


class RequestValidationError(ValueError):
    """A request that was rejected before sending. ``field`` names the argument (the same
    names the server uses in ``context.field``) and ``reason`` is a stable code."""

    def __init__(self, field: str, reason: str, message: str) -> None:
        super().__init__(message)
        self.field = field
        self.reason = reason


class ApiError(Exception):
    """A failed API call: the envelope's ``code``/``message``/``retryable``/``context`` plus the
    HTTP ``status`` (``None`` when no response arrived) and the derived ``category``."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        status: int | None = None,
        retryable: bool = False,
        context: Any = None,
        category: str | None = None,
        api_version: str | None = None,
    ) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message
        self.status = status
        self.retryable = retryable
        self.context = context
        self.category = category if category is not None else CATEGORY_OF_CODE.get(code, "unknown")
        self.api_version = api_version


class ValidationApiError(ApiError):
    """``validation_invalid_request``: the server rejected the request (HTTP 422)."""


class NotFoundApiError(ApiError):
    """``not_found``, ``instrument_not_found`` or ``order_not_found`` (HTTP 404)."""


class AuthApiError(ApiError):
    """``unauthorized`` or ``forbidden``."""


class RateLimitedApiError(ApiError):
    """``rate_limited``."""


class RiskApiError(ApiError):
    """A ``risk_*`` refusal (HTTP 422)."""


class OrderRejectedApiError(ApiError):
    """``order_rejected`` or ``order_execution_unavailable``."""


class MarketDataUnavailableApiError(ApiError):
    """``market_data_unavailable``: the source has no such data (e.g. no depth, no quote)."""


class TransportApiError(ApiError):
    """``timeout`` / ``transport_error``: reported by the server, or raised by the HTTP
    transport when no response arrived (``status`` is then ``None``)."""


class InternalApiError(ApiError):
    """``internal_error``."""


class UnsupportedApiError(ApiError):
    """``unsupported``: not available on this surface or build."""


class NotImplementedApiError(UnsupportedApiError):
    """``not_implemented``: the route is in the contract but not built yet (HTTP 501)."""


class InvalidResponseError(ApiError):
    """The response is not what the contract promises (not an envelope, or a payload that
    does not parse). ``code`` is ``invalid_response``; the cause, if any, is chained."""

    def __init__(self, message: str, *, status: int | None = None) -> None:
        super().__init__("invalid_response", message, status=status, category="internal")


class RunTimeoutError(Exception):
    """``Client.wait`` gave up before the run reached a terminal status.

    The run itself is untouched and still addressable: ``run_id`` and the last polled state
    (``last``, a :class:`~honba.client.BacktestResult`) are kept so the caller can poll on.
    """

    def __init__(self, run_id: str, last: Any, timeout: float) -> None:
        super().__init__(f"run {run_id} still {last.status} after {timeout:g}s")
        self.run_id = run_id
        self.last = last
        self.timeout = timeout


CATEGORY_OF_CODE: Mapping[str, str] = {
    "validation_invalid_request": "validation",
    "not_found": "not_found",
    "order_not_found": "not_found",
    "instrument_not_found": "not_found",
    "unauthorized": "auth",
    "forbidden": "auth",
    "rate_limited": "rate_limit",
    "risk_max_notional_exceeded": "risk",
    "risk_max_position_exceeded": "risk",
    "risk_max_drawdown_exceeded": "risk",
    "risk_trading_halted": "risk",
    "risk_order_rate_exceeded": "risk",
    "risk_quantity_below_min": "risk",
    "risk_quantity_over_freeze": "risk",
    "risk_lot_multiple_violation": "risk",
    "risk_tick_size_violation": "risk",
    "risk_price_band_exceeded": "risk",
    "risk_reduce_only_violation": "risk",
    "risk_instrument_unknown": "risk",
    "risk_max_participation_exceeded": "risk",
    "risk_feed_stale": "risk",
    "order_rejected": "order",
    "order_execution_unavailable": "order",
    "market_data_unavailable": "market_data",
    "timeout": "transport",
    "transport_error": "transport",
    "internal_error": "internal",
    "unsupported": "unsupported",
    "not_implemented": "unsupported",
}
"""``honba_messages::ErrorCode::category`` for every code (checked against the generated stub)."""

ERROR_CLASS_OF_CODE: Mapping[str, type[ApiError]] = {
    "validation_invalid_request": ValidationApiError,
    "not_found": NotFoundApiError,
    "order_not_found": NotFoundApiError,
    "instrument_not_found": NotFoundApiError,
    "unauthorized": AuthApiError,
    "forbidden": AuthApiError,
    "rate_limited": RateLimitedApiError,
    "risk_max_notional_exceeded": RiskApiError,
    "risk_max_position_exceeded": RiskApiError,
    "risk_max_drawdown_exceeded": RiskApiError,
    "risk_trading_halted": RiskApiError,
    "risk_order_rate_exceeded": RiskApiError,
    "risk_quantity_below_min": RiskApiError,
    "risk_quantity_over_freeze": RiskApiError,
    "risk_lot_multiple_violation": RiskApiError,
    "risk_tick_size_violation": RiskApiError,
    "risk_price_band_exceeded": RiskApiError,
    "risk_reduce_only_violation": RiskApiError,
    "risk_instrument_unknown": RiskApiError,
    "risk_max_participation_exceeded": RiskApiError,
    "risk_feed_stale": RiskApiError,
    "order_rejected": OrderRejectedApiError,
    "order_execution_unavailable": OrderRejectedApiError,
    "market_data_unavailable": MarketDataUnavailableApiError,
    "timeout": TransportApiError,
    "transport_error": TransportApiError,
    "internal_error": InternalApiError,
    "unsupported": UnsupportedApiError,
    "not_implemented": NotImplementedApiError,
}


def error_from_envelope(status: int, body: Any) -> ApiError:
    """Build the :class:`ApiError` for a failed response ``body`` (the parsed JSON)."""
    detail = body.get("error") if isinstance(body, Mapping) else None
    if (
        not isinstance(detail, Mapping)
        or not isinstance(detail.get("code"), str)
        or not isinstance(detail.get("message"), str)
    ):
        return InvalidResponseError(f"HTTP {status} without an error envelope", status=status)
    code = detail["code"]
    api_version = body.get("api_version")
    cls = ERROR_CLASS_OF_CODE.get(code, ApiError)
    return cls(
        code,
        detail["message"],
        status=status,
        retryable=detail.get("retryable") is True,
        context=detail.get("context"),
        api_version=api_version if isinstance(api_version, str) else None,
    )
