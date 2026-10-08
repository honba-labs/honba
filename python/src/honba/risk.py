"""Pre-trade risk for Python (ADR 0018).

A thin face on the Rust risk stage: the types are the native ones (``honba._honba``), so the
rules, their order and the numbers they report are identical to the Rust engine and to the
shared golden vectors. Importing this module does not need the extension; the first attribute
access does (see :mod:`honba._native`).

* :class:`RiskLimits`: per-run limits (``max_notional``, ``order_rate``); ``RiskLimits.from_dict``
  is the one parser of the ``[risk]`` config table (unknown keys are refused).
* :class:`RiskStage`: ``check(request)`` returns a :class:`RiskDecision` with ``approved``,
  ``code``, ``rule`` and ``context``.
* :class:`TradingState`: ``ACTIVE`` / ``REDUCING`` / ``HALTED``; ``str()`` is the wire spelling.
* :func:`limits_from_config`: reads ``[risk]`` from a run's parsed TOML table.

``honba._honba.run_strategy(..., risk=...)`` runs a reference strategy behind a stage; its spec is
``{"limits", "market", "positions", "trading_state", "state_changes"}`` (see its docstring).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from honba._native import native_attr
from honba.entities.order import OrderSide

if TYPE_CHECKING:
    from honba._honba import RiskDecision, RiskLimits, RiskStage, TradingState

__all__ = [
    "OrderRejected",
    "RiskDecision",
    "RiskLimits",
    "RiskRefusal",
    "RiskRefused",
    "RiskStage",
    "TradingState",
    "check_state",
    "limits_from_config",
]

_POSITION_EPS = 1e-9  # as honba_risk::stage::POSITION_EPS

_NATIVE = frozenset({"RiskDecision", "RiskLimits", "RiskStage", "TradingState"})


def __getattr__(name: str) -> Any:
    if name in _NATIVE:
        return native_attr(name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def limits_from_config(config: Mapping[str, Any]) -> RiskLimits:
    """The :class:`RiskLimits` of a run's parsed TOML table.

    Reads the ``[risk]`` table (and ``[risk.order_rate]``) through ``RiskLimits.from_dict``, the
    same parser the Rust config uses; an absent section means no limits. Raises ``ValueError``
    for an unknown key or an invalid value.
    """
    table = config.get("risk")
    return native_attr("RiskLimits").from_dict({} if table is None else dict(table))


@dataclass(frozen=True, slots=True)
class RiskRefusal:
    """A refused order: the ``ErrorCode`` wire ``code``, the ``rule`` and its ``context``."""

    code: str
    rule: str
    context: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class RiskRefused:
    """Audit record: the risk gate refused ``order_id`` at ``ts`` (ns). Always followed by
    the :class:`OrderRejected` for the same order (``AuditKind::RiskRefused``)."""

    order_id: str
    ts: int
    code: str
    rule: str
    context: dict[str, Any]


@dataclass(frozen=True, slots=True)
class OrderRejected:
    """Audit record: ``order_id`` was refused before submit; ``reason`` is the ``ErrorCode``
    wire spelling (``AuditKind::OrderRejected``)."""

    order_id: str
    ts: int
    reason: str


def check_state(
    side: OrderSide, quantity: float, position: float, trading_state: TradingState
) -> RiskRefusal | None:
    """Rules 1-2 of ADR 0018, pure: ``Halted`` and reduce-only (``honba_risk::check_state``).

    ``position`` is the signed position plus the same-side working remainder. While ``Reducing``
    an order passes only if ``position + sign * quantity`` stays within
    ``[min(position, 0), max(position, 0)]`` (a flat position refuses everything).
    """
    state = str(trading_state)
    if state == "halted":
        return RiskRefusal("risk_trading_halted", "trading_halted", {"rule": "trading_halted"})
    if state != "reducing":
        return None
    sign = 1.0 if side is OrderSide.BUY else -1.0
    after = position + sign * quantity
    if (
        abs(position) > _POSITION_EPS
        and after >= min(position, 0.0) - _POSITION_EPS
        and after <= max(position, 0.0) + _POSITION_EPS
    ):
        return None
    return RiskRefusal(
        "risk_reduce_only_violation",
        "reduce_only",
        {
            "rule": "reduce_only",
            "position": position,
            "side": side.value,
            "quantity": quantity,
        },
    )
