"""Request builders: bad input is rejected before anything is sent."""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone

import pytest

from honba.client import RequestValidationError
from honba.client import requests as rq
from honba.entities.instrument import InstrumentId as DomainId
from honba.wire.wire import InstrumentId, UnixNanos

IST = timezone(timedelta(hours=5, minutes=30))


def test_health_and_instruments_requests() -> None:
    assert rq.health() == rq.ApiRequest("GET", "/health")
    assert rq.instruments() == rq.ApiRequest("GET", "/instruments")
    got = rq.instruments(exchange="NSE", symbol="TCS")
    assert got.query == {"exchange": "NSE", "symbol": "TCS"}


@pytest.mark.parametrize("bad", ["", "  ", 5])
def test_instrument_filters_must_be_non_empty_strings(bad: object) -> None:
    with pytest.raises(RequestValidationError) as err:
        rq.instruments(exchange=bad)  # type: ignore[arg-type]
    assert err.value.field == "exchange"


def test_instrument_ids_accept_text_wire_and_domain_forms_and_are_path_encoded() -> None:
    assert rq.instrument("TCS.NSE").path == "/instruments/TCS.NSE"
    assert rq.instrument(InstrumentId(symbol="TCS", exchange="NSE")).path == "/instruments/TCS.NSE"
    assert rq.instrument(DomainId("TCS", "NSE")).path == "/instruments/TCS.NSE"
    # The symbol itself may hold a dot or characters that are special in a URL.
    assert rq.instrument("M&M.NSE").path == "/instruments/M%26M.NSE"
    assert rq.instrument("A/B.NSE").path == "/instruments/A%2FB.NSE"
    assert rq.instrument("BRK.B.NYSE").path == "/instruments/BRK.B.NYSE"


@pytest.mark.parametrize("bad", ["TCS", ".NSE", "TCS.", "", " TCS.NSE", None, 3])
def test_malformed_instrument_ids_are_rejected(bad: object) -> None:
    with pytest.raises(RequestValidationError) as err:
        rq.instrument(bad)  # type: ignore[arg-type]
    assert (err.value.field, err.value.reason) == ("id", "invalid_instrument_id")


def test_bars_defaults_send_no_query() -> None:
    assert rq.bars("TCS.NSE") == rq.ApiRequest("GET", "/bars/TCS.NSE")


def test_bars_timeframe_grammar_matches_the_server() -> None:
    for ok in ("30s", "1m", "4h", "1d", "2w", "1mo", "15m"):
        assert rq.bars("TCS.NSE", tf=ok).query == {"tf": ok}
    for bad in ("", "m", "0m", "1M", "1x", "-1m", "1.5m", "1 m", 1):
        with pytest.raises(RequestValidationError) as err:
            rq.bars("TCS.NSE", tf=bad)  # type: ignore[arg-type]
        assert (err.value.field, err.value.reason) == ("tf", "invalid_timeframe")


def test_bar_bounds_accept_text_dates_datetimes_and_unix_nanos() -> None:
    got = rq.bars(
        "TCS.NSE",
        from_="2024-01-01",
        to=datetime(2024, 1, 2, 5, 30, tzinfo=IST),
    )
    assert got.query == {"from": "2024-01-01", "to": "2024-01-02T00:00:00.000000000Z"}
    assert rq.bars("T.N", from_=date(2024, 1, 1)).query == {"from": "2024-01-01"}
    stamp = UnixNanos.from_ns(1_704_067_200_000_000_000)
    assert rq.bars("T.N", from_=stamp).query == {"from": stamp.iso}
    assert rq.bars("T.N", to="2024-01-01T00:00:00Z").query == {"to": "2024-01-01T00:00:00Z"}
    assert rq.bars("T.N", to="2024-01-01T09:15:00.5+05:30").query["to"].endswith("+05:30")


@pytest.mark.parametrize(
    ("value", "reason"),
    [
        ("yesterday", "invalid_time"),
        ("2024-13-01", "invalid_time"),
        ("2024-02-30", "invalid_time"),
        ("2024-01-01T25:00:00Z", "invalid_time"),
        ("2024-01-01T00:00:00", "invalid_time"),  # no offset
        (datetime(2024, 1, 1), "naive_datetime"),  # noqa: DTZ001
        ("1969-12-31", "before_epoch"),
        (12345, "invalid_time"),
    ],
)
def test_bad_bounds_are_rejected_naming_the_field(value: object, reason: str) -> None:
    with pytest.raises(RequestValidationError) as err:
        rq.bars("TCS.NSE", from_=value)  # type: ignore[arg-type]
    assert (err.value.field, err.value.reason) == ("from", reason)


def test_an_empty_or_inverted_range_is_rejected() -> None:
    for from_, to in [("2024-01-02", "2024-01-01"), ("2024-01-01", "2024-01-01")]:
        with pytest.raises(RequestValidationError) as err:
            rq.bars("TCS.NSE", from_=from_, to=to)
        assert (err.value.field, err.value.reason) == ("to", "empty_range")
    # One day vs the same instant written differently is still empty.
    with pytest.raises(RequestValidationError):
        rq.bars("T.N", from_="2024-01-01", to="2024-01-01T00:00:00Z")


