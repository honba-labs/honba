//! Unit tests for `crate::trade`.

use honba_messages::{OrderId, OrderSide, UnixNanos};

use super::any_instrument;

use crate::{Currency, Money, Trade};

fn trade() -> Trade {
    Trade::new(
        OrderId::new("O-9"),
        any_instrument(),
        OrderSide::Sell,
        2.0,
        10.0,
        Currency::Inr,
        UnixNanos::from_u64(1),
        UnixNanos::from_u64(1),
    )
}

#[test]
fn costs_default_to_zero_and_do_not_change_notional() {
    let t = trade();
    assert_eq!(t.costs().minor(), 0);
    let t = t.with_costs(Money::from_major_f64(1.5, Currency::Inr).unwrap());
    assert_eq!(t.costs().minor(), 150);
    assert_eq!(t.notional(), 20.0);
    assert_eq!(t.order_id().as_str(), "O-9");
}

#[test]
fn costs_are_an_integer_on_the_wire() {
    // ADR 0011: serialization emits the integer — never a JSON float.
    let t = trade().with_costs(Money::from_major_f64(1.5, Currency::Inr).unwrap());
    let v = serde_json::to_value(&t).unwrap();
    assert_eq!(v["costs"]["amount"], serde_json::json!(150));
    let back: Trade = serde_json::from_value(v).unwrap();
    assert_eq!(back, t);
}

#[test]
fn a_legacy_float_cost_still_parses() {
    // Older producers wrote `costs: 45.67`; readers round once, at the door.
    let json = serde_json::json!({
        "order_id": "O-9",
        "instrument_id": {"symbol": "X", "exchange": "NSE"},
        "side": "sell",
        "quantity": 2.0,
        "price": 10.0,
        "costs": 1.5,
        "ts_event": {"iso": "1970-01-01T00:00:00.000000001Z", "unix_nanos": "1"},
        "ts_init": {"iso": "1970-01-01T00:00:00.000000001Z", "unix_nanos": "1"},
    });
    let t: Trade = serde_json::from_value(json).unwrap();
    assert_eq!(t.costs().minor(), 150);
}

#[test]
fn validate_reports_typed_errors() {
    use honba_messages::InvariantError::*;
    assert_eq!(trade().validate(), Ok(()));
    let cases = [
        (
            Trade {
                quantity: -1.0,
                ..trade()
            },
            NotPositive {
                field: "quantity",
                value: -1.0,
            },
        ),
        (
            Trade {
                price: 0.0,
                ..trade()
            },
            NotPositive {
                field: "price",
                value: 0.0,
            },
        ),
        (
            Trade {
                side: OrderSide::NoOrderSide,
                ..trade()
            },
            NotAllowed { field: "side" },
        ),
    ];
    for (t, err) in cases {
        assert_eq!(t.validate(), Err(err), "{t:?}");
    }
    let json = serde_json::to_value(Trade {
        side: OrderSide::NoOrderSide,
        ..trade()
    })
    .unwrap();
    assert!(serde_json::from_value::<Trade>(json).is_err());
}

#[test]
fn trade_json_requires_costs_field() {
    let mut json = serde_json::to_value(trade()).unwrap();
    json.as_object_mut().unwrap().remove("costs");
    assert!(serde_json::from_value::<Trade>(json).is_err());
}
