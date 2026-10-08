use honba_entities::Currency;
use honba_messages::{
    Exchange, InstrumentId, Order, OrderId, OrderSide, OrderType, TimeInForce, UnixNanos,
};
use honba_ports::{ExecutionGateway, PortError};

use super::FakeTransport;
use crate::client::{KiteClient, KiteConfig};
use crate::gateway::ZerodhaGateway;
use crate::mapping::{parse_kite_timestamp, Product};
use crate::transport::{Method, TransportError};

const NOW: u64 = 42;

fn gateway(
    responses: Vec<Result<crate::transport::HttpResponse, TransportError>>,
) -> ZerodhaGateway<FakeTransport> {
    let mut cfg = KiteConfig::new("k");
    cfg.access_token = Some("t".into());
    let client = KiteClient::new(cfg, FakeTransport::new(responses));
    ZerodhaGateway::new(client, Product::Mis, Box::new(|| UnixNanos::from_u64(NOW)))
}

fn order(id: &str, ty: OrderType, price: Option<f64>, trigger: Option<f64>) -> Order {
    let o = Order::new(
        OrderId::new(id),
        InstrumentId::new("INFY", Exchange::new("NSE")),
        OrderSide::Buy,
        ty,
        10.0,
        price,
        TimeInForce::Day,
        UnixNanos::from_u64(1),
        UnixNanos::from_u64(1),
    );
    match trigger {
        Some(t) => o.with_trigger_price(t),
        None => o,
    }
}

fn placed(venue: &str) -> Result<crate::transport::HttpResponse, TransportError> {
    FakeTransport::ok(
        200,
        &format!(r#"{{"status":"success","data":{{"order_id":"{venue}"}}}}"#),
    )
}

fn trades(
    rows: &[(&str, &str, u64, f64)],
) -> Result<crate::transport::HttpResponse, TransportError> {
    let items: Vec<String> = rows
        .iter()
        .map(|(tid, oid, q, p)| {
            format!(
                r#"{{"trade_id":"{tid}","order_id":"{oid}","tradingsymbol":"INFY","exchange":"NSE","transaction_type":"BUY","quantity":{q},"average_price":{p},"fill_timestamp":"2024-01-02 09:15:30"}}"#
            )
        })
        .collect();
    FakeTransport::ok(
        200,
        &format!(r#"{{"status":"success","data":[{}]}}"#, items.join(",")),
    )
}

fn form<'a>(req: &'a crate::transport::HttpRequest, k: &str) -> Option<&'a str> {
    req.form
        .iter()
        .find(|(n, _)| n == k)
        .map(|(_, v)| v.as_str())
}

#[tokio::test]
async fn submit_maps_limit_order_and_returns_caller_id() {
    let mut gw = gateway(vec![placed("2201")]);
    let id = gw
        .submit_order(order("O-1", OrderType::Limit, Some(1500.5), None))
        .await
        .unwrap();
    assert_eq!(id, OrderId::new("O-1"));
    let reqs = gw.client().transport().requests();
    assert_eq!(reqs.len(), 1);
    assert_eq!(reqs[0].method, Method::Post);
    assert!(reqs[0].url.ends_with("/orders/regular"));
    assert_eq!(form(&reqs[0], "price"), Some("1500.5"));
    assert_eq!(form(&reqs[0], "trigger_price"), None);
    assert_eq!(form(&reqs[0], "product"), Some("MIS"));
    assert_eq!(form(&reqs[0], "tag"), Some("O1"));
    assert_eq!(form(&reqs[0], "tradingsymbol"), Some("INFY"));
    assert_eq!(form(&reqs[0], "exchange"), Some("NSE"));
    assert_eq!(form(&reqs[0], "quantity"), Some("10"));
    assert_eq!(form(&reqs[0], "validity"), Some("DAY"));
}

#[tokio::test]
async fn submit_market_sends_no_price_and_stop_market_sends_trigger_only() {
    let mut gw = gateway(vec![placed("1"), placed("2")]);
    gw.submit_order(order("M", OrderType::Market, None, None))
        .await
        .unwrap();
    gw.submit_order(order("S", OrderType::StopMarket, None, Some(99.0)))
        .await
        .unwrap();
    let reqs = gw.client().transport().requests();
    assert_eq!(form(&reqs[0], "price"), None);
    assert_eq!(form(&reqs[0], "trigger_price"), None);
    assert_eq!(form(&reqs[1], "price"), None);
    assert_eq!(form(&reqs[1], "trigger_price"), Some("99"));
}

