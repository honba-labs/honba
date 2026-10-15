//! An async `ExecutionGateway` behind `/orders` through `GatewayVenue` (ADR 0030): the routes
//! return at once, and the gateway's fills, refusals and cancels reach the order ledger when the
//! venue drain runs (`AppState::spawn_venue_drain`, no bars needed).

use std::time::Duration;

use axum::body::Body;
use axum::http::{Method, Request, StatusCode};
use honba_api_rest::{api_router_with, AppState, GatewayVenue};
use honba_entities::{Currency, Instrument, InstrumentKind};
use honba_feed::{ColumnarSliceBuilder, Dataset, DatasetReader};
use honba_messages::UnixNanos;
use honba_ports::PortError;
use honba_testing::ScriptedGateway;
use serde_json::{json, Value};
use tower::util::ServiceExt;

fn state(gw: &ScriptedGateway) -> AppState {
    let id = honba_messages::InstrumentId::new("X", honba_messages::Exchange::new("NSE"));
    let spec = honba_api::parse_timeframe("1m").unwrap();
    let mut builder = ColumnarSliceBuilder::new(id.clone(), spec);
    builder
        .push(UnixNanos::new(1), 100.0, 100.0, 100.0, 100.0, 1.0)
        .unwrap();
    let dataset = Dataset::from_slices(vec![builder.finish().unwrap()]).unwrap();
    let instrument = Instrument::new(id, InstrumentKind::Equity, Currency::Inr, 25.0, 0.05);
    AppState::from_reader(DatasetReader::new(dataset, vec![instrument])).with_execution_gateway(
        GatewayVenue::spawn_with_poll(gw.clone(), Duration::from_millis(5)),
    )
}

async fn call(st: &AppState, method: Method, uri: &str, body: Option<Value>) -> StatusCode {
    let mut builder = Request::builder().method(method).uri(uri);
    let body = match body {
        Some(value) => {
            builder = builder.header("content-type", "application/json");
            Body::from(value.to_string())
        }
        None => Body::empty(),
    };
    api_router_with(st.clone())
        .oneshot(builder.body(body).unwrap())
        .await
        .unwrap()
        .status()
}

fn buy(qty: f64, id: &str) -> Value {
    json!({"instrument_id": {"symbol": "X", "exchange": "NSE"},
           "side": "buy", "qty": qty, "client_order_id": id})
}

fn status_of(st: &AppState, id: &str) -> String {
    serde_json::to_value(st.orders.get(id).unwrap()).unwrap()["status"]
        .as_str()
        .unwrap()
        .to_owned()
}

async fn until(st: &AppState, id: &str, want: &str) {
    for _ in 0..400 {
        if status_of(st, id) == want {
            return;
        }
        tokio::time::sleep(Duration::from_millis(5)).await;
    }
    panic!("order {id} is {}, wanted {want}", status_of(st, id));
}

#[tokio::test]
async fn fills_reach_the_ledger_through_the_venue_drain() {
    let gw = ScriptedGateway::new();
    let st = state(&gw);
    let drain = st.spawn_venue_drain(Duration::from_millis(5));

    assert_eq!(
        call(&st, Method::POST, "/orders", Some(buy(25.0, "o1"))).await,
        StatusCode::OK
    );
    assert_eq!(status_of(&st, "o1"), "submitted");
    for _ in 0..400 {
        if !gw.venue_ids().is_empty() {
            break;
        }
        tokio::time::sleep(Duration::from_millis(5)).await;
    }
    let vid = gw.venue_ids()[0].clone();
    gw.script_fill(&vid, 10.0, 100.0);
    until(&st, "o1", "partially_filled").await;
    gw.script_fill(&vid, 15.0, 100.0);
    until(&st, "o1", "filled").await;
    drain.abort();
}

#[tokio::test]
async fn gateway_refusal_rejects_the_ledger_row() {
    let gw = ScriptedGateway::new();
    let st = state(&gw);
    let drain = st.spawn_venue_drain(Duration::from_millis(5));
    gw.fail_next_submit(PortError::Rejected {
        code: "RMS".into(),
        message: "margin".into(),
    });
    call(&st, Method::POST, "/orders", Some(buy(25.0, "o2"))).await;
    until(&st, "o2", "rejected").await;
    drain.abort();
}

#[tokio::test]
async fn delete_order_cancels_on_the_gateway_by_venue_id() {
    let gw = ScriptedGateway::new();
    let st = state(&gw);
    let drain = st.spawn_venue_drain(Duration::from_millis(5));
    call(&st, Method::POST, "/orders", Some(buy(25.0, "o3"))).await;
    for _ in 0..400 {
        if !gw.venue_ids().is_empty() {
            break;
        }
        tokio::time::sleep(Duration::from_millis(5)).await;
    }
    call(&st, Method::DELETE, "/orders/o3", None).await;
    assert_eq!(status_of(&st, "o3"), "cancelled");
    for _ in 0..400 {
        if !gw.cancellations().is_empty() {
            break;
        }
        tokio::time::sleep(Duration::from_millis(5)).await;
    }
    assert_eq!(gw.cancellations(), gw.venue_ids());
    drain.abort();
}
