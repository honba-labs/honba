//! Unit tests for `crate::trade`.

use honba_messages::{OrderId, OrderSide, UnixNanos};

use super::any_instrument;

use crate::Trade;

fn trade() -> Trade {
    Trade::new(
        OrderId::new("O-9"),
        any_instrument(),
        OrderSide::Sell,
        2.0,
        10.0,
        UnixNanos::from_u64(1),
        UnixNanos::from_u64(1),
    )
}

#[test]
fn costs_default_to_zero_and_do_not_change_notional() {
    let t = trade();
    assert_eq!(t.costs(), 0.0);
    let t = t.with_costs(1.5);
    assert_eq!(t.costs(), 1.5);
    assert_eq!(t.notional(), 20.0);
    assert_eq!(t.order_id().as_str(), "O-9");
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
        (
            Trade {
                costs: f64::INFINITY,
                ..trade()
            },
            NonFinite { field: "costs" },
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
fn non_finite_costs_never_serialize_as_null() {
    let t = Trade {
        costs: f64::NAN,
        ..trade()
    };
    assert!(serde_json::to_string(&t).is_err());
}

#[test]
fn trade_json_requires_costs_field() {
    let mut json = serde_json::to_value(trade()).unwrap();
    json.as_object_mut().unwrap().remove("costs");
    assert!(serde_json::from_value::<Trade>(json).is_err());
}
