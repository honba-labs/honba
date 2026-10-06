//! Routes whose behaviour is not built yet answer 501 with the standard
//! envelope (`not_implemented`), never a fake success.

use axum::body::Body;
use axum::http::{Request, StatusCode};
use serde_json::{json, Value};
use tower::util::ServiceExt;

async fn call(method: &str, uri: &str, body: Option<&str>) -> (StatusCode, Value) {
    let mut req = Request::builder().method(method).uri(uri);
    let body = match body {
        Some(text) => {
            req = req.header("content-type", "application/json");
            Body::from(text.to_owned())
        }
        None => Body::empty(),
    };
    let res = honba_api_rest::api_router()
        .oneshot(req.body(body).unwrap())
        .await
        .unwrap();
    let status = res.status();
    let bytes = axum::body::to_bytes(res.into_body(), usize::MAX)
        .await
        .unwrap();
    let parsed = serde_json::from_slice(&bytes)
        .unwrap_or_else(|_| panic!("{uri}: non-JSON body {:?}", String::from_utf8_lossy(&bytes)));
    (status, parsed)
}

#[tokio::test]
async fn unbuilt_routes_answer_501_not_implemented_envelopes() {
    let routes: [(&str, &str, Option<&str>); 11] = [
        ("POST", "/backtests", Some("{}")),
        ("GET", "/backtests/r1", None),
        ("GET", "/backtests/r1/journal", None),
        ("POST", "/sweeps", Some("{}")),
        ("GET", "/sweeps/j1", None),
        ("POST", "/orders", Some("{}")),
        ("GET", "/orders", None),
        ("DELETE", "/orders/o1", None),
        ("POST", "/positions/close", None),
        ("GET", "/screener/scan", None),
        ("GET", "/journals/j1", None),
    ];
    for (method, uri, body) in routes {
        let (status, parsed) = call(method, uri, body).await;
        assert_eq!(status, StatusCode::NOT_IMPLEMENTED, "{method} {uri}");
        assert_eq!(parsed["error"]["code"], json!("not_implemented"), "{uri}");
        assert!(parsed["data"].is_null(), "{uri}: {parsed}");
        assert!(parsed["api_version"].is_string(), "{uri}");
    }
}
