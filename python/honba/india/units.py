"""Market-specific unit tables and currency configurations (Design.md Section 6.3)."""

from __future__ import annotations

from typing import NamedTuple


class MultiplierSpec(NamedTuple):
    canonical: str
    multiplier: float
    aliases: tuple[str, ...]


INDIA_MULTIPLIERS: dict[str, MultiplierSpec] = {
    "lk": MultiplierSpec("Lk", 1e5, ("lk", "l", "lakh", "lakhs", "lac")),
    "cr": MultiplierSpec("Cr", 1e7, ("cr", "crore", "crores")),
}

INDIA_CURRENCY_SYMBOLS: dict[str, str] = {
    "₹": "INR",
    "rs": "INR",
    "rs.": "INR",
    "inr": "INR",
}
