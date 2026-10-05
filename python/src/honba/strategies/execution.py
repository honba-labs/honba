"""The ``ExecutionPort`` contract: where the runner sends orders (ADR 008).

A simulator in backtest, a broker adapter in live. The runner owns the strategy's
context; a port never touches it. Everything a port has to say about an order goes
back through two queues the runner drains after every event:

* ``drain_fills()`` (required): fills, booked in the context and passed to ``on_fill``;
* ``drain_rejections()`` (optional): an order, or the unfilled part of one, that will
  never fill (rejected by the venue or port, or cancelled). The runner releases it in
  the context so the strategy no longer sees the instrument as busy.

``cancel(order_id)`` (optional) asks the port to cancel a working order; the port
reports the cancelled quantity through ``drain_rejections`` with ``cancelled=True``.
Cancelling an unknown or already finished order is a no-op.

Ports written before the reject/cancel path (only ``submit`` and ``drain_fills``) keep
working: the runner reaches the optional methods through :func:`drain_port_rejections`
and :func:`cancel_order`, which treat a missing method as "nothing to report" /
"cannot cancel". New ports can subclass :class:`BaseExecutionPort` for the defaults.

Pure contract: no I/O, no wall clock.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from honba.entities.order import OrderIntent
from honba.entities.trade import Trade

__all__ = [
    "BaseExecutionPort",
    "ExecutionPort",
    "OrderRejection",
    "RejectingExecutionPort",
    "cancel_order",
    "drain_port_rejections",
]


@dataclass(frozen=True, slots=True)
class OrderRejection:
    """An order, or the part of one, that will never fill.

    ``intent`` carries the quantity that is released (the unfilled remainder for a
    partial fill). ``cancelled`` distinguishes a cancel (wire ``order_cancelled``)
    from a rejection by the venue or port (wire ``order_rejected``). ``ts`` is the
    port's time for the event in unix ns (0 if it has none).
    """

    order_id: str
    intent: OrderIntent
    reason: str
    ts: int = 0
    cancelled: bool = False

    def __post_init__(self) -> None:
        if not self.order_id:
            raise ValueError("OrderRejection.order_id must not be empty")


@runtime_checkable
class ExecutionPort(Protocol):
    """The required port surface: accept orders, hand back fills."""

    def submit(self, order_id: str, intent: OrderIntent, ts: int) -> None: ...

    def drain_fills(self) -> list[Trade]: ...


@runtime_checkable
class RejectingExecutionPort(ExecutionPort, Protocol):
    """A port that also reports rejections and accepts cancels."""

    def cancel(self, order_id: str) -> None: ...

    def drain_rejections(self) -> list[OrderRejection]: ...


class BaseExecutionPort(ABC):
    """Convenience base: implement ``submit`` and ``drain_fills``; reject/cancel default inert.

    The defaults suit a port that fills every order it accepts (no rejections) and has
    no working orders to cancel. Override both when the port can hold or refuse orders.
    """

    @abstractmethod
    def submit(self, order_id: str, intent: OrderIntent, ts: int) -> None: ...

    @abstractmethod
    def drain_fills(self) -> list[Trade]: ...

    def cancel(self, order_id: str) -> None:
        """Cancel a working order. Default: nothing is working, so nothing to cancel."""

    def drain_rejections(self) -> list[OrderRejection]:
        """Return and clear rejections. Default: this port never rejects."""
        return []


def drain_port_rejections(port: object) -> list[OrderRejection]:
    """``port.drain_rejections()``, or ``[]`` for a port without the reject path."""
    drain = getattr(port, "drain_rejections", None)
    return list(drain()) if drain is not None else []


def cancel_order(port: object, order_id: str) -> bool:
    """Ask ``port`` to cancel ``order_id``; ``False`` if the port cannot cancel at all."""
    cancel = getattr(port, "cancel", None)
    if cancel is None:
        return False
    cancel(order_id)
    return True
