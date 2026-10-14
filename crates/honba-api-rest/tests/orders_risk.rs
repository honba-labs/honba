//! `POST /orders`, `DELETE /orders/{id}` and `POST /positions/close` through the real
//! router: the risk gate of ADR 0018 decision 7 (E2-S2 r5b) and the approval queue
//! (E5-S4, E11-S7).
//!
//! No network and no wall clock: the state is an in-memory dataset plus one instrument.

use axum::body::Body;
use axum::http::{Method, Request, StatusCode};
use axum::Router;
use honba_api_rest::{api_router_with, AppState};
use honba_entities::{Currency, Instrument, InstrumentKind};
use honba_feed::{ColumnarSliceBuilder, Dataset, DatasetReader};
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
    // An approved order answers with its ledger row and is recorded, so
    // `GET /orders` reads it back with the rest of the ledger.
    let app = api_router_with(state());
    let mut placed = order("X", "NSE", "buy", 25.0, Some(100.0));
    placed["client_order_id"] = json!("post-flow");
    let (status, body) = call(app.clone(), Method::POST, "/orders", Some(placed)).await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(body["data"]["order_id"], json!("post-flow"));
    assert_eq!(body["data"]["status"], json!("submitted"));

    let (status, body) = call(app, Method::GET, "/orders", None).await;
    assert_eq!(status, StatusCode::OK);
    let orders = body["data"]["orders"].as_array().unwrap();
    assert_eq!(orders.len(), 1);
    assert_eq!(orders[0]["order_id"], json!("post-flow"));
    assert_eq!(
        orders[0]["instrument_id"],
        json!({"symbol": "X", "exchange": "NSE"})
    );
    assert_eq!(orders[0]["side"], json!("buy"));
    assert_eq!(orders[0]["quantity"], json!(25.0));
    assert_eq!(orders[0]["status"], json!("submitted"));
}

#[tokio::test]
async fn a_repeated_idempotency_key_returns_the_first_row_and_adds_nothing() {
    let app = api_router_with(state());
    let mut placed = order("X", "NSE", "buy", 25.0, Some(100.0));
    placed["client_order_id"] = json!("dup-key");
    for _ in 0..2 {
        let (status, body) = call(app.clone(), Method::POST, "/orders", Some(placed.clone())).await;
        assert_eq!(status, StatusCode::OK);
        assert_eq!(body["data"]["order_id"], json!("dup-key"));
        assert_eq!(body["data"]["status"], json!("submitted"));
    }

    let (status, body) = call(app, Method::GET, "/orders", None).await;
    assert_eq!(status, StatusCode::OK);
    let orders = body["data"]["orders"].as_array().unwrap();
    assert_eq!(orders.len(), 1);
}

