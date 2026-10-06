"""The explicit owner of the interpreter's one async runtime (ADR 0015, E10-S7).

``honba._honba`` runs async Rust work (and the in-process REST router) on a single tokio
runtime. This module is the only thing that creates and destroys it::

    from honba import event_loop

    with event_loop.running():          # or event_loop.start() ... event_loop.stop()
        ...                             # in-process calls reuse the started runtime

* :func:`start` is idempotent for the owner and raises :class:`EventLoopError` if a runtime
  was started by someone else (a direct ``honba._honba.runtime_start`` call).
* :func:`stop` shuts the runtime down and joins its threads; it is idempotent and also runs at
  interpreter exit, so audit streams are flushed before the process ends.
* The runtime can be started again after :func:`stop`; :attr:`RuntimeInfo.generation` counts
  starts.

Without a started loop the in-process REST path still works: it builds a throwaway
current-thread runtime per call, so results are identical either way.
"""

from __future__ import annotations

import atexit
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass

from honba._native import native_attr

__all__ = ["EventLoopError", "RuntimeInfo", "info", "is_running", "running", "start", "stop"]


class EventLoopError(RuntimeError):
    """The runtime cannot be started as asked (owned elsewhere, or a conflicting request)."""


@dataclass(frozen=True)
class RuntimeInfo:
    """Description of the running runtime."""

    flavor: str
    worker_threads: int
    generation: int


_lock = threading.RLock()
_owned = False
_atexit_registered = False


def _info() -> RuntimeInfo | None:
    raw = native_attr("runtime_info")()
    return None if raw is None else RuntimeInfo(*raw)


def info() -> RuntimeInfo | None:
    """The running runtime's description, or ``None`` when none is running."""
    return _info()


def is_running() -> bool:
    """Whether a runtime is running (started by this module or not)."""
    return _info() is not None


def start(worker_threads: int | None = None) -> RuntimeInfo:
    """Start the runtime, or return the running one when this module already started it.

    ``worker_threads`` defaults to one per core. Raises ``ValueError`` for 0, and
    :class:`EventLoopError` if the runtime was started outside this module or is running with
    a different explicit ``worker_threads``.
    """
    global _owned, _atexit_registered
    with _lock:
        if _owned:
            current = _info()
            if current is not None:
                if worker_threads is not None and worker_threads != current.worker_threads:
                    raise EventLoopError(
                        f"the event loop is running with worker_threads={current.worker_threads}; "
                        f"stop() it before starting with worker_threads={worker_threads}"
                    )
                return current
            _owned = False  # stopped behind our back
        try:
            raw = native_attr("runtime_start")(worker_threads)
        except RuntimeError as exc:
            raise EventLoopError(
                f"{exc}; it was not started by honba.event_loop, so it cannot be adopted"
            ) from exc
        _owned = True
        if not _atexit_registered:
            atexit.register(stop)
            _atexit_registered = True
        return RuntimeInfo(*raw)


def stop() -> bool:
    """Stop the runtime this module started and join its threads.

    Returns whether it stopped one. Idempotent; a runtime started elsewhere is left alone.
    """
    global _owned
    with _lock:
        if not _owned:
            return False
        _owned = False
        return bool(native_attr("runtime_stop")())


@contextmanager
def running(worker_threads: int | None = None) -> Iterator[RuntimeInfo]:
    """Run the block with the runtime started; stop it on exit only if this block started it."""
    with _lock:
        started_here = not _owned or not is_running()
        current = start(worker_threads)
    try:
        yield current
    finally:
        if started_here:
            stop()
