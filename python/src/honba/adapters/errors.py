"""Adapter error taxonomy (E1-S1).

Three rules hold across the whole adapter layer, and the contract suite checks each of them:

1. A capability the adapter does not have raises :class:`CapabilityError`. This is a
   configuration or programming error, so it is an exception.
2. An order the *broker* refuses is **not** an exception: it comes back as an
   :class:`~honba.adapters.models.OrderReport` with ``status=REJECTED`` and a
   ``reject_reason``. A refusal is data the engine journals and reconciles.
3. Connection and auth problems are either recoverable (the adapter retries or reconnects)
   or terminal (the run halts). :class:`AdapterFatalError` marks the terminal case; the
   default :class:`AdapterError` is recoverable.

The ``code`` values are provisional: E0-S5 defines the shared taxonomy across the
codebase, and this module is expected to fold into it. Untrusted broker text must never be
rendered as an error message on its own (E5-S2 trust envelope).
"""

from __future__ import annotations

from typing import ClassVar


class AdapterError(Exception):
    """Base class for every adapter failure. Recoverable unless ``fatal`` is set.

    ``fatal`` is a class-level marker so generic code can branch on it without importing
    the terminal subclass (mirrors barter's Terminal/Unrecoverable marker traits).
    """

    fatal: ClassVar[bool] = False
    code: ClassVar[str] = "adapter.error"


class AdapterFatalError(AdapterError):
    """Unrecoverable: invalid credentials, suspended account, malformed documented response.

    The run must halt cleanly. Retrying will not help.
    """

    fatal: ClassVar[bool] = True
    code: ClassVar[str] = "adapter.fatal"


class AdapterNotFound(AdapterError):
    """No adapter is registered under that name (and none is installed)."""

    code: ClassVar[str] = "adapter.not_found"


class CapabilityError(AdapterError):
    """The adapter does not support the requested capability, product or order type.

    Raised before any network call, from the capability descriptor. Never raised for a
    broker refusal: that is an ``OrderReport`` with ``status=REJECTED``.
    """

    code: ClassVar[str] = "adapter.capability_unsupported"


class SessionError(AdapterError):
    """Recoverable session problem: token expired, keepalive failed, not yet connected."""

    code: ClassVar[str] = "adapter.session"


class SessionExpired(SessionError):
    """The broker session token expired and must be refreshed (E1-S5 owns the refresh policy)."""

    code: ClassVar[str] = "adapter.session_expired"


__all__ = [
    "AdapterError",
    "AdapterFatalError",
    "AdapterNotFound",
    "CapabilityError",
    "SessionError",
    "SessionExpired",
]
