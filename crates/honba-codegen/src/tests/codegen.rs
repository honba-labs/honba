//! Unit tests for `crate`.

use crate::*;
use serde_json::{json, Value};

#[test]
fn the_version_axes_are_owned_here() {
    // plan.md 4.1: both constants have exactly one owner; codegen
    // re-exports them rather than repeating the literal.
    assert_eq!(SCHEMA_VERSION, honba_messages::SCHEMA_VERSION);
    assert_eq!(API_VERSION, honba_messages::API_VERSION);
    assert!(!API_VERSION.is_empty());
}

#[test]
fn the_bundle_declares_a_draft_that_supports_defs() {
    let schema = Codegen::new().json_schema();
    assert_eq!(
        schema["$schema"],
        json!("https://json-schema.org/draft/2020-12/schema")
    );
    assert!(schema["$defs"].is_object());
}

#[test]
fn every_ref_in_the_bundle_is_resolvable() {
    // The defect this guards: the bundle declared `$defs` while the
    // generated types still used `#/definitions/`, so 23 nested references
    // pointed at a key that did not exist.
    let schema = Codegen::new().json_schema();
    let defs = schema["$defs"].as_object().expect("$defs object");
    let mut missing = Vec::new();
    check_refs(&schema["$defs"], "#/$defs/", defs, &mut missing);
    assert!(missing.is_empty(), "unresolvable $refs: {missing:?}");
}

fn check_refs(
    value: &Value,
    prefix: &str,
    defs: &serde_json::Map<String, Value>,
    missing: &mut Vec<String>,
) {
    match value {
        Value::Object(map) => {
            for (key, child) in map {
                if key == "$ref" {
                    if let Some(reference) = child.as_str() {
                        let name = reference.strip_prefix(prefix).unwrap_or(reference);
                        if !defs.contains_key(name) {
                            missing.push(reference.to_string());
                        }
                    }
                } else {
                    check_refs(child, prefix, defs, missing);
                }
            }
        }
        Value::Array(items) => {
            for item in items {
                check_refs(item, prefix, defs, missing);
            }
        }
        _ => {}
    }
}

#[test]
fn the_openapi_document_has_paths() {
    // An OpenAPI document with no `paths` describes nothing; codegen used to
    // emit only info and components.
    let spec = Codegen::new().openapi();
    assert_eq!(spec["openapi"], json!("3.1.0"));
    let paths = spec["paths"].as_object().expect("paths object");
    assert!(!paths.is_empty());
    assert!(paths.contains_key("/health"));
    assert!(paths.contains_key("/instruments"));
}

#[test]
fn every_ref_in_the_openapi_document_resolves_as_a_json_pointer() {
    // The defect this guards: components carried bare refs such as
    // `{"$ref": "AccountConfig"}`, which no OpenAPI tool can resolve. The old
    // check stripped an optional prefix and looked the remainder up in the
    // registry, so a bare name passed. Resolve each ref against the document
    // itself instead, the way a validator would.
    let spec = Codegen::new().openapi();
    let unresolved = unresolved_local_refs(&spec);
    assert!(unresolved.is_empty(), "unresolvable $refs: {unresolved:?}");
}

#[test]
fn every_openapi_ref_points_into_components_schemas() {
    let spec = Codegen::new().openapi();
    let refs = local_refs(&spec);
    assert!(!refs.is_empty());
    let stray: Vec<_> = refs
        .iter()
        .filter(|r| !r.starts_with("#/components/schemas/"))
        .collect();
    assert!(stray.is_empty(), "refs outside components: {stray:?}");
}

#[test]
fn the_json_schema_bundle_refs_resolve_as_json_pointers() {
    let unresolved = unresolved_local_refs(&Codegen::new().json_schema());
    assert!(unresolved.is_empty(), "unresolvable $refs: {unresolved:?}");
}

#[test]
fn the_openapi_paths_are_the_endpoint_registry_paths() {
    let spec = Codegen::new().openapi();
    assert_eq!(
        spec["paths"],
        endpoints::openapi_paths(Codegen::new().schemas())
    );
}

#[test]
fn a_bare_ref_is_reported_as_unresolved() {
    let doc = json!({"components": {"schemas": {"A": {"type": "object"}, "B": {"$ref": "A"}}}});
    assert_eq!(unresolved_local_refs(&doc), vec!["A".to_string()]);
}

#[test]
fn a_pointer_ref_that_exists_resolves() {
    let doc = json!({"$defs": {"A": {"type": "object"}, "B": {"$ref": "#/$defs/A"}}});
    assert!(unresolved_local_refs(&doc).is_empty());
}

#[test]
fn a_pointer_ref_to_a_missing_key_is_unresolved() {
    let doc = json!({"$defs": {"B": {"$ref": "#/$defs/A"}}});
    assert_eq!(unresolved_local_refs(&doc), vec!["#/$defs/A".to_string()]);
}

#[test]
fn pointer_escapes_are_decoded() {
    let doc = json!({"$defs": {"a/b": {"x": 1}, "c~d": {"y": 2}},
                     "r1": {"$ref": "#/$defs/a~1b"}, "r2": {"$ref": "#/$defs/c~0d"}});
    assert!(unresolved_local_refs(&doc).is_empty());
}

#[test]
fn the_bundle_carries_both_version_axes() {
    let schema = Codegen::new().json_schema();
    assert_eq!(schema["schema_version"], json!(SCHEMA_VERSION));
    assert_eq!(schema["api_version"], json!(API_VERSION));
}

#[test]
fn the_core_version_is_reported() {
    assert!(!CORE_VERSION.is_empty());
}
