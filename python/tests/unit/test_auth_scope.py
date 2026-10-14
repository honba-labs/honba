"""The scope map covers every endpoint and flags an unscoped one (E5-S3, ADR 0023)."""

from __future__ import annotations

from honba.api.auth import SCOPES, required_scope_for_route, unscoped_routes

#: The full served surface, mirroring the Rust `ROUTE_SCOPES` table.
KNOWN_ROUTES = [
    ("GET", "/capabilities"),
    ("GET", "/health"),
    ("GET", "/schema"),
    ("GET", "/instruments"),
    ("GET", "/instruments/ABC"),
    ("GET", "/quotes"),
    ("GET", "/bars/ABC"),
    ("GET", "/depth/ABC"),
    ("GET", "/screener/scan"),
    ("GET", "/strategies"),
    ("POST", "/strategies"),
    ("POST", "/strategies/verify"),
    ("POST", "/backtests"),
    ("GET", "/backtests/abc"),
    ("GET", "/backtests/abc/journal"),
    ("GET", "/backtests/abc/events"),
    ("POST", "/sweeps"),
    ("GET", "/sweeps/abc"),
    ("GET", "/sweeps/abc/journal"),
    ("GET", "/journals/abc"),
    ("POST", "/orders"),
    ("GET", "/orders"),
    ("DELETE", "/orders/abc"),
    ("POST", "/positions/close"),
    ("GET", "/trading-state"),
    ("PUT", "/trading-state"),
    ("GET", "/approval/queue"),
    ("POST", "/approval/approve"),
    ("POST", "/approval/reject"),
]


def test_every_known_endpoint_has_a_scope() -> None:
    assert unscoped_routes(KNOWN_ROUTES) == []


def test_scope_names_are_from_the_defined_set() -> None:
    for path in ("/instruments", "/bars/ABC", "/screener/scan"):
        assert required_scope_for_route("GET", path) in SCOPES
    for path in ("/strategies", "/backtests/abc", "/journals/abc"):
        assert required_scope_for_route("GET", path) in SCOPES
    for method, path in (
        ("POST", "/orders"),
        ("DELETE", "/orders/ABC"),
        ("GET", "/approval/queue"),
    ):
        assert required_scope_for_route(method, path) in SCOPES


def test_drift_fails_on_new_route() -> None:
    # A route that is not in the map is reported, so the guard bites.
    assert unscoped_routes([("GET", "/brand/new")]) == ["GET /brand/new"]
    # Public routes are never reported.
    assert unscoped_routes([("GET", "/health")]) == []


def test_trading_state_routes_need_the_write_scope() -> None:
    for method in ("GET", "PUT"):
        assert required_scope_for_route(method, "/trading-state") == "write:orders"
