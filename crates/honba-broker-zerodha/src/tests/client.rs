use honba_messages::{OrderSide, OrderType, TimeInForce};
use honba_ports::PortError;

use super::FakeTransport;
use crate::client::{KiteClient, KiteConfig, PlaceOrder};
use crate::mapping::Product;
use crate::transport::{HttpResponse, Method, TransportError};

const KEY: &str = "kitekey";
const TOKEN: &str = "SECRET_ACCESS_TOKEN";

fn client(responses: Vec<Result<HttpResponse, TransportError>>) -> KiteClient<FakeTransport> {
    let mut cfg = KiteConfig::new(KEY);
    cfg.access_token = Some(TOKEN.to_owned());
    KiteClient::new(cfg, FakeTransport::new(responses))
}

fn header<'a>(req: &'a crate::transport::HttpRequest, name: &str) -> Option<&'a str> {
    req.headers
        .iter()
        .find(|(k, _)| k == name)
        .map(|(_, v)| v.as_str())
}

fn form<'a>(req: &'a crate::transport::HttpRequest, name: &str) -> Option<&'a str> {
    req.form
        .iter()
        .find(|(k, _)| k == name)
        .map(|(_, v)| v.as_str())
}

fn order() -> PlaceOrder {
    PlaceOrder {
        variety: "regular".into(),
        tradingsymbol: "INFY".into(),
        exchange: "NSE".into(),
        side: OrderSide::Buy,
        order_type: OrderType::Limit,
        quantity: 10,
        price: Some(1500.5),
        trigger_price: None,
        validity: TimeInForce::Day,
        product: Product::Cnc,
        tag: "HB1".into(),
    }
}

#[test]
fn default_base_url_and_config_debug_redacts_token() {
    let cfg = KiteConfig::new(KEY);
    assert_eq!(cfg.base_url, "https://api.kite.trade");
    assert_eq!(cfg.access_token, None);
    let mut cfg = cfg;
    cfg.access_token = Some(TOKEN.into());
    assert!(!format!("{cfg:?}").contains(TOKEN));
}

#[tokio::test]
async fn generate_session_posts_checksum_and_stores_token() {
    let mut c = KiteClient::new(
        KiteConfig::new(KEY),
        FakeTransport::new(vec![FakeTransport::ok(
            200,
            r#"{"status":"success","data":{"access_token":"newtok","user_id":"AB1234"}}"#,
        )]),
    );
    let user = c.generate_session("requesttoken", "secret").await.unwrap();
    assert_eq!(user, "AB1234");
    assert_eq!(c.access_token(), Some("newtok"));
    let r = &c.transport().requests()[0];
    assert_eq!(r.method, Method::Post);
    assert_eq!(r.url, "https://api.kite.trade/session/token");
    assert_eq!(header(r, "X-Kite-Version"), Some("3"));
    assert_eq!(form(r, "api_key"), Some(KEY));
    assert_eq!(form(r, "request_token"), Some("requesttoken"));
    // sha256("kitekey" + "requesttoken" + "secret")
    assert_eq!(
        form(r, "checksum"),
        Some("592048358eb98402d234059376af6fd55b22e27c5d65695098dcf7c206c333e5")
    );
    assert!(!r.form.iter().any(|(_, v)| v == "secret"));
}

#[tokio::test]
async fn place_order_request_shape() {
    let c = client(vec![FakeTransport::ok(
        200,
        r#"{"status":"success","data":{"order_id":"151220000000000"}}"#,
    )]);
    let id = c.place_order(&order()).await.unwrap();
    assert_eq!(id, "151220000000000");
    let r = &c.transport().requests()[0];
    assert_eq!(r.method, Method::Post);
    assert_eq!(r.url, "https://api.kite.trade/orders/regular");
    assert_eq!(
        header(r, "Authorization"),
        Some("token kitekey:SECRET_ACCESS_TOKEN")
    );
    assert_eq!(header(r, "X-Kite-Version"), Some("3"));
    assert_eq!(form(r, "tradingsymbol"), Some("INFY"));
    assert_eq!(form(r, "exchange"), Some("NSE"));
    assert_eq!(form(r, "transaction_type"), Some("BUY"));
    assert_eq!(form(r, "order_type"), Some("LIMIT"));
    assert_eq!(form(r, "quantity"), Some("10"));
    assert_eq!(form(r, "price"), Some("1500.5"));
    assert_eq!(form(r, "trigger_price"), None);
    assert_eq!(form(r, "validity"), Some("DAY"));
    assert_eq!(form(r, "product"), Some("CNC"));
    assert_eq!(form(r, "tag"), Some("HB1"));
}

