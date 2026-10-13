//! The route-to-scope map covers every registry endpoint (E5-S3, ADR 0023 §2).

use honba_api::ENDPOINTS;

use crate::{scope_for, unscoped_routes, RouteScope, ROUTE_SCOPES};

/// The scopes ADR 0023 §2 defines; no route may name any other.
const SCOPES: &[&str] = &["read:market", "read:account", "write:orders", "research"];

#[test]
fn scope_for_every_endpoint() {
    let missing = unscoped_routes(ENDPOINTS);
    assert!(
        missing.is_empty(),
        "endpoints with no scope row (would be public): {missing:?}"
    );

    for (method, path) in ENDPOINTS {
        let scope = scope_for(method, path)
            .unwrap_or_else(|| panic!("{method} {path} has no route-to-scope row"));
        if let RouteScope::Required(name) = scope {
            assert!(
                SCOPES.contains(&name),
                "{method} {path} names unknown scope {name:?}"
            );
        }
    }
}

#[test]
fn route_scopes_have_no_duplicate_keys() {
    let mut seen = std::collections::BTreeSet::new();
    for (method, path, _) in ROUTE_SCOPES {
        assert!(
            seen.insert((*method, *path)),
            "duplicate scope row for {method} {path}"
        );
    }
}

#[test]
fn known_route_needs_its_scope_and_unknown_is_unmapped() {
    assert_eq!(
        scope_for("GET", "/instruments"),
        Some(RouteScope::Required("read:market"))
    );
    assert_eq!(
        scope_for("GET", "/bars/ABC"),
        Some(RouteScope::Required("read:market"))
    );
    assert_eq!(
        scope_for("POST", "/orders"),
        Some(RouteScope::Required("write:orders"))
    );
    assert_eq!(scope_for("GET", "/capabilities"), Some(RouteScope::Public));
    assert_eq!(scope_for("GET", "/brand/new"), None);
}
