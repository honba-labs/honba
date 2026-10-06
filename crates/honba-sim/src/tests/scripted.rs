//! Unit tests for `crate::scripted`.

use honba_engine::{ExecutionEngine, OrderRejection};
use honba_messages::OrderSide;

use super::{any_instrument, market};
use crate::{Behavior, ScriptedExecution};

#[test]
fn unscripted_orders_fill_in_full_at_the_price() {
    let mut exec = ScriptedExecution::new(10.0);
    exec.submit(market("O-1", OrderSide::Buy, 4.0, 5)).unwrap();
    let fills = exec.drain_fills().unwrap();
    assert_eq!(fills.len(), 1);
    assert_eq!(fills[0].quantity(), 4.0);
    assert_eq!(fills[0].price(), 10.0);
    assert_eq!(fills[0].ts_event().as_u64(), 5);
    assert!(exec.drain_rejections().unwrap().is_empty());
}

#[test]
fn a_rejected_order_reports_its_whole_quantity() {
    let mut exec = ScriptedExecution::new(10.0).with("O-1", Behavior::reject("no_position"));
    exec.submit(market("O-1", OrderSide::Sell, 5.0, 3)).unwrap();
    assert!(exec.drain_fills().unwrap().is_empty());
    let rejections = exec.drain_rejections().unwrap();
    assert_eq!(rejections.len(), 1);
    let r = &rejections[0];
    assert_eq!(
        (r.order_id.as_str(), r.quantity, r.reason.as_str()),
        ("O-1", 5.0, "no_position")
    );
    assert_eq!(
        (r.side, r.cancelled, r.ts.as_u64()),
        (OrderSide::Sell, false, 3)
    );
    assert_eq!(r.instrument_id, any_instrument());
}

#[test]
fn a_partial_fill_rejects_only_the_remainder() {
    let mut exec =
        ScriptedExecution::new(10.0).with("O-1", Behavior::partial(4.0, "insufficient_funds"));
    exec.submit(market("O-1", OrderSide::Buy, 10.0, 1)).unwrap();
    assert_eq!(exec.drain_fills().unwrap()[0].quantity(), 4.0);
    assert_eq!(exec.drain_rejections().unwrap()[0].quantity, 6.0);
}

#[test]
fn drains_clear_the_queues() {
    let mut exec = ScriptedExecution::new(10.0).with("O-1", Behavior::reject("x"));
    exec.submit(market("O-1", OrderSide::Buy, 1.0, 1)).unwrap();
    assert_eq!(exec.drain_rejections().unwrap().len(), 1);
    assert!(exec.drain_rejections().unwrap().is_empty());
}

#[test]
fn cancel_reports_the_working_remainder_once() {
    let mut exec = ScriptedExecution::new(10.0).with("O-1", Behavior::Hold);
    exec.submit(market("O-1", OrderSide::Buy, 7.0, 2)).unwrap();
    assert_eq!(exec.working_orders(), vec!["O-1".to_string()]);
    assert!(exec.drain_rejections().unwrap().is_empty());

    exec.cancel("O-1").unwrap();
    let got = exec.drain_rejections().unwrap();
    assert_eq!(
        got,
        vec![OrderRejection::cancelled(
            honba_messages::OrderId::new("O-1"),
            any_instrument(),
            OrderSide::Buy,
            7.0,
            honba_messages::UnixNanos::from_u64(2),
        )]
    );
    assert!(exec.working_orders().is_empty());

    exec.cancel("O-1").unwrap();
    assert!(exec.drain_rejections().unwrap().is_empty());
}

#[test]
fn cancelling_a_finished_or_unknown_order_is_a_no_op() {
    let mut exec = ScriptedExecution::new(10.0);
    exec.submit(market("O-1", OrderSide::Buy, 1.0, 1)).unwrap();
    exec.cancel("O-1").unwrap();
    exec.cancel("nope").unwrap();
    assert!(exec.drain_rejections().unwrap().is_empty());
}