#[tokio::test]
async fn place_stop_order_sends_trigger_price_and_variety_in_url() {
    let c = client(vec![FakeTransport::ok(
        200,
        r#"{"status":"success","data":{"order_id":"9"}}"#,
    )]);
    let mut o = order();
    o.variety = "amo".into();
    o.order_type = OrderType::StopMarket;
    o.price = None;
    o.trigger_price = Some(1490.0);
    o.side = OrderSide::Sell;
    o.validity = TimeInForce::Ioc;
    o.product = Product::Mis;
    c.place_order(&o).await.unwrap();
    let r = &c.transport().requests()[0];
    assert_eq!(r.url, "https://api.kite.trade/orders/amo");
    assert_eq!(form(r, "order_type"), Some("SL-M"));
    assert_eq!(form(r, "price"), None);
    assert_eq!(form(r, "trigger_price"), Some("1490"));
    assert_eq!(form(r, "transaction_type"), Some("SELL"));
    assert_eq!(form(r, "validity"), Some("IOC"));
    assert_eq!(form(r, "product"), Some("MIS"));
}

#[tokio::test]
async fn place_order_with_unsupported_validity_is_unsupported_and_sends_nothing() {
    let c = client(vec![]);
    let mut o = order();
    o.validity = TimeInForce::Gtc;
    let err = c.place_order(&o).await.unwrap_err();
    assert!(matches!(err, PortError::Unsupported(_)), "{err:?}");
    assert!(c.transport().requests().is_empty());
}

#[tokio::test]
async fn modify_order_request_shape() {
    let c = client(vec![FakeTransport::ok(
        200,
        r#"{"status":"success","data":{"order_id":"77"}}"#,
    )]);
    let id = c
        .modify_order("regular", "77", Some(5), Some(1501.0), None)
        .await
        .unwrap();
    assert_eq!(id, "77");
    let r = &c.transport().requests()[0];
    assert_eq!(r.method, Method::Put);
    assert_eq!(r.url, "https://api.kite.trade/orders/regular/77");
    assert_eq!(form(r, "quantity"), Some("5"));
    assert_eq!(form(r, "price"), Some("1501"));
    assert_eq!(form(r, "trigger_price"), None);
    assert_eq!(
        header(r, "Authorization"),
        Some("token kitekey:SECRET_ACCESS_TOKEN")
    );
}

#[tokio::test]
async fn cancel_order_request_shape() {
    let c = client(vec![FakeTransport::ok(
        200,
        r#"{"status":"success","data":{"order_id":"77"}}"#,
    )]);
    assert_eq!(c.cancel_order("regular", "77").await.unwrap(), "77");
    let r = &c.transport().requests()[0];
    assert_eq!(r.method, Method::Delete);
    assert_eq!(r.url, "https://api.kite.trade/orders/regular/77");
    assert!(r.form.is_empty());
}

