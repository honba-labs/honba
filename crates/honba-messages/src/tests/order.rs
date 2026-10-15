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
    assert_eq!(OrderType::ALL.len(), 5);
    assert_eq!(OrderStatus::ALL.len(), 8);
    assert_eq!(TimeInForce::ALL.len(), 5);
    assert_eq!(
        Product::ALL,
        &[Product::Delivery, Product::Intraday, Product::Carry]
    );
}

#[test]
fn product_serializes_snake_case() {
    for (product, wire) in [
        (Product::Delivery, "delivery"),
        (Product::Intraday, "intraday"),
        (Product::Carry, "carry"),
    ] {
        assert_eq!(
            serde_json::to_value(product).unwrap(),
            serde_json::json!(wire)
        );
    }
}

#[test]
fn order_product_is_optional_and_omitted_when_absent() {
    let plain = order();
    assert_eq!(plain.product(), None);
    assert!(serde_json::to_value(&plain)
        .unwrap()
        .get("product")
        .is_none());

    let held = order().with_product(Product::Intraday);
    assert_eq!(held.product(), Some(Product::Intraday));
    let json = serde_json::to_value(&held).unwrap();
    assert_eq!(json["product"], "intraday");
    let back: Order = serde_json::from_value(json).unwrap();
    assert_eq!(back, held);

    // A v1 record without the additive field still decodes to `None`.
    let legacy: Order = serde_json::from_value(serde_json::to_value(order()).unwrap()).unwrap();
    assert_eq!(legacy.product(), None);
}

#[test]
fn trailing_stop_order_validation_and_serde() {
    let ts = Order {
        order_type: OrderType::TrailingStop,
        price: None,
        ..order()
    }
    .with_trail_amount(15.0);
    assert_eq!(ts.validate(), Ok(()));
    assert_eq!(ts.trail_amount(), Some(15.0));
    assert_eq!(ts.trail_percent(), None);

    let json = serde_json::to_value(&ts).unwrap();
    assert_eq!(json["order_type"], "trailing_stop");
    assert_eq!(json["trail_amount"], 15.0);
    assert!(json.get("trail_percent").is_none());
    let back: Order = serde_json::from_value(json).unwrap();
    assert_eq!(back, ts);

    // Trail percent
    let ts_pct = Order {
        order_type: OrderType::TrailingStop,
        price: None,
        ..order()
    }
    .with_trail_percent(2.5);
    assert_eq!(ts_pct.validate(), Ok(()));
    assert_eq!(ts_pct.trail_percent(), Some(2.5));

    // Non-positive trail amount
    let bad_amt = order().with_trail_amount(0.0);
    assert_eq!(
        bad_amt.validate(),
        Err(NotPositive {
            field: "trail_amount",
            value: 0.0,
        })
    );

    // Out of range trail percent
    let bad_pct = order().with_trail_percent(100.0);
    assert_eq!(
        bad_pct.validate(),
        Err(OutsideRange {
            field: "trail_percent",
        })
    );
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
    let nan_trail = order().with_trail_amount(f64::NAN);
    assert!(serde_json::to_string(&nan_trail).is_err());
    let nan_pct = order().with_trail_percent(f64::NAN);
    assert!(serde_json::to_string(&nan_pct).is_err());
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

#[test]
fn fill_and_reject_fields_default_and_are_skipped_when_unset() {
    let o = order();
    assert_eq!(o.filled_qty(), 0.0);
    assert_eq!(o.avg_fill_price(), None);
    assert_eq!(o.reject_reason(), None);
    let json = serde_json::to_value(&o).unwrap();
    for key in ["filled_qty", "avg_fill_price", "reject_reason"] {
        assert!(json.get(key).is_none(), "{key} must be additive");
    }
    // An old payload (no new keys) still parses.
    assert_eq!(serde_json::from_value::<Order>(json).unwrap(), o);
}

#[test]
fn fill_and_reject_fields_roundtrip() {
    let o = order()
        .with_status(OrderStatus::PartiallyFilled)
        .with_fill(2.0, 10.5);
    let json = serde_json::to_value(&o).unwrap();
    assert_eq!(json["filled_qty"], 2.0);
    assert_eq!(json["avg_fill_price"], 10.5);
    assert_eq!(serde_json::from_value::<Order>(json).unwrap(), o);

    let rejected = order()
        .with_status(OrderStatus::Rejected)
        .with_reject_reason(RejectReason::new(
            crate::ErrorCode::OrderRejected,
            "no_position",
        ));
    let json = serde_json::to_value(&rejected).unwrap();
    assert_eq!(json["reject_reason"]["code"], "order_rejected");
    assert_eq!(json["reject_reason"]["message"], "no_position");
    assert_eq!(serde_json::from_value::<Order>(json).unwrap(), rejected);
}

#[test]
fn fill_invariants_reject_negative_overfilled_and_non_positive_price() {
    let over = order().with_fill(6.0, 10.0);
    assert!(over.validate().is_err());
    let negative = order().with_fill(-1.0, 10.0);
    assert!(negative.validate().is_err());
    let bad_px = order().with_fill(1.0, 0.0);
    assert!(bad_px.validate().is_err());
    assert_eq!(order().with_fill(5.0, 10.0).validate(), Ok(()));
}

#[test]
fn without_reject_reason_clears_the_reason() {
    let reason = RejectReason::new(crate::ErrorCode::OrderRejected, "remainder");
    let o = order().with_reject_reason(reason);
    assert!(o.reject_reason().is_some());
    let o = o.without_reject_reason();
    assert_eq!(o.reject_reason(), None);
    assert!(serde_json::to_value(&o)
        .unwrap()
        .get("reject_reason")
        .is_none());
}

#[test]
fn filled_at_is_omitted_until_a_fill_and_round_trips() {
    let o = order();
    assert_eq!(o.filled_at(), None);
    assert!(serde_json::to_value(&o).unwrap().get("filled_at").is_none());

    let o = o
        .with_status(OrderStatus::PartiallyFilled)
        .with_fill(2.0, 10.5)
        .with_filled_at(42.into());
    assert_eq!(o.filled_at(), Some(42.into()));
    let json = serde_json::to_value(&o).unwrap();
    assert!(json.get("filled_at").is_some());
    assert_eq!(serde_json::from_value::<Order>(json).unwrap(), o);

    // A row from an older server has no `filled_at`.
    let mut old = serde_json::to_value(order()).unwrap();
    old.as_object_mut().unwrap().remove("filled_at");
    assert_eq!(
        serde_json::from_value::<Order>(old).unwrap().filled_at(),
        None
    );
}
