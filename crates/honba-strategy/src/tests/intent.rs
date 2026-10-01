//! Unit tests for `crate::intent`.

use honba_messages::{OrderId, OrderSide, OrderType};

use super::any_instrument;

use crate::intent::*;

#[test]
fn constructors_are_valid() {
    for i in [
        OrderIntent::market_buy(any_instrument(), 1.0),
        OrderIntent::market_sell(any_instrument(), 1.0),
        OrderIntent::limit_buy(any_instrument(), 1.0, 10.0),
        OrderIntent::limit_sell(any_instrument(), 1.0, 10.0),
        OrderIntent::stop_buy(any_instrument(), 1.0, 10.0),
        OrderIntent::stop_sell(any_instrument(), 1.0, 10.0),
        OrderIntent::stop_limit_buy(any_instrument(), 1.0, 10.0, 10.5),
        OrderIntent::stop_limit_sell(any_instrument(), 1.0, 10.0, 9.5),
    ] {
        assert_eq!(i.validate(), Ok(()), "{i:?}");
    }
}

#[test]
fn stop_constructors_set_trigger_not_limit() {
    let i = OrderIntent::stop_sell(any_instrument(), 2.0, 99.0);
    assert_eq!(i.order_type, OrderType::StopMarket);
    assert_eq!((i.price, i.trigger_price), (None, Some(99.0)));
    assert_eq!(i.side, OrderSide::Sell);
}

#[test]
fn validate_rejects_each_broken_rule() {
    use IntentError::*;
    let base = OrderIntent::stop_limit_buy(any_instrument(), 1.0, 10.0, 10.5);
    let cases = [
        (
            OrderIntent {
                quantity: 0.0,
                ..base.clone()
            },
            NonPositiveQuantity(0.0),
        ),
        (
            OrderIntent {
                side: OrderSide::NoOrderSide,
                ..base.clone()
            },
            NoSide,
        ),
        (
            OrderIntent {
                price: Some(f64::NAN),
                ..base.clone()
            },
            NonFinitePrice,
        ),
        (
            OrderIntent {
                price: None,
                ..base.clone()
            },
            MissingPrice(OrderType::StopLimit),
        ),
        (
            OrderIntent {
                trigger_price: None,
                ..base.clone()
            },
            MissingTriggerPrice(OrderType::StopLimit),
        ),
        (
            OrderIntent {
                order_type: OrderType::Market,
                ..base.clone()
            },
            UnexpectedPrice(OrderType::Market),
        ),
        (
            OrderIntent {
                order_type: OrderType::Limit,
                ..base.clone()
            },
            UnexpectedTriggerPrice(OrderType::Limit),
        ),
    ];
    for (intent, err) in cases {
        assert_eq!(intent.validate(), Err(err), "{intent:?}");
    }
    assert!(OrderIntent {
        quantity: f64::NAN,
        ..base
    }
    .validate()
    .is_err());
}

#[test]
fn into_order_keeps_trigger_price() {
    let order = OrderIntent::stop_buy(any_instrument(), 1.0, 10.0)
        .into_order(OrderId::new("O"), 1.into())
        .unwrap();
    assert_eq!(order.trigger_price(), Some(10.0));
    assert_eq!(order.price(), None);
    let order = OrderIntent::market_buy(any_instrument(), 1.0)
        .into_order(OrderId::new("O"), 1.into())
        .unwrap();
    assert_eq!(order.trigger_price(), None);
}

#[test]
fn into_order_rejects_invalid_intents() {
    let o = OrderId::new("O");
    assert_eq!(
        OrderIntent::market_buy(any_instrument(), -1.0).into_order(o.clone(), 1.into()),
        Err(IntentError::NonPositiveQuantity(-1.0))
    );
    let no_price = OrderIntent {
        price: None,
        ..OrderIntent::limit_sell(any_instrument(), 1.0, 10.0)
    };
    assert_eq!(
        no_price.into_order(o.clone(), 1.into()),
        Err(IntentError::MissingPrice(OrderType::Limit))
    );
    assert_eq!(
        OrderIntent::stop_sell(any_instrument(), 1.0, f64::INFINITY).into_order(o, 1.into()),
        Err(IntentError::NonFinitePrice)
    );
}
