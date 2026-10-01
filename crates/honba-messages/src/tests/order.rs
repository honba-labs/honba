//! Unit tests for `crate::orders::order`.

use crate::identifiers::{InstrumentId, OrderId, Venue};
use crate::orders::order::*;
use crate::validation::InvariantError::*;

fn order() -> Order {
    Order::new(
        OrderId::new("O"),
        InstrumentId::new("X", Venue::new("NSE")),
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
