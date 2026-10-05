//! Turning the endpoint registry into OpenAPI `paths`.
//!
//! plan.md §4.3 requires the spec to be generated, never hand-written. Reading
//! the registry rather than a second list is what keeps the two in step: a route
//! added to the registry appears in the spec, and a route missing from the
//! registry cannot appear in the spec.

use honba_messages::{Access, HttpMethod, ENDPOINTS, WRITE_PATHS};
use serde_json::{json, Map, Value};

use crate::schemas::SchemaSet;

/// The `$ref` prefix used inside the OpenAPI document.
const COMPONENT_PREFIX: &str = "#/components/schemas/";

/// The response payload type for each endpoint, so a path can name it.
fn response_type_for(method: &str, path: &str) -> Option<&'static str> {
    Some(match (method, path) {
        ("GET", "/capabilities") => "Capabilities",
        ("GET", "/health") => "Capabilities",
        ("GET", "/schema") => "Capabilities",
        ("GET", "/instruments") => "InstrumentsResponse",
        ("GET", "/instruments/{id}") => "InstrumentsResponse",
        ("GET", "/quotes") => "QuotesResponse",
        ("GET", "/bars/{id}") => "BarsResponse",
        ("GET", "/depth/{id}") => "DepthResponse",
        ("POST", "/strategies") => "StrategiesResponse",
        ("GET", "/strategies") => "StrategiesResponse",
        ("POST", "/backtests") => "BacktestResponse",
        ("GET", "/backtests/{id}") => "BacktestResponse",
        ("GET", "/backtests/{id}/journal") => "TradesResponse",
        ("POST", "/sweeps") => "SweepResponse",
        ("GET", "/sweeps/{id}") => "SweepResponse",
        ("POST", "/orders") => "OrdersResponse",
        ("GET", "/orders") => "OrdersResponse",
        ("DELETE", "/orders/{id}") => "OrdersResponse",
        ("POST", "/positions/close") => "PositionsResponse",
        ("GET", "/screener/scan") => "TradesResponse",
        ("GET", "/journals/{id}") => "TradesResponse",
        _ => return None,
    })
}

/// The request body type for each endpoint that takes one.
fn request_type_for(method: &str, path: &str) -> Option<&'static str> {
    Some(match (method, path) {
        ("POST", "/strategies") => "StrategiesRequest",
        ("POST", "/backtests") => "BacktestRequest",
        ("POST", "/sweeps") => "SweepRequest",
        ("POST", "/orders") => "OrdersRequest",
        ("GET", "/instruments") => "InstrumentsQuery",
        ("GET", "/quotes") => "QuotesQuery",
        ("GET", "/bars/{id}") => "BarsQuery",
        ("GET", "/depth/{id}") => "DepthQuery",
        _ => return None,
    })
}

/// Renders the `paths` object.
pub fn openapi_paths(set: &SchemaSet) -> Value {
    let mut paths: Map<String, Value> = Map::new();

    for (method, path) in ENDPOINTS {
        let entry = paths
            .entry((*path).to_string())
            .or_insert_with(|| Value::Object(Map::new()));
        let key = method.to_ascii_lowercase();
        entry[key.as_str()] = operation(set, method, path);
    }

    Value::Object(paths)
}

