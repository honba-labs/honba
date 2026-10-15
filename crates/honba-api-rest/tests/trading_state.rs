//! `GET` / `PUT /trading-state`: the operator route that changes the trading state at runtime
//! (ADR 0018 decision 7). The state is shared by every clone of the `AppState`, so the order
//! gate and `POST /positions/close` see a change on the very next request.

use axum::body::Body;
use axum::http::{Method, Request, StatusCode};
use axum::Router;
use honba_api_rest::{api_router_with, api_router_with_config, ApiConfig, AppState};
use honba_entities::{Currency, Instrument, InstrumentKind};
use honba_feed::{ColumnarSliceBuilder, Dataset, DatasetReader};
use honba_messages::{Exchange, InstrumentId, TradingState, UnixNanos};
use serde_json::{json, Value};
use tower::util::ServiceExt;

fn iid() -> InstrumentId {
    InstrumentId::new("X", Exchange::new("NSE"))
}

fn state() -> AppState {
    let id = iid();
    let spec = honba_api::parse_timeframe("1m").unwrap();
    let mut builder = ColumnarSliceBuilder::new(id.clone(), spec);
    builder
        .push(UnixNanos::new(1), 100.0, 100.0, 100.0, 100.0, 1.0)
        .unwrap();
    let dataset = Dataset::from_slices(vec![builder.finish().unwrap()]).unwrap();
    let instrument = Instrument::new(id, InstrumentKind::Equity, Currency::Inr, 25.0, 0.05);
    AppState::from_reader(DatasetReader::new(dataset, vec![instrument]))
}

async fn send(
    app: Router,
    method: Method,
    uri: &str,
    body: Option<Value>,
    token: Option<&str>,
) -> (StatusCode, Value) {
    let mut builder = Request::builder().method(method).uri(uri);
    if let Some(t) = token {
        builder = builder.header("authorization", format!("Bearer {t}"));
    }
    let body = match body {
        Some(value) => {
            builder = builder.header("content-type", "application/json");
            Body::from(value.to_string())
        }
        None => Body::empty(),
    };
    let res = app.oneshot(builder.body(body).unwrap()).await.unwrap();
    let status = res.status();
    let bytes = axum::body::to_bytes(res.into_body(), usize::MAX)
        .await
        .unwrap();
    let parsed = serde_json::from_slice(&bytes)
        .unwrap_or_else(|_| panic!("{uri}: non-JSON body {:?}", String::from_utf8_lossy(&bytes)));
    (status, parsed)
}

async fn call(app: &Router, method: Method, uri: &str, body: Option<Value>) -> (StatusCode, Value) {
    send(app.clone(), method, uri, body, None).await
}

async fn put_state(app: &Router, body: Value) -> (StatusCode, Value) {
    call(app, Method::PUT, "/trading-state", Some(body)).await
}

/// A market order; `key` is its idempotency key (a repeat of a key is a no-op duplicate).
fn order(side: &str, key: &str) -> Value {
    json!({
        "instrument_id": {"symbol": "X", "exchange": "NSE"},
        "side": side,
        "qty": 25.0,
        "client_order_id": key,
    })
}

#[tokio::test]
async fn get_returns_the_configured_state() {
    let app = api_router_with(state().with_trading_state(TradingState::Reducing));
    let (status, body) = call(&app, Method::GET, "/trading-state", None).await;
    assert_eq!(status, StatusCode::OK, "{body}");
    assert_eq!(body["data"]["state"], json!("reducing"));
}

#[tokio::test]
async fn get_defaults_to_active() {
    let app = api_router_with(state());
    let (_, body) = call(&app, Method::GET, "/trading-state", None).await;
    assert_eq!(body["data"]["state"], json!("active"));
}

