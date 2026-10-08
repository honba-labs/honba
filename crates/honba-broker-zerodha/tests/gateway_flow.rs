//! Public-API flow for `ZerodhaGateway`: submit -> fills -> modify/cancel, rejection mapping.
//!
//! Shared contract: the assertions of `crates/honba-ports/tests/ports_contract.rs`
//! (`gateway_submits_cancels_modifies_and_fills_once` and
//! `all_six_ports_fit_in_a_registry_struct`) are replicated in
//! `contract_submit_cancel_modify_fill_once` and `contract_fits_in_boxed_port`:
//! - submitting two orders returns two ids; cancel and modify of accepted ids succeed (modify
//!   with `(qty, Some(price))` is supported, so `Unsupported` is not expected);
//! - `next_fill` yields the fill with the right order id, quantity and price, then `None`
//!   ("fills exhausted");
//! - the gateway is usable as `Box<dyn ExecutionGateway>`.
//!
//! The stub there invents ids (`STUB-n`); the port docs say the id returned is the one the order
//! was accepted under, and this gateway returns the caller's `OrderId` unchanged.

use std::collections::VecDeque;
use std::sync::Mutex;

use async_trait::async_trait;
use honba_broker_zerodha::client::{KiteClient, KiteConfig};
use honba_broker_zerodha::mapping::Product;
use honba_broker_zerodha::transport::{
    HttpRequest, HttpResponse, HttpTransport, Method, TransportError,
};
use honba_broker_zerodha::ZerodhaGateway;
use honba_entities::Currency;
use honba_messages::{
    Exchange, InstrumentId, Order, OrderId, OrderSide, OrderType, TimeInForce, UnixNanos,
};
use honba_ports::{ExecutionGateway, PortError};

struct Scripted {
    responses: Mutex<VecDeque<Result<HttpResponse, TransportError>>>,
    seen: Mutex<Vec<HttpRequest>>,
}

impl Scripted {
    fn new(bodies: &[(u16, String)]) -> Self {
        Self {
            responses: Mutex::new(
                bodies
                    .iter()
                    .map(|(s, b)| {
                        Ok(HttpResponse {
                            status: *s,
                            body: b.clone(),
                        })
                    })
                    .collect(),
            ),
            seen: Mutex::new(Vec::new()),
        }
    }
}

#[async_trait]
impl HttpTransport for Scripted {
    async fn send(&self, req: HttpRequest) -> Result<HttpResponse, TransportError> {
        self.seen.lock().unwrap().push(req);
        self.responses
            .lock()
            .unwrap()
            .pop_front()
            .expect("script exhausted")
    }
}

fn gateway(script: &[(u16, String)]) -> ZerodhaGateway<Scripted> {
    let mut cfg = KiteConfig::new("key");
    cfg.access_token = Some("tok".into());
    ZerodhaGateway::new(
        KiteClient::new(cfg, Scripted::new(script)),
        Product::Cnc,
        Box::new(|| UnixNanos::from_u64(7)),
    )
}

