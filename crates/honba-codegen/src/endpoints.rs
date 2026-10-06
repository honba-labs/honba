//! Turning the endpoint registry into OpenAPI `paths`.
//!
//! plan.md §4.3 requires the spec to be generated, never hand-written. Reading
//! the registry rather than a second list is what keeps the two in step: a route
//! added to the registry appears in the spec, and a route missing from the
//! registry cannot appear in the spec.

use honba_messages::{Access, HttpMethod, ENDPOINTS, WRITE_PATHS};
use serde_json::{json, Map, Value};

use crate::schemas::{SchemaSet, COMPONENTS_PREFIX};

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
        ("POST", "/strategies") => "CompiledStrategy",
        ("GET", "/strategies") => "StrategiesResponse",
        ("POST", "/strategies/verify") => "StrategyIr",
        ("POST", "/backtests") => "BacktestResponse",
        ("GET", "/backtests/{id}") => "BacktestResponse",
        ("GET", "/backtests/{id}/journal") => "TradesResponse",
        ("POST", "/sweeps") => "SweepResponse",
        ("GET", "/sweeps/{id}") => "SweepResponse",
        ("POST", "/orders") => "OrdersResponse",
        ("GET", "/orders") => "OrdersResponse",
        ("DELETE", "/orders/{id}") => "OrdersResponse",
        ("POST", "/positions/close") => "PositionsResponse",
        ("GET", "/screener/scan") => "ScreenerResponse",
        ("GET", "/journals/{id}") => "TradesResponse",
        _ => return None,
    })
}

/// The request body type for each endpoint that takes one.
fn request_type_for(method: &str, path: &str) -> Option<&'static str> {
    Some(match (method, path) {
        ("POST", "/strategies") => "StrategiesRequest",
        ("POST", "/strategies/verify") => "StrategyManifest",
        ("POST", "/backtests") => "BacktestRequest",
        ("POST", "/sweeps") => "SweepRequest",
        ("POST", "/orders") => "OrdersRequest",
        ("GET", "/instruments") => "InstrumentsQuery",
        ("GET", "/quotes") => "QuotesQuery",
        ("GET", "/bars/{id}") => "BarsQuery",
        ("GET", "/depth/{id}") => "DepthQuery",
        ("GET", "/screener/scan") => "ScreenerQuery",
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
    // Only a GET reads its DTO from the query string; a POST's DTO is the body.
    if method == HttpMethod::Get.as_str() {
        if let Some(query) = request_type_for(method, path) {
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
                "content": {"application/json": {"schema": {"$ref": format!("{COMPONENTS_PREFIX}{body}")}}},
            }));
        }
    }

    let response = response_type_for(method, path)
        .map(|name| format!("{COMPONENTS_PREFIX}{name}"))
        .unwrap_or_else(|| format!("{COMPONENTS_PREFIX}Capabilities"));

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
            "content": {"application/json": {"schema": {"$ref": format!("{COMPONENTS_PREFIX}ResponseEnvelope")}}},
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
pub(crate) fn query_params(set: &SchemaSet, type_name: &str) -> Vec<Value> {
    // Read the normalized copy: a fragment embedded in the spec must carry the
    // spec's own $ref spelling, not the generator's internal one.
    let normalized = set.normalized(type_name, COMPONENTS_PREFIX);
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
pub(crate) fn path_params(path: &str) -> Vec<&str> {
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
