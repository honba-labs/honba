"""Point-in-time universe resolution wired end-to-end (Balch pitfall #2).

The Parquet catalog, the env-discovered data root, resolution and a real
``Honba.backtest`` session in one flow: a symbol that left the index is
reachable in its own window and absent from the modern snapshot — no
retroactive constituents, no network, no wall clock.

The symbols here are fictional index members; only the interval semantics are
under test, not anyone's actual index history.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Sequence

import pytest

from honba.domain.instrument import InstrumentKind
from honba.entities.bar import Bar
from honba.entities.instrument import Instrument, InstrumentId
from honba.entities.order import OrderIntent
from honba.markets.india.universes import (
    Constituent,
    UniverseHistory,
    register_universe_history,
    resolve_universe,
    save_universe_history,
    universe_history,
)
from honba.session import Honba
from honba.strategies.base import Strategy

D = dt.date
DAY_NS = 86_400 * 10**9
T0 = int(dt.datetime(2021, 1, 4, tzinfo=dt.timezone.utc).timestamp()) * 10**9

HISTORY = UniverseHistory(
    "pit_demo",
    (
        Constituent("ALPHA", D(2020, 1, 1), excluded_on=D(2023, 6, 1)),  # delisted
        Constituent("BETA", D(2020, 1, 1)),
        Constituent("GAMMA", D(2023, 6, 1)),  # took ALPHA's slot
    ),
)


@pytest.fixture(autouse=True)
def _clean_history_registry():
    from honba.markets.india.universes import _HISTORY_REGISTRY

    _HISTORY_REGISTRY.clear()
    yield
    _HISTORY_REGISTRY.clear()


class RecordingProvider:
    def __init__(self, bars: list[Bar]) -> None:
        self._bars = bars
        self.queries: list[tuple[dt.date, dt.date]] = []

    def bars(self, instrument_id, *, timeframe, start, end) -> Sequence[Bar]:
        lo = int(start.replace(tzinfo=dt.timezone.utc).timestamp() * 10**9)
        hi = int(end.replace(tzinfo=dt.timezone.utc).timestamp() * 10**9)
        self.queries.append((start.date(), end.date()))
        return [b for b in self._bars if b.instrument_id == instrument_id and lo <= b.ts < hi]

    def instrument(self, instrument_id) -> Instrument:
        return Instrument(instrument_id, InstrumentKind.EQUITY, 1.0, 0.05)


class BuyOnce(Strategy):
    name = "buy_once"

    def on_bar(self, bar: Bar) -> None:
        if self.ctx.position(bar.instrument_id) == 0 and not self.ctx.busy(bar.instrument_id):
            self.ctx.submit(OrderIntent.market_buy(bar.instrument_id, 10))


def _bars(iid: InstrumentId) -> list[Bar]:
    return [
        Bar(iid, T0 + i * DAY_NS, 100.0 + i, 101.0 + i, 99.0 + i, 100.5 + i, 1_000.0)
        for i in range(20)
    ]


def test_env_discovered_data_root_supplies_the_catalog(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    save_universe_history(HISTORY, tmp_path)
    monkeypatch.setenv("HONBA_DATA_DIR", str(tmp_path))
    assert universe_history("pit_demo") is None  # nothing registered: catalog only

    during_alpha = resolve_universe("pit_demo", as_of=D(2021, 1, 1))
    today = resolve_universe("pit_demo", as_of=D(2024, 1, 1))

    assert [i.symbol for i in during_alpha] == ["ALPHA", "BETA"]
    assert [i.symbol for i in today] == ["BETA", "GAMMA"]
    # The delisted name never leaks into the modern snapshot...
    assert "ALPHA" not in {i.symbol for i in today}
    # ...and the later addition never leaks backwards into the past snapshot.
    assert "GAMMA" not in {i.symbol for i in during_alpha}


def test_a_delisted_constituent_still_backtests_in_its_own_window(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    save_universe_history(HISTORY, tmp_path)
    monkeypatch.setenv("HONBA_DATA_DIR", str(tmp_path))

    snapshot = resolve_universe("pit_demo", as_of=D(2021, 1, 1), exchange="NSE")
    alpha = next(i for i in snapshot if i.symbol == "ALPHA")
    assert alpha == InstrumentId("ALPHA", "NSE")

    provider = RecordingProvider(_bars(alpha))
    result = Honba.backtest(
        BuyOnce(),
        symbol="ALPHA",
        start="2021-01-04",
        end="2021-01-20",
        data=provider,
        cash=100_000.0,
    ).run()

    assert provider.queries == [(D(2021, 1, 4), D(2021, 1, 20))]
    assert len(result.fills) == 1  # reachable in its window, fills like any other name
    assert "ALPHA" not in {i.symbol for i in resolve_universe("pit_demo", as_of=D(2024, 1, 1))}


def test_registered_history_wins_over_the_catalog_and_drives_resolution(tmp_path) -> None:
    save_universe_history(HISTORY, tmp_path)  # catalog says ALPHA ends 2023-06-01
    register_universe_history(
        UniverseHistory(
            "pit_demo",
            (
                Constituent("ALPHA", D(2020, 1, 1), excluded_on=D(2021, 12, 31)),
                Constituent("BETA", D(2020, 1, 1)),
            ),
        ),
        replace=True,
    )

    snapshot = resolve_universe("pit_demo", as_of=D(2022, 6, 1), data_dir=tmp_path)
    assert [i.symbol for i in snapshot] == ["BETA"]  # registered history: ALPHA already out