#[tokio::test]
async fn orders_history_and_trades_requests() {
    let c = client(vec![
        FakeTransport::ok(
            200,
            r#"{"status":"success","data":[{"order_id":"1","status":"OPEN"}]}"#,
        ),
        FakeTransport::ok(
            200,
            r#"{"status":"success","data":[{"order_id":"1"},{"order_id":"1"}]}"#,
        ),
        FakeTransport::ok(200, r#"{"status":"success","data":[{"trade_id":"T1"}]}"#),
    ]);
    assert_eq!(c.orders().await.unwrap().len(), 1);
    assert_eq!(c.order_history("1").await.unwrap().len(), 2);
    assert_eq!(c.trades().await.unwrap()[0].trade_id, "T1");
    let reqs = c.transport().requests();
    assert_eq!(
        (reqs[0].method, reqs[0].url.as_str()),
        (Method::Get, "https://api.kite.trade/orders")
    );
    assert_eq!(reqs[1].url, "https://api.kite.trade/orders/1");
    assert_eq!(reqs[2].url, "https://api.kite.trade/trades");
    assert!(reqs
        .iter()
        .all(|r| r.method == Method::Get && r.form.is_empty()));
}

#[tokio::test]
async fn instruments_csv_returns_raw_text() {
    let c = client(vec![
        FakeTransport::ok(200, "instrument_token,exchange\n1,NSE\n"),
        FakeTransport::ok(200, "a,b\n"),
    ]);
    assert_eq!(
        c.instruments_csv(None).await.unwrap(),
        "instrument_token,exchange\n1,NSE\n"
    );
    c.instruments_csv(Some("NSE")).await.unwrap();
    let reqs = c.transport().requests();
    assert_eq!(reqs[0].url, "https://api.kite.trade/instruments");
    assert_eq!(reqs[1].url, "https://api.kite.trade/instruments/NSE");
}

#[tokio::test]
async fn instruments_csv_error_status_is_mapped() {
    let c = client(vec![FakeTransport::ok(503, "")]);
    assert!(matches!(
        c.instruments_csv(None).await,
        Err(PortError::Transport(_))
    ));
}

#[tokio::test]
async fn custom_base_url_is_used() {
    let mut cfg = KiteConfig::new(KEY);
    cfg.base_url = "http://localhost:9".into();
    cfg.access_token = Some(TOKEN.into());
    let c = KiteClient::new(
        cfg,
        FakeTransport::new(vec![FakeTransport::ok(
            200,
            r#"{"status":"success","data":[]}"#,
        )]),
    );
    c.orders().await.unwrap();
    assert_eq!(c.transport().requests()[0].url, "http://localhost:9/orders");
}

#[tokio::test]
async fn calls_without_session_are_unavailable_and_send_nothing() {
    let c = KiteClient::new(KiteConfig::new(KEY), FakeTransport::new(vec![]));
    let err = c.orders().await.unwrap_err();
    assert!(matches!(err, PortError::Unavailable(_)), "{err:?}");
    assert!(c.transport().requests().is_empty());
}

async fn err_for(status: u16, body: &str) -> PortError {
    client(vec![FakeTransport::ok(status, body)])
        .orders()
        .await
        .unwrap_err()
}

fn err_body(kind: &str, msg: &str) -> String {
    format!(r#"{{"status":"error","message":"{msg}","error_type":"{kind}"}}"#)
}

#[tokio::test]
async fn token_exception_is_unavailable() {
    let e = err_for(403, &err_body("TokenException", "Session expired")).await;
    match e {
        PortError::Unavailable(m) => assert!(m.contains("Session expired"), "{m}"),
        other => panic!("{other:?}"),
    }
}

#[tokio::test]
async fn business_exceptions_are_rejected_with_code_and_message() {
    for (status, kind) in [
        (400, "InputException"),
        (400, "OrderException"),
        (400, "MarginException"),
        (403, "PermissionException"),
    ] {
        let e = err_for(status, &err_body(kind, "nope")).await;
        assert_eq!(
            e,
            PortError::Rejected {
                code: kind.into(),
                message: "nope".into()
            },
            "{kind}"
        );
    }
}

#[tokio::test]
async fn other_4xx_with_message_is_rejected_with_http_code() {
    let e = err_for(404, r#"{"status":"error","message":"gone"}"#).await;
    assert_eq!(
        e,
        PortError::Rejected {
            code: "HTTP_404".into(),
            message: "gone".into()
        }
    );
}

#[tokio::test]
async fn rate_limit_and_server_errors_are_transport() {
    assert!(matches!(
        err_for(429, &err_body("NetworkException", "slow down")).await,
        PortError::Transport(_)
    ));
    assert!(matches!(err_for(429, "").await, PortError::Transport(_)));
    assert!(matches!(
        err_for(503, &err_body("NetworkException", "down")).await,
        PortError::Transport(_)
    ));
    assert!(matches!(
        err_for(500, &err_body("GeneralException", "oops")).await,
        PortError::Transport(_)
    ));
    assert!(matches!(
        err_for(502, "<html>bad gateway</html>").await,
        PortError::Transport(_)
    ));
}

#[tokio::test]
async fn transport_failures_map() {
    let c = client(vec![Err(TransportError::Timeout)]);
    assert_eq!(c.orders().await.unwrap_err(), PortError::Timeout);
    let c = client(vec![Err(TransportError::Connect("refused".into()))]);
    assert_eq!(
        c.orders().await.unwrap_err(),
        PortError::Transport("refused".into())
    );
}

#[tokio::test]
async fn undecodable_body_is_internal_and_leaks_nothing() {
    let body = format!("not json {TOKEN} secret checksum");
    let e = err_for(200, &body).await;
    assert!(matches!(e, PortError::Internal(_)), "{e:?}");
    let text = format!("{e:?} {e}");
    assert!(!text.contains(TOKEN) && !text.contains("secret"), "{text}");
    // 200 with wrong data shape.
    let e = err_for(200, r#"{"status":"success","data":"oops"}"#).await;
    assert!(matches!(e, PortError::Internal(_)), "{e:?}");
    // Success envelope with no data.
    let e = err_for(200, r#"{"status":"success"}"#).await;
    assert!(matches!(e, PortError::Internal(_)), "{e:?}");
    // Unparseable 4xx.
    let e = err_for(400, "garbage").await;
    assert!(matches!(e, PortError::Internal(_)), "{e:?}");
}

#[tokio::test]
async fn session_errors_never_leak_secrets() {
    let mut c = KiteClient::new(
        KiteConfig::new(KEY),
        FakeTransport::new(vec![
            FakeTransport::ok(200, "api_secret=topsecret"),
            Err(TransportError::Timeout),
            FakeTransport::ok(403, &err_body("TokenException", "Invalid checksum")),
        ]),
    );
    for _ in 0..3 {
        let e = c.generate_session("rt", "topsecret").await.unwrap_err();
        let text = format!("{e:?} {e}");
        assert!(!text.contains("topsecret"), "{text}");
        assert!(!text.contains("592048"), "{text}");
    }
    assert_eq!(c.access_token(), None);
}
