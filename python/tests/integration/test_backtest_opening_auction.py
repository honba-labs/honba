"""Opening-auction realism driven through a real ``Honba.backtest`` session (Balch pitfall #8).

The model (``tests/unit/test_opening_auction.py``) and the port wiring
(``tests/unit/test_opening_auction_fills.py``) are unit-tested; this proves the
public ``auction=`` knob reaches the fill, the ledger and the run config through
the normal session path — that a daily run pays the spread buffer and an
intraday run waits out the post-open delay — and that leaving it out changes
nothing.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Sequence

import pytest

from honba.backtest.opening_auction import OpeningAuction
from honba.domain.instrument import InstrumentKind
from honba.domain.money import Currency
from honba.entities.bar import Bar
from honba.entities.instrument import Instrument, InstrumentId
from honba.entities.order import OrderIntent
from honba.session import Honba
from honba.strategies.base import Strategy

X = InstrumentId("XYZ", "NSE")
INR = Currency.INR
DAY_NS = 86_400 * 10**9
MIN_NS = 60 * 10**9
T0 = int(dt.datetime(2024, 1, 1, tzinfo=dt.timezone.utc).timestamp()) * 10**9
N_BARS = 20
BUFFER_BPS = 25.0
BUFFER = BUFFER_BPS / 10_000.0
QUANTITY = 50.0
BUY_ON_BAR = 10  # wait for 11 sessions of history before the fill at session 11

OPENS = [100.0 + (i % 7) for i in range(N_BARS)]
CLOSES = [o + 0.75 for o in OPENS]


def _daily_bars() -> list[Bar]:
    return [
        Bar(
            X,
            T0 + i * DAY_NS,
            OPENS[i],
            max(OPENS[i], CLOSES[i]),
            min(OPENS[i], CLOSES[i]),
            CLOSES[i],
            1_000.0,
        )
        for i in range(N_BARS)
    ]


def _intraday_bars(n: int, step_min: int = 5) -> list[Bar]:
    return [
        Bar(X, T0 + i * step_min * MIN_NS, 100.0 + i, 101.0 + i, 99.0 + i, 100.5 + i, 1_000.0)
        for i in range(n)
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


class BuyOnFirstBar(Strategy):
    name = "buy_on_first_bar"

    def on_bar(self, bar: Bar) -> None:
        if self.ctx.position(X) == 0 and not self.ctx.busy(X):
            self.ctx.submit(OrderIntent.market_buy(X, QUANTITY))


def _run(strategy, bars: list[Bar], **kw):
    return Honba.backtest(
        strategy,
        symbol="XYZ",
        start="2024-01-01",
        end="2024-01-25",
        data=Provider(bars),
        cash=100_000.0,
        costs="none",
        **kw,
    ).run()


def test_a_backtest_fill_pays_the_buffered_open() -> None:
    result = _run(BuyAfterHistory(), _daily_bars(), auction=OpeningAuction(spread_bps=BUFFER_BPS))

    (fill,) = result.fills
    assert fill.price == pytest.approx(OPENS[BUY_ON_BAR + 1] * (1.0 + BUFFER))
    assert fill.price > OPENS[BUY_ON_BAR + 1]  # worse than the printed open
    assert result.metrics["final_cash"] == pytest.approx(100_000.0 - QUANTITY * fill.price)
    assert result.config.auction == OpeningAuction(spread_bps=BUFFER_BPS)


def test_a_backtest_without_an_auction_fills_at_the_printed_open() -> None:
    result = _run(BuyAfterHistory(), _daily_bars())
    (fill,) = result.fills
    assert fill.price == OPENS[BUY_ON_BAR + 1]
    assert result.config.auction is None
    assert result.metrics["final_cash"] == pytest.approx(100_000.0 - QUANTITY * fill.price)


def test_an_intraday_run_waits_delay_bars_past_the_open() -> None:
    bars = _intraday_bars(8)
    natural = _run(
        BuyOnFirstBar(),
        bars,
        timeframe="5m",
        settlement_days=0,
    )
    delayed = _run(
        BuyOnFirstBar(),
        bars,
        timeframe="5m",
        settlement_days=0,
        auction=OpeningAuction(delay_bars=2),
    )

    # The first bar decides; the natural fill is the next bar's open, two bars later
    # with delay_bars=2 — the 09:15 auction turbulence is skipped.
    (natural_fill,) = natural.fills
    (delayed_fill,) = delayed.fills
    assert natural_fill.ts == T0 + 1 * 5 * MIN_NS
    assert natural_fill.price == 101.0
    assert delayed_fill.ts == T0 + 3 * 5 * MIN_NS
    assert delayed_fill.price == 103.0


def test_a_daily_run_refuses_a_post_open_delay() -> None:
    with pytest.raises(ValueError, match="delay_bars"):
        Honba.backtest(
            BuyAfterHistory(),
            symbol="XYZ",
            start="2024-01-01",
            end="2024-01-25",
            data=Provider(_daily_bars()),
            auction=OpeningAuction(delay_bars=5),
        )
