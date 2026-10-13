"""Every route the generated OpenAPI spec exposes has a scope (E5-S3, ADR 0023).

The spec is generated from the Rust endpoint registry, so this test ties the
Python scope mirror to the authoritative route list: adding an endpoint without a
scope makes ``unscoped_routes`` non-empty and fails the build.
"""

from __future__ import annotations

import json
from pathlib import Path

from honba.api.auth import unscoped_routes

OPENAPI = Path(__file__).resolve().parents[3] / "schema" / "openapi" / "openapi.json"


def _spec_routes() -> list[tuple[str, str]]:
    spec = json.loads(OPENAPI.read_text(encoding="utf-8"))
    routes: list[tuple[str, str]] = []
    for path, methods in spec["paths"].items():
        for method in methods:
            if method.upper() in {"GET", "POST", "DELETE", "PUT", "PATCH"}:
                routes.append((method.upper(), path))
    return routes


def test_scope_for_every_endpoint() -> None:
    routes = _spec_routes()
    assert routes, "the OpenAPI spec listed no routes"
    assert unscoped_routes(routes) == [], "generated endpoints without a scope"
