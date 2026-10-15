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

fn refusal() -> honba_messages::RejectReason {
    honba_messages::RejectReason::new(
        honba_messages::ErrorCode::OrderRejected,
        "remainder refused",
    )
}

#[test]
fn completing_a_part_filled_row_clears_the_reject_reason_of_its_remainder() {
    let ledger = OrderLedger::new();
    working(&ledger, "o1", OrderSide::Buy, 10.0);
    assert!(ledger.apply_fill("o1", 4.0, 100.0, 4.0, OrderStatus::PartiallyFilled));
    assert!(ledger.reject("o1", refusal()));
    let row = ledger.get("o1").unwrap();
    assert_eq!(row.status(), OrderStatus::PartiallyFilled);
    assert!(row.reject_reason().is_some());

    // The venue then fills the rest: a completed order was not refused.
    assert!(ledger.apply_fill("o1", 6.0, 101.0, 10.0, OrderStatus::Filled));
    let row = ledger.get("o1").unwrap();
    assert_eq!(row.status(), OrderStatus::Filled);
    assert_eq!(row.reject_reason(), None);
    assert_eq!(row.filled_qty(), 10.0);
}

#[test]
fn a_reject_reason_survives_a_partial_that_is_then_cancelled() {
    let ledger = OrderLedger::new();
    working(&ledger, "o1", OrderSide::Buy, 10.0);
    ledger.apply_fill("o1", 4.0, 100.0, 4.0, OrderStatus::PartiallyFilled);
    ledger.reject("o1", refusal());
    ledger.set_status("o1", OrderStatus::Cancelled);
    let row = ledger.get("o1").unwrap();
    assert_eq!(row.status(), OrderStatus::Cancelled);
    assert_eq!(row.reject_reason(), Some(&refusal()));
}

#[test]
fn the_average_follows_cum_qty_deltas_when_the_venue_disagrees_with_fill_sizes() {
    // The venue's cumulative quantity is the truth for how much has filled. Here its second
    // report says 10 cumulative although the two fill sizes add to 6: the 6 new units
    // (10 - 4) are priced at the latest fill price, so the average is (4*100 + 6*110) / 10.
    let ledger = OrderLedger::new();
    working(&ledger, "o1", OrderSide::Buy, 10.0);
    ledger.apply_fill("o1", 4.0, 100.0, 4.0, OrderStatus::PartiallyFilled);
    ledger.apply_fill("o1", 2.0, 110.0, 10.0, OrderStatus::Filled);
    let row = ledger.get("o1").unwrap();
    assert_eq!(row.filled_qty(), 10.0);
    assert!((row.avg_fill_price().unwrap() - 106.0).abs() < 1e-9);
}

#[test]
fn a_replayed_or_stale_fill_does_not_move_the_average() {
    let ledger = OrderLedger::new();
    working(&ledger, "o1", OrderSide::Buy, 10.0);
    ledger.apply_fill("o1", 4.0, 100.0, 4.0, OrderStatus::PartiallyFilled);
    // The same report again, and an older one (lower cumulative quantity).
    ledger.apply_fill("o1", 4.0, 100.0, 4.0, OrderStatus::PartiallyFilled);
    ledger.apply_fill("o1", 1.0, 500.0, 2.0, OrderStatus::PartiallyFilled);
    let row = ledger.get("o1").unwrap();
    assert_eq!(row.filled_qty(), 4.0);
    assert_eq!(row.avg_fill_price(), Some(100.0));
}

#[test]
fn a_first_report_larger_than_its_fill_prices_the_unseen_quantity_at_that_fill() {
    // The ledger missed earlier fills (cum 5 on the first report it sees): all 5 units are
    // booked at the only price known.
    let ledger = OrderLedger::new();
    working(&ledger, "o1", OrderSide::Buy, 10.0);
    ledger.apply_fill("o1", 2.0, 100.0, 5.0, OrderStatus::PartiallyFilled);
    let row = ledger.get("o1").unwrap();
    assert_eq!(row.filled_qty(), 5.0);
    assert_eq!(row.avg_fill_price(), Some(100.0));
}