#[tokio::test]
async fn put_changes_the_state_and_reports_the_previous_one() {
    let app = api_router_with(state());
    let (status, body) = put_state(&app, json!({"state": "halted", "reason": "drill"})).await;
    assert_eq!(status, StatusCode::OK, "{body}");
    assert_eq!(
        body["data"],
        json!({"state": "halted", "previous": "active"})
    );

    let (_, body) = call(&app, Method::GET, "/trading-state", None).await;
    assert_eq!(body["data"]["state"], json!("halted"));
}

#[tokio::test]
async fn put_is_idempotent() {
    let app = api_router_with(state());
    put_state(&app, json!({"state": "reducing"})).await;
    let (status, body) = put_state(&app, json!({"state": "reducing"})).await;
    assert_eq!(status, StatusCode::OK, "{body}");
    assert_eq!(
        body["data"],
        json!({"state": "reducing", "previous": "reducing"})
    );
}

#[tokio::test]
async fn every_transition_is_allowed() {
    let app = api_router_with(state());
    let all = ["active", "reducing", "halted"];
    for from in all {
        for to in all {
            put_state(&app, json!({"state": from})).await;
            let (status, body) = put_state(&app, json!({"state": to})).await;
            assert_eq!(status, StatusCode::OK, "{from} -> {to}: {body}");
            assert_eq!(body["data"], json!({"state": to, "previous": from}));
        }
    }
}

#[tokio::test]
async fn invalid_state_is_422() {
    let app = api_router_with(state());
    let (status, body) = put_state(&app, json!({"state": "paused"})).await;
    assert_eq!(status, StatusCode::UNPROCESSABLE_ENTITY, "{body}");
    assert_eq!(body["error"]["code"], json!("validation_invalid_request"));
    let (status, _) = put_state(&app, json!({})).await;
    assert_eq!(status, StatusCode::UNPROCESSABLE_ENTITY);
    let (status, _) = put_state(&app, json!({"state": "halted", "bogus": 1})).await;
    assert_eq!(status, StatusCode::UNPROCESSABLE_ENTITY);
    // Nothing changed.
    let (_, body) = call(&app, Method::GET, "/trading-state", None).await;
    assert_eq!(body["data"]["state"], json!("active"));
}

#[tokio::test]
async fn the_order_gate_follows_the_runtime_state() {
    let app = api_router_with(state());

    let (status, body) = call(&app, Method::POST, "/orders", Some(order("buy", "o1"))).await;
    assert_eq!(status, StatusCode::OK, "{body}");

    // Reducing: an opening order (flat ledger -> everything opens) is refused.
    put_state(&app, json!({"state": "reducing"})).await;
    let (status, body) = call(&app, Method::POST, "/orders", Some(order("buy", "o2"))).await;
    assert_eq!(status, StatusCode::UNPROCESSABLE_ENTITY, "{body}");
    assert_eq!(body["error"]["code"], json!("risk_reduce_only_violation"));

    // Halted: refused.
    put_state(&app, json!({"state": "halted"})).await;
    let (status, body) = call(&app, Method::POST, "/orders", Some(order("buy", "o3"))).await;
    assert_eq!(status, StatusCode::UNPROCESSABLE_ENTITY, "{body}");
    assert_eq!(body["error"]["code"], json!("risk_trading_halted"));

    // Back to active: accepted again.
    put_state(&app, json!({"state": "active"})).await;
    let (status, body) = call(&app, Method::POST, "/orders", Some(order("sell", "o4"))).await;
    assert_eq!(status, StatusCode::OK, "{body}");
}

#[tokio::test]
async fn close_is_allowed_when_reducing() {
    let st = state();
    let app = api_router_with(st.clone());
    let (status, _) = call(&app, Method::POST, "/orders", Some(order("buy", "o5"))).await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(st.orders.position(&iid()), 25.0);

    put_state(&app, json!({"state": "reducing"})).await;
    let (status, body) = call(&app, Method::POST, "/positions/close", None).await;
    assert_eq!(status, StatusCode::OK, "{body}");
    assert_eq!(st.orders.position(&iid()), 0.0);
}

