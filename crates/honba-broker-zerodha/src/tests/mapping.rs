use honba_messages::{OrderId, OrderSide, OrderStatus, OrderType, TimeInForce, UnixNanos};

use crate::mapping::{
    kite_tag, order_side_from_kite, order_side_to_kite, order_status, order_type_from_kite,
    order_type_to_kite, parse_kite_timestamp, status_from_kite, tif_to_kite, Product,
};
use crate::wire::OrderRecord;

#[test]
fn side_round_trip() {
    assert_eq!(order_side_to_kite(OrderSide::Buy).unwrap(), "BUY");
    assert_eq!(order_side_to_kite(OrderSide::Sell).unwrap(), "SELL");
    assert!(order_side_to_kite(OrderSide::NoOrderSide).is_err());
    assert_eq!(order_side_from_kite("BUY"), Some(OrderSide::Buy));
    assert_eq!(order_side_from_kite("SELL"), Some(OrderSide::Sell));
    assert_eq!(order_side_from_kite("HOLD"), None);
}

#[test]
fn order_type_round_trip() {
    for (t, s) in [
        (OrderType::Market, "MARKET"),
        (OrderType::Limit, "LIMIT"),
        (OrderType::StopMarket, "SL-M"),
        (OrderType::StopLimit, "SL"),
    ] {
        assert_eq!(order_type_to_kite(t).unwrap(), s);
        assert_eq!(order_type_from_kite(s), Some(t));
    }
    assert_eq!(order_type_from_kite("XX"), None);
}

#[test]
fn time_in_force_supports_day_and_ioc_only() {
    assert_eq!(tif_to_kite(TimeInForce::Day).unwrap(), "DAY");
    assert_eq!(tif_to_kite(TimeInForce::Ioc).unwrap(), "IOC");
    for t in [TimeInForce::Gtc, TimeInForce::Fok, TimeInForce::Gtd] {
        assert!(tif_to_kite(t).is_err(), "{t:?}");
    }
}

#[test]
fn product_strings() {
    assert_eq!(Product::Cnc.as_str(), "CNC");
    assert_eq!(Product::Mis.as_str(), "MIS");
    assert_eq!(Product::Nrml.as_str(), "NRML");
}

#[test]
fn status_vocabulary() {
    for s in [
        "OPEN",
        "TRIGGER PENDING",
        "PUT ORDER REQ RECEIVED",
        "VALIDATION PENDING",
        "WEIRD",
    ] {
        assert_eq!(status_from_kite(s), OrderStatus::Accepted, "{s}");
    }
    assert_eq!(status_from_kite("COMPLETE"), OrderStatus::Filled);
    assert_eq!(status_from_kite("CANCELLED"), OrderStatus::Cancelled);
    assert_eq!(status_from_kite("REJECTED"), OrderStatus::Rejected);
}

fn rec(status: &str, filled: Option<u64>, qty: Option<u64>) -> OrderRecord {
    serde_json::from_value(serde_json::json!({
        "order_id": "1", "status": status, "filled_quantity": filled, "quantity": qty
    }))
    .unwrap()
}

#[test]
fn partial_fill_is_open_with_some_filled() {
    assert_eq!(
        order_status(&rec("OPEN", Some(3), Some(10))),
        OrderStatus::PartiallyFilled
    );
    assert_eq!(
        order_status(&rec("OPEN", Some(0), Some(10))),
        OrderStatus::Accepted
    );
    assert_eq!(
        order_status(&rec("OPEN", Some(10), Some(10))),
        OrderStatus::Accepted
    );
    assert_eq!(
        order_status(&rec("OPEN", None, Some(10))),
        OrderStatus::Accepted
    );
    assert_eq!(
        order_status(&rec("COMPLETE", Some(10), Some(10))),
        OrderStatus::Filled
    );
    let none: OrderRecord = serde_json::from_str(r#"{"order_id":"1"}"#).unwrap();
    assert_eq!(order_status(&none), OrderStatus::Accepted);
}

#[test]
fn timestamp_epoch_in_ist() {
    assert_eq!(
        parse_kite_timestamp("1970-01-01 05:30:00").unwrap(),
        UnixNanos::new(0)
    );
    assert_eq!(
        parse_kite_timestamp("1970-01-01 05:30:01").unwrap(),
        UnixNanos::new(1_000_000_000)
    );
}

#[test]
fn timestamp_leap_day() {
    // 2024-02-29 09:15:00 IST == 03:45:00 UTC == 1709164800 + 13500.
    assert_eq!(
        parse_kite_timestamp("2024-02-29 09:15:00").unwrap(),
        UnixNanos::new(1_709_178_300 * 1_000_000_000)
    );
    // Day after the leap day.
    assert_eq!(
        parse_kite_timestamp("2024-03-01 05:30:00").unwrap(),
        UnixNanos::new((1_709_164_800 + 86_400) * 1_000_000_000)
    );
}

#[test]
fn timestamp_century_leap_year_2000() {
    // 2000-03-01 00:00:00 UTC is 951868800; IST midnight is 19800 s earlier.
    assert_eq!(
        parse_kite_timestamp("2000-03-01 00:00:00").unwrap(),
        UnixNanos::new(951_849_000 * 1_000_000_000)
    );
}

#[test]
fn timestamp_rejects_invalid_input() {
    for bad in [
        "",
        "garbage",
        "2024-02-29T09:15:00",
        "2023-02-29 09:15:00",
        "2100-02-29 09:15:00",
        "2024-13-01 09:15:00",
        "2024-00-10 09:15:00",
        "2024-04-31 09:15:00",
        "2024-04-30 24:00:00",
        "2024-04-30 12:60:00",
        "2024-04-30 12:00:60",
        "1970-01-01 00:00:00",
        "2024-4-30 12:00:00",
        "+024-04-30 12:00:00",
    ] {
        assert!(parse_kite_timestamp(bad).is_err(), "{bad:?}");
    }
}

#[test]
fn tag_is_alphanumeric_truncated_and_deterministic() {
    assert_eq!(kite_tag(&OrderId::new("hb-123_abc")), "hb123abc");
    let long = OrderId::new("a1".repeat(20));
    let t = kite_tag(&long);
    assert_eq!(t.len(), 20);
    assert_eq!(t, "a1".repeat(10));
    assert_eq!(kite_tag(&long), t);
    assert_eq!(kite_tag(&OrderId::new("---")), "HONBA");
    assert_eq!(kite_tag(&OrderId::new("")), "HONBA");
    assert_eq!(kite_tag(&OrderId::new("é-ü")), "HONBA");
}
