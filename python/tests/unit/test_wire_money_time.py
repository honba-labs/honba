"""Wire ``Money`` (integer minor units, legacy-float reader) and ``UnixNanos`` helpers.

Mirrors the Rust readers in ``honba_entities::{Money, Trade, Position}`` and
``honba_messages::UnixNanos`` (ADR 0011, E11-S2).
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from honba.domain.money import Currency
from honba.domain.money import Money as DomainMoney
from honba.entities import wire

TRADE = {
    "order_id": "O-1",
    "instrument_id": {"symbol": "NIFTY50", "exchange": "NSE"},
    "side": "buy",
    "quantity": 75.0,
    "price": 22000.0,
    "ts_event": {"iso": "1970-01-01T00:00:00.000000001Z", "unix_nanos": "1"},
    "ts_init": {"iso": "1970-01-01T00:00:00.000000001Z", "unix_nanos": "1"},
}


def test_money_amount_is_an_integer_and_not_a_string():
    assert wire.Money.model_validate({"amount": 4567, "currency": "INR"}).amount == 4567
    with pytest.raises(ValidationError):
        wire.Money.model_validate({"amount": "4567", "currency": "INR"})
    with pytest.raises(ValidationError):
        wire.Money.model_validate({"amount": True, "currency": "INR"})


def test_a_legacy_float_money_amount_is_major_units_rounded_once():
    m = wire.Money.model_validate({"amount": 45.676, "currency": "INR"})
    assert m.amount == 4568
    assert wire.Money.model_validate({"amount": 0.125, "currency": "INR"}).amount == 13
    with pytest.raises(ValidationError):
        wire.Money.model_validate({"amount": 1e30, "currency": "INR"})


def test_legacy_float_trade_costs_are_inr_minor_units():
    t = wire.Trade.model_validate({**TRADE, "costs": 45.67})
    assert t.costs == wire.Money(amount=4567, currency=Currency.INR)
    assert t.model_dump(mode="json")["costs"] == {"amount": 4567, "currency": "INR"}


def test_legacy_float_realized_pnl_takes_the_position_currency():
    p = wire.Position.model_validate(
        {
            "instrument_id": {"symbol": "X", "exchange": "NSE"},
            "currency": "USD",
            "side": "short",
            "quantity": 1.0,
            "avg_price": 1.0,
            "realized_pnl": -80.0,
        }
    )
    assert p.realized_pnl.to_domain() == DomainMoney(-8000, Currency.USD)


@pytest.mark.parametrize(
    ("ns", "iso"),
    [
        (0, "1970-01-01T00:00:00.000000000Z"),
        (1_000, "1970-01-01T00:00:00.000001000Z"),
        (1_700_000_060_000_000_000, "2023-11-14T22:14:20.000000000Z"),
        (1_700_000_060_123_456_789, "2023-11-14T22:14:20.123456789Z"),
    ],
)
def test_unix_nanos_from_ns_matches_the_rust_iso_form(ns: int, iso: str):
    ts = wire.UnixNanos.from_ns(ns)
    assert (ts.iso, ts.unix_nanos) == (iso, str(ns))
    assert ts.to_ns() == ns


def test_unix_nanos_rejects_values_outside_u64():
    for bad in (-1, 2**64):
        with pytest.raises(ValueError):
            wire.UnixNanos.from_ns(bad)
    with pytest.raises(ValidationError):
        wire.UnixNanos.model_validate({"iso": "x", "unix_nanos": "-5"})


def test_message_wrap_stamps_a_correct_iso_for_large_timestamps():
    bar = wire.BarEvent.model_validate(
        {
            "type": "bar",
            "bar_type": {
                "instrument_id": {"symbol": "X", "exchange": "NSE"},
                "spec": {"step": 1, "aggregation": "minute", "price_type": "last"},
            },
            "open": 1.0,
            "high": 1.0,
            "low": 1.0,
            "close": 1.0,
            "volume": 1.0,
            "ts_event": wire.UnixNanos.from_ns(5).model_dump(),
            "ts_init": wire.UnixNanos.from_ns(5).model_dump(),
        }
    )
    msg = wire.Message.wrap(bar, 1_700_000_060_000_000_000)
    assert msg.ts_init == wire.UnixNanos.from_ns(1_700_000_060_000_000_000)
