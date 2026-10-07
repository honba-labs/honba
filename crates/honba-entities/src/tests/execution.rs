//! Unit tests for `crate::execution`.

use honba_messages::{OrderEvent, OrderId, OrderSide, UnixNanos, VenueOrderId};

use super::any_instrument;
use crate::{Currency, ExecutionEvent, Trade};

fn oid() -> OrderId {
    OrderId::new("O-1")
}

fn ts() -> UnixNanos {
    UnixNanos::from_u64(5)
}

fn fill(qty: f64, complete: bool) -> ExecutionEvent {
    let t = Trade::new(
        oid(),
        any_instrument(),
        OrderSide::Buy,
        qty,
        10.0,
        Currency::Inr,
        ts(),
        ts(),
    );
    ExecutionEvent::Fill {
        trade: t,
        cum_qty: qty,
        complete,
        venue_order_id: None,
    }
}

fn all_variants() -> Vec<ExecutionEvent> {
    let (i, s) = (any_instrument(), OrderSide::Buy);
    vec![
        ExecutionEvent::Submitted {
            order_id: oid(),
            instrument_id: i.clone(),
            side: s,
            quantity: 3.0,
            ts: ts(),
        },
        ExecutionEvent::Accepted {
            order_id: oid(),
            instrument_id: i.clone(),
            side: s,
            quantity: 3.0,
            venue_order_id: Some(VenueOrderId::new("V-1")),
            ts: ts(),
        },
        ExecutionEvent::Rejected {
            order_id: oid(),
            instrument_id: i.clone(),
            side: s,
            quantity: 3.0,
            reason: "rms".into(),
            venue_order_id: None,
            ts: ts(),
        },
        fill(1.0, false),
        ExecutionEvent::CancelRequested {
            order_id: oid(),
            ts: ts(),
        },
        ExecutionEvent::Cancelled {
            order_id: oid(),
            instrument_id: i.clone(),
            side: s,
            quantity: 2.0,
            venue_order_id: None,
            ts: ts(),
        },
        ExecutionEvent::Expired {
            order_id: oid(),
            instrument_id: i,
            side: s,
            quantity: 2.0,
            venue_order_id: None,
            ts: ts(),
        },
    ]
}

#[test]
fn order_id_is_available_on_every_variant() {
    for ev in all_variants() {
        assert_eq!(ev.order_id(), &oid(), "{ev:?}");
    }
}

#[test]
fn submitted_projects_its_quantity() {
    assert_eq!(
        all_variants()[0].order_event(),
        OrderEvent::Submitted { quantity: 3.0 }
    );
}

#[test]
fn fill_projects_trade_quantity_and_complete_flag() {
    assert_eq!(
        fill(1.5, false).order_event(),
        OrderEvent::Fill {
            last_qty: 1.5,
            complete: false
        }
    );
    assert_eq!(
        fill(2.0, true).order_event(),
        OrderEvent::Fill {
            last_qty: 2.0,
            complete: true
        }
    );
}

#[test]
fn remaining_variants_project_to_their_verbs() {
    let v = all_variants();
    assert_eq!(v[1].order_event(), OrderEvent::Accepted);
    assert_eq!(v[2].order_event(), OrderEvent::Rejected);
    assert_eq!(v[4].order_event(), OrderEvent::CancelRequested);
    assert_eq!(v[5].order_event(), OrderEvent::Cancelled);
    assert_eq!(v[6].order_event(), OrderEvent::Expired);
}
