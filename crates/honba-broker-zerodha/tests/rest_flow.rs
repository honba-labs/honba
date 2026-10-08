//! Public-API flow: session -> place -> list -> modify -> cancel against a scripted transport.

use std::collections::VecDeque;
use std::sync::Mutex;

use async_trait::async_trait;
use honba_broker_zerodha::client::{KiteClient, KiteConfig, PlaceOrder};
use honba_broker_zerodha::mapping::{
    kite_tag, order_side_from_kite, order_status, parse_kite_timestamp, Product,
};
use honba_broker_zerodha::transport::{
    HttpRequest, HttpResponse, HttpTransport, Method, TransportError,
};
use honba_messages::{OrderId, OrderSide, OrderStatus, OrderType, TimeInForce};
use honba_ports::PortError;

struct Scripted {
    responses: Mutex<VecDeque<Result<HttpResponse, TransportError>>>,
    seen: Mutex<Vec<HttpRequest>>,
}

impl Scripted {
    fn new(bodies: &[(u16, &str)]) -> Self {
        let q = bodies
            .iter()
            .map(|(s, b)| {
                Ok(HttpResponse {
                    status: *s,
                    body: (*b).to_owned(),
                })
            })
            .collect();
        Self {
            responses: Mutex::new(q),
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

#[tokio::test]
async fn session_place_list_modify_cancel() {
    let script = Scripted::new(&[
        (
            200,
            r#"{"status":"success","data":{"access_token":"tok","user_id":"AB1234"}}"#,
        ),
        (200, r#"{"status":"success","data":{"order_id":"2201"}}"#),
        (
            200,
            r#"{"status":"success","data":[{"order_id":"2201","status":"OPEN","transaction_type":"BUY",
            "quantity":10,"filled_quantity":4,"order_timestamp":"2024-02-29 09:15:00"}]}"#,
        ),
        (200, r#"{"status":"success","data":{"order_id":"2201"}}"#),
        (200, r#"{"status":"success","data":{"order_id":"2201"}}"#),
        (
            403,
            r#"{"status":"error","message":"Token expired","error_type":"TokenException"}"#,
        ),
    ]);
    let mut client = KiteClient::new(KiteConfig::new("key"), script);

    // Before a session nothing is sent.
    assert!(matches!(
        client.orders().await,
        Err(PortError::Unavailable(_))
    ));

    assert_eq!(
        client.generate_session("rt", "sec").await.unwrap(),
        "AB1234"
    );

    let id = client
        .place_order(&PlaceOrder {
            variety: "regular".into(),
            tradingsymbol: "INFY".into(),
            exchange: "NSE".into(),
            side: OrderSide::Buy,
            order_type: OrderType::Limit,
            quantity: 10,
            price: Some(1500.0),
            trigger_price: None,
            validity: TimeInForce::Day,
            product: Product::Mis,
            tag: kite_tag(&OrderId::new("hb-order-1")),
        })
        .await
        .unwrap();
    assert_eq!(id, "2201");

    let orders = client.orders().await.unwrap();
    assert_eq!(orders[0].order_id, id);
    assert_eq!(order_status(&orders[0]), OrderStatus::PartiallyFilled);
    assert_eq!(
        order_side_from_kite(orders[0].transaction_type.as_deref().unwrap()),
        Some(OrderSide::Buy)
    );
    assert!(parse_kite_timestamp(orders[0].order_timestamp.as_deref().unwrap()).is_ok());

    client
        .modify_order("regular", &id, Some(6), Some(1499.5), None)
        .await
        .unwrap();
    client.cancel_order("regular", &id).await.unwrap();

    // Session expiry is a non-retryable rejection, not Unavailable.
    assert!(matches!(
        client.trades().await,
        Err(PortError::Rejected { ref code, .. }) if code == "TokenException"
    ));

    let seen = client.transport().seen.lock().unwrap().clone();
    let shape: Vec<(Method, &str)> = seen.iter().map(|r| (r.method, r.url.as_str())).collect();
    assert_eq!(
        shape,
        vec![
            (Method::Post, "https://api.kite.trade/session/token"),
            (Method::Post, "https://api.kite.trade/orders/regular"),
            (Method::Get, "https://api.kite.trade/orders"),
            (Method::Put, "https://api.kite.trade/orders/regular/2201"),
            (Method::Delete, "https://api.kite.trade/orders/regular/2201"),
            (Method::Get, "https://api.kite.trade/trades"),
        ]
    );
    // Every call after the session carries the new token.
    assert!(seen[1..].iter().all(|r| r
        .headers
        .iter()
        .any(|(k, v)| k == "Authorization" && v == "token key:tok")));
}
