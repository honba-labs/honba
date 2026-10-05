"""Quantity and unit parser (Design.md Section 6).

Pure domain component: parses numeric values with multipliers and currency symbols
into unit-free numeric values and validates them against metric definitions.
"""

from __future__ import annotations

import decimal
import re
from dataclasses import dataclass
from typing import NamedTuple

from honba.markets.india.units import INDIA_CURRENCY_SYMBOLS, INDIA_MULTIPLIERS
from honba.wire.screener import MetricDefinition, UnitType, ValueType


class QuantityError(ValueError):
    """Raised when a quantity string is invalid."""


class UnitMismatchError(QuantityError):
    """Raised when a quantity unit/suffix does not match the metric's expected unit."""


class MultiplierSpec(NamedTuple):
    canonical: str
    multiplier: float
    aliases: tuple[str, ...]


SHARED_MULTIPLIERS: dict[str, MultiplierSpec] = {
    "k": MultiplierSpec("K", 1e3, ("k", "thousand")),
    "mn": MultiplierSpec("Mn", 1e6, ("mn", "million")),
    "bn": MultiplierSpec("Bn", 1e9, ("bn", "billion")),
    "tn": MultiplierSpec("Tn", 1e12, ("tn", "trillion")),
    "%": MultiplierSpec("%", 1e-2, ("%", "percent", "pct")),
}


@dataclass(frozen=True)
class Quantity:
    """Parsed quantity representing an evaluated numeric value and optional metadata."""

    value: float
    raw_number: float
    unit_suffix: str | None = None
    currency: str | None = None


_CURRENCY_PREFIXES = ("₹", "rs.", "rs", "inr", "$", "€", "£", "¥")


def _build_multiplier_map(market: str | None) -> dict[str, MultiplierSpec]:
    mapping: dict[str, MultiplierSpec] = {}
    for spec in SHARED_MULTIPLIERS.values():
        for alias in spec.aliases:
            mapping[alias.lower()] = spec

    if market == "india":
        for spec in INDIA_MULTIPLIERS.values():
            for alias in spec.aliases:
                mapping[alias.lower()] = spec

    return mapping


def _build_currency_map(market: str | None) -> dict[str, str]:
    if market == "india":
        return {k.lower(): v for k, v in INDIA_CURRENCY_SYMBOLS.items()}
    return {}


def parse_quantity(text: str, market: str | None = None) -> Quantity:
    """Parse a quantity string into a Quantity object."""
    cleaned = text.strip()
    if not cleaned:
        raise QuantityError("empty quantity string")

    mult_map = _build_multiplier_map(market)
    curr_map = _build_currency_map(market)

    # Check for currency prefix
    currency: str | None = None
    remaining = cleaned
    for prefix in sorted(_CURRENCY_PREFIXES, key=len, reverse=True):
        if remaining.lower().startswith(prefix):
            # matched prefix
            matched = prefix
            if matched.lower() in curr_map:
                currency = curr_map[matched.lower()]
            else:
                raise QuantityError(f"unsupported currency marker: {matched!r}")
            remaining = remaining[len(matched) :].strip()
            break

    # Extract number and suffix
    num_match = re.match(r"^([\+\-]?(?:\d+(?:\.\d*)?|\.\d+))\s*(.*)$", remaining)
    if not num_match:
        raise QuantityError(f"invalid quantity string: {text!r}")

    num_str, rest = num_match.groups()
    try:
        dec_num = decimal.Decimal(num_str)
        raw_num = float(dec_num)
    except decimal.InvalidOperation:
        raise QuantityError(f"invalid number: {num_str!r}")

    unit_suffix: str | None = None
    multiplier = 1.0

    rest = rest.strip()
    if rest:
        # Check if rest has suffix and/or currency suffix
        tokens = rest.split()
        if len(tokens) == 1:
            token = tokens[0].lower()
            if token in mult_map:
                spec = mult_map[token]
                unit_suffix = spec.canonical
                if unit_suffix != "%":
                    multiplier = spec.multiplier
            elif token in curr_map and not currency:
                currency = curr_map[token]
            elif token in [c.lower() for c in _CURRENCY_PREFIXES] and not currency:
                raise QuantityError(f"unsupported currency marker: {token!r}")
            else:
                raise QuantityError(f"unknown suffix or unit: {rest!r}")
        elif len(tokens) == 2:
            suf_token, curr_token = tokens[0].lower(), tokens[1].lower()
            if suf_token in mult_map:
                spec = mult_map[suf_token]
                unit_suffix = spec.canonical
                if unit_suffix != "%":
                    multiplier = spec.multiplier
            else:
                raise QuantityError(f"unknown suffix: {tokens[0]!r}")

            if curr_token in curr_map:
                if currency and currency != curr_map[curr_token]:
                    raise QuantityError(
                        f"conflicting currencies: {currency} and {curr_map[curr_token]}"
                    )
                currency = curr_map[curr_token]
            else:
                raise QuantityError(f"unsupported currency marker: {tokens[1]!r}")
        else:
            raise QuantityError(f"invalid trailing tokens: {rest!r}")

    final_val = float(decimal.Decimal(str(raw_num)) * decimal.Decimal(str(multiplier)))

    return Quantity(
        value=final_val,
        raw_number=raw_num,
        unit_suffix=unit_suffix,
        currency=currency,
    )


def validate_quantity_for_metric(quantity: Quantity, metric: MetricDefinition) -> None:
    """Validate that the quantity's unit/suffix and currency are permitted for this metric.

    Rules (Design.md Section 6.2):
    - A suffix (K, Mn, Bn, Tn, Lk, Cr) is valid only for metrics whose unit is money
      (CURRENCY, PRICE) or SHARES.
    - '%' is valid only for PCT and RATIO.
    - Currencies are valid only for CURRENCY and PRICE.
    - Plain numbers are always valid.
    """
    unit = metric.unit
    val_type = metric.value_type

    is_money = unit in (UnitType.CURRENCY, UnitType.PRICE) or val_type == ValueType.MONEY
    is_shares = unit == UnitType.SHARES
    is_percent = unit in (UnitType.PCT, UnitType.RATIO)

    if quantity.currency is not None and not is_money:
        raise UnitMismatchError(
            f"currency {quantity.currency!r} cannot be used with metric {metric.key!r} "
            f"which is not a money/price metric"
        )

    if quantity.unit_suffix is not None:
        if quantity.unit_suffix == "%":
            if not is_percent:
                raise UnitMismatchError(
                    f"suffix '%' is not valid for metric {metric.key!r} with unit {unit}"
                )
        else:
            # Multiplier suffixes (K, Lk, Mn, Cr, Bn, Tn)
            if not (is_money or is_shares):
                raise UnitMismatchError(
                    f"multiplier {quantity.unit_suffix!r} is not valid for metric {metric.key!r} "
                    f"with unit {unit}"
                )
