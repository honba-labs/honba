"""Currency minor-unit accessors and exponent-generic money maths (ADR 0011).

Mirrors ``crates/honba-entities/src/tests/currency.rs``: the same table, the same
rounding at exponents 0 and 3, and the shared vector
``schema/conformance/currency_minor_units.json``.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

from honba.domain import money as money_mod
from honba.domain.money import Currency, Money

VECTORS = (
    Path(__file__).resolve().parents[3] / "schema" / "conformance" / "currency_minor_units.json"
)
DOC = json.loads(VECTORS.read_text())


@pytest.mark.parametrize("case", DOC["cases"], ids=lambda c: c["name"])
def test_currency_table_matches_shared_vector(case):
    v = case["value"]
    c = Currency(v["currency"])
    assert c.minor_exponent == v["minor_exponent"]
    assert c.minor_unit.singular == v["singular"]
    assert c.minor_unit.plural == v["plural"]


def test_shared_vector_covers_every_currency():
    assert {c["value"]["currency"] for c in DOC["cases"]} == {c.value for c in Currency}


@pytest.mark.parametrize("case", DOC["format_minor"], ids=lambda c: c["name"])
def test_format_minor_matches_shared_vector(case):
    m = Money(case["amount"], Currency(case["currency"]))
    assert m.format_minor() == case["text"]


@pytest.mark.parametrize("case", DOC["major_to_minor"], ids=lambda c: c["name"])
def test_major_to_minor_bounds_match_shared_vector(case):
    c = Currency(case["currency"])
    makers = (Money.from_major, Money.payout_from_major, Money.stake_from_major)
    for make in makers:
        if case["minor"] is None:
            with pytest.raises(ValueError, match="i64"):
                make(case["major"], c)
        else:
            assert make(case["major"], c).amount == case["minor"]


def test_minor_per_major_is_ten_to_the_exponent():
    for c in Currency:
        assert c.minor_per_major == 10**c.minor_exponent


def test_str_uses_the_currency_exponent():
    assert str(Money(12_345, Currency.INR)) == "INR 123.45"


@pytest.mark.parametrize(
    ("amount", "exp", "want"),
    [
        (12_345, 0, "XXX 12345"),
        (12_345, 2, "XXX 123.45"),
        (12_345, 3, "XXX 12.345"),
        (-5, 3, "XXX -0.005"),
    ],
)
def test_format_major_uses_exactly_exponent_decimals(amount, exp, want):
    assert money_mod._format_major("XXX", amount, exp) == want


@pytest.mark.parametrize(("amount", "exp", "want"), [(1_234, 0, 1_234.0), (1_234, 3, 1.234)])
def test_to_major_divides_by_the_exponent(amount, exp, want):
    assert money_mod._to_major(amount, exp) == want


@pytest.mark.parametrize(
    ("price", "exp", "want"),
    [(2.5, 0, 3.0), (-2.5, 0, -3.0), (1.2345, 3, 1.235), (1.2344, 3, 1.234), (1.2345, 2, 1.23)],
)
def test_position_price_rounding_follows_the_exponent(price, exp, want):
    from honba.domain import position

    assert position._round_to_exponent(price, exp) == want


def test_legacy_minor_per_major_constant_is_deprecated_but_available():
    with pytest.warns(DeprecationWarning, match="minor_per_major"):
        assert money_mod.MINOR_PER_MAJOR == 100
    with pytest.raises(AttributeError):
        money_mod.NOT_A_THING  # noqa: B018


@pytest.mark.parametrize(
    ("value", "exp", "want"),
    [
        (2.5, 0, 3),
        (-2.5, 0, -3),
        (2.4, 0, 2),
        (1.0005, 3, 1001),
        (-1.0005, 3, -1001),
        (1.0004, 3, 1000),
    ],
)
def test_round_half_away_from_zero_at_non_two_exponents(value, exp, want):
    assert money_mod._round_major_to_minor(value, exp) == want


@pytest.mark.parametrize(
    ("value", "exp", "floor", "ceil"),
    [
        (10.7, 0, 10, 11),
        (-10.2, 0, -11, -10),
        (1.2349, 3, 1234, 1235),
        (-1.2341, 3, -1235, -1234),
        (0.1 * 3, 3, 300, 300),
    ],
)
def test_payout_floor_and_stake_ceil_at_non_two_exponents(value, exp, floor, ceil):
    assert math.floor(money_mod._snapped(value, exp)) == floor
    assert math.ceil(money_mod._snapped(value, exp)) == ceil


@pytest.mark.parametrize("exp", [0, 2, 3])
def test_generic_scaling_rejects_non_finite_and_overflow(exp):
    with pytest.raises(ValueError, match="finite"):
        money_mod._round_major_to_minor(math.nan, exp)
    with pytest.raises(ValueError, match="i64"):
        money_mod._round_major_to_minor(1e300, exp)


def test_money_conversions_use_the_currency_exponent():
    for c in Currency:
        e = c.minor_exponent
        assert Money.from_major(10.005, c).amount == money_mod._round_major_to_minor(10.005, e)
        assert Money.payout_from_major(10.019, c).amount == 1001
        assert Money.stake_from_major(10.011, c).amount == 1002
