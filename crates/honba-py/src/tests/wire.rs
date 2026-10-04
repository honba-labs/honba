//! Unit tests for `crate::pyclasses::wire`.

use crate::pyclasses::wire::*;

#[test]
fn enum_values_list_every_variant_as_its_wire_string() {
    let values = enum_values();
    assert_eq!(values.len(), ENUM_KINDS.len());
    assert_eq!(values["OrderSide"], ["buy", "sell", "no_order_side"]);
    assert_eq!(values["Currency"], ["INR", "USD", "EUR", "GBP"]);
    for (kind, variants) in &values {
        for v in variants {
            let json = serde_json::to_string(v).unwrap();
            assert_eq!(canonical(kind, &json).unwrap(), json, "{kind}");
        }
    }
    assert!(canonical("OrderSide", "\"sideways\"").is_err());
}

#[test]
fn canonical_roundtrips_and_rejects() {
    let id = r#"{"symbol":"X","exchange":"NSE"}"#;
    assert_eq!(canonical("InstrumentId", id).unwrap(), id);
    assert!(canonical("Nope", "{}")
        .unwrap_err()
        .contains("unknown kind"));
    assert!(canonical("Order", "{}").is_err());
}
