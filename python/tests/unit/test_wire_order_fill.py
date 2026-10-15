"""The additive ledger fill fields of ``honba.wire.wire.Order`` (ADR 0030)."""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from honba.client.models import LedgerOrder
from honba.wire.wire import Order, RejectReason, UnixNanos

STAMP = {"iso": "1970-01-01T00:00:00.000000100Z", "unix_nanos": "100"}


def body(**over: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "order_id": "O-1",
        "instrument_id": {"symbol": "TCS", "exchange": "NSE"},
        "side": "buy",
        "order_type": "market",
        "quantity": 10.0,
        "price": None,
        "trigger_price": None,
        "status": "submitted",
        "time_in_force": "day",
        "ts_event": STAMP,
        "ts_init": STAMP,
    }
    row.update(over)
    return row


def test_a_row_from_an_older_server_parses_with_defaults_and_dumps_without_the_fields() -> None:
    order = Order.model_validate(body())
    assert order.filled_qty == 0.0
    assert order.avg_fill_price is None
    assert order.reject_reason is None
    assert order.filled_at is None
    dumped = order.model_dump(mode="json")
    for key in ("filled_qty", "avg_fill_price", "reject_reason", "filled_at"):
        assert key not in dumped


def test_fill_fields_round_trip() -> None:
    row = body(
        status="partially_filled",
        filled_qty=4.0,
        avg_fill_price=101.5,
        filled_at=STAMP,
        reject_reason={"code": "order_rejected", "message": "RMS: margin"},
    )
    order = Order.model_validate(row)
    assert order.filled_qty == 4.0
    assert order.avg_fill_price == 101.5
    assert order.filled_at == UnixNanos.from_ns(100)
    assert order.reject_reason == RejectReason(code="order_rejected", message="RMS: margin")
    assert order.model_dump(mode="json") == row


@pytest.mark.parametrize(
    "over",
    [{"filled_qty": 11.0}, {"filled_qty": -1.0}, {"avg_fill_price": 0.0}],
)
def test_invalid_fill_fields_are_rejected(over: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        Order.model_validate(body(**over))


def test_the_ledger_row_model_carries_filled_at() -> None:
    row = LedgerOrder.model_validate(body(status="filled", filled_qty=10.0, filled_at=STAMP))
    assert row.filled_at == UnixNanos.from_ns(100)
    assert LedgerOrder.model_validate(body()).filled_at is None
