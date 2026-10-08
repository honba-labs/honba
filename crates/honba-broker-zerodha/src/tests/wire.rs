use crate::wire::{Envelope, OrderRecord, SessionData, TradeRecord};

#[test]
fn success_envelope_with_order_list() {
    let body = r#"{"status":"success","data":[{
        "order_id":"151220000000000","exchange_order_id":"511220371736111",
        "status":"COMPLETE","status_message":null,"tradingsymbol":"INFY","exchange":"NSE",
        "transaction_type":"BUY","order_type":"LIMIT","product":"CNC","quantity":10,
        "filled_quantity":10,"pending_quantity":0,"price":1500.5,"trigger_price":0,
        "average_price":1500.25,"validity":"DAY","tag":"HB1",
        "order_timestamp":"2024-02-29 09:15:00","exchange_update_timestamp":"2024-02-29 09:15:01",
        "unknown_field":42}]}"#;
    let env: Envelope<Vec<OrderRecord>> = serde_json::from_str(body).unwrap();
    assert!(env.is_success());
    let o = &env.data.unwrap()[0];
    assert_eq!(o.order_id, "151220000000000");
    assert_eq!(o.status.as_deref(), Some("COMPLETE"));
    assert_eq!(o.status_message, None);
    assert_eq!(o.quantity, Some(10));
    assert_eq!(o.price, Some(1500.5));
    assert_eq!(o.tag.as_deref(), Some("HB1"));
    assert_eq!(o.order_timestamp.as_deref(), Some("2024-02-29 09:15:00"));
}

#[test]
fn order_record_tolerates_missing_and_null_fields() {
    let o: OrderRecord =
        serde_json::from_str(r#"{"order_id":"1","price":null,"tag":null}"#).unwrap();
    assert_eq!(o.order_id, "1");
    assert_eq!(o.price, None);
    assert_eq!(o.tag, None);
    assert_eq!(o.status, None);
    assert_eq!(o.exchange_update_timestamp, None);
}

#[test]
fn error_envelope() {
    let body =
        r#"{"status":"error","message":"Invalid token","error_type":"TokenException","data":null}"#;
    let env: Envelope<serde_json::Value> = serde_json::from_str(body).unwrap();
    assert!(!env.is_success());
    assert_eq!(env.message.as_deref(), Some("Invalid token"));
    assert_eq!(env.error_type.as_deref(), Some("TokenException"));
}

#[test]
fn trade_record() {
    let t: TradeRecord = serde_json::from_str(
        r#"{"trade_id":"T1","order_id":"O1","tradingsymbol":"INFY","exchange":"NSE",
        "transaction_type":"SELL","quantity":4,"average_price":1501.0,
        "fill_timestamp":"2024-02-29 09:16:00"}"#,
    )
    .unwrap();
    assert_eq!(t.trade_id, "T1");
    assert_eq!(t.order_id.as_deref(), Some("O1"));
    assert_eq!(t.quantity, Some(4));
    assert_eq!(t.average_price, Some(1501.0));
    assert_eq!(t.fill_timestamp.as_deref(), Some("2024-02-29 09:16:00"));
}

#[test]
fn session_data() {
    let s: SessionData =
        serde_json::from_str(r#"{"access_token":"tok","user_id":"AB1234","extra":1}"#).unwrap();
    assert_eq!(s.access_token, "tok");
    assert_eq!(s.user_id, "AB1234");
}
