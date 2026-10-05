"""Integration: integer money across instrument, account, position and the wire (ADR 0011).

Wires the Python domain types together and checks the legacy-float reader
against the Rust one through ``honba._honba.canonical_json``.
"""

from __future__ import annotations

import json

import pytest

from honba.domain.instrument import Instrument, InstrumentId, InstrumentKind
from honba.domain.money import Currency, Money
from honba.domain.portfolio import Account
from honba.domain.position import Position, PositionSide
from honba.entities import wire

X = InstrumentId("X", "NSE")


def nifty() -> Instrument:
    return Instrument(X, InstrumentKind.FUTURE, lot_size=75.0, tick_size=0.05)


def test_account_lockstep_with_a_position_over_many_round_trips():
    # Integration of Instrument, Account and Position (mirrors
    # crates/honba-entities/tests/money_rounding.rs).
    i = nifty()
    start = Money(100_000_000, Currency.INR)
    acct = Account("MAIN", start)
    pos = Position(X)
    for k in range(500):
        buy = 100.0 + 0.05 * (k % 7)
        sell = buy + 0.1 + 0.05
        acct.debit(i.settle_notional(75.0, buy))
        pos.apply_fill(PositionSide.LONG, 75.0, buy)
        acct.credit(i.settle_notional(75.0, sell))
        pos.apply_fill(PositionSide.SHORT, 75.0, sell)
    assert pos.is_flat
    assert pos.realized_pnl == Money(500 * 1125, Currency.INR)
    assert acct.cash - start == pos.realized_pnl


TS = {"iso": "1970-01-01T00:00:00.000000001Z", "unix_nanos": "1"}


@pytest.mark.parametrize("costs", [45.67, 0.125, 0, 12, {"amount": 4567, "currency": "INR"}])
def test_python_and_rust_read_legacy_trade_costs_identically(costs):
    from honba import _honba

    raw = {
        "order_id": "O-1",
        "instrument_id": {"symbol": "X", "exchange": "NSE"},
        "side": "sell",
        "quantity": 1.0,
        "price": 10.0,
        "costs": costs,
        "ts_event": TS,
        "ts_init": TS,
    }
    rust = json.loads(_honba.canonical_json("Trade", json.dumps(raw)))
    python = wire.Trade.model_validate(raw).model_dump(mode="json")
    assert python == rust
    assert isinstance(rust["costs"]["amount"], int)
