//! `GET /capabilities` is derived from the endpoint registry and agrees with the router.

use axum::body::Body;
use axum::http::{Request, StatusCode};
use honba_api::ENDPOINTS;
use honba_api_rest::{api_router, NOT_IMPLEMENTED_ENDPOINTS};
use serde_json::Value;
use tower::util::ServiceExt;

async fn call(method: &str, uri: &str) -> (StatusCode, Value) {
    let res = api_router()
        .oneshot(
            Request::builder()
                .method(method)
                .uri(uri)
                .header("content-type", "application/json")
                .body(Body::from("{}"))
                .unwrap(),
        )
        .await
        .unwrap();
    let status = res.status();
    let bytes = axum::body::to_bytes(res.into_body(), usize::MAX)
        .await
        .unwrap();
    (
        status,
        serde_json::from_slice(&bytes).unwrap_or(Value::Null),
    )
}

fn strings(value: &Value) -> Vec<String> {
    value
        .as_array()
        .unwrap()
        .iter()
        .map(|v| v.as_str().unwrap().to_owned())
        .collect()
}

#[tokio::test]
async fn capabilities_list_every_registry_endpoint_in_order() {
    let (status, body) = call("GET", "/capabilities").await;
    assert_eq!(status, StatusCode::OK);
    let manifest = &body["data"]["capabilities"];
    let want: Vec<String> = ENDPOINTS.iter().map(|(m, p)| format!("{m} {p}")).collect();
    assert_eq!(strings(&manifest["endpoints"]), want);
}

#[tokio::test]
async fn the_not_implemented_flags_match_what_the_router_answers() {
    let (_, body) = call("GET", "/capabilities").await;
    let flagged = strings(&body["data"]["capabilities"]["not_implemented"]);
    let mut probed = Vec::new();
    for (method, path) in ENDPOINTS {
        let uri = path.replace("{id}", "X.NSE");
        let (status, _) = call(method, &uri).await;
        assert_ne!(status, StatusCode::METHOD_NOT_ALLOWED, "{method} {path}");
        if status == StatusCode::NOT_IMPLEMENTED {
            probed.push(format!("{method} {path}"));
        }
    }
    assert_eq!(flagged, probed);
    let constant: Vec<String> = NOT_IMPLEMENTED_ENDPOINTS
        .iter()
        .map(|(m, p)| format!("{m} {p}"))
        .collect();
    assert_eq!(flagged, constant);
}
