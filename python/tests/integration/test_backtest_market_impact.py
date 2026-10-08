"""Market impact driven through a real ``Honba.backtest`` session (Balch pitfall #4).

The model (``tests/unit/test_market_impact.py``) and the port wiring
(``tests/unit/test_impact_fill_prices.py``) are unit-tested; this proves the
public ``impact=`` knob reaches the fill, the ledger and the cost function
through the normal session path — and that leaving it out changes nothing.
"""

from __future__ import annotations

import datetime as dt
import math
import statistics
from collections.abc import Sequence
from itertools import pairwise

import pytest

from honba.backtest.impact import MarketImpact
from honba.domain.instrument import InstrumentKind
from honba.domain.money import Currency, Money
from honba.entities.bar import Bar
from honba.entities.instrument import Instrument, InstrumentId
from honba.entities.order import OrderIntent, OrderSide
from honba.session import Honba
from honba.strategies.base import Strategy

X = InstrumentId("XYZ", "NSE")
INR = Currency.INR
DAY_NS = 86_400 * 10**9
T0 = int(dt.datetime(2024, 1, 1, tzinfo=dt.timezone.utc).timestamp()) * 10**9
N_BARS = 20
VOLUME = 1_000.0
KAPPA = 2.0
QUANTITY = 50.0
BUY_ON_BAR = 10  # wait for 11 sessions of history before the fill at session 11

OPENS = [100.0 + (i % 7) for i in range(N_BARS)]
CLOSES = [o + 0.75 for o in OPENS]


def _bars() -> list[Bar]:
    return [
        Bar(
            X,
            T0 + i * DAY_NS,
            OPENS[i],
            max(OPENS[i], CLOSES[i]),
            min(OPENS[i], CLOSES[i]),
            CLOSES[i],
            VOLUME,
        )
        for i in range(N_BARS)
    ]


def _to_ns(value: dt.datetime) -> int:
    return int(value.replace(tzinfo=dt.timezone.utc).timestamp() * 10**9)


class Provider:
    def __init__(self, bars: list[Bar]) -> None:
        self._bars = bars

    def bars(self, instrument_id, *, timeframe, start, end) -> Sequence[Bar]:
        lo, hi = _to_ns(start), _to_ns(end)
        return [b for b in self._bars if b.instrument_id == instrument_id and lo <= b.ts < hi]

    def instrument(self, instrument_id) -> Instrument:
        return Instrument(instrument_id, InstrumentKind.EQUITY, 1.0, 0.05)


class BuyAfterHistory(Strategy):
    """Buys once it has seen ``BUY_ON_BAR`` bars, so the fill has real market history."""

    name = "buy_after_history"

    def __init__(self) -> None:
        self.seen = 0

    def on_bar(self, bar: Bar) -> None:
        first = self.seen == BUY_ON_BAR
        self.seen += 1
        if first and self.ctx.position(X) == 0 and not self.ctx.busy(X):
            self.ctx.submit(OrderIntent.market_buy(X, QUANTITY))


def _expected_price() -> float:
    """The fill session's open degraded by kappa * sigma * sqrt(qty / ADV)."""
    returns = [b / a - 1.0 for a, b in pairwise(CLOSES[: BUY_ON_BAR + 1])]
    fraction = KAPPA * statistics.stdev(returns) * math.sqrt(QUANTITY / VOLUME)
    return OPENS[BUY_ON_BAR + 1] * (1.0 + fraction)


def _run(**kw):
    return Honba.backtest(
        BuyAfterHistory(),
        symbol="XYZ",
        start="2024-01-01",
        end="2024-01-25",
        data=Provider(_bars()),
        cash=100_000.0,
        **kw,
    ).run()


def test_a_backtest_fill_pays_the_impacted_open() -> None:
    result = _run(impact=MarketImpact(kappa=KAPPA))

    (fill,) = result.fills
    expected = _expected_price()
    assert fill.price == pytest.approx(expected)
    assert fill.price > OPENS[BUY_ON_BAR + 1]  # worse than the printed open
    # Ledger: notional at the impacted price plus the statutory costs charged on it.
    assert result.metrics["final_cash"] == pytest.approx(
        100_000.0 - QUANTITY * fill.price - fill.costs.to_major(), abs=0.01
    )


def test_a_backtest_without_impact_fills_at_the_printed_open() -> None:
    result = _run()
    (fill,) = result.fills
    assert fill.price == OPENS[BUY_ON_BAR + 1]
    assert result.metrics["final_cash"] == pytest.approx(
        100_000.0 - QUANTITY * fill.price - fill.costs.to_major(), abs=0.01
    )


def test_the_cost_function_sees_the_impacted_price() -> None:
    seen: list[tuple[OrderSide, float, float]] = []

    def costs(side: OrderSide, quantity: float, price: float) -> Money:
        seen.append((side, quantity, price))
        return Money.zero(INR)

    result = _run(costs=costs, impact=MarketImpact(kappa=KAPPA))
    (fill,) = result.fills

    expected = _expected_price()
    # The simulator probes the cost function for affordability too; every call sees
    # the same impacted price, never the printed open.
    assert seen
    assert all(
        side is OrderSide.BUY and quantity == QUANTITY and price == pytest.approx(expected)
        for side, quantity, price in seen
    )
    assert fill.price == pytest.approx(expected)
