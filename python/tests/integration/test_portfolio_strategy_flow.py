"""PortfolioStrategy end to end: strategy -> runner -> next-open simulator -> ledger.

Reuses the synthetic bars of ``test_target_weight_flow``. Includes the parity check against
a TargetWeightStrategy equal-weight book and the imperative Alpha-30 catalog strategy.
"""

from __future__ import annotations

import pytest
from test_target_weight_flow import (
    BASE,
    CASH,
    DAY,
    IDS,
    REBALANCE_DAYS,
    REFERENCE,
    T0,
    load_reference,
    run,
)

from honba.strategies import PortfolioStrategy, StaticUniverse, TargetWeightStrategy
from honba.strategies.portfolio import EveryNDays

ALLOCATION = 0.995
MEMBERS = ("AAA", "BBB", "CCC", "DDD")


def fill_key(result):
    return sorted((f.ts, f.instrument_id.symbol, f.side.name, f.quantity) for f in result.fills)


def portfolio(symbols=MEMBERS, **kw):
    kw.setdefault("allocation", ALLOCATION)
    return PortfolioStrategy(
        StaticUniverse([IDS[s] for s in symbols]),
        schedule=EveryNDays(REBALANCE_DAYS),
        **kw,
    )


class TWBook(TargetWeightStrategy):
    name = "tw_parity"
    rebalance_days = REBALANCE_DAYS
    allocation = ALLOCATION

    def universe(self):
        return [IDS[s] for s in MEMBERS]


def test_runs_through_runner_with_custom_name_and_no_rejections():
    s = portfolio(name="pf_flow")
    result = run(s, list(MEMBERS))
    assert not result.rejections and not result.order_rejections
    assert result.fills
    assert {p for p in result.ctx.positions()} == {IDS[m] for m in MEMBERS}


def test_membership_change_mid_run_exits_leaver_and_enters_joiner():
    universe = StaticUniverse([IDS[s] for s in MEMBERS])
    s = PortfolioStrategy(universe, schedule=EveryNDays(REBALANCE_DAYS))
    base_on_bar = s.on_bar

    def on_bar(bar):
        if (bar.ts - T0) // DAY == 45:
            universe.set_members([IDS[x] for x in ("AAA", "BBB", "CCC", "EEE")])
        base_on_bar(bar)

    s.on_bar = on_bar
    result = run(s, list(BASE))
    assert not result.rejections and not result.order_rejections
    held = {i.symbol for i in result.ctx.positions()}
    assert held == {"AAA", "BBB", "CCC", "EEE"}
    exits = [
        f
        for f in result.fills
        if f.instrument_id == IDS["DDD"] and f.side.name == "SELL" and f.ts > T0 + 45 * DAY
    ]
    assert len(exits) == 1


def test_parity_with_target_weight_strategy():
    new = run(portfolio(), list(MEMBERS))
    old = run(TWBook(), list(MEMBERS))
    assert fill_key(new) == fill_key(old)
    assert new.ctx.positions() == old.ctx.positions()
    assert new.ctx.cash() == old.ctx.cash()


@pytest.mark.skipif(not REFERENCE.exists(), reason="honba-strategies checkout not present")
def test_parity_with_imperative_alpha30_equal_weight(monkeypatch):
    from honba.markets.india.universes import UNIVERSES
    from honba.strategies.config import StrategyConfig

    ref = load_reference()
    monkeypatch.setitem(UNIVERSES, "parity4", MEMBERS)
    cfg = StrategyConfig(
        name="ref",
        symbol="AAA",
        params={
            "capital": CASH,
            "universe_name": "parity4",
            "rebalance_days": REBALANCE_DAYS,
            "allocation": ALLOCATION,
        },
    )
    ref_result = run(ref.Alpha30EqualWeight(cfg), list(MEMBERS))
    new = run(portfolio(), list(MEMBERS))
    assert fill_key(new) == fill_key(ref_result)
    assert new.ctx.positions() == ref_result.ctx.positions()
    assert new.ctx.cash() == ref_result.ctx.cash()
