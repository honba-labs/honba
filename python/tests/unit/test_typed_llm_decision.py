"""Unit tests for typed LLM decisions (E5-S7).

Acceptance: `schema_rejects_increase`, `decision_in_journal`.
"""

import pytest

from honba.ai.decision import (
    DecisionKind,
    LlmDecision,
    apply_llm_decision,
    validate_llm_decision,
)
from honba.domain.instrument import InstrumentId
from honba.domain.order import OrderIntent, OrderSide


def test_schema_rejects_increase() -> None:
    # 1. Reject decided quantity > original quantity
    with pytest.raises(ValueError, match="schema_rejects_increase"):
        LlmDecision(
            order_id="ord-1",
            symbol="RELIANCE",
            side=OrderSide.BUY,
            original_quantity=100.0,
            decision=DecisionKind.SIZE_DOWN,
            decided_quantity=120.0,
            rationale="attempting to scale up",
        )

    # 2. Reject non-positive original quantity
    with pytest.raises(ValueError, match="original_quantity must be positive"):
        LlmDecision(
            order_id="ord-2",
            symbol="RELIANCE",
            side=OrderSide.BUY,
            original_quantity=0.0,
            decision=DecisionKind.VETO,
            decided_quantity=0.0,
            rationale="zero qty",
        )

    # 3. Reject veto with non-zero quantity
    with pytest.raises(ValueError, match="veto decision must have decided_quantity 0.0"):
        LlmDecision(
            order_id="ord-3",
            symbol="RELIANCE",
            side=OrderSide.BUY,
            original_quantity=50.0,
            decision=DecisionKind.VETO,
            decided_quantity=10.0,
            rationale="veto with remainder",
        )

    # 4. Reject approve with modified quantity
    with pytest.raises(ValueError, match="approve decision must keep original_quantity"):
        LlmDecision(
            order_id="ord-4",
            symbol="RELIANCE",
            side=OrderSide.BUY,
            original_quantity=50.0,
            decision=DecisionKind.APPROVE,
            decided_quantity=25.0,
            rationale="approve but modified",
        )

    # 5. Reject size_down with equal or zero quantity
    with pytest.raises(ValueError, match="size_down decision must have 0 < decided < original"):
        LlmDecision(
            order_id="ord-5",
            symbol="RELIANCE",
            side=OrderSide.BUY,
            original_quantity=50.0,
            decision=DecisionKind.SIZE_DOWN,
            decided_quantity=50.0,
            rationale="size_down unchanged",
        )

    with pytest.raises(ValueError, match="size_down decision must have 0 < decided < original"):
        LlmDecision(
            order_id="ord-6",
            symbol="RELIANCE",
            side=OrderSide.BUY,
            original_quantity=50.0,
            decision=DecisionKind.SIZE_DOWN,
            decided_quantity=0.0,
            rationale="size_down zero",
        )


def test_valid_decisions_and_application() -> None:
    intent = OrderIntent.market_buy(InstrumentId("RELIANCE", "NSE"), 100.0)

    # 1. Approve
    dec_app = LlmDecision(
        order_id="intent-1",
        symbol="RELIANCE",
        side=OrderSide.BUY,
        original_quantity=100.0,
        decision=DecisionKind.APPROVE,
        decided_quantity=100.0,
        rationale="market trend healthy",
        confidence=0.95,
    )
    result_app = apply_llm_decision(intent, dec_app)
    assert result_app is not None
    assert result_app.quantity == 100.0
    assert result_app == intent

    # 2. Size Down
    dec_size = LlmDecision(
        order_id="intent-1",
        symbol="RELIANCE",
        side=OrderSide.BUY,
        original_quantity=100.0,
        decision=DecisionKind.SIZE_DOWN,
        decided_quantity=40.0,
        rationale="high intraday volatility; reducing size by 60%",
        confidence=0.8,
    )
    result_size = apply_llm_decision(intent, dec_size)
    assert result_size is not None
    assert result_size.quantity == 40.0
    assert result_size.instrument_id == intent.instrument_id
    assert result_size.side == intent.side

    # 3. Veto
    dec_veto = LlmDecision(
        order_id="intent-1",
        symbol="RELIANCE",
        side=OrderSide.BUY,
        original_quantity=100.0,
        decision=DecisionKind.VETO,
        decided_quantity=0.0,
        rationale="adverse news breakout detected; cancelling order",
        confidence=0.99,
    )
    result_veto = apply_llm_decision(intent, dec_veto)
    assert result_veto is None


def test_symbol_and_side_mismatches_rejected() -> None:
    intent = OrderIntent.market_buy(InstrumentId("RELIANCE", "NSE"), 100.0)

    # Mismatched symbol
    dec_sym = LlmDecision(
        order_id="intent-1",
        symbol="TCS",
        side=OrderSide.BUY,
        original_quantity=100.0,
        decision=DecisionKind.APPROVE,
        decided_quantity=100.0,
        rationale="ok",
    )
    with pytest.raises(ValueError, match="symbol mismatch"):
        dec_sym.apply_to_intent(intent)

    # Mismatched side
    dec_side = LlmDecision(
        order_id="intent-1",
        symbol="RELIANCE",
        side=OrderSide.SELL,
        original_quantity=100.0,
        decision=DecisionKind.APPROVE,
        decided_quantity=100.0,
        rationale="ok",
    )
    with pytest.raises(ValueError, match="side mismatch"):
        dec_side.apply_to_intent(intent)


def test_decision_in_journal() -> None:
    dec = LlmDecision(
        order_id="order-xyz-10",
        symbol="INFY",
        side=OrderSide.SELL,
        original_quantity=200.0,
        decision=DecisionKind.SIZE_DOWN,
        decided_quantity=75.0,
        rationale="support level nearby, trimming short stake",
        confidence=0.88,
    )
    record = dec.to_journal_record()
    assert record["type"] == "llm_decision_recorded"
    assert record["order_id"] == "order-xyz-10"
    assert record["decision"] == "size_down"
    assert record["original_qty"] == 200.0
    assert record["decided_qty"] == 75.0
    assert record["confidence"] == 0.88
    assert "support level nearby" in record["rationale"]


def test_validate_llm_decision_from_raw_dict() -> None:
    intent = OrderIntent.market_buy(InstrumentId("TCS", "NSE"), 50.0)

    # Valid size down from LLM json response
    raw_response = {
        "order_id": "tcs-intent",
        "decision": "size_down",
        "decided_quantity": 25.0,
        "rationale": "trimming exposure",
        "confidence": 0.75,
    }
    validated = validate_llm_decision(raw_response, original_intent=intent)
    assert validated.decision == DecisionKind.SIZE_DOWN
    assert validated.original_quantity == 50.0
    assert validated.decided_quantity == 25.0
    assert validated.symbol == "TCS"
    assert validated.side == OrderSide.BUY

    # Invalid attempt to increase from raw LLM output
    bad_response = {
        "order_id": "tcs-intent",
        "decision": "size_down",
        "decided_quantity": 75.0,
        "rationale": "hallucinated size increase",
    }
    with pytest.raises(ValueError, match="schema_rejects_increase"):
        validate_llm_decision(bad_response, original_intent=intent)
