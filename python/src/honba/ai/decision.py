"""Typed LLM decisions (veto and size-down only) for AI-assisted execution (E5-S7).

Enforces safety bounds on AI interventions: an LLM can veto or reduce the quantity
of an existing order or intent, but can never increase exposure, flip side, or create
unsolicited positions.
"""

from __future__ import annotations

import dataclasses
import math
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from honba.domain.order import OrderIntent, OrderSide

__all__ = [
    "DecisionKind",
    "LlmDecision",
    "apply_llm_decision",
    "validate_llm_decision",
]


class DecisionKind(str, Enum):
    """The kind of decision an LLM can make on an order or intent."""

    APPROVE = "approve"
    VETO = "veto"
    SIZE_DOWN = "size_down"


@dataclass(frozen=True, slots=True)
class LlmDecision:
    """A validated, typed decision produced by an AI/LLM model.

    Safety constraints enforced by ``schema_rejects_increase``:
    - ``original_quantity`` must be positive and finite.
    - ``decided_quantity`` can NEVER exceed ``original_quantity``.
    - ``VETO`` requires ``decided_quantity == 0.0``.
    - ``APPROVE`` requires ``decided_quantity == original_quantity``.
    - ``SIZE_DOWN`` requires ``0.0 < decided_quantity < original_quantity``.
    """

    order_id: str
    symbol: str
    side: OrderSide | str
    original_quantity: float
    decision: DecisionKind
    decided_quantity: float
    rationale: str
    confidence: float | None = None
    metadata: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not (math.isfinite(self.original_quantity) and self.original_quantity > 0):
            raise ValueError(
                f"original_quantity must be positive and finite, got {self.original_quantity}"
            )

        if not math.isfinite(self.decided_quantity):
            raise ValueError(f"decided_quantity must be finite, got {self.decided_quantity}")

        # Hard invariant: LLM decisions cannot increase quantity
        if self.decided_quantity > self.original_quantity + 1e-9:
            raise ValueError(
                f"LLM decision cannot increase quantity: original {self.original_quantity}, "
                f"decided {self.decided_quantity} (schema_rejects_increase)"
            )

        if self.decision == DecisionKind.VETO:
            if abs(self.decided_quantity) > 1e-9:
                raise ValueError(
                    f"veto decision must have decided_quantity 0.0, got {self.decided_quantity}"
                )
        elif self.decision == DecisionKind.APPROVE:
            if abs(self.decided_quantity - self.original_quantity) > 1e-9:
                raise ValueError(
                    f"approve decision must keep original_quantity {self.original_quantity}, "
                    f"got {self.decided_quantity}"
                )
        elif self.decision == DecisionKind.SIZE_DOWN and (
            self.decided_quantity <= 1e-9 or self.decided_quantity >= self.original_quantity - 1e-9
        ):
            raise ValueError(
                f"size_down decision must have 0 < decided < original: "
                f"original {self.original_quantity}, decided {self.decided_quantity}"
            )

        if self.confidence is not None and not (0.0 <= self.confidence <= 1.0):
            raise ValueError(f"confidence must be in [0.0, 1.0], got {self.confidence}")

    def apply_to_intent(self, intent: OrderIntent) -> OrderIntent | None:
        """Apply this decision to an OrderIntent.

        Returns:
            - ``None`` if vetoed (cancelled).
            - The original ``OrderIntent`` if approved.
            - A new ``OrderIntent`` with reduced quantity if sized down.
        """
        intent_sym = intent.instrument_id.symbol
        if self.symbol != intent_sym:
            raise ValueError(
                f"symbol mismatch: decision is for {self.symbol}, intent is for {intent_sym}"
            )

        intent_side_str = (
            intent.side.value if isinstance(intent.side, OrderSide) else str(intent.side)
        )
        decision_side_str = self.side.value if isinstance(self.side, OrderSide) else str(self.side)
        if intent_side_str.lower() != decision_side_str.lower():
            raise ValueError(
                f"side mismatch: decision is for {self.side}, intent is for {intent.side}"
            )

        if self.decision == DecisionKind.VETO:
            return None
        if self.decision == DecisionKind.APPROVE:
            return intent
        if self.decision == DecisionKind.SIZE_DOWN:
            return dataclasses.replace(intent, quantity=self.decided_quantity)
        return intent

    def to_journal_record(self) -> dict[str, Any]:
        """Convert to standard Journal v1 NDJSON audit entry."""
        return {
            "type": "llm_decision_recorded",
            "order_id": self.order_id,
            "decision": self.decision.value,
            "original_qty": self.original_quantity,
            "decided_qty": self.decided_quantity,
            "rationale": self.rationale,
            "confidence": self.confidence,
        }


def apply_llm_decision(
    intent: OrderIntent,
    decision: LlmDecision,
) -> OrderIntent | None:
    """Convenience helper to apply an LLM decision to an intent."""
    return decision.apply_to_intent(intent)


def validate_llm_decision(
    raw: dict[str, Any],
    original_intent: OrderIntent | None = None,
) -> LlmDecision:
    """Validate a raw LLM output dictionary into a typed LlmDecision.

    If ``original_intent`` is provided, fields are checked against it
    to ensure the LLM cannot flip sides, change symbols, or increase quantities.
    """
    order_id = str(raw.get("order_id", "intent-0"))
    symbol = str(raw.get("symbol", ""))
    side_raw = raw.get("side", "buy")
    decision_raw = raw.get("decision", "approve")

    if isinstance(decision_raw, DecisionKind):
        decision_kind = decision_raw
    else:
        try:
            decision_kind = DecisionKind(str(decision_raw).lower())
        except ValueError:
            raise ValueError(f"unknown decision kind {decision_raw!r}")

    orig_qty = float(raw.get("original_quantity", 0.0))
    decided_qty = float(raw.get("decided_quantity", orig_qty))
    rationale = str(raw.get("rationale", ""))
    confidence = (
        float(raw["confidence"]) if "confidence" in raw and raw["confidence"] is not None else None
    )

    if original_intent is not None:
        symbol = original_intent.instrument_id.symbol
        side_raw = original_intent.side
        orig_qty = original_intent.quantity

    return LlmDecision(
        order_id=order_id,
        symbol=symbol,
        side=side_raw,
        original_quantity=orig_qty,
        decision=decision_kind,
        decided_quantity=decided_qty,
        rationale=rationale,
        confidence=confidence,
    )
