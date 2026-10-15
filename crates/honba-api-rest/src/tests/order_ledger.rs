//! `OrderLedger` rules that no route reaches directly: fill booking, exposure.

use honba_messages::{
    Exchange, InstrumentId, Order, OrderSide, OrderStatus, OrderType, TimeInForce, UnixNanos,
};

use crate::OrderLedger;

fn iid() -> InstrumentId {
    InstrumentId::new("X", Exchange::new("NSE"))
}

/// A `Submitted` market order of `qty` for `X.NSE` recorded under `id`.
fn working(ledger: &OrderLedger, id: &str, side: OrderSide, qty: f64) {
    ledger.record(
        Order::new(
            honba_messages::OrderId::new(id),
            iid(),
            side,
            OrderType::Market,
            qty,
            None,
            TimeInForce::Day,
            UnixNanos::new(0),
            UnixNanos::new(0),
        )
        .with_status(OrderStatus::Submitted),
    );
}

#[test]
fn a_fill_with_a_non_finite_or_non_positive_price_or_quantity_is_dropped() {
    // `apply_fill` is public, so a venue adapter (or a future caller) can hand it values a
    // simulator never produces; the guard keeps them out of the row, which would otherwise
    // fail validation or serialization of `GET /orders`.
    let ledger = OrderLedger::new();
    working(&ledger, "o1", OrderSide::Buy, 10.0);
    for (qty, px, cum) in [
        (5.0, f64::NAN, 5.0),
        (5.0, f64::INFINITY, 5.0),
        (5.0, 0.0, 5.0),
        (5.0, -1.0, 5.0),
        (0.0, 100.0, 5.0),
        (-5.0, 100.0, 5.0),
        (f64::NAN, 100.0, 5.0),
        (5.0, 100.0, f64::NAN),
        (5.0, 100.0, f64::INFINITY),
    ] {
        assert!(ledger.apply_fill("o1", qty, px, cum, OrderStatus::PartiallyFilled));
        let row = ledger.get("o1").unwrap();
        assert_eq!(row.status(), OrderStatus::Submitted, "{qty} {px} {cum}");
        assert_eq!(row.filled_qty(), 0.0, "{qty} {px} {cum}");
        assert_eq!(row.avg_fill_price(), None, "{qty} {px} {cum}");
        assert!(serde_json::to_value(&row).is_ok());
    }
}
