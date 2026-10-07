//! Unit tests for `crate::identifiers`.

use crate::identifiers::*;

#[test]
fn order_id_from_str_and_string() {
    let a: OrderId = "O-1".into();
    let b: OrderId = String::from("O-1").into();
    assert_eq!(a, b);
    assert_eq!(a.as_str(), "O-1");
}

#[test]
fn venue_order_id_is_distinct_from_order_id() {
    let v = VenueOrderId::new("1100000012345");
    assert_eq!(v.as_str(), "1100000012345");
    assert_eq!(v.to_string(), "1100000012345");
    assert_eq!(v, VenueOrderId::new(String::from("1100000012345")));
    let json = serde_json::to_string(&v).unwrap();
    assert_eq!(json, "\"1100000012345\"");
    assert_eq!(serde_json::from_str::<VenueOrderId>(&json).unwrap(), v);
}
