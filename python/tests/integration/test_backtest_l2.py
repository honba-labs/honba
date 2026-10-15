"""``Honba.backtest(fill="l2", ticks=...)``: bars drive the strategy, quotes/trades drive the L2 port.

Both L2 backends (native and pure-Python reference) must give the same fills through the session.
"""

from __future__ import annotations

import pytest

from honba.backtest.l2_sim import L2Execution
from honba.domain.instrument import InstrumentKind
from honba.domain.money import Currency, Money
from honba.entities.bar import Bar
from honba.entities.instrument import Instrument, InstrumentId
from honba.entities.order import OrderIntent
from honba.entities.tick import AggressorSide, QuoteTick, TradeTick
from honba.risk import RiskLimits
from honba.session import Honba
from honba.strategies.base import Strategy

X = InstrumentId("XYZ", "NSE")
SEC = 10**9
DAY = 86_400 * SEC
T0 = 1_704_067_200 * SEC  # 2024-01-01 UTC
INR = Currency.INR


def _bars(closes: list[float]) -> list[Bar]:
    return [Bar(X, T0 + (i + 1) * DAY, c, c, c, c, 1_000.0) for i, c in enumerate(closes)]


class Provider:
    def __init__(self, bars: list[Bar], ticks: list | None = None) -> None:
        self._bars = bars
        if ticks is not None:
            self._ticks = ticks

            def ticks_fn(instrument_id, *, start, end):
                return [t for t in self._ticks if t.instrument_id == instrument_id]

            self.ticks = ticks_fn

    def bars(self, instrument_id, *, timeframe, start, end) -> list[Bar]:
        return list(self._bars)

    def instrument(self, instrument_id) -> Instrument:
        return Instrument(instrument_id, InstrumentKind.EQUITY, 1.0, 0.05)


class BuyOnce(Strategy):
    name = "buy_once"

    def __init__(self) -> None:
        self.quotes = 0

    def on_quote(self, quote: QuoteTick) -> None:
        self.quotes += 1

    def on_bar(self, bar: Bar) -> None:
        if self.ctx.position(X) == 0 and not self.ctx.busy(X):
            self.ctx.submit(OrderIntent.market_buy(X, 10))


def quote(ts: int, bid: float, ask: float, size: float = 100.0) -> QuoteTick:
    return QuoteTick(X, ts, bid, ask, size, size)


def _run(ticks, *, strategy=None, provider_ticks=None, **kw):
    return Honba.backtest(
        strategy or BuyOnce(),
        symbol="XYZ",
        start="2024-01-01",
        end="2024-02-01",
        cash=100_000.0,
        costs="none",
        fill="l2",
        data=Provider(_bars([100.0, 100.0, 100.0]), provider_ticks),
        ticks=ticks,
        **kw,
    ).run()


def test_l2_session_fills_market_buy_at_the_ask_standing_at_submit() -> None:
    strategy = BuyOnce()
    result = _run(
        [quote(T0 + DAY - SEC, 99.9, 100.1), quote(T0 + DAY + SEC, 99.8, 100.2)], strategy=strategy
    )
    (fill,) = result.fills
    assert fill.price == pytest.approx(100.1)  # the book standing when the bar's order is sent
    assert fill.quantity == 10
    assert result.ctx.cash() == Money.from_major(100_000.0 - 1_001.0, INR)
    assert strategy.quotes == 2  # the strategy sees the quote stream too


def test_market_buy_with_no_standing_quote_waits_for_the_next_one() -> None:
    result = _run([quote(T0 + DAY + SEC, 99.8, 100.2)])
    (fill,) = result.fills
    assert fill.price == pytest.approx(100.2)
    assert fill.ts == T0 + DAY + SEC


def test_execution_port_is_the_l2_port() -> None:
    session = Honba.backtest(
        BuyOnce(),
        symbol="XYZ",
        start="2024-01-01",
        end="2024-02-01",
        fill="l2",
        data=Provider(_bars([100.0])),
        ticks=[],
    )
    assert isinstance(session.execution, L2Execution)


