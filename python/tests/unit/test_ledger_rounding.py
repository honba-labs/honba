"""Conservative rounding at lots, ticks, positions and accounts (ADR 0011).

Mirrors ``crates/honba-entities/src/tests/{instrument,position}.rs``.
"""

from __future__ import annotations

import math

import pytest

from honba.domain.instrument import Instrument, InstrumentId, InstrumentKind
from honba.domain.money import Currency, Money
from honba.domain.portfolio import Account
from honba.domain.position import Position, PositionSide

X = InstrumentId("X", "NSE")


def nifty() -> Instrument:
    return Instrument(X, InstrumentKind.FUTURE, lot_size=75.0, tick_size=0.05)


def test_a_stake_rounds_up_to_the_next_lot_multiple():
    i = nifty()
    assert [i.stake_quantity(q) for q in (1.0, 75.0, 76.0, 0.0)] == [75.0, 75.0, 150.0, 0.0]


def test_a_stake_on_a_lot_multiple_with_float_noise_is_not_bumped_a_lot():
    fx = Instrument(X, InstrumentKind.FX, lot_size=0.1, tick_size=0.0001, currency="USD")
    assert fx.stake_quantity(0.1 * 3.0) == pytest.approx(0.3, abs=1e-12)


@pytest.mark.parametrize("bad", [-1.0, math.nan, math.inf])
def test_a_stake_quantity_rejects_negative_and_non_finite(bad: float):
    with pytest.raises(ValueError):
        nifty().stake_quantity(bad)


def test_tick_alignment_tolerates_float_noise_only():
    i = nifty()
    assert i.is_on_tick(22_000.05) and i.is_on_tick(100.0) and i.is_on_tick(0.1 + 0.2 - 0.25)
    assert not i.is_on_tick(100.03)
    assert not i.is_on_tick(math.nan)


def test_settlement_rejects_an_off_tick_price_and_rounds_on_tick_notional_once():
    i = nifty()
    with pytest.raises(ValueError, match="tick"):
        i.settle_notional(75.0, 100.03)
    assert i.settle_notional(75.0, 22_000.05) == Money(165_000_375, Currency.INR)


def test_avg_price_rounds_to_the_minor_unit_on_every_fill():
    p = Position(X)
    p.apply_fill(PositionSide.LONG, 3.0, 10.00)
    p.apply_fill(PositionSide.LONG, 1.0, 10.01)  # exact average 10.0025
    assert p.avg_price == 10.00
    p.apply_fill(PositionSide.SHORT, 4.0, 10.01)
    assert p.realized_pnl == Money(4, Currency.INR)


def test_opening_and_reversing_fills_also_round_avg_price():
    p = Position(X)
    p.apply_fill(PositionSide.LONG, 1.0, 10.006)
    assert p.avg_price == 10.01
    p.apply_fill(PositionSide.SHORT, 3.0, 9.994)
    assert p.side is PositionSide.SHORT and p.avg_price == 9.99


def test_realized_pnl_accumulates_exactly_over_many_fills():
    p = Position(X)
    for _ in range(1000):
        p.apply_fill(PositionSide.LONG, 1.0, 0.1)
        p.apply_fill(PositionSide.SHORT, 1.0, 0.1 + 0.2)
    assert p.realized_pnl == Money(20_000, Currency.INR)


def test_account_cash_is_integer_money():
    acct = Account("MAIN", Money(1_000, Currency.INR))
    acct.debit(Money(250, Currency.INR))
    acct.credit(Money.payout_from_major(0.019, Currency.INR))
    assert acct.cash == Money(751, Currency.INR)
    with pytest.raises(ValueError, match="currency"):
        acct.debit(Money(1, Currency.USD))
    assert acct.cash == Money(751, Currency.INR)
    with pytest.raises(TypeError):
        acct.credit(1.5)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="insufficient"):
        acct.debit(Money(752, Currency.INR))
    with pytest.raises(ValueError, match=">= 0"):
        acct.credit(Money(-1, Currency.INR))
    assert acct.cash == Money(751, Currency.INR)