def test_quotes_join_symbols_and_carry_venue_and_as_of() -> None:
    got = rq.quotes(["TCS", "INFY"], venue="NSE", as_of="2024-01-01")
    assert got == rq.ApiRequest(
        "GET", "/quotes", {"symbols": "TCS,INFY", "venue": "NSE", "as_of": "2024-01-01"}
    )
    assert rq.quotes("TCS").query == {"symbols": "TCS"}
    assert rq.quotes(("TCS",)).query == {"symbols": "TCS"}


@pytest.mark.parametrize("bad", [[], "", [""], ["TCS", " "], ["A,B"], "A,B", [1]])
def test_bad_symbol_lists_are_rejected(bad: object) -> None:
    with pytest.raises(RequestValidationError) as err:
        rq.quotes(bad)  # type: ignore[arg-type]
    assert err.value.field == "symbols"


def test_depth_levels_are_1_to_50() -> None:
    assert rq.depth("TCS.NSE") == rq.ApiRequest("GET", "/depth/TCS.NSE")
    assert rq.depth("TCS.NSE", levels=50).query == {"depth": 50}
    for bad in (0, 51, -1, 2.5, True, "5"):
        with pytest.raises(RequestValidationError) as err:
            rq.depth("TCS.NSE", levels=bad)  # type: ignore[arg-type]
        assert err.value.field == "depth"


def test_verify_strategy_posts_a_json_object() -> None:
    got = rq.verify_strategy({"name": "x"})
    assert got == rq.ApiRequest("POST", "/strategies/verify", body={"name": "x"})
    for bad in ({}, [], "text", None):
        with pytest.raises(RequestValidationError) as err:
            rq.verify_strategy(bad)  # type: ignore[arg-type]
        assert err.value.field == "manifest"


def test_verify_strategy_accepts_a_manifest_model() -> None:
    from honba.entities.instrument import InstrumentId as Id
    from honba.strategies.manifest import StrategyManifest, Subscriptions, TimeframeSpec, Universe
    from honba.wire.wire import BarAggregation

    manifest = StrategyManifest.build(
        "s",
        "sha256:1",
        Universe.of_explicit([Id("TCS", "NSE")]),
        TimeframeSpec(interval=1, aggregation=BarAggregation.DAY),
        subscriptions=Subscriptions.of([Id("TCS", "NSE")]),
    )
    assert rq.verify_strategy(manifest).body == manifest.to_json_dict()


def test_strategies_is_a_plain_get() -> None:
    assert rq.strategies() == rq.ApiRequest("GET", "/strategies")


def test_compile_strategy_wraps_the_manifest_in_a_request_body() -> None:
    got = rq.compile_strategy({"name": "x"})
    assert got == rq.ApiRequest("POST", "/strategies", body={"manifest": {"name": "x"}})
    for bad in ({}, [], "text", None):
        with pytest.raises(RequestValidationError) as err:
            rq.compile_strategy(bad)  # type: ignore[arg-type]
        assert err.value.field == "manifest"


def test_compile_strategy_accepts_a_manifest_model() -> None:
    from honba.entities.instrument import InstrumentId as Id
    from honba.strategies.manifest import StrategyManifest, Subscriptions, TimeframeSpec, Universe
    from honba.wire.wire import BarAggregation

    manifest = StrategyManifest.build(
        "s",
        "sha256:1",
        Universe.of_explicit([Id("TCS", "NSE")]),
        TimeframeSpec(interval=1, aggregation=BarAggregation.DAY),
        subscriptions=Subscriptions.of([Id("TCS", "NSE")]),
    )
    assert rq.compile_strategy(manifest).body == {"manifest": manifest.to_json_dict()}


I64_MAX = 2**63 - 1


@pytest.mark.parametrize(
    "value",
    [
        "2300-01-01",
        "2262-04-12",
        "2262-04-11T23:47:16.854775808Z",
        datetime(2300, 1, 1, tzinfo=timezone.utc),
        date(2300, 1, 1),
        datetime(9999, 12, 31, 23, tzinfo=timezone(-timedelta(hours=5))),
        UnixNanos.from_ns(I64_MAX + 1),
    ],
)
def test_times_beyond_the_server_nanosecond_range_are_rejected_client_side(value: object) -> None:
    with pytest.raises(RequestValidationError) as err:
        rq.bars("TCS.NSE", to=value)  # type: ignore[arg-type]
    assert (err.value.field, err.value.reason) == ("to", "invalid_time")


def test_the_last_representable_nanosecond_is_accepted() -> None:
    assert rq.bars("T.N", to="2262-04-11T23:47:16.854775807Z").query["to"].endswith("807Z")
    assert rq.bars("T.N", to=UnixNanos.from_ns(I64_MAX)).query


class _NanoStamp(datetime):
    """Stand-in for pandas.Timestamp: a datetime subclass with a ``nanosecond`` part."""

    nanosecond = 0