/// Renders one operation object.
fn operation(set: &SchemaSet, method: &str, path: &'static str) -> Value {
    let mut op = Map::new();
    op.insert("summary".into(), json!(summary(method, path)));
    op.insert("operationId".into(), json!(operation_id(method, path)));
    op.insert("tags".into(), json!([tag_for(path)]));

    let mut params = Vec::new();
    for name in path_params(path) {
        params.push(json!({
            "name": name,
            "in": "path",
            "required": true,
            "schema": {"type": "string"},
        }));
    }
    if let Some(query) = request_type_for(method, path) {
        if method != HttpMethod::Delete.as_str() {
            params.extend(query_params(set, query));
        }
    }
    if !params.is_empty() {
        op.insert("parameters".into(), Value::Array(params));
    }

    if matches!(method, "POST" | "DELETE") {
        if let Some(body) = request_type_for(method, path) {
            op.insert("requestBody".into(), json!({
                "required": true,
                "content": {"application/json": {"schema": {"$ref": format!("{COMPONENT_PREFIX}{body}")}}},
            }));
        }
    }

    let response = response_type_for(method, path)
        .map(|name| format!("{COMPONENT_PREFIX}{name}"))
        .unwrap_or_else(|| "#/components/schemas/Capabilities".to_string());

    let mut responses = Map::new();
    responses.insert(
        "200".into(),
        json!({
            "description": "Success. Always enveloped; see ResponseEnvelope.",
            "content": {"application/json": {"schema": {"$ref": response}}},
        }),
    );
    responses.insert(
        "default".into(),
        json!({
            "description": "Failure. Carries the envelope's `error` with a stable ErrorCode.",
            "content": {"application/json": {"schema": {"$ref": format!("{COMPONENT_PREFIX}ResponseEnvelope")}}},
        }),
    );
    op.insert("responses".into(), Value::Object(responses));

    let is_write = WRITE_PATHS.iter().any(|(m, p)| *m == method && *p == path);
    let access = if is_write {
        Access::Write
    } else {
        Access::ReadOnly
    };
    op.insert("x-honba-access".into(), json!(access_label(access)));

    Value::Object(op)
}

fn access_label(access: Access) -> &'static str {
    match access {
        Access::ReadOnly => "read_only",
        Access::Write => "write",
    }
}

/// Expands a query DTO's properties into OpenAPI query parameters.
fn query_params(set: &SchemaSet, type_name: &str) -> Vec<Value> {
    // Read the normalized copy: a fragment embedded in the spec must carry the
    // spec's own $ref spelling, not the generator's internal one.
    let normalized = set.normalized(type_name, COMPONENT_PREFIX);
    let Some(schema) = set.get(type_name) else {
        return Vec::new();
    };
    let Some(props) = schema.get("properties").and_then(Value::as_object) else {
        return Vec::new();
    };
    let required: Vec<String> = schema
        .get("required")
        .and_then(Value::as_array)
        .map(|a| {
            a.iter()
                .filter_map(|v| v.as_str())
                .map(String::from)
                .collect()
        })
        .unwrap_or_default();

    props
        .iter()
        .filter(|(name, _)| !required.contains(*name))
        .map(|(name, _)| {
            json!({
                "name": name,
                "in": "query",
                "required": false,
                "schema": normalized["properties"][name],
            })
        })
        .collect()
}

/// Extracts `{name}` placeholders from a path, in order.
fn path_params(path: &str) -> Vec<&str> {
    let mut out = Vec::new();
    let mut rest = path;
    while let Some(start) = rest.find('{') {
        let after = &rest[start + 1..];
        match after.find('}') {
            Some(end) => {
                out.push(&after[..end]);
                rest = &after[end + 1..];
            }
            None => break,
        }
    }
    out
}

/// Groups paths into OpenAPI tags by their first segment.
///
/// Two segments are folded together so the runs and the trading surface read as
/// coherent groups in the generated docs.
fn tag_for(path: &str) -> String {
    match path.trim_start_matches('/').split('/').next().unwrap_or("") {
        "backtests" | "sweeps" => "runs".to_string(),
        "positions" | "orders" => "trading".to_string(),
        "capabilities" | "health" | "schema" | "journals" => "meta".to_string(),
        other => other.to_string(),
    }
}

/// Builds a stable, unique operation id from method and path.
fn operation_id(method: &str, path: &str) -> String {
    let verb = method.to_ascii_lowercase();
    let rest: Vec<String> = path
        .trim_start_matches('/')
        .split('/')
        .filter(|s| !s.is_empty())
        .map(|segment| {
            if segment.starts_with('{') && segment.ends_with('}') {
                format!("by_{}", segment.trim_matches(|c| c == '{' || c == '}'))
            } else {
                segment.to_string()
            }
        })
        .collect();
    format!("{verb}_{}", rest.join("_"))
}

fn summary(method: &str, path: &str) -> String {
    let subject = path.trim_start_matches('/').replace(['/', '{', '}'], " ");
    format!("{} {}", method, subject.trim())
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::registry;

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
}
