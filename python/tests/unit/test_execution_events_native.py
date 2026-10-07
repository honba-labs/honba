"""``events_from_native``: ``_honba`` event dicts -> ``ExecutionEvent`` dataclasses (ADR 0019)."""

from __future__ import annotations

import pytest

from honba.domain.money import Currency
from honba.entities.instrument import InstrumentId
from honba.entities.order import OrderIntent, OrderSide
from honba.strategies.execution import (
    Accepted,
    Cancelled,
    CancelRequested,
    Expired,
    Fill,
    Rejected,
    Submitted,
    events_from_native,
)

IID = InstrumentId("AAA", "NSE")


def raw(kind: str, **extra: object) -> dict[str, object]:
    base: dict[str, object] = {
        "kind": kind,
        "order_id": "o-0",
        "symbol": "AAA",
        "exchange": "NSE",
        "side": "buy",
        "quantity": 4.0,
        "ts": 9,
        "venue_order_id": None,
    }
    return {**base, **extra}


def test_every_kind_maps_to_its_dataclass() -> None:
    got = events_from_native(
        [
            raw("submitted"),
            raw("accepted", venue_order_id="V-1"),
            raw("rejected", reason="no_position"),
            raw("fill", price=110.0, costs=250, cum_qty=4.0, complete=True),
            {"kind": "cancel_requested", "order_id": "o-0", "ts": 5},
            raw("cancelled"),
            raw("expired", side="sell"),
        ]
    )
    intent = OrderIntent(IID, OrderSide.BUY, 4.0)
    assert got[0] == Submitted("o-0", intent, 9)
    assert got[1] == Accepted("o-0", intent, 9, "V-1")
    assert got[2] == Rejected("o-0", intent, "no_position", 9)
    assert isinstance(got[3], Fill)
    assert (got[3].trade.price, got[3].trade.costs.amount, got[3].cum_qty) == (110.0, 250, 4.0)
    assert got[3].complete is True
    assert got[3].trade.order_id == "o-0"
    assert got[4] == CancelRequested("o-0", 5)
    assert got[5] == Cancelled("o-0", intent, 9)
    assert got[6] == Expired("o-0", OrderIntent(IID, OrderSide.SELL, 4.0), 9)


def test_user_id_and_intent_hooks_apply_at_the_boundary() -> None:
    limit = OrderIntent.limit_buy(IID, 9.0, 99.0)
    got = events_from_native(
        [raw("cancelled", order_id="native-o-0", quantity=3.0)],
        user_id=lambda s: s.removeprefix("native-"),
        intent_of=lambda oid, default: limit if oid == "o-0" else default,
    )
    assert got == [Cancelled("o-0", limit, 9)]


def test_currency_is_applied_to_fill_costs() -> None:
    (fill,) = events_from_native(
        [raw("fill", price=1.0, costs=5, cum_qty=4.0, complete=False)], currency="USD"
    )
    assert isinstance(fill, Fill)
    assert fill.trade.costs.currency is Currency.USD


def test_an_unknown_kind_is_a_value_error() -> None:
    with pytest.raises(ValueError, match="kind"):
        events_from_native([raw("teleported")])
