"""End-to-end backtests through the core next-open simulator.

strategy -> ``StrategyRunner`` (warm-up gate) -> ``NextOpenExecution`` -> fills -> ledger,
both through the ``Honba.backtest`` facade (single symbol, in-memory data provider) and
directly with ``group_sessions`` for a multi-instrument portfolio. No network, no clock.
"""

from __future__ import annotations

import datetime as dt
import json
from collections.abc import Sequence
from pathlib import Path

import pytest

from honba.backtest.simulated import NextOpenExecution, group_sessions
from honba.domain.instrument import InstrumentKind
from honba.domain.money import Currency, Money
from honba.entities.bar import Bar
from honba.entities.instrument import Instrument, InstrumentId
from honba.entities.order import OrderIntent, OrderSide
from honba.markets.india.costs import nse_equity_delivery_fill_cost
from honba.session import Honba
from honba.strategies.base import Strategy
from honba.strategies.context import LedgerContext
from honba.strategies.loader import CATALOG_ENV
from honba.strategies.runner import StrategyRunner

X = InstrumentId("XYZ", "NSE")
DAY = 86_400 * 10**9
T0 = int(dt.datetime(2024, 1, 1, tzinfo=dt.timezone.utc).timestamp()) * 10**9
INR = Currency.INR


def _bars(iid: InstrumentId, opens: Sequence[float]) -> list[Bar]:
    # close = open + 5, so a decision-bar-close fill is distinguishable from a next-open fill.
    return [Bar(iid, T0 + i * DAY, o, o + 6, o - 1, o + 5, 1_000.0) for i, o in enumerate(opens)]


class Provider:
    def __init__(self, bars: list[Bar]) -> None:
        self._bars = bars

    def bars(self, instrument_id, *, timeframe, start, end) -> list[Bar]:
        return [b for b in self._bars if b.instrument_id == instrument_id]

    def instrument(self, instrument_id) -> Instrument:
        return Instrument(instrument_id, InstrumentKind.EQUITY, 1.0, 0.05)


class BuyOnce(Strategy):
    name = "buy_once"

    def on_bar(self, bar: Bar) -> None:
        if self.ctx.position(X) == 0 and not self.ctx.busy(X):
            self.ctx.submit(OrderIntent.market_buy(X, 10))


def _run(strategy, opens=(100.0, 110.0, 120.0, 130.0), **kw):
    return Honba.backtest(
        strategy,
        symbol="XYZ",
        start="2024-01-01",
        end="2024-02-01",
        cash=100_000.0,
        data=Provider(_bars(X, opens)),
        **kw,
    ).run()


def test_default_fill_is_the_next_open_not_the_decision_close() -> None:
    result = _run(BuyOnce(), costs="none")
    (fill,) = result.fills
    assert (fill.price, fill.ts) == (110.0, T0 + DAY)  # bar 1 decided, bar 2 opened
    assert result.ctx.cash() == Money.from_major(100_000.0 - 1_100.0, INR)
    assert result.metrics["final_cash"] == pytest.approx(98_900.0)
    assert result.metrics["final_equity"] == pytest.approx(98_900.0 + 10 * 135.0)


def test_india_costs_reach_the_fill_and_the_ledger() -> None:
    result = _run(BuyOnce(), costs="india.equity")
    (fill,) = result.fills
    assert fill.costs == nse_equity_delivery_fill_cost(OrderSide.BUY, 10, 110.0)
    assert fill.costs.amount > 0
    assert result.ctx.cash() == Money.from_major(100_000.0, INR) - (
        Money.mul_qty(10, 110.0, INR) + fill.costs
    )


def test_warmup_suppresses_orders_until_enough_bars() -> None:
    result = _run(BuyOnce(), costs="none", warmup_bars=2)
    assert [s.ts_init for s in result.suppressed] == [T0, T0 + DAY]
    (fill,) = result.fills
    assert fill.price == 130.0  # decided on bar 3, filled at bar 4's open


