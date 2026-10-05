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
fn the_openapi_document_has_no_dangling_refs() {
    let spec = Codegen::new().openapi();
    let components = &spec["components"]["schemas"];
    assert_eq!(
        spec["paths"],
        endpoints::openapi_paths(&Codegen::new().schemas().clone())
    );
    let _ = components;
    let mut missing = Vec::new();
    check_component_refs(&spec, &mut missing);
    assert!(missing.is_empty(), "unresolvable $refs: {missing:?}");
}

fn check_component_refs(value: &Value, missing: &mut Vec<String>) {
    let prefix = "#/components/schemas/";
    match value {
        Value::Object(map) => {
            for (key, child) in map {
                if key == "$ref" {
                    if let Some(reference) = child.as_str() {
                        let name = reference.strip_prefix(prefix).unwrap_or(reference);
                        if !Codegen::new().schemas().get(name).is_some() {
                            missing.push(reference.to_string());
                        }
                    }
                } else {
                    check_component_refs(child, missing);
                }
            }
        }
        Value::Array(items) => {
            for item in items {
                check_component_refs(item, missing);
            }
        }
        _ => {}
    }
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
