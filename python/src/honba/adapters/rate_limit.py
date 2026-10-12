"""Token-bucket rate limiting, per adapter and per endpoint (E1-S5).

A broker enforces request budgets; the control plane must stay under them across every
coroutine *and* every worker. :class:`RateLimiter` is a token bucket labelled by an opaque
``key`` (``"dhan:orders"``), and the bucket's state lives behind a
:class:`SharedBucketStore` so two independent limiters can share one budget:

* :class:`InMemoryBucket` — one process, one dict; the reference store and the default.
* :class:`FileBackedBucket` — a JSON file per key under a directory, guarded by an
  ``fcntl.flock`` on a sibling lock file and written atomically (temp file + ``os.replace``).
  This is the simplest backing that holds a limit across worker processes without Redis; two
  limiters pointed at the same directory never exceed the shared capacity.

The clock is injected (``() -> int``, unix nanoseconds) so refill is tested with a fake clock
and never reads the wall clock inside a test. Buckets are lazy: the first read at time
``now`` seeds a full bucket, and later reads refill at ``refill_rate`` tokens per second,
capped at ``capacity``.
"""

from __future__ import annotations

import asyncio
import contextlib
import fcntl
import hashlib
import json
import os
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable

__all__ = [
    "SECOND_NS",
    "BucketState",
    "FileBackedBucket",
    "InMemoryBucket",
    "RateLimitExceeded",
    "RateLimiter",
    "RateLimiterRegistry",
    "SharedBucketStore",
]

#: Nanoseconds in one second, matching the unix-nanosecond convention in ``honba.domain``.
SECOND_NS = 1_000_000_000

#: A monotonic source of nanoseconds; injected so tests use a fake clock.
Clock = Callable[[], int]


class RateLimitExceeded(Exception):
    """``acquire`` cannot ever satisfy the request (no tokens and no refill)."""


@dataclass(frozen=True, slots=True)
class BucketState:
    """Tokens available and the nanosecond instant they were last refilled to."""

    tokens: float
    updated_at_ns: int


@runtime_checkable
class SharedBucketStore(Protocol):
    """Where bucket state lives, so limits can span independent limiter instances.

    ``read``/``write`` move opaque state; ``lock`` must make a read-refill-write cycle atomic
    against every other holder of the same store (and, for a file store, other processes).
    """

    def read(self, key: str) -> BucketState | None:
        """The stored state for ``key``, or ``None`` when the bucket does not exist yet."""
        ...

    def write(self, key: str, state: BucketState) -> None:
        """Persist ``state`` for ``key``."""
        ...

    def lock(self, key: str) -> AbstractContextManager[None]:
        """An exclusive lock covering a read-refill-write cycle for ``key``."""
        ...


class InMemoryBucket:
    """A process-local :class:`SharedBucketStore` backed by a dict of locks."""

    def __init__(self) -> None:
        self._states: dict[str, BucketState] = {}
        self._locks: dict[str, threading.Lock] = {}
        self._guard = threading.Lock()

    def _lock_for(self, key: str) -> threading.Lock:
        with self._guard:
            return self._locks.setdefault(key, threading.Lock())

    def read(self, key: str) -> BucketState | None:
        return self._states.get(key)

    def write(self, key: str, state: BucketState) -> None:
        self._states[key] = state

    @contextlib.contextmanager
    def lock(self, key: str) -> Iterator[None]:
        with self._lock_for(key):
            yield


def _safe_name(key: str) -> str:
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]
    cleaned = "".join(c if c.isalnum() or c in "-_" else "_" for c in key)[:40]
    return f"{cleaned}-{digest}" if cleaned else digest