#[tokio::test]
async fn submit_stop_limit_sends_both_prices() {
    let mut gw = gateway(vec![placed("1")]);
    gw.submit_order(order("S", OrderType::StopLimit, Some(100.0), Some(99.0)))
        .await
        .unwrap();
    let reqs = gw.client().transport().requests();
    assert_eq!(form(&reqs[0], "price"), Some("100"));
    assert_eq!(form(&reqs[0], "trigger_price"), Some("99"));
}

#[tokio::test]
async fn submit_missing_price_or_trigger_is_invalid_before_network() {
    let mut gw = gateway(vec![]);
    for o in [
        order("A", OrderType::StopLimit, Some(1.0), None),
        order("B", OrderType::StopMarket, None, None),
        order("C", OrderType::StopLimit, None, Some(1.0)),
    ] {
        let err = gw.submit_order(o).await.unwrap_err();
        assert!(matches!(err, PortError::InvalidRequest(_)), "{err:?}");
    }
    assert!(gw.client().transport().requests().is_empty());
}

#[tokio::test]
async fn resubmitting_known_id_is_idempotent() {
    let mut gw = gateway(vec![placed("2201")]);
    let o = order("O-1", OrderType::Limit, Some(10.0), None);
    let a = gw.submit_order(o.clone()).await.unwrap();
    let b = gw.submit_order(o).await.unwrap();
    assert_eq!(a, b);
    assert_eq!(gw.client().transport().requests().len(), 1);
}

#[tokio::test]
async fn submit_rejection_stays_rejected_and_is_not_remembered() {
    let mut gw = gateway(vec![
        FakeTransport::ok(
            400,
            r#"{"status":"error","message":"Insufficient funds","error_type":"MarginException"}"#,
        ),
        placed("7"),
    ]);
    let o = order("O-1", OrderType::Limit, Some(10.0), None);
    let err = gw.submit_order(o.clone()).await.unwrap_err();
    assert!(matches!(err, PortError::Rejected { .. }), "{err:?}");
    // A rejected submit is not "known": a retry goes to the network again.
    assert_eq!(gw.submit_order(o).await.unwrap(), OrderId::new("O-1"));
    assert_eq!(gw.client().transport().requests().len(), 2);
}

#[tokio::test]
async fn cancel_and_modify_unknown_id_are_invalid_without_network() {
    let mut gw = gateway(vec![]);
    let e1 = gw.cancel_order(OrderId::new("nope")).await.unwrap_err();
    let e2 = gw
        .modify_order(OrderId::new("nope"), 5.0, None)
        .await
        .unwrap_err();
    assert!(matches!(e1, PortError::InvalidRequest(_)));
    assert!(matches!(e2, PortError::InvalidRequest(_)));
    assert!(gw.client().transport().requests().is_empty());
}

#[tokio::test]
async fn cancel_uses_venue_id() {
    let mut gw = gateway(vec![placed("2201"), placed("2201")]);
    gw.submit_order(order("O-1", OrderType::Limit, Some(10.0), None))
        .await
        .unwrap();
    gw.cancel_order(OrderId::new("O-1")).await.unwrap();
    let reqs = gw.client().transport().requests();
    assert_eq!(reqs[1].method, Method::Delete);
    assert!(reqs[1].url.ends_with("/orders/regular/2201"));
}

#[tokio::test]
async fn cancel_rejection_stays_rejected() {
    let mut gw = gateway(vec![
        placed("2201"),
        FakeTransport::ok(
            400,
            r#"{"status":"error","message":"Order already cancelled","error_type":"OrderException"}"#,
        ),
    ]);
    gw.submit_order(order("O-1", OrderType::Limit, Some(10.0), None))
        .await
        .unwrap();
    let err = gw.cancel_order(OrderId::new("O-1")).await.unwrap_err();
    assert!(matches!(err, PortError::Rejected { .. }), "{err:?}");
}

