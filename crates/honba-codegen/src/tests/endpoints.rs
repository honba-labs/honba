//! Unit tests for `crate::endpoints`.

use crate::endpoints::*;
use crate::registry;
use crate::schemas::SchemaSet;
use honba_messages::ENDPOINTS;
use serde_json::{json, Value};

fn paths() -> Value {
    openapi_paths(&registry::full_registry())
}

#[test]
fn every_registered_endpoint_appears_in_the_spec() {
    let paths = paths();
    for (method, path) in ENDPOINTS {
        let op = paths[path][method.to_ascii_lowercase()]
            .as_object()
            .unwrap_or_else(|| panic!("{method} {path} missing from spec"));
        assert!(
            op.contains_key("responses"),
            "{method} {path} has no responses"
        );
    }
}

#[test]
fn operation_ids_are_unique() {
    let paths = paths();
    let mut ids = std::collections::BTreeSet::new();
    for (_, item) in paths.as_object().unwrap() {
        for (_, op) in item.as_object().unwrap() {
            let id = op["operationId"].as_str().expect("operationId");
            assert!(ids.insert(id.to_string()), "duplicate operationId {id}");
        }
    }
}

#[test]
fn path_parameters_are_declared_and_required() {
    let paths = paths();
    let params = paths["/backtests/{id}"]["get"]["parameters"]
        .as_array()
        .expect("parameters");
    let id = params
        .iter()
        .find(|p| p["name"] == "id")
        .expect("id parameter declared");
    assert_eq!(id["in"], "path");
    assert_eq!(id["required"], json!(true));
}

#[test]
fn a_write_endpoint_is_labelled_as_one() {
    // plan.md 4.3: the approval queue is gated off this label.
    let paths = paths();
    assert_eq!(paths["/orders"]["post"]["x-honba-access"], json!("write"));
    assert_eq!(
        paths["/orders/{id}"]["delete"]["x-honba-access"],
        json!("write")
    );
    assert_eq!(
        paths["/positions/close"]["post"]["x-honba-access"],
        json!("write")
    );
}

#[test]
fn a_read_endpoint_is_labelled_read_only() {
    let paths = paths();
    assert_eq!(
        paths["/health"]["get"]["x-honba-access"],
        json!("read_only")
    );
    assert_eq!(
        paths["/instruments"]["get"]["x-honba-access"],
        json!("read_only")
    );
}

#[test]
fn a_post_declares_a_request_body() {
    let paths = paths();
    let body = &paths["/backtests"]["post"]["requestBody"];
    assert_eq!(body["required"], json!(true));
    assert_eq!(
        body["content"]["application/json"]["schema"]["$ref"],
        json!("#/components/schemas/BacktestRequest")
    );
}

#[test]
fn every_failure_is_covered_by_a_default_response() {
    // plan.md 4.2: errors are enveloped, so every operation needs one.
    let paths = paths();
    for (_, item) in paths.as_object().unwrap() {
        for (_, op) in item.as_object().unwrap() {
            assert!(
                op["responses"]["default"].is_object(),
                "operation missing a default error response"
            );
        }
    }
}

#[test]
fn every_ref_in_the_paths_resolves_in_the_registry() {
    let set = registry::full_registry();
    let paths = openapi_paths(&set);
    let mut missing = Vec::new();
    check(&paths, &set, &mut missing);
    assert!(
        missing.is_empty(),
        "unresolvable refs in paths: {missing:?}"
    );
}

fn check(value: &Value, set: &SchemaSet, missing: &mut Vec<String>) {
    match value {
        Value::Object(map) => {
            for (key, child) in map {
                if key == "$ref" {
                    if let Some(reference) = child.as_str() {
                        let name = reference
                            .rsplit('/')
                            .next()
                            .unwrap_or(reference)
                            .trim_start_matches("schemas_");
                        if set.get(name).is_none() {
                            missing.push(reference.to_string());
                        }
                    }
                } else {
                    check(child, set, missing);
                }
            }
        }
        Value::Array(items) => {
            for item in items {
                check(item, set, missing);
            }
        }
        _ => {}
    }
}

#[test]
fn a_path_with_no_placeholders_yields_no_path_parameters() {
    assert!(path_params("/health").is_empty());
    assert_eq!(path_params("/instruments/{id}"), vec!["id"]);
}

#[test]
fn query_parameters_come_from_the_dto() {
    let set = registry::full_registry();
    let params = query_params(&set, "BarsQuery");
    let names: Vec<&str> = params.iter().filter_map(|p| p["name"].as_str()).collect();
    assert!(names.contains(&"tf"), "{names:?}");
    assert!(names.contains(&"from"), "{names:?}");
    assert!(names.contains(&"to"), "{names:?}");
}