def _stamp(nanosecond: int) -> _NanoStamp:
    stamp = _NanoStamp(2024, 1, 1, 0, 0, 0, 123456, tzinfo=timezone.utc)
    stamp.nanosecond = nanosecond
    return stamp


def test_datetime_subclasses_keep_their_sub_microsecond_nanoseconds() -> None:
    got = rq.bars("T.N", to=_stamp(789))
    assert got.query == {"to": "2024-01-01T00:00:00.123456789Z"}


def test_a_nanosecond_attribute_of_zero_changes_nothing() -> None:
    assert rq.bars("T.N", to=_stamp(0)).query == {"to": "2024-01-01T00:00:00.123456000Z"}


# --- screener_scan -----------------------------------------------------------------------

AND_CLOSE_GT_100 = {"operator": "AND", "items": [{"key": "close", "op": "gt", "value": 100}]}


def test_screener_scan_sends_json_encoded_universe_and_filters() -> None:
    got = rq.screener_scan(["TCS.NSE", "INFY.NSE"], AND_CLOSE_GT_100, tf="1m", as_of="2024-01-02")
    assert got.method == "GET" and got.path == "/screener/scan" and got.body is None
    assert got.query is not None
    assert json.loads(got.query["universe"]) == ["TCS.NSE", "INFY.NSE"]
    assert json.loads(got.query["filters"]) == AND_CLOSE_GT_100
    assert got.query["tf"] == "1m" and got.query["as_of"] == "2024-01-02"


def test_screener_scan_defaults_send_only_the_universe() -> None:
    got = rq.screener_scan("TCS.NSE")
    assert got.query is not None and set(got.query) == {"universe"}
    assert json.loads(got.query["universe"]) == ["TCS.NSE"]


def test_screener_scan_accepts_domain_and_wire_ids() -> None:
    got = rq.screener_scan([DomainId("TCS", "NSE"), InstrumentId(symbol="INFY", exchange="NSE")])
    assert got.query is not None
    assert json.loads(got.query["universe"]) == ["TCS.NSE", "INFY.NSE"]


def test_screener_scan_accepts_filter_models_and_a_bare_predicate() -> None:
    from honba.wire.screener import FilterOp, ScreenerFilterGroup, ScreenerFilterPredicate

    pred = ScreenerFilterPredicate(key="close", op=FilterOp.GT, value=100)
    group = ScreenerFilterGroup(operator="AND", items=[pred])
    for filters in (group, pred):
        got = rq.screener_scan("TCS.NSE", filters)
        assert got.query is not None
        assert json.loads(got.query["filters"]) == AND_CLOSE_GT_100


def test_screener_scan_sends_metric_references() -> None:
    from honba.wire.screener import FilterOp, MetricRef, ScreenerFilterPredicate

    pred = ScreenerFilterPredicate(
        key="SMA50", op=FilterOp.CROSSES_ABOVE, value=MetricRef(key="SMA200")
    )
    got = rq.screener_scan("TCS.NSE", pred)
    assert got.query is not None
    assert json.loads(got.query["filters"])["items"] == [
        {"key": "SMA50", "op": "crosses_above", "value": {"key": "SMA200"}}
    ]


@pytest.mark.parametrize(
    ("universe", "reason"),
    [
        ([], "missing_universe"),
        ("", "invalid_instrument_id"),
        (["TCS"], "invalid_instrument_id"),
        ([1], "invalid_instrument_id"),
        ([f"S{i}.NSE" for i in range(rq.MAX_SCREENER_UNIVERSE + 1)], "too_many_rows"),
    ],
)
def test_screener_scan_rejects_a_bad_universe_naming_the_field(
    universe: object, reason: str
) -> None:
    with pytest.raises(RequestValidationError) as err:
        rq.screener_scan(universe)  # type: ignore[arg-type]
    assert (err.value.field, err.value.reason) == ("universe", reason)


@pytest.mark.parametrize(
    "filters",
    [
        {"operator": "XOR", "items": []},
        {"operator": "AND", "items": [{"key": "close", "op": "between", "value": 1}]},
        {"operator": "AND"},
        "close > 100",
        42,
    ],
)
def test_screener_scan_rejects_malformed_filters(filters: object) -> None:
    with pytest.raises(RequestValidationError) as err:
        rq.screener_scan("TCS.NSE", filters)  # type: ignore[arg-type]
    assert (err.value.field, err.value.reason) == ("filters", "invalid_filter")


def test_screener_scan_validates_tf_and_as_of_like_the_server() -> None:
    with pytest.raises(RequestValidationError) as err:
        rq.screener_scan("TCS.NSE", tf="banana")
    assert (err.value.field, err.value.reason) == ("tf", "invalid_timeframe")
    with pytest.raises(RequestValidationError) as err:
        rq.screener_scan("TCS.NSE", as_of="soon")
    assert (err.value.field, err.value.reason) == ("as_of", "invalid_time")
    got = rq.screener_scan("TCS.NSE", as_of=datetime(2024, 1, 2, tzinfo=timezone.utc))
    assert got.query is not None and got.query["as_of"].startswith("2024-01-02")