def test_an_order_still_working_at_the_end_is_cancelled_and_released() -> None:
    result = _run(BuyOnce(), opens=(100.0,), costs="none")
    assert result.fills == []
    (rej,) = result.order_rejections
    assert rej.cancelled and rej.intent.quantity == 10
    assert not result.ctx.busy(X)


def test_bar_close_remains_available_for_conformance() -> None:
    result = _run(BuyOnce(), fill="bar_close")
    assert [f.price for f in result.fills] == [105.0]


def test_a_strategy_file_path_is_loaded(tmp_path: Path) -> None:
    path = tmp_path / "s.py"
    path.write_text(
        "from honba.strategies.base import Strategy\n"
        "from honba.entities.order import OrderIntent\n"
        "class S(Strategy):\n"
        "    name = 's'\n"
        "    def on_bar(self, bar):\n"
        "        if not self.ctx.position(bar.instrument_id) and not self.ctx.busy(bar.instrument_id):\n"
        "            self.ctx.submit(OrderIntent.market_buy(bar.instrument_id, 1))\n"
    )
    result = _run(path, costs="none")
    assert [f.price for f in result.fills] == [110.0]


def test_a_catalog_strategy_runs_by_name_with_its_config_warmup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "catalog"
    sdir = root / "demo"
    sdir.mkdir(parents=True)
    (root / "registry.json").write_text(
        json.dumps({"strategies": [{"name": "demo", "path": "demo"}]})
    )
    (sdir / "strategy.py").write_text(
        "from honba.strategies.base import Strategy\n"
        "from honba.entities.order import OrderIntent\n"
        "class Demo(Strategy):\n"
        "    name = 'demo'\n"
        "    def __init__(self, config):\n"
        "        self.qty = config.params['qty']\n"
        "        self.iid = config.instrument_id\n"
        "    def on_bar(self, bar):\n"
        "        if not self.ctx.position(self.iid) and not self.ctx.busy(self.iid):\n"
        "            self.ctx.submit(OrderIntent.market_buy(self.iid, self.qty))\n"
    )
    (sdir / "config.toml").write_text(
        'name = "demo"\nsymbol = "XYZ"\nwarmup_bars = 1\n[params]\nqty = 3\n'
    )
    monkeypatch.setenv(CATALOG_ENV, str(root))
    result = _run("demo", costs="none")
    (fill,) = result.fills
    assert (fill.quantity, fill.price) == (3, 120.0)  # warm-up 1: decided on bar 2


def test_multi_instrument_rotation_with_t2_settlement() -> None:
    """Sell A and buy B on the same decision: the buy waits for A's proceeds (T+2)."""
    a, b = InstrumentId("AAA", "NSE"), InstrumentId("BBB", "NSE")

    class Rotate(Strategy):
        name = "rotate"

        def __init__(self) -> None:
            self.session = 0

        def on_bar(self, bar: Bar) -> None:
            if bar.instrument_id != b:
                return
            self.session += 1
            if self.session == 1:
                self.ctx.submit(OrderIntent.market_buy(a, 10))
            elif self.session == 2:
                self.ctx.submit(OrderIntent.market_buy(b, 10))
                self.ctx.submit(OrderIntent.market_sell(a, 10))

    bars = _bars(a, [100.0] * 6) + _bars(b, [100.0] * 6)
    port = NextOpenExecution(cash=Money.from_major(1_000.0, INR), settlement_days=2)
    runner = StrategyRunner(Rotate(), port, ctx=LedgerContext(cash=1_000.0))
    result = runner.run(group_sessions(bars))
    sides = [(f.instrument_id.symbol, f.side, f.ts) for f in result.fills]
    assert sides == [
        ("AAA", OrderSide.BUY, T0 + DAY),
        ("AAA", OrderSide.SELL, T0 + 2 * DAY),
        # Waits two sessions for A's proceeds to settle, then fills in full.
        ("BBB", OrderSide.BUY, T0 + 4 * DAY),
    ]
    assert result.ctx.positions() == {b: 10.0}
    assert result.ctx.cash() == port.cash
    assert result.order_rejections == []
