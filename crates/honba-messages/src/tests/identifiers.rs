//! Unit tests for `crate::identifiers`.

use crate::identifiers::*;

#[test]
fn order_id_from_str_and_string() {
    let a: OrderId = "O-1".into();
    let b: OrderId = String::from("O-1").into();
    assert_eq!(a, b);
    assert_eq!(a.as_str(), "O-1");
}