#[tokio::test]
async fn modify_sends_qty_and_price() {
    let mut gw = gateway(vec![placed("2201"), placed("2201")]);
    gw.submit_order(order("O-1", OrderType::Limit, Some(10.0), None))
        .await
        .unwrap();
    gw.modify_order(OrderId::new("O-1"), 5.0, Some(11.5))
        .await
        .unwrap();
    let reqs = gw.client().transport().requests();
    assert_eq!(reqs[1].method, Method::Put);
    assert!(reqs[1].url.ends_with("/orders/regular/2201"));
    assert_eq!(form(&reqs[1], "quantity"), Some("5"));
    assert_eq!(form(&reqs[1], "price"), Some("11.5"));
}

#[tokio::test]
async fn modify_rejects_non_positive_or_fractional_qty() {
    let mut gw = gateway(vec![placed("2201")]);
    gw.submit_order(order("O-1", OrderType::Limit, Some(10.0), None))
        .await
        .unwrap();
    for q in [0.0, -1.0, 2.5, f64::NAN] {
        let err = gw
            .modify_order(OrderId::new("O-1"), q, None)
            .await
            .unwrap_err();
        assert!(matches!(err, PortError::InvalidRequest(_)), "{q}: {err:?}");
    }
    assert_eq!(gw.client().transport().requests().len(), 1);
}

#[tokio::test]
async fn next_fill_emits_known_trades_once_and_queues_without_repolling() {
    let rows = [
        ("T1", "2201", 4, 100.0),
        ("T2", "2201", 6, 101.0),
        ("T3", "9999", 1, 5.0),
    ];
    let mut gw = gateway(vec![placed("2201"), trades(&rows), trades(&rows)]);
    gw.submit_order(order("O-1", OrderType::Limit, Some(100.0), None))
        .await
        .unwrap();

    let f1 = gw.next_fill().await.unwrap().expect("first fill");
    assert_eq!(f1.order_id(), &OrderId::new("O-1"));
    assert_eq!(f1.quantity(), 4.0);
    assert_eq!(f1.price(), 100.0);
    assert_eq!(f1.side(), OrderSide::Buy);
    assert_eq!(f1.instrument_id().symbol(), "INFY");
    assert_eq!(f1.costs().currency(), Currency::Inr);
    assert_eq!(
        f1.ts_event(),
        parse_kite_timestamp("2024-01-02 09:15:30").unwrap()
    );
    assert_eq!(f1.ts_init(), UnixNanos::from_u64(NOW));
    // Only the /trades poll so far (plus the placement).
    assert_eq!(gw.client().transport().requests().len(), 2);

    // Queue drains without another HTTP poll.
    let f2 = gw.next_fill().await.unwrap().expect("second fill");
    assert_eq!(f2.quantity(), 6.0);
    assert_eq!(gw.client().transport().requests().len(), 2);

    // Queue empty: re-poll, same trades, nothing new, unknown order ignored.
    assert!(gw.next_fill().await.unwrap().is_none());
    assert_eq!(gw.client().transport().requests().len(), 3);
}

#[tokio::test]
async fn next_fill_picks_up_new_trades_on_later_polls() {
    let mut gw = gateway(vec![
        placed("1"),
        trades(&[("T1", "1", 1, 10.0)]),
        trades(&[("T1", "1", 1, 10.0), ("T2", "1", 2, 11.0)]),
    ]);
    gw.submit_order(order("O-1", OrderType::Limit, Some(10.0), None))
        .await
        .unwrap();
    assert!(gw.next_fill().await.unwrap().is_some());
    let f = gw.next_fill().await.unwrap().expect("T2");
    assert_eq!(f.quantity(), 2.0);
}

#[tokio::test]
async fn next_fill_propagates_transport_errors() {
    let mut gw = gateway(vec![Err(TransportError::Timeout)]);
    assert_eq!(gw.next_fill().await.unwrap_err(), PortError::Timeout);
}

#[tokio::test]
async fn next_fill_skips_malformed_trade_rows() {
    let bad = FakeTransport::ok(
        200,
        r#"{"status":"success","data":[{"trade_id":"T1","order_id":"1","quantity":0,"average_price":10.0,"fill_timestamp":"2024-01-02 09:15:30"},{"trade_id":"T2","order_id":"1","quantity":1,"average_price":10.0,"fill_timestamp":"garbage"}]}"#,
    );
    let mut gw = gateway(vec![placed("1"), bad]);
    gw.submit_order(order("O-1", OrderType::Limit, Some(10.0), None))
        .await
        .unwrap();
    assert!(gw.next_fill().await.unwrap().is_none());
}