#[tokio::test]
async fn clones_of_the_state_share_the_trading_state() {
    let st = state();
    let app = api_router_with(st.clone());
    put_state(&app, json!({"state": "halted"})).await;
    assert_eq!(st.trading_state(), TradingState::Halted);
}

// Both routes are operator routes: scope write:orders, the strictest scope ADR 0023 defines.

// sha256("test")
const TEST_HASH: &str = "9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08";

fn authed(scopes: &str) -> (Router, tempfile::TempDir) {
    let dir = tempfile::tempdir().unwrap();
    let path = dir.path().join("tokens.toml");
    std::fs::write(
        &path,
        format!(
            "[[tokens]]\ntoken_id = \"t1\"\ntoken_hash = \"sha256:{TEST_HASH}\"\n\
             scopes = [{scopes}]\nlabel = \"t\"\ncreated_at = \"2026-01-01T00:00:00Z\"\n"
        ),
    )
    .unwrap();
    let mut config = ApiConfig::default();
    config.auth.tokens_file = Some(path);
    (api_router_with_config(state(), &config), dir)
}

#[tokio::test]
async fn auth_requires_a_token() {
    let (app, _dir) = authed("\"write:orders\"");
    let body = Some(json!({"state": "halted"}));
    let (s, v) = send(app.clone(), Method::PUT, "/trading-state", body, None).await;
    assert_eq!(s, StatusCode::UNAUTHORIZED, "{v}");
    let (s, _) = send(app, Method::GET, "/trading-state", None, None).await;
    assert_eq!(s, StatusCode::UNAUTHORIZED);
}

#[tokio::test]
async fn auth_refuses_insufficient_scope() {
    let (app, _dir) = authed("\"read:market\", \"research\"");
    let body = Some(json!({"state": "halted"}));
    let (s, v) = send(
        app.clone(),
        Method::PUT,
        "/trading-state",
        body,
        Some("test"),
    )
    .await;
    assert_eq!(s, StatusCode::FORBIDDEN, "{v}");
    assert_eq!(v["error"]["code"], json!("forbidden"));
    let (s, _) = send(app, Method::GET, "/trading-state", None, Some("test")).await;
    assert_eq!(s, StatusCode::FORBIDDEN);
}

#[tokio::test]
async fn auth_accepts_the_operator_scope() {
    let (app, _dir) = authed("\"operator\"");
    let body = Some(json!({"state": "halted"}));
    let (s, v) = send(
        app.clone(),
        Method::PUT,
        "/trading-state",
        body,
        Some("test"),
    )
    .await;
    assert_eq!(s, StatusCode::OK, "{v}");
    assert_eq!(v["data"], json!({"state": "halted", "previous": "active"}));
    let (s, v) = send(app, Method::GET, "/trading-state", None, Some("test")).await;
    assert_eq!(s, StatusCode::OK);
    assert_eq!(v["data"]["state"], json!("halted"));
}

#[tokio::test]
async fn write_orders_alone_can_no_longer_change_the_state() {
    let (app, _dir) = authed("\"write:orders\", \"read:account\"");
    let body = Some(json!({"state": "halted"}));
    let (s, v) = send(
        app.clone(),
        Method::PUT,
        "/trading-state",
        body,
        Some("test"),
    )
    .await;
    assert_eq!(s, StatusCode::FORBIDDEN, "{v}");
    assert_eq!(v["error"]["code"], json!("forbidden"));
    let (s, _) = send(app, Method::GET, "/trading-state", None, Some("test")).await;
    assert_eq!(s, StatusCode::FORBIDDEN);
}

#[tokio::test]
async fn operator_scope_does_not_imply_write_orders() {
    let (app, _dir) = authed("\"operator\"");
    let (s, v) = send(
        app,
        Method::POST,
        "/orders",
        Some(order("buy", "k1")),
        Some("test"),
    )
    .await;
    assert_eq!(s, StatusCode::FORBIDDEN, "{v}");
    assert_eq!(v["error"]["code"], json!("forbidden"));
}
