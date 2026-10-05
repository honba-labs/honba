"""Integer Money in minor units and its conservative rounding (ADR 0011).

Mirrors ``crates/honba-entities/src/tests/money.rs``: the same inputs give the
same minor units in Rust and Python.
"""

from __future__ import annotations

import math

import pytest

from honba.domain.money import Currency, Money
from honba.entities import Money as EntitiesMoney
from honba.entities import wire


def test_money_is_exported_from_honba_entities_with_one_currency_enum():
    assert EntitiesMoney is Money
    assert wire.Currency is Currency


@pytest.mark.parametrize(
    ("major", "minor"),
    [
        (0.125, 13),  # half away from zero; Python's round() would give 12
        (-0.125, -13),
        (0.375, 38),
        (2.5 / 100, 3),  # 2.5 paise
        (10.004, 1000),
        (-10.006, -1001),
    ],
)
def test_from_major_rounds_half_away_from_zero(major: float, minor: int):
    assert Money.from_major(major, Currency.INR).amount == minor


@pytest.mark.parametrize("bad", [math.nan, math.inf, -math.inf])
def test_from_major_rejects_non_finite(bad: float):
    with pytest.raises(ValueError, match="finite"):
        Money.from_major(bad, Currency.INR)


def test_from_major_rejects_overflow():
    with pytest.raises(ValueError, match="i64"):
        Money.from_major(1e30, Currency.INR)


def test_mul_qty_rounds_the_product_once():
    assert Money.mul_qty(3.0, 33.333, Currency.INR).amount == 10_000
    assert Money.mul_qty(-3.0, 33.333, Currency.INR).amount == -10_000
    with pytest.raises(ValueError):
        Money.mul_qty(math.nan, 1.0, Currency.INR)


def test_a_payout_floors_and_a_stake_rounds_up():
    assert Money.payout_from_major(10.019, Currency.INR).amount == 1001
    assert Money.payout_from_major(10.005, Currency.INR).amount == 1000
    assert Money.payout_from_major(-10.011, Currency.INR).amount == -1002
    assert Money.stake_from_major(10.011, Currency.INR).amount == 1002
    assert Money.stake_from_major(-10.019, Currency.INR).amount == -1001


def test_float_noise_on_an_exact_amount_is_not_rounded_against_anyone():
    assert Money.stake_from_major(0.1 * 3.0, Currency.INR).amount == 30
    assert Money.payout_from_major(0.7 * 3.0, Currency.INR).amount == 210


@pytest.mark.parametrize("bad", [math.nan, math.inf])
def test_payout_and_stake_reject_non_finite(bad: float):
    with pytest.raises(ValueError):
        Money.payout_from_major(bad, Currency.INR)
    with pytest.raises(ValueError):
        Money.stake_from_major(bad, Currency.INR)


def test_amount_must_be_an_integer():
    with pytest.raises(TypeError):
        Money(1.5, Currency.INR)  # type: ignore[arg-type]


def test_arithmetic_is_exact_and_currency_checked():
    total = Money.zero(Currency.INR)
    for _ in range(1000):
        total = total + Money.from_major(0.1, Currency.INR)
    assert total == Money(10_000, Currency.INR)
    with pytest.raises(ValueError, match="currency"):
        Money(1, Currency.INR) + Money(1, Currency.USD)


def test_wire_round_trip_is_the_integer_form():
    m = Money(4567, Currency.INR)
    w = wire.Money.from_domain(m)
    assert w.model_dump(mode="json") == {"amount": 4567, "currency": "INR"}
    assert w.to_domain() == m
