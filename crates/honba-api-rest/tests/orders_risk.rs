//! `POST /orders`, `DELETE /orders/{id}` and `POST /positions/close` through the real
//! router: the risk gate of ADR 0018 decision 7 (E2-S2 r5b).
//!
//! No network and no wall clock: the state is an in-memory dataset plus one instrument.

use axum::body::Body;
use axum::http::{Method, Request, StatusCode};
use axum::Router;
use honba_api_rest::{api_router_with, AppState};
use honba_data::{ColumnarSliceBuilder, Dataset, DatasetReader};
use honba_entities::{Currency, Instrument, InstrumentKind};
use honba_messages::{Exchange, InstrumentId, TradingState, UnixNanos};
use serde_json::{json, Value};
use tower::util::ServiceExt;

fn instrument_id() -> InstrumentId {
    InstrumentId::new("X", Exchange::new("NSE"))
}

/// One instrument `X.NSE` (INR equity, lot 25, tick 0.05) with one bar closing at
/// 100; the bar store derives bid = ask = 100, so `reference_price` is 100.
fn state() -> AppState {
    let id = instrument_id();
    let spec = honba_api::parse_timeframe("1m").unwrap();
    let mut builder = ColumnarSliceBuilder::new(id.clone(), spec);
    builder
        .push(UnixNanos::new(1), 100.0, 100.0, 100.0, 100.0, 1.0)
        .unwrap();
    let dataset = Dataset::from_slices(vec![builder.finish().unwrap()]).unwrap();
    let instrument = Instrument::new(id, InstrumentKind::Equity, Currency::Inr, 25.0, 0.05);
    AppState::from_reader(DatasetReader::new(dataset, vec![instrument]))
}

async fn call(app: Router, method: Method, uri: &str, body: Option<Value>) -> (StatusCode, Value) {
    let mut builder = Request::builder().method(method).uri(uri);
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

fn order(symbol: &str, exchange: &str, side: &str, qty: f64, limit: Option<f64>) -> Value {
    let mut body = json!({
        "instrument_id": {"symbol": symbol, "exchange": exchange},
        "side": side,
        "qty": qty,
    });
    if let Some(px) = limit {
        body["order_type"] = json!("limit");
        body["price"] = json!(px);
    }
    body
}

#[tokio::test]
async fn order_refused_by_risk() {
    // An instrument the master does not know is refused and named.
    let (status, body) = call(
        api_router_with(state()),
        Method::POST,
        "/orders",
        Some(order("Z", "NSE", "buy", 25.0, None)),
    )
    .await;
    assert_eq!(status, StatusCode::UNPROCESSABLE_ENTITY);
    assert_eq!(body["error"]["code"], json!("risk_instrument_unknown"));
    assert_eq!(
        body["error"]["context"]["rule"],
        json!("instrument_unknown")
    );
    assert!(body["data"].is_null());

    // A quantity that is not a lot multiple is a typed shape refusal with numbers.
    let (status, body) = call(
        api_router_with(state()),
        Method::POST,
        "/orders",
        Some(order("X", "NSE", "buy", 30.0, Some(100.0))),
    )
    .await;
    assert_eq!(status, StatusCode::UNPROCESSABLE_ENTITY);
    assert_eq!(body["error"]["code"], json!("risk_lot_multiple_violation"));
    assert_eq!(body["error"]["context"]["rule"], json!("lot_multiple"));
    assert_eq!(body["error"]["context"]["lot"], json!(25.0));

    // A price off the tick grid is refused with the field and tick.
    let (status, body) = call(
        api_router_with(state()),
        Method::POST,
        "/orders",
        Some(order("X", "NSE", "buy", 25.0, Some(100.03))),
    )
    .await;
    assert_eq!(status, StatusCode::UNPROCESSABLE_ENTITY);
    assert_eq!(body["error"]["code"], json!("risk_tick_size_violation"));
    assert_eq!(body["error"]["context"]["rule"], json!("tick_size"));
    assert_eq!(body["error"]["context"]["field"], json!("price"));
    assert_eq!(body["error"]["context"]["reason"], json!("off_tick"));
}

#[tokio::test]
async fn post_order_flows_to_sim_only() {
    // An approved order is acknowledged; the execution gateway is not wired yet, so
    // nothing is persisted and the (unbuilt) order ledger still answers 501.
    let app = api_router_with(state());
    let (status, body) = call(
        app.clone(),
        Method::POST,
        "/orders",
        Some(order("X", "NSE", "buy", 25.0, Some(100.0))),
    )
    .await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(body["data"]["status"], json!("acknowledged"));

    let (status, body) = call(app, Method::GET, "/orders", None).await;
    assert_eq!(status, StatusCode::NOT_IMPLEMENTED);
    assert_eq!(body["error"]["code"], json!("not_implemented"));
}

#[tokio::test]
async fn halted_refusal_precedes_instrument_lookup() {
    // Regression (ADR 0018 decision 4: the state rules are 1-2, unknown instrument
    // is 3). A halted engine reports why it is closed even for an unknown symbol.
    let app = api_router_with(state().with_trading_state(TradingState::Halted));
    let (status, body) = call(
        app,
        Method::POST,
        "/orders",
        Some(order("Z", "NSE", "buy", 25.0, None)),
    )
    .await;
    assert_eq!(status, StatusCode::UNPROCESSABLE_ENTITY);
    assert_eq!(body["error"]["code"], json!("risk_trading_halted"));
}

#[tokio::test]
async fn cancel_and_close_allowed_when_halted() {
    let app = api_router_with(state().with_trading_state(TradingState::Halted));

    // A placement is refused while halted.
    let (status, body) = call(
        app.clone(),
        Method::POST,
        "/orders",
        Some(order("X", "NSE", "buy", 25.0, Some(100.0))),
    )
    .await;
    assert_eq!(status, StatusCode::UNPROCESSABLE_ENTITY);
    assert_eq!(body["error"]["code"], json!("risk_trading_halted"));

    // Cancelling and closing cannot add exposure, so both are allowed while halted.
    let (status, body) = call(app.clone(), Method::DELETE, "/orders/o1", None).await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(body["data"]["status"], json!("cancelled"));
    let (status, body) = call(app, Method::POST, "/positions/close", None).await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(body["data"]["status"], json!("closing"));
}
