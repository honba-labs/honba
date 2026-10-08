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
from typing import TYPE_CHECKING, Any

from honba._native import native_attr

if TYPE_CHECKING:
    from honba._honba import RiskDecision, RiskLimits, RiskStage, TradingState

__all__ = [
    "RiskDecision",
    "RiskLimits",
    "RiskStage",
    "TradingState",
    "limits_from_config",
]

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
