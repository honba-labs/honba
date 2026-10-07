"""The order-state machine (ADR 0019): events as verbs over ``OrderStatus``.

Pure and quantity-only, the Python reference for ``honba-messages::orders::state``. A
producer feeds ``OrderEvent`` values to ``OrderState.apply``; an event the transition
table forbids raises ``IllegalTransition`` and leaves the state untouched. Check order:
legality, quantity validity, overfill, fill mismatch.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum

from honba.domain.order import OrderStatus

__all__ = [
    "FillMismatch",
    "IllegalStatusTransition",
    "IllegalTransition",
    "InvalidQuantity",
    "OrderEvent",
    "OrderEventKind",
    "OrderState",
    "Overfill",
]

QTY_EPS = 1e-9
"""Tolerance for comparing cumulative quantities (ADR 0016 convention)."""


class OrderEventKind(Enum):
    """The kind of an ``OrderEvent``, without payload."""

    SUBMITTED = "submitted"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    FILL = "fill"
    CANCEL_REQUESTED = "cancel_requested"
    CANCELLED = "cancelled"
    EXPIRED = "expired"


@dataclass(frozen=True, slots=True)
class OrderEvent:
    """A lifecycle event. ``quantity`` is set only by ``SUBMITTED``; ``last_qty`` and
    ``complete`` only by ``FILL`` (``complete`` is the producer's claim)."""

    kind: OrderEventKind
    quantity: float = 0.0
    last_qty: float = 0.0
    complete: bool = False

    @classmethod
    def submitted(cls, quantity: float) -> OrderEvent:
        return cls(OrderEventKind.SUBMITTED, quantity=quantity)

    @classmethod
    def accepted(cls) -> OrderEvent:
        return cls(OrderEventKind.ACCEPTED)

    @classmethod
    def rejected(cls) -> OrderEvent:
        return cls(OrderEventKind.REJECTED)

    @classmethod
    def fill(cls, last_qty: float, complete: bool) -> OrderEvent:
        return cls(OrderEventKind.FILL, last_qty=last_qty, complete=complete)

    @classmethod
    def cancel_requested(cls) -> OrderEvent:
        return cls(OrderEventKind.CANCEL_REQUESTED)

    @classmethod
    def cancelled(cls) -> OrderEvent:
        return cls(OrderEventKind.CANCELLED)

    @classmethod
    def expired(cls) -> OrderEvent:
        return cls(OrderEventKind.EXPIRED)


def _num(value: float) -> str:
    """Format like Rust's ``{}`` for f64 (``1`` not ``1.0``)."""
    if math.isfinite(value) and value == int(value):
        return str(int(value))
    return repr(value)


def _status_name(status: OrderStatus) -> str:
    return "".join(part.capitalize() for part in status.value.split("_"))


class IllegalTransition(ValueError):
    """Why an ``OrderEvent`` could not be applied. The state is unchanged."""


class IllegalStatusTransition(IllegalTransition):
    """The transition table marks this ``(state, event)`` cell illegal."""

    def __init__(self, status: OrderStatus, cancel_requested: bool, event: OrderEventKind) -> None:
        self.status = status
        self.cancel_requested = cancel_requested
        self.event = event
        super().__init__(
            f"illegal transition: {event.value} in status {_status_name(status)} "
            f"(cancel_requested={str(cancel_requested).lower()})"
        )


class Overfill(IllegalTransition):
    """The fill would exceed the order quantity."""

    def __init__(self, quantity: float, filled_qty: float, last_qty: float) -> None:
        self.quantity = quantity
        self.filled_qty = filled_qty
        self.last_qty = last_qty
        super().__init__(
            f"overfill: filled {_num(filled_qty)} + last {_num(last_qty)} "
            f"exceeds quantity {_num(quantity)}"
        )


class FillMismatch(IllegalTransition):
    """The producer's ``complete`` flag disagrees with the derived completeness."""

    def __init__(self, claimed_complete: bool, derived_complete: bool) -> None:
        self.claimed_complete = claimed_complete
        self.derived_complete = derived_complete
        super().__init__(
            f"fill mismatch: claimed complete={str(claimed_complete).lower()}, "
            f"derived complete={str(derived_complete).lower()}"
        )


class InvalidQuantity(IllegalTransition):
    """A quantity (order or fill) is non-finite or not positive."""

    def __init__(self, value: float) -> None:
        self.value = value
        super().__init__(f"invalid quantity: {_num(value)}")


_TERMINAL = frozenset(
    {OrderStatus.FILLED, OrderStatus.CANCELLED, OrderStatus.REJECTED, OrderStatus.EXPIRED}
)
_TERMINAL_KIND = {
    OrderStatus.CANCELLED: OrderEventKind.CANCELLED,
    OrderStatus.REJECTED: OrderEventKind.REJECTED,
    OrderStatus.EXPIRED: OrderEventKind.EXPIRED,
}
_WORKING = frozenset({OrderStatus.SUBMITTED, OrderStatus.ACCEPTED, OrderStatus.PARTIALLY_FILLED})


def _check_qty(value: float) -> None:
    if not (math.isfinite(value) and value > 0.0):
        raise InvalidQuantity(value)


@dataclass(slots=True)
class OrderState:
    """The state of one order: status, quantities and a pending-cancel flag."""

    status: OrderStatus = OrderStatus.INITIALIZED
    quantity: float = 0.0
    filled_qty: float = 0.0
    cancel_requested: bool = False

    @staticmethod
    def can_transition(frm: tuple[OrderStatus, bool], via: OrderEventKind) -> bool:
        """Whether ``via`` is legal (transition or duplicate no-op) from ``(status, cr)``."""
        status = frm[0]
        if status is OrderStatus.INITIALIZED:
            return via in (OrderEventKind.SUBMITTED, OrderEventKind.REJECTED)
        if status in _WORKING:
            return via is not OrderEventKind.SUBMITTED
        if status is OrderStatus.FILLED:
            return False
        return _TERMINAL_KIND.get(status) is via

    def apply(self, ev: OrderEvent) -> bool:
        """Apply ``ev``. True = transitioned, False = duplicate no-op.

        Raises ``IllegalTransition`` (state unchanged) if the event is illegal.
        """
        kind = ev.kind
        # A repeated fill after completion is indistinguishable from an overfill.
        if self.status is OrderStatus.FILLED and kind is OrderEventKind.FILL:
            _check_qty(ev.last_qty)
            raise Overfill(self.quantity, self.filled_qty, ev.last_qty)
        if not self.can_transition((self.status, self.cancel_requested), kind):
            raise IllegalStatusTransition(self.status, self.cancel_requested, kind)
        if kind is OrderEventKind.SUBMITTED:
            _check_qty(ev.quantity)
            self.quantity = ev.quantity
            self.status = OrderStatus.SUBMITTED
        elif kind is OrderEventKind.ACCEPTED:
            if self.status is not OrderStatus.SUBMITTED:
                return False
            self.status = OrderStatus.ACCEPTED
        elif kind in (OrderEventKind.REJECTED, OrderEventKind.CANCELLED, OrderEventKind.EXPIRED):
            if self.status in _TERMINAL:
                return False
            self._terminate(
                {
                    OrderEventKind.REJECTED: OrderStatus.REJECTED,
                    OrderEventKind.CANCELLED: OrderStatus.CANCELLED,
                    OrderEventKind.EXPIRED: OrderStatus.EXPIRED,
                }[kind]
            )
        elif kind is OrderEventKind.CANCEL_REQUESTED:
            if self.cancel_requested:
                return False
            self.cancel_requested = True
        else:
            self._fill(ev)
        return True

    def _fill(self, ev: OrderEvent) -> None:
        _check_qty(ev.last_qty)
        cum = self.filled_qty + ev.last_qty
        if cum > self.quantity + QTY_EPS:
            raise Overfill(self.quantity, self.filled_qty, ev.last_qty)
        derived = cum + QTY_EPS >= self.quantity
        if ev.complete != derived:
            raise FillMismatch(ev.complete, derived)
        self.filled_qty = cum
        if derived:
            self._terminate(OrderStatus.FILLED)
        else:
            self.status = OrderStatus.PARTIALLY_FILLED

    def _terminate(self, status: OrderStatus) -> None:
        self.status = status
        self.cancel_requested = False