def test_trade_prints_fill_a_resting_limit() -> None:
    class Rest(Strategy):
        name = "rest"

        def on_bar(self, bar: Bar) -> None:
            if not self.ctx.busy(X) and self.ctx.position(X) == 0:
                self.ctx.submit(OrderIntent.limit_buy(X, 10, 99.0))

    ticks = [
        quote(T0 + DAY - SEC, 99.9, 100.1),
        TradeTick(X, T0 + DAY + SEC, 98.5, 50.0, AggressorSide.SELLER, "t1"),
    ]
    result = _run(ticks, strategy=Rest())
    (fill,) = result.fills
    assert fill.price == pytest.approx(99.0)
    assert fill.quantity == 10


def test_ticks_come_from_the_data_provider_when_not_passed() -> None:
    ticks = [quote(T0 + DAY - SEC, 99.9, 100.1), quote(T0 + DAY + SEC, 99.8, 100.2)]
    result = _run(None, provider_ticks=ticks)
    (fill,) = result.fills
    assert fill.price == pytest.approx(100.1)


@pytest.mark.parametrize("backend", ["python", "native"])
def test_backends_agree_through_the_session(backend: str, monkeypatch) -> None:
    monkeypatch.setenv("HONBA_SIM_BACKEND", backend)
    ticks = [quote(T0 + DAY - SEC, 99.9, 100.1), quote(T0 + DAY + SEC, 99.8, 100.2)]
    result = _run(ticks)
    assert [(f.price, f.quantity) for f in result.fills] == [(pytest.approx(100.1), 10)]


def test_ticks_are_replayed_in_time_order_with_bars() -> None:
    # The unsorted input is merged by ts: the quote at T0+DAY+SEC must not precede the bar.
    ticks = [quote(T0 + DAY + SEC, 99.8, 100.2), quote(T0 + DAY - SEC, 99.9, 100.1)]
    result = _run(ticks)
    (fill,) = result.fills
    assert fill.price == pytest.approx(100.1)


def test_ticks_with_the_default_simulator_do_not_break() -> None:
    result = Honba.backtest(
        BuyOnce(),
        symbol="XYZ",
        start="2024-01-01",
        end="2024-02-01",
        costs="none",
        data=Provider(_bars([100.0, 101.0, 102.0])),
        ticks=[quote(T0 + DAY - SEC, 99.9, 100.1)],
    ).run()
    assert len(result.fills) == 1  # next_open ignores quotes


# -- price collar through the session (ADR 0018 addendum) ----------------------------------------

WILD = [quote(T0 + DAY - SEC, 109.0, 110.0)]  # the ask is 10% above the 100.0 bar close


def test_collar_refuses_a_market_buy_far_from_the_last_close() -> None:
    session = _session(WILD, risk=RiskLimits(price_collar_bps=500))
    result = session.run()
    assert result.fills == []
    # the strategy retries on every bar; each attempt is refused
    assert [r.reason for r in result.order_rejections] == ["risk_price_collar"] * 3
    refused = session.runner.audit[0]
    assert refused.context["expected_price"] == pytest.approx(110.0)
    assert refused.context["reference_price"] == pytest.approx(100.0)


def test_collar_wide_enough_lets_the_order_fill_at_the_ask() -> None:
    result = _session(WILD, risk=RiskLimits(price_collar_bps=1000)).run()
    (fill,) = result.fills
    assert fill.price == pytest.approx(110.0)


def test_no_risk_option_means_no_collar() -> None:
    result = _session(WILD).run()
    assert len(result.fills) == 1


def test_risk_limits_are_recorded_on_the_session() -> None:
    limits = RiskLimits(price_collar_bps=500)
    session = _session(WILD, risk=limits)
    assert session.risk_limits == limits
    assert session.runner.holds_risk_stage
    assert _session(WILD).risk_limits is None


def _session(ticks, **kw):
    return Honba.backtest(
        BuyOnce(),
        symbol="XYZ",
        start="2024-01-01",
        end="2024-02-01",
        cash=100_000.0,
        costs="none",
        fill="l2",
        data=Provider(_bars([100.0, 100.0, 100.0])),
        ticks=ticks,
        **kw,
    )
