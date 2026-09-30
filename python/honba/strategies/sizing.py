"""Position sizing helpers. NSE cash equity trades in whole shares."""
from __future__ import annotations

import math


def whole_shares(capital: float, allocation: float, price: float) -> int:
    """Whole shares purchasable with ``allocation`` (0-1] of ``capital`` at ``price``."""
    if not 0 < allocation <= 1:
        raise ValueError(f"allocation must be in (0, 1], got {allocation}")
    if not price > 0:
        raise ValueError(f"price must be positive, got {price}")
    return math.floor(capital * allocation / price)
