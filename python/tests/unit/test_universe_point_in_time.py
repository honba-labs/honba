"""Point-in-time universe resolution (Balch pitfall #2: survivorship bias).

Today's index constituents projected backwards are the classic lie: the losers
that were delisted are simply gone from the list. These tests pin the semantics
that prevent it — an ``as_of`` snapshot only ever contains symbols whose
inclusion interval covers that date, history is required (never silently
substituted with the modern list), and the Parquet catalog round-trips.
"""

from __future__ import annotations

import datetime as dt

import pytest

from honba.entities.instrument import InstrumentId
from honba.markets.india.universes import (
    Constituent,
    UniverseHistory,
    load_universe_history,
    register_universe_history,
    resolve_universe,
    save_universe_history,
    universe_history,
)

D = dt.date


@pytest.fixture(autouse=True)
def _clean_history_registry():
    """Each test starts (and ends) with an empty point-in-time registry."""
    from honba.markets.india.universes import _HISTORY_REGISTRY

    _HISTORY_REGISTRY.clear()
    yield
    _HISTORY_REGISTRY.clear()


# ---------------------------------------------------------------------------
# Constituent intervals
# ---------------------------------------------------------------------------


def test_constituent_is_active_from_inclusion_until_exclusion() -> None:
    c = Constituent("TRENT", D(2021, 9, 30), excluded_on=D(2024, 3, 15))
    assert not c.active_on(D(2021, 9, 29))  # day before inclusion
    assert c.active_on(D(2021, 9, 30))  # inclusion day counts
    assert c.active_on(D(2024, 3, 14))  # day before exclusion counts
    assert not c.active_on(D(2024, 3, 15))  # exclusion day: already out


def test_open_ended_constituent_stays_active_indefinitely() -> None:
    c = Constituent("INFY", D(2005, 6, 12))
    assert c.active_on(D(2005, 6, 12))
    assert c.active_on(D(2035, 1, 1))
    assert c.excluded_on is None


def test_constituent_rejects_a_symbol_or_interval_that_cannot_be_true() -> None:
    with pytest.raises(ValueError, match="symbol"):
        Constituent("  ", D(2020, 1, 1))
    with pytest.raises(ValueError, match="excluded_on"):
        Constituent("X", D(2020, 1, 1), excluded_on=D(2020, 1, 1))
    with pytest.raises(ValueError, match="excluded_on"):
        Constituent("X", D(2020, 1, 1), excluded_on=D(2019, 1, 1))


# ---------------------------------------------------------------------------
# History snapshots
# ---------------------------------------------------------------------------


def _demo_history() -> UniverseHistory:
    return UniverseHistory(
        "demo_50",
        (
            Constituent("AAA", D(2020, 1, 1)),
            Constituent("BBB", D(2020, 1, 1), excluded_on=D(2022, 6, 1)),
            Constituent("CCC", D(2021, 3, 1)),
        ),
    )


def test_symbols_on_returns_only_the_constituents_active_that_day() -> None:
    h = _demo_history()
    assert h.symbols_on(D(2019, 12, 31)) == ()  # before any inclusion
    assert h.symbols_on(D(2020, 6, 1)) == ("AAA", "BBB")
    assert h.symbols_on(D(2021, 2, 28)) == ("AAA", "BBB")  # CCC not in yet
    assert h.symbols_on(D(2022, 6, 1)) == ("AAA", "CCC")  # BBB excluded that day
    assert h.symbols_on(D(2030, 1, 1)) == ("AAA", "CCC")


def test_a_symbol_may_leave_and_rejoin_but_never_overlap_itself() -> None:
    rejoined = UniverseHistory(
        "re",
        (
            Constituent("X", D(2020, 1, 1), excluded_on=D(2021, 1, 1)),
            Constituent("X", D(2022, 1, 1)),  # back after a gap: fine
        ),
    )
    assert rejoined.symbols_on(D(2020, 6, 1)) == ("X",)
    assert rejoined.symbols_on(D(2021, 6, 1)) == ()
    assert rejoined.symbols_on(D(2022, 6, 1)) == ("X",)

    with pytest.raises(ValueError, match="overlap"):
        UniverseHistory(
            "bad",
            (
                Constituent("X", D(2020, 1, 1)),
                Constituent("X", D(2021, 1, 1)),  # active while the first is too
            ),
        )


