"""Broker token sessions with an explicit refresh policy (E1-S5).

A broker issues an API token with an expiry; the control plane must refresh it *before* it
dies, not when a request fails. This module is that policy, decoupled from any broker:
:class:`TokenSession` holds a :class:`Token`, answers ``is_expired`` / ``should_refresh``
against an injected clock, and refreshes through a caller-supplied ``refresh_fn`` the first
time ``ensure_valid`` sees the token inside its refresh margin. Concurrent callers coalesce
into one refresh.

Two rules the rest of the codebase depends on:

* the clock is injected (``clock: () -> int``, unix nanoseconds) so expiry is testable with a
  fake clock and never reads the wall clock inside a test;
* a token's value is a secret: ``repr`` and ``str`` of both :class:`Token` and
  :class:`TokenSession` redact it, so a token cannot leak into a log line.

The broker-specific login/refresh dance (including any TOTP auto-login) is deliberately out
of scope here; an adapter supplies the ``refresh_fn``. TOTP auto-login stays a per-broker
review (ROADMAP section 3.5/3.1) and is deferred.
"""

from __future__ import annotations

import asyncio
import time
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

__all__ = [
    "DEFAULT_REFRESH_MARGIN_NS",
    "SECOND_NS",
    "Clock",
    "RefreshFn",
    "SessionProvider",
    "Token",
    "TokenSession",
]

#: Nanoseconds in one second, matching the unix-nanosecond convention in ``honba.domain``.
SECOND_NS = 1_000_000_000
#: Refresh this long before expiry, so a call in flight never races the token dying.
DEFAULT_REFRESH_MARGIN_NS = 5 * 60 * SECOND_NS

#: A monotonic-ish source of unix nanoseconds; injected so tests use a fake clock.
Clock = Callable[[], int]
#: Produces a fresh token when the current one is within its refresh margin.
RefreshFn = Callable[[], Awaitable["Token"]]


@dataclass(frozen=True, slots=True, repr=False)
class Token:
    """An API token and its expiry (unix nanoseconds; ``None`` means the broker reports none).

    The value is a secret and never appears in ``repr``/``str``.
    """

    value: str
    expires_at: int | None = None

    def __repr__(self) -> str:
        return f"Token(value=<redacted>, expires_at={self.expires_at!r})"

    __str__ = __repr__


class SessionProvider(ABC):
    """The token lifecycle an adapter depends on, independent of any broker or transport."""

    @property
    @abstractmethod
    def expires_at(self) -> int | None:
        """Unix-nanosecond expiry of the current token, or ``None`` when it has none."""
        ...

    @abstractmethod
    def is_expired(self, now_ns: int | None = None) -> bool:
        """Whether the token is past its expiry at ``now_ns`` (defaults to the injected clock)."""
        ...

    @abstractmethod
    def should_refresh(self, now_ns: int | None = None) -> bool:
        """Whether the token is within its refresh margin and ought to be renewed."""
        ...

    @abstractmethod
    async def ensure_valid(self, now_ns: int | None = None) -> Token:
        """Return a valid token, refreshing first when one is due.

        Concurrent callers within the margin perform a single refresh.
        """
        ...

    @abstractmethod
    def invalidate(self) -> None:
        """Force the next :meth:`ensure_valid` to refresh (e.g. after a 401)."""
        ...


class TokenSession(SessionProvider):
    """A :class:`SessionProvider` holding one token and a caller-supplied refresh function."""

    def __init__(
        self,
        token: Token,
        *,
        refresh_fn: RefreshFn,
        clock: Clock = time.time_ns,
        refresh_margin_ns: int = DEFAULT_REFRESH_MARGIN_NS,
    ) -> None:
        if refresh_margin_ns < 0:
            raise ValueError(f"refresh_margin_ns must be >= 0, got {refresh_margin_ns}")
        self._token = token
        self._refresh_fn = refresh_fn
        self._clock = clock
        self._refresh_margin_ns = refresh_margin_ns
        self._lock = asyncio.Lock()

    @property
    def expires_at(self) -> int | None:
        return self._token.expires_at

    @property
    def token(self) -> str:
        """The current token value. Treat the returned string as a secret."""
        return self._token.value

    def _now(self, now_ns: int | None) -> int:
        return self._clock() if now_ns is None else now_ns

    def is_expired(self, now_ns: int | None = None) -> bool:
        expires_at = self._token.expires_at
        if expires_at is None:
            return False
        return self._now(now_ns) >= expires_at

    def should_refresh(self, now_ns: int | None = None) -> bool:
        expires_at = self._token.expires_at
        if expires_at is None:
            return False
        return self._now(now_ns) >= expires_at - self._refresh_margin_ns

    async def ensure_valid(self, now_ns: int | None = None) -> Token:
        now = self._now(now_ns)
        if not self.should_refresh(now):
            return self._token
        async with self._lock:
            if not self.should_refresh(now):
                return self._token
            self._token = await self._refresh_fn()
            return self._token

    def invalidate(self) -> None:
        self._token = Token(value=self._token.value, expires_at=0)

    def __repr__(self) -> str:
        return f"TokenSession(token=<redacted>, expires_at={self._token.expires_at!r})"

    __str__ = __repr__
