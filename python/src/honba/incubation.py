"""Paper incubation: verify forward testing before live capital (Balch pitfall #10).

Strategies that jump straight from a backtest to a broker miss every operational
discrepancy a backtest hides. Honba's defense is the unified promotion path —
**Backtest -> Paper -> Live** — where the paper stage is a mandatory incubation
whose recorded execution stream proves, before any live capital is risked, that:

* **the period ran** (``min_days`` of wall-clock span across the events),
* **orders fill** (``min_fill_rate`` of every order the stream touched, rejects
  and cancels counting against it),
* **the venue is responsive** (median ``Submitted -> Accepted`` ack latency
  within ``max_median_ack_ms``; a port that never acknowledges — a pure backtest
  simulator — cannot demonstrate this and fails the gate, which is the point),
* **the books balance** (``max_accounting_drift``: the reported final cash is
  recomputed from the initial cash plus every fill's signed notional and costs
  in integer ``Money`` (ADR 0011), so a balanced ledger drifts by exactly 0).

The input is one ordered ``ExecutionPort`` event stream (ADR 0019) plus the cash
either side — pure, no I/O, no wall clock — so the same gate judges a recorded
backtest-shaped session today and an ``E3-S7`` sandbox adapter's live-stream
session tomorrow. :func:`require_promotion` is the mandatory hop: ``PAPER ->
LIVE`` raises :class:`PromotionRefused` unless the incubation passed, and
``BACKTEST -> LIVE`` is refused outright.

Example::

    from honba.incubation import incubation_report, require_promotion
    from honba.adapters.models import RunMode

    report = incubation_report(
        recorded_events,
        initial_cash=Money.from_major(100_000.0, Currency.INR),
        final_cash=port.cash,
    )
    print(report.summary())
    require_promotion(RunMode.PAPER, RunMode.LIVE, incubation=report)
"""

from __future__ import annotations

import math
import statistics
from collections.abc import Sequence
from dataclasses import dataclass, field

from honba.adapters.models import RunMode
from honba.domain.money import Money
from honba.entities.order import OrderSide
from honba.strategies.execution import (
    Accepted,
    ExecutionEvent,
    Fill,
    Submitted,
    event_order_id,
)

__all__ = [
    "IncubationGates",
    "IncubationResult",
    "PromotionRefused",
    "incubation_report",
    "require_promotion",
]

NS_PER_MS = 10**6
NS_PER_DAY = 86_400 * 10**9


@dataclass(frozen=True, slots=True)
class IncubationGates:
    """What a paper incubation must demonstrate before live capital.

    Defaults are deliberate: at least one full day of recorded sessions, 95% of
    orders filled, a median venue acknowledgement within one second, and books
    that balance to the paisa.
    """

    min_days: float = 1.0
    min_fill_rate: float = 0.95
    max_median_ack_ms: float = 1_000.0
    max_accounting_drift: float = 0.0

    def __post_init__(self) -> None:
        if not (math.isfinite(self.min_days) and self.min_days >= 0.0):
            raise ValueError(f"min_days must be a finite number >= 0, got {self.min_days}")
        if not (math.isfinite(self.min_fill_rate) and 0.0 <= self.min_fill_rate <= 1.0):
            raise ValueError(
                f"min_fill_rate must be a finite number in [0, 1], got {self.min_fill_rate}"
            )
        if not (math.isfinite(self.max_median_ack_ms) and self.max_median_ack_ms >= 0.0):
            raise ValueError(
                f"max_median_ack_ms must be a finite number >= 0, got {self.max_median_ack_ms}"
            )
        if not (math.isfinite(self.max_accounting_drift) and self.max_accounting_drift >= 0.0):
            raise ValueError(
                "max_accounting_drift must be a finite number >= 0, "
                f"got {self.max_accounting_drift}"
            )


def incubation_report(
    events: Sequence[ExecutionEvent],
    *,
    initial_cash: Money,
    final_cash: Money,
    gates: IncubationGates | None = None,
) -> IncubationResult:
    """Judge one paper incubation's recorded event stream against ``gates``.

    Args:
        events: Everything the port drained during the incubation, in order
            (``Submitted`` / ``Accepted`` / ``Fill`` / ``Rejected`` / ...).
        initial_cash / final_cash: The books before and after — the *booked*
            cash (what a port reports as ``cash``, not the settlement-available
            balance), same currency.
        gates: Thresholds to judge against; ``IncubationGates()`` defaults.

    Raises ``ValueError`` on a malformed record: mixed currencies, an ack before
    its submission (non-monotonic timestamps), or a ``Fill`` without an order id.
    """
    gates = IncubationGates() if gates is None else gates
    if not isinstance(initial_cash, Money) or not isinstance(final_cash, Money):
        raise TypeError("initial_cash and final_cash must be Money (integer minor units)")
    if initial_cash.currency != final_cash.currency:
        raise ValueError(
            f"currency mismatch: initial {initial_cash.currency}, final {final_cash.currency}"
        )

    order_ids: set[str] = set()
    filled: set[str] = set()
    submitted_ts: dict[str, int] = {}
    accepted_ts: dict[str, int] = {}
    timestamps: list[int] = []
    expected = initial_cash

    for ev in events:
        timestamps.append(_event_ts(ev))
        oid = event_order_id(ev)
        order_ids.add(oid)
        if isinstance(ev, Submitted):
            submitted_ts.setdefault(oid, ev.ts)
        elif isinstance(ev, Accepted):
            accepted_ts.setdefault(oid, ev.ts)
        elif isinstance(ev, Fill):
            filled.add(oid)
            expected = expected + _cash_flow(ev, initial_cash.currency)

    acks: list[int] = []
    for oid, sub_ts in submitted_ts.items():
        acc_ts = accepted_ts.get(oid)
        if acc_ts is None:
            continue  # submitted but never acknowledged: no latency sample, no free pass
        delta = acc_ts - sub_ts
        if delta < 0:
            raise ValueError(
                f"non-monotonic timestamps: order {oid} acknowledged before it was submitted"
            )
        acks.append(delta)

    orders = len(order_ids)
    fill_rate = len(filled) / orders if orders else 0.0
    median_ack_ms = statistics.median(acks) / NS_PER_MS if acks else None
    span_days = (max(timestamps) - min(timestamps)) / NS_PER_DAY if timestamps else 0.0
    drift = abs((final_cash - expected).to_major())

    return IncubationResult(
        orders=orders,
        filled=len(filled),
        fill_rate=fill_rate,
        median_ack_ms=median_ack_ms,
        span_days=span_days,
        accounting_drift=drift,
        gates=gates,
    )