def test_history_requires_a_name() -> None:
    with pytest.raises(ValueError, match="name"):
        UniverseHistory("", (Constituent("AAA", D(2020, 1, 1)),))


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


def test_registered_history_is_found_under_an_alias() -> None:
    register_universe_history(_demo_history())
    assert universe_history("DEMO-50") == _demo_history()
    assert universe_history("demo_50") == _demo_history()


def test_lookup_of_a_never_registered_history_is_none() -> None:
    assert universe_history("never_registered_universe_xyz") is None


def test_registering_a_second_history_for_a_name_needs_replace() -> None:
    register_universe_history(_demo_history())
    other = UniverseHistory("demo_50", (Constituent("ZZZ", D(2020, 1, 1)),))
    with pytest.raises(ValueError, match="already registered"):
        register_universe_history(other)
    register_universe_history(other, replace=True)
    assert universe_history("demo_50").symbols_on(D(2020, 6, 1)) == ("ZZZ",)


# ---------------------------------------------------------------------------
# resolve_universe(as_of=...)
# ---------------------------------------------------------------------------


def test_resolve_universe_as_of_returns_the_snapshot_effective_that_day() -> None:
    register_universe_history(_demo_history())
    early = resolve_universe("demo_50", as_of=D(2020, 6, 1), data_dir=None)
    late = resolve_universe("demo_50", as_of=D(2025, 1, 1), data_dir=None)
    assert [i.symbol for i in early] == ["AAA", "BBB"]
    assert [i.symbol for i in late] == ["AAA", "CCC"]  # BBB gone, CCC in
    assert all(isinstance(i, InstrumentId) for i in early)
    assert all(i.exchange == "NSE" for i in late)
    assert [i.symbol for i in resolve_universe("demo_50", "BSE", as_of=D(2025, 1, 1))] == [
        "AAA",
        "CCC",
    ]


def test_resolve_universe_as_of_fails_closed_without_history(tmp_path) -> None:
    # Never fall back to today's static list: that is the survivorship bias itself.
    with pytest.raises(ValueError, match="history"):
        resolve_universe("nifty50", as_of=D(2015, 1, 31), data_dir=tmp_path)
    with pytest.raises(ValueError, match="history"):
        resolve_universe("no_such_universe_at_all", as_of=D(2015, 1, 31), data_dir=tmp_path)


def test_resolve_universe_without_as_of_keeps_the_static_list(tmp_path) -> None:
    # Today's view (aliases, static tuples) is unchanged for existing callers.
    assert resolve_universe("alpha30", data_dir=tmp_path) == resolve_universe(
        "nifty200_alpha_30", data_dir=tmp_path
    )


# ---------------------------------------------------------------------------
# Parquet catalog
# ---------------------------------------------------------------------------


def test_universe_history_round_trips_through_the_parquet_catalog(tmp_path) -> None:
    h = UniverseHistory(
        "disk_50",
        (
            Constituent("AAA", D(2020, 1, 1)),
            Constituent("BBB", D(2020, 1, 1), excluded_on=D(2022, 6, 1)),
        ),
    )
    path = save_universe_history(h, tmp_path)
    assert path.name == "disk_50.parquet"
    assert load_universe_history("disk_50", data_dir=tmp_path) == h
    assert load_universe_history("DISK-50", data_dir=tmp_path) == h  # alias-normalized


def test_loading_a_history_that_was_never_saved_returns_none(tmp_path) -> None:
    assert load_universe_history("absent_universe", data_dir=tmp_path) is None


def test_resolve_universe_reads_history_from_the_catalog_on_disk(tmp_path) -> None:
    # No registry entry: the snapshot can only come from the Parquet catalog.
    assert universe_history("disk_50") is None
    save_universe_history(_demo_history(), tmp_path)  # saved under demo_50's content
    snapshot = resolve_universe("demo_50", as_of=D(2022, 6, 1), data_dir=tmp_path)
    assert universe_history("demo_50") is None  # reading does not populate the registry
    assert [i.symbol for i in snapshot] == ["AAA", "CCC"]
