//! Unit tests for `crate::orders::order`.

use super::any_instrument;
use crate::identifiers::OrderId;
use crate::orders::order::*;
use crate::validation::InvariantError::*;

fn order() -> Order {
    Order::new(
        OrderId::new("O"),
        any_instrument(),
        OrderSide::Buy,
        OrderType::Limit,
        5.0,
        Some(10.0),
        TimeInForce::Day,
        1.into(),
        1.into(),
    )
}

#[test]
fn validate_reports_typed_errors() {
    assert_eq!(order().validate(), Ok(()));
    let negative = Order {
        quantity: -5.0,
        ..order()
    };
    assert_eq!(
        negative.validate(),
        Err(NotPositive {
            field: "quantity",
            value: -5.0,
        })
    );
    let json = serde_json::to_value(negative).unwrap();
    let err = serde_json::from_value::<Order>(json)
        .unwrap_err()
        .to_string();
    assert!(err.contains("quantity"), "{err}");
}

#[test]
fn all_lists_every_variant_once() {
    assert_eq!(
        OrderSide::ALL,
        &[OrderSide::Buy, OrderSide::Sell, OrderSide::NoOrderSide]
    );
    assert_eq!(OrderType::ALL.len(), 4);
    assert_eq!(OrderStatus::ALL.len(), 8);
    assert_eq!(TimeInForce::ALL.len(), 5);
}

#[test]
fn non_finite_prices_never_serialize_as_null() {
    let nan_trigger = order().with_trigger_price(f64::NAN);
    assert!(serde_json::to_string(&nan_trigger).is_err());
    let inf_price = Order {
        price: Some(f64::INFINITY),
        ..order()
    };
    assert!(serde_json::to_string(&inf_price).is_err());
    // `None` is still written as `null`.
    let market = Order {
        price: None,
        ..order()
    };
    assert_eq!(
        serde_json::to_value(market).unwrap()["price"],
        serde_json::Value::Null
    );
}

#[test]
fn cancel_requested_defaults_false_and_is_skipped_when_false() {
    let o = order();
    assert!(!o.cancel_requested());
    let json = serde_json::to_value(&o).unwrap();
    assert!(json.get("cancel_requested").is_none());
    let back: Order = serde_json::from_value(json).unwrap();
    assert_eq!(back, o);
}

#[test]
fn cancel_requested_invariant_only_for_working_statuses() {
    use OrderStatus::*;
    for status in OrderStatus::ALL {
        let o = order().with_status(*status).with_cancel_requested(true);
        let working = matches!(status, Submitted | Accepted | PartiallyFilled);
        if working {
            assert_eq!(o.validate(), Ok(()), "{status:?}");
            assert!(o.cancel_requested());
            let json = serde_json::to_value(&o).unwrap();
            assert_eq!(json["cancel_requested"], true);
            assert_eq!(serde_json::from_value::<Order>(json).unwrap(), o);
        } else {
            assert_eq!(
                o.validate(),
                Err(NotAllowed {
                    field: "cancel_requested"
                }),
                "{status:?}"
            );
            let json = serde_json::to_value(&o).unwrap();
            assert!(serde_json::from_value::<Order>(json).is_err(), "{status:?}");
        }
    }
}
