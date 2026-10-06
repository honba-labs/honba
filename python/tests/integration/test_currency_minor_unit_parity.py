"""Integration: the Python currency table is the Rust table (ADR 0011).

``honba.domain.money`` reads ``honba._honba.currency_minor_units``; this pins
every currency against the native table and the shared conformance vector that
``crates/honba-entities/tests/currency_minor_unit.rs`` also reads, and runs a
position/account flow in each currency.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from honba.domain.instrument import InstrumentId
from honba.domain.money import Currency, Money
from honba.domain.portfolio import Account
from honba.domain.position import Position, PositionSide

_honba = pytest.importorskip("honba._honba")

VECTORS = (
    Path(__file__).resolve().parents[3] / "schema" / "conformance" / "currency_minor_units.json"
)


def test_python_currency_table_equals_the_rust_table():
    table = _honba.currency_minor_units()
    assert set(table) == {c.value for c in Currency}
    for c in Currency:
        exponent, singular, plural = table[c.value]
        assert c.minor_exponent == exponent
        assert (c.minor_unit.singular, c.minor_unit.plural) == (singular, plural)


def test_rust_table_matches_the_shared_vector():
    doc = json.loads(VECTORS.read_text())
    table = _honba.currency_minor_units()
    for case in doc["cases"]:
        v = case["value"]
        assert table[v["currency"]] == (v["minor_exponent"], v["singular"], v["plural"])


@pytest.mark.parametrize("currency", list(Currency))
def test_position_and_account_settle_in_each_currency(currency):
    x = InstrumentId("X", "NSE")
    acct = Account("MAIN", Money(10_000_000, currency))
    pos = Position(x, currency=currency, realized_pnl=Money.zero(currency))
    pos.apply_fill(PositionSide.LONG, 100.0, 10.0)
    pos.apply_fill(PositionSide.SHORT, 40.0, 12.0)
    assert pos.realized_pnl.amount == 8_000
    acct.credit(Money(8_000, currency))
    assert acct.cash.format_minor().endswith(currency.minor_unit.plural)