class FileBackedBucket:
    """A cross-process :class:`SharedBucketStore`: one JSON file per key, flock-guarded."""

    def __init__(self, directory: str | os.PathLike[str]) -> None:
        self._dir = Path(directory)
        self._dir.mkdir(parents=True, exist_ok=True)

    def _paths(self, key: str) -> tuple[Path, Path]:
        name = _safe_name(key)
        return self._dir / f"{name}.json", self._dir / f"{name}.lock"

    def read(self, key: str) -> BucketState | None:
        data_path, _ = self._paths(key)
        try:
            raw = data_path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return None
        if not raw.strip():
            return None
        payload = json.loads(raw)
        return BucketState(float(payload["tokens"]), int(payload["updated_at_ns"]))

    def write(self, key: str, state: BucketState) -> None:
        data_path, _ = self._paths(key)
        payload = json.dumps({"tokens": state.tokens, "updated_at_ns": state.updated_at_ns})
        tmp = data_path.with_name(f".{data_path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            os.write(fd, payload.encode("utf-8"))
        finally:
            os.close(fd)
        os.replace(tmp, data_path)

    @contextlib.contextmanager
    def lock(self, key: str) -> Iterator[None]:
        _, lock_path = self._paths(key)
        fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


class RateLimiter:
    """A token bucket of ``capacity`` burst tokens refilling at ``refill_rate`` per second."""

    def __init__(
        self,
        *,
        capacity: float,
        refill_rate: float,
        key: str = "default",
        store: SharedBucketStore | None = None,
        clock: Clock = time.monotonic_ns,
    ) -> None:
        if capacity <= 0:
            raise ValueError(f"capacity must be > 0, got {capacity}")
        if refill_rate < 0:
            raise ValueError(f"refill_rate must be >= 0, got {refill_rate}")
        self._capacity = float(capacity)
        self._refill_rate = float(refill_rate)
        self._key = key
        self._store: SharedBucketStore = store if store is not None else InMemoryBucket()
        self._clock = clock

    @property
    def key(self) -> str:
        return self._key

    @property
    def capacity(self) -> float:
        return self._capacity

    @property
    def refill_rate(self) -> float:
        return self._refill_rate

    def _now(self, now_ns: int | None) -> int:
        return self._clock() if now_ns is None else now_ns

    def _refilled(self, state: BucketState | None, now_ns: int) -> BucketState:
        if state is None:
            return BucketState(self._capacity, now_ns)
        elapsed_ns = max(0, now_ns - state.updated_at_ns)
        tokens = min(self._capacity, state.tokens + elapsed_ns / SECOND_NS * self._refill_rate)
        return BucketState(tokens, now_ns)

    def available_tokens(self, now_ns: int | None = None) -> float:
        """Tokens currently available, refilled up to ``now_ns``. Does not consume or persist."""
        now = self._now(now_ns)
        return self._refilled(self._store.read(self._key), now).tokens

    def try_acquire(self, amount: float = 1.0, now_ns: int | None = None) -> bool:
        """Take ``amount`` tokens if available, atomically. Never blocks."""
        if amount <= 0:
            raise ValueError(f"amount must be > 0, got {amount}")
        now = self._now(now_ns)
        with self._store.lock(self._key):
            state = self._refilled(self._store.read(self._key), now)
            if state.tokens < amount:
                self._store.write(self._key, state)
                return False
            self._store.write(self._key, BucketState(state.tokens - amount, now))
            return True

    async def acquire(self, amount: float = 1.0) -> None:
        """Take ``amount`` tokens, sleeping until the bucket refills if needed."""
        if amount <= 0:
            raise ValueError(f"amount must be > 0, got {amount}")
        if amount > self._capacity:
            raise RateLimitExceeded(
                f"cannot acquire {amount} tokens from a bucket of capacity {self._capacity}"
            )
        while not self.try_acquire(amount):
            now = self._now(None)
            with self._store.lock(self._key):
                available = self._refilled(self._store.read(self._key), now).tokens
            deficit = max(0.0, amount - available)
            if self._refill_rate <= 0:
                raise RateLimitExceeded(f"bucket {self._key!r} is empty and has no refill rate")
            await asyncio.sleep(deficit / self._refill_rate)


class RateLimiterRegistry:
    """One :class:`RateLimiter` per ``(adapter, endpoint)`` key, sharing a single store."""

    def __init__(
        self,
        *,
        store: SharedBucketStore | None = None,
        clock: Clock = time.monotonic_ns,
    ) -> None:
        self._store: SharedBucketStore = store if store is not None else InMemoryBucket()
        self._clock = clock
        self._limiters: dict[str, RateLimiter] = {}

    @staticmethod
    def key_for(adapter: str, endpoint: str | None = None) -> str:
        """The bucket key for an adapter-wide (``endpoint is None``) or endpoint limit."""
        return adapter if endpoint is None else f"{adapter}:{endpoint}"

    def get(
        self,
        adapter: str,
        endpoint: str | None = None,
        *,
        capacity: float,
        refill_rate: float,
    ) -> RateLimiter:
        """The limiter for ``(adapter, endpoint)``, created on first use."""
        key = self.key_for(adapter, endpoint)
        limiter = self._limiters.get(key)
        if limiter is None:
            limiter = RateLimiter(
                capacity=capacity,
                refill_rate=refill_rate,
                key=key,
                store=self._store,
                clock=self._clock,
            )
            self._limiters[key] = limiter
        return limiter