#[tokio::test]
async fn the_ledger_keeps_every_acknowledged_order_and_treats_unknown_filters_as_empty() {
    // One keyed order per idempotency key: ADR 0019 decision 3 deduplicates
    // on the client order id, so the two submits are two rows.
    let app = api_router_with(state());
    let mut n = 0;
    for (side, qty) in [("buy", 25.0), ("sell", 25.0)] {
        n += 1;
        let mut body = order("X", "NSE", side, qty, Some(100.0));
        body["client_order_id"] = json!(format!("key-{n}"));
        let (status, _) = call(app.clone(), Method::POST, "/orders", Some(body)).await;
        assert_eq!(status, StatusCode::OK);
    }

    let (status, body) = call(app.clone(), Method::GET, "/orders", None).await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(body["data"]["orders"].as_array().unwrap().len(), 2);

    // An unknown query key is `validation_invalid_request`, never silently ignored.
    let (status, body) = call(app, Method::GET, "/orders?charger=1", None).await;
    assert_eq!(status, StatusCode::UNPROCESSABLE_ENTITY);
    assert_eq!(body["error"]["code"], json!("validation_invalid_request"));
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

#[tokio::test]
async fn approval_queue_holds_placements() {
    // When approval queue is enabled, POST /orders enqueues the order
    let app = api_router_with(state().with_approval_queue(10));
    let (status, body) = call(
        app.clone(),
        Method::POST,
        "/orders",
        Some(order("X", "NSE", "buy", 25.0, Some(100.0))),
    )
    .await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(body["data"]["status"], json!("queued"));
    assert_eq!(body["data"]["queue_position"], json!(1));
    assert!(body["data"]["message"]
        .as_str()
        .unwrap()
        .contains("approval queue"));

    // GET /approval/queue returns the queued order
    let (status, body) = call(app.clone(), Method::GET, "/approval/queue", None).await;
    assert_eq!(status, StatusCode::OK);
    let queue = body["data"]["queue"].as_array().unwrap();
    assert_eq!(queue.len(), 1);
    assert_eq!(queue[0]["queue_id"], json!("rest"));
    assert_eq!(queue[0]["status"], json!("pending"));
    assert_eq!(queue[0]["instrument_id"], json!("X.NSE"));
    assert_eq!(queue[0]["side"], json!("buy"));
    assert_eq!(queue[0]["qty"], json!(25.0));
    assert_eq!(queue[0]["price"], json!(100.0));
}

#[tokio::test]
async fn approve_flows_through_risk_to_sim() {
    // Approve a queued order: it re-enters the risk stage, lands in the ledger
    // and answers with its row.
    let app = api_router_with(state().with_approval_queue(10));
    let mut placed = order("X", "NSE", "buy", 25.0, Some(100.0));
    placed["client_order_id"] = json!("approve-flow");
    let (status, body) = call(app.clone(), Method::POST, "/orders", Some(placed)).await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(body["data"]["status"], json!("queued"));

    // Approve it
    let (status, body) = call(app.clone(), Method::POST, "/approval/approve", None).await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(body["data"]["order_id"], json!("approve-flow"));
    assert_eq!(body["data"]["status"], json!("submitted"));

    // Queue is now empty
    let (status, body) = call(app, Method::GET, "/approval/queue", None).await;
    assert_eq!(status, StatusCode::OK);
    let queue = body["data"]["queue"].as_array().unwrap();
    assert_eq!(queue.len(), 0);
}

#[tokio::test]
async fn reject_removes_from_queue() {
    // Reject a queued order: it is removed and returns rejected status
    let app = api_router_with(state().with_approval_queue(10));
    let (status, body) = call(
        app.clone(),
        Method::POST,
        "/orders",
        Some(order("X", "NSE", "buy", 25.0, Some(100.0))),
    )
    .await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(body["data"]["status"], json!("queued"));

    // Reject it
    let (status, body) = call(app.clone(), Method::POST, "/approval/reject", None).await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(body["data"]["status"], json!("rejected"));
    assert_eq!(body["data"]["message"], json!("Order rejected by operator"));

    // Queue is now empty
    let (status, body) = call(app, Method::GET, "/approval/queue", None).await;
    assert_eq!(status, StatusCode::OK);
    let queue = body["data"]["queue"].as_array().unwrap();
    assert_eq!(queue.len(), 0);
}

#[tokio::test]
async fn cancel_and_close_bypass_queue_even_when_halted() {
    // With approval queue enabled and trading halted
    let app = api_router_with(
        state()
            .with_approval_queue(10)
            .with_trading_state(TradingState::Halted),
    );

    // A placement is queued (not refused by risk because queue catches it first)
    let (status, body) = call(
        app.clone(),
        Method::POST,
        "/orders",
        Some(order("X", "NSE", "buy", 25.0, Some(100.0))),
    )
    .await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(body["data"]["status"], json!("queued"));

    // Cancelling and closing bypass the queue entirely
    let (status, body) = call(app.clone(), Method::DELETE, "/orders/o1", None).await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(body["data"]["status"], json!("cancelled"));
    let (status, body) = call(app, Method::POST, "/positions/close", None).await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(body["data"]["status"], json!("closing"));
}

#[tokio::test]
async fn approval_queue_full_returns_429() {
    // Queue with size 1: second placement returns 429
    let app = api_router_with(state().with_approval_queue(1));
    let (status, body) = call(
        app.clone(),
        Method::POST,
        "/orders",
        Some(order("X", "NSE", "buy", 25.0, Some(100.0))),
    )
    .await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(body["data"]["status"], json!("queued"));

    // Second order should fail with queue full
    let (status, body) = call(
        app,
        Method::POST,
        "/orders",
        Some(order("X", "NSE", "buy", 25.0, Some(100.0))),
    )
    .await;
    assert_eq!(status, StatusCode::TOO_MANY_REQUESTS);
    assert_eq!(body["error"]["code"], json!("rate_limited"));
    assert_eq!(
        body["error"]["context"]["reason"],
        json!("approval_queue_full")
    );
}

/// Seeds the write ledger with one working buy of 25 on `X.NSE`, then runs the
/// app in the reduce-only state.
fn reducing_state_with_long_25() -> AppState {
    let state = state();
    state.orders.record(
        honba_messages::Order::new(
            honba_messages::OrderId::new("seed-1"),
            instrument_id(),
            honba_messages::OrderSide::Buy,
            honba_messages::OrderType::Market,
            25.0,
            None,
            honba_messages::TimeInForce::Day,
            UnixNanos::new(0),
            UnixNanos::new(0),
        )
        .with_status(honba_messages::OrderStatus::Submitted),
    );
    state.with_trading_state(TradingState::Reducing)
}

#[tokio::test]
async fn position_from_ledger_drives_reduce_only() {
    // The ledger shows a long 25: a sell of 25 reduces it and passes ...
    let app = api_router_with(reducing_state_with_long_25());
    let (status, body) = call(
        app.clone(),
        Method::POST,
        "/orders",
        Some(order("X", "NSE", "sell", 25.0, None)),
    )
    .await;
    assert_eq!(status, StatusCode::OK, "{body}");

    // ... a buy would add exposure and is refused with the derived position ...
    let (status, body) = call(
        api_router_with(reducing_state_with_long_25()),
        Method::POST,
        "/orders",
        Some(order("X", "NSE", "buy", 25.0, None)),
    )
    .await;
    assert_eq!(status, StatusCode::UNPROCESSABLE_ENTITY);
    assert_eq!(body["error"]["code"], json!("risk_reduce_only_violation"));

    // ... and a sell larger than the position would flip it, so it is refused.
    let (status, _) = call(
        api_router_with(reducing_state_with_long_25()),
        Method::POST,
        "/orders",
        Some(order("X", "NSE", "sell", 50.0, None)),
    )
    .await;
    assert_eq!(status, StatusCode::UNPROCESSABLE_ENTITY);
}

#[tokio::test]
async fn flat_ledger_refuses_everything_when_reducing() {
    let app = api_router_with(state().with_trading_state(TradingState::Reducing));
    let (status, _) = call(
        app,
        Method::POST,
        "/orders",
        Some(order("X", "NSE", "sell", 25.0, None)),
    )
    .await;
    assert_eq!(status, StatusCode::UNPROCESSABLE_ENTITY);
}
