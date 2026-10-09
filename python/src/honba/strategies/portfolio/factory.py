"""Build a ``PortfolioStrategy`` from plain, TOML-friendly params.

Grammar (every key optional except ``universe``; unknown keys raise ``ValueError``):

* ``universe``: a name (``"nifty50"`` -> ``NamedUniverse``) or a list of ``"SYMBOL"`` /
  ``"SYMBOL:EXCHANGE"`` (-> ``StaticUniverse``). ``exchange`` (default ``"NSE"``) fills in
  missing exchanges; ``point_in_time`` (bool, named universes only) uses historical snapshots.
* ``weighting``: ``"equal"`` (default) | ``"inverse_vol"`` | ``"inverse_vol:30"`` (lookback).
* ``schedule``: ``"every:15d"`` (default) | ``"monthly:first_session"`` | ``"drift:0.05"``;
  join with ``+`` to rebalance when any is due, e.g. ``"monthly:first_session+drift:0.05"``.
* ``select``: omitted keeps everything; ``"top:10:momentum:126"`` | ``"top:10:low_vol:60"`` | ``"top:10:mean_reversion:20"``.
* ``allocation``: fraction of portfolio value to deploy, in (0, 1] (default 0.98).
* ``history_len``: closes kept per instrument. Omitted, it is auto-sized to
  ``max(64, longest lookback in use)``; given, it must be >= the longest lookback or a
  ``ValueError`` is raised.

Errors are ``ValueError`` s that name the offending key and value.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping
from typing import Any

from honba.entities.instrument import InstrumentId
from honba.strategies.portfolio.schedule import (
    AnyOf,
    DriftBand,
    EveryNDays,
    MonthlyFirstSession,
    RebalanceSchedule,
)
from honba.strategies.portfolio.scoring import low_volatility, mean_reversion, momentum
from honba.strategies.portfolio.selection import SelectAll, Selector, TopN
from honba.strategies.portfolio.strategy import PortfolioStrategy
from honba.strategies.portfolio.universe import NamedUniverse, StaticUniverse, Universe
from honba.strategies.portfolio.weighting import EqualWeight, InverseVolatility, WeightingScheme

KEYS = frozenset(
    {
        "universe",
        "exchange",
        "point_in_time",
        "weighting",
        "schedule",
        "select",
        "allocation",
        "history_len",
    }
)
_MIN_HISTORY = 64
_SCORES: dict[str, Callable[[int], Callable]] = {
    "momentum": momentum,
    "low_vol": low_volatility,
    "mean_reversion": mean_reversion,
}


def _fail(key: str, value: object, why: str) -> ValueError:
    return ValueError(f"params[{key!r}]={value!r}: {why}")


def _int(key: str, value: object, text: str, minimum: int, whole: object = None) -> int:
    try:
        number = int(text)
    except ValueError:
        raise _fail(key, whole if whole is not None else value, f"{text!r} is not an integer")
    if number < minimum:
        raise _fail(key, whole if whole is not None else value, f"must be >= {minimum}")
    return number


def _text(key: str, value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise _fail(key, value, "must be a non-empty string")
    return value.strip()


def _universe(params: Mapping[str, Any]) -> Universe:
    exchange = _text("exchange", params.get("exchange", "NSE"))
    pit = params.get("point_in_time", False)
    if not isinstance(pit, bool):
        raise _fail("point_in_time", pit, "must be a bool")
    value = params["universe"]
    if isinstance(value, str):
        return NamedUniverse(_text("universe", value), exchange, point_in_time=pit)
    if not isinstance(value, (list, tuple)) or not value:
        raise _fail("universe", value, "must be a universe name or a non-empty list of symbols")
    if pit:
        raise _fail("point_in_time", pit, "only applies to a named universe")
    ids = []
    for item in value:
        parts = item.split(":") if isinstance(item, str) else []
        if not 1 <= len(parts) <= 2 or not all(p.strip() for p in parts):
            raise _fail("universe", item, "expected 'SYMBOL' or 'SYMBOL:EXCHANGE'")
        ids.append(
            InstrumentId(parts[0].strip(), parts[1].strip() if len(parts) == 2 else exchange)
        )
    return StaticUniverse(ids)


def _weighting(value: object) -> tuple[WeightingScheme, int]:
    parts = _text("weighting", value).split(":")
    if parts == ["equal"]:
        return EqualWeight(), 0
    if parts[0] == "inverse_vol" and len(parts) <= 2:
        lookback = _int("weighting", value, parts[1], 2) if len(parts) == 2 else 20
        return InverseVolatility(lookback), lookback
    raise _fail("weighting", value, "expected 'equal', 'inverse_vol' or 'inverse_vol:<lookback>'")


def _one_schedule(text: str, whole: object) -> RebalanceSchedule:
    kind, _, arg = text.partition(":")
    if kind == "every" and arg.endswith("d"):
        return EveryNDays(_int("schedule", text, arg[:-1], 1, whole))
    if text == "monthly:first_session":
        return MonthlyFirstSession()
    if kind == "drift" and arg:
        try:
            tolerance = float(arg)
            if not (math.isfinite(tolerance) and tolerance > 0):
                raise ValueError
        except ValueError:
            raise _fail(
                "schedule",
                whole,
                f"drift tolerance {arg!r} must be a number > 0",
            )
        return DriftBand(tolerance)
    raise _fail(
        "schedule",
        whole,
        f"unknown schedule {text!r}; use every:<N>d, monthly:first_session, drift:<x>",
    )


def _schedule(value: object) -> RebalanceSchedule:
    parts = [_one_schedule(p.strip(), value) for p in _text("schedule", value).split("+")]
    return parts[0] if len(parts) == 1 else AnyOf(*parts)


def _select(value: object) -> tuple[Selector, int]:
    parts = _text("select", value).split(":")
    if len(parts) != 4 or parts[0] != "top" or parts[2] not in _SCORES:
        raise _fail(
            "select",
            value,
            "expected 'top:<n>:momentum:<lookback>', 'top:<n>:low_vol:<lookback>', or 'top:<n>:mean_reversion:<lookback>'",
        )
    n = _int("select", value, parts[1], 1)
    lookback = _int("select", value, parts[3], 2)
    return TopN(n, _SCORES[parts[2]](lookback)), lookback


def build_portfolio_strategy(
    params: Mapping[str, Any], *, name: str | None = None
) -> PortfolioStrategy:
    """Construct a ``PortfolioStrategy`` from TOML-friendly ``params`` (see module docs).

    Deterministic and free of I/O. Raises ``ValueError`` naming the offending key and value
    for unknown keys, a missing ``universe``, or any malformed value.
    """
    unknown = sorted(set(params) - KEYS)
    if unknown:
        raise ValueError(f"unknown portfolio params {unknown}; allowed: {sorted(KEYS)}")
    if "universe" not in params:
        raise ValueError("params['universe'] is required")
    universe = _universe(params)
    lookbacks = [0]
    weighting = EqualWeight()
    if "weighting" in params:
        weighting, lookback = _weighting(params["weighting"])
        lookbacks.append(lookback)
    schedule = _schedule(params["schedule"]) if "schedule" in params else EveryNDays(15)
    selector: Selector = SelectAll()
    if "select" in params:
        selector, lookback = _select(params["select"])
        lookbacks.append(lookback)
    kwargs: dict[str, Any] = {}
    if "allocation" in params:
        alloc = params["allocation"]
        if isinstance(alloc, bool) or not isinstance(alloc, (int, float)):
            raise _fail("allocation", alloc, "must be a number in (0, 1]")
        if not (math.isfinite(alloc) and 0 < alloc <= 1):
            raise _fail("allocation", alloc, "must be in (0, 1]")
        kwargs["allocation"] = float(alloc)
    needed = max(lookbacks)
    if "history_len" in params:
        hist = params["history_len"]
        if isinstance(hist, bool) or not isinstance(hist, int) or hist < 1:
            raise _fail("history_len", hist, "must be an int >= 1")
        if hist < needed:
            raise _fail("history_len", hist, f"must be >= the longest lookback in use ({needed})")
        kwargs["history_len"] = hist
    else:
        kwargs["history_len"] = max(_MIN_HISTORY, needed)
    return PortfolioStrategy(universe, weighting, schedule, selector, name=name, **kwargs)
