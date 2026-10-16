"""The scope map covers every endpoint and flags an unscoped one (E5-S3, ADR 0023)."""

from __future__ import annotations

import pytest

from honba.api.auth import (
    AUTHENTICATED,
    SCOPES,
    AuthError,
    AuthErrorCode,
    AuthLayer,
    TokenInfo,
    TokenStore,
    required_scope_for_route,
    unscoped_routes,
)

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
    ("GET", "/profile"),
    ("PUT", "/profile"),
    ("GET", "/profile/layouts"),
    ("PUT", "/profile/layouts/main"),
    ("DELETE", "/profile/layouts/main"),
    ("GET", "/profile/modules"),
    ("PUT", "/profile/modules/m1"),
    ("DELETE", "/profile/modules/m1"),
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


def test_trading_state_routes_need_the_operator_scope() -> None:
    for method in ("GET", "PUT"):
        assert required_scope_for_route(method, "/trading-state") == "operator"


def test_scopes_are_the_five_adr_0023_scopes() -> None:
    assert set(SCOPES) == {
        "read:market",
        "read:account",
        "write:orders",
        "research",
        "operator",
    }


class _OneTokenStore(TokenStore):
    """Accepts the token ``good`` with no scopes at all."""

    def validate(self, token: str) -> TokenInfo:
        if token != "good":
            raise AuthError(AuthErrorCode.INVALID_TOKEN, "Invalid or unknown token")
        return TokenInfo(token_id="alice", scopes=[])


def test_profile_routes_need_a_token_but_no_scope() -> None:
    assert AUTHENTICATED not in SCOPES
    for method, path in KNOWN_ROUTES:
        if path == "/profile" or path.startswith("/profile/"):
            assert required_scope_for_route(method, path) == AUTHENTICATED
    # A look-alike prefix is not a profile route.
    assert required_scope_for_route("GET", "/profiles") is None


def test_extract_enforces_any_valid_token_on_profile_routes() -> None:
    layer = AuthLayer(store=_OneTokenStore())
    for method, path in (("GET", "/profile"), ("PUT", "/profile/layouts/main")):
        with pytest.raises(AuthError) as missing:
            layer.extract(method, path, None)
        assert missing.value.code == AuthErrorCode.MISSING_TOKEN
        with pytest.raises(AuthError) as bad:
            layer.extract(method, path, "Bearer nope")
        assert bad.value.code == AuthErrorCode.INVALID_TOKEN
        info = layer.extract(method, path, "Bearer good")
        assert info is not None and info.token_id == "alice"