fn placed(id: &str) -> (u16, String) {
    (
        200,
        format!(r#"{{"status":"success","data":{{"order_id":"{id}"}}}}"#),
    )
}

fn trades(rows: &[(&str, &str, u64, f64)]) -> (u16, String) {
    let items: Vec<String> = rows
        .iter()
        .map(|(t, o, q, p)| {
            format!(
                r#"{{"trade_id":"{t}","order_id":"{o}","tradingsymbol":"RELIANCE","exchange":"NSE","transaction_type":"BUY","quantity":{q},"average_price":{p},"fill_timestamp":"2024-03-01 10:00:00"}}"#
            )
        })
        .collect();
    (
        200,
        format!(r#"{{"status":"success","data":[{}]}}"#, items.join(",")),
    )
}

fn buy_order(id: &str) -> Order {
    Order::new(
        OrderId::new(id),
        InstrumentId::new("RELIANCE", Exchange::new("NSE")),
        OrderSide::Buy,
        OrderType::Limit,
        10.0,
        Some(100.0),
        TimeInForce::Day,
        UnixNanos::from_u64(1),
        UnixNanos::from_u64(1),
    )
}

#[tokio::test]
async fn contract_submit_cancel_modify_fill_once() {
    let mut gw = gateway(&[
        placed("A1"),
        placed("A2"),
        placed("A1"),
        placed("A2"),
        trades(&[("T1", "A1", 10, 100.0)]),
        trades(&[("T1", "A1", 10, 100.0)]),
    ]);

    let first = gw.submit_order(buy_order("O-1")).await.unwrap();
    let second = gw.submit_order(buy_order("O-2")).await.unwrap();
    gw.cancel_order(first.clone()).await.unwrap();
    gw.modify_order(second.clone(), 5.0, Some(101.5))
        .await
        .unwrap();

    assert_eq!(first, OrderId::new("O-1"));
    assert_eq!(second, OrderId::new("O-2"));

    let fill = gw.next_fill().await.unwrap().expect("one fill");
    assert_eq!(fill.order_id(), &first);
    assert_eq!(fill.quantity(), 10.0);
    assert_eq!(fill.price(), 100.0);
    assert_eq!(fill.costs().currency(), Currency::Inr);
    assert!(gw.next_fill().await.unwrap().is_none(), "fills exhausted");
}

#[tokio::test]
async fn contract_fits_in_boxed_port() {
    let mut boxed: Box<dyn ExecutionGateway> = Box::new(gateway(&[placed("A1")]));
    assert_eq!(
        boxed.submit_order(buy_order("O-1")).await.unwrap(),
        OrderId::new("O-1")
    );
}

#[tokio::test]
async fn submit_partial_fills_then_cancel_flow() {
    let mut gw = gateway(&[
        placed("A1"),
        trades(&[("T1", "A1", 4, 100.0), ("T9", "OTHER", 1, 1.0)]),
        trades(&[
            ("T1", "A1", 4, 100.0),
            ("T2", "A1", 6, 100.5),
            ("T9", "OTHER", 1, 1.0),
        ]),
        (
            200,
            r#"{"status":"success","data":{"order_id":"A1"}}"#.to_owned(),
        ),
    ]);
    let id = gw.submit_order(buy_order("O-1")).await.unwrap();

    let f1 = gw.next_fill().await.unwrap().unwrap();
    assert_eq!(f1.quantity(), 4.0);
    let f2 = gw.next_fill().await.unwrap().unwrap();
    assert_eq!(f2.quantity(), 6.0);
    assert_eq!(f2.price(), 100.5);
    assert_eq!(f2.ts_init(), UnixNanos::from_u64(7));
    gw.cancel_order(id).await.unwrap();

    let seen = gw.client().transport().seen.lock().unwrap();
    let kinds: Vec<(Method, &str)> = seen
        .iter()
        .map(|r| (r.method, r.url.rsplit('/').next().unwrap()))
        .collect();
    assert_eq!(
        kinds,
        vec![
            (Method::Post, "regular"),
            (Method::Get, "trades"),
            (Method::Get, "trades"),
            (Method::Delete, "A1"),
        ]
    );
}

#[tokio::test]
async fn rejections_map_to_port_rejected() {
    let reject = |msg: &str| {
        (
            400,
            format!(r#"{{"status":"error","message":"{msg}","error_type":"OrderException"}}"#),
        )
    };
    let mut gw = gateway(&[
        reject("Insufficient funds"),
        placed("A1"),
        reject("Order already complete"),
        reject("Cannot modify"),
    ]);
    let err = gw.submit_order(buy_order("O-1")).await.unwrap_err();
    assert!(
        matches!(&err, PortError::Rejected { code, .. } if code == "OrderException"),
        "{err:?}"
    );
    assert!(!err.is_retryable());

    gw.submit_order(buy_order("O-1")).await.unwrap();
    assert!(matches!(
        gw.cancel_order(OrderId::new("O-1")).await,
        Err(PortError::Rejected { .. })
    ));
    assert!(matches!(
        gw.modify_order(OrderId::new("O-1"), 5.0, None).await,
        Err(PortError::Rejected { .. })
    ));
}

#[tokio::test]
async fn invalid_requests_never_reach_the_network() {
    let mut gw = gateway(&[]);
    let stop = Order::new(
        OrderId::new("S"),
        InstrumentId::new("RELIANCE", Exchange::new("NSE")),
        OrderSide::Buy,
        OrderType::StopMarket,
        1.0,
        None,
        TimeInForce::Day,
        UnixNanos::from_u64(1),
        UnixNanos::from_u64(1),
    );
    assert!(matches!(
        gw.submit_order(stop).await,
        Err(PortError::InvalidRequest(_))
    ));
    assert!(matches!(
        gw.cancel_order(OrderId::new("ghost")).await,
        Err(PortError::InvalidRequest(_))
    ));
    assert!(gw.client().transport().seen.lock().unwrap().is_empty());
}