@dataclass(frozen=True, slots=True)
class IncubationResult:
    """The incubation's aggregates and the gate verdict."""

    orders: int
    filled: int
    fill_rate: float
    median_ack_ms: float | None
    span_days: float
    accounting_drift: float
    gates: IncubationGates = field(repr=False)

    @property
    def checks(self) -> dict[str, bool]:
        """Each gate by name. Equality sits on the passing side (the documented >=)."""
        return {
            "period": self.span_days >= self.gates.min_days,
            "fill_rate": self.fill_rate >= self.gates.min_fill_rate,
            "ack_latency": self.median_ack_ms is not None
            and self.median_ack_ms <= self.gates.max_median_ack_ms,
            "accounting": self.accounting_drift <= self.gates.max_accounting_drift,
        }

    @property
    def passed(self) -> bool:
        """True only when every gate passes."""
        return all(self.checks.values())

    def summary(self) -> str:
        """A human- and log-friendly one-block rendering of the verdict."""
        ack = "n/a" if self.median_ack_ms is None else f"{self.median_ack_ms:.1f} ms"
        lines = [
            f"Paper incubation: {self.orders} orders over {self.span_days:.2f} days",
            (
                f"Fill rate: {self.fill_rate:.3f} (>= {self.gates.min_fill_rate:.3f}), "
                f"median ack {ack} (<= {self.gates.max_median_ack_ms:.1f} ms), "
                f"accounting drift {self.accounting_drift:.2f} "
                f"(<= {self.gates.max_accounting_drift:.2f})"
            ),
        ]
        if self.passed:
            lines.append("RESULT: PASSED")
        else:
            failed = sum(1 for ok in self.checks.values() if not ok)
            lines.append(f"RESULT: FAILED ({failed} of {len(self.checks)} checks failed)")
        return "\n".join(lines)


class PromotionRefused(RuntimeError):
    """Raised when a run mode promotion lacks a passed paper incubation."""


def require_promotion(
    current: RunMode,
    target: RunMode,
    *,
    incubation: IncubationResult | None = None,
) -> None:
    """Enforce the mandatory Backtest -> Paper -> Live path; raise if refused.

    * Demotions (``target`` is ``BACKTEST`` or ``current`` is ``LIVE``) and staying
      put are always allowed: exposure only ever decreases.
    * ``BACKTEST -> PAPER`` is open: paper carries no capital risk.
    * ``PAPER -> LIVE`` requires ``incubation`` and ``incubation.passed``;
      otherwise :class:`PromotionRefused` names the missing or failed gates.
    * ``BACKTEST -> LIVE`` is refused outright: the paper hop is not optional.
    """
    for mode in (current, target):
        if not isinstance(mode, RunMode):
            raise TypeError(f"RunMode expected, got {type(mode).__name__}")
    if target is RunMode.BACKTEST or target is current or current is RunMode.LIVE:
        # Demotions, staying put: capital exposure only ever decreases, always allowed.
        return
    if target is RunMode.PAPER:
        return  # entering paper is open: paper carries no capital risk
    if current is RunMode.BACKTEST:
        raise PromotionRefused(
            "BACKTEST -> LIVE skips paper incubation: promote through RunMode.PAPER first"
        )
    # The only forward hop left is PAPER -> LIVE.
    if incubation is None:
        raise PromotionRefused(
            "paper -> live requires an incubation report: run the paper incubation first"
        )
    if not incubation.passed:
        failed = ", ".join(name for name, ok in incubation.checks.items() if not ok)
        raise PromotionRefused(
            f"paper incubation failed (gates not met: {failed}); "
            "extend the incubation or fix the strategy before live deployment"
        )


# -- internals -----------------------------------------------------------------
def _event_ts(ev: ExecutionEvent) -> int:
    """When ``ev`` happened (a fill reads it from its trade)."""
    return ev.trade.ts if isinstance(ev, Fill) else ev.ts


def _cash_flow(ev: Fill, currency) -> Money:
    """Signed booked cash of one fill: buys leave, sells arrive (costs included)."""
    trade = ev.trade
    if trade.costs.currency != currency:
        raise ValueError(f"currency mismatch: fill costs {trade.costs.currency}, books {currency}")
    notional = Money.mul_qty(trade.quantity, trade.price, currency)
    if trade.side is OrderSide.SELL:
        return notional - trade.costs
    return Money.zero(currency) - (notional + trade.costs)
