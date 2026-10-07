//! Integration: `ExecutionEvent` -> `order_event()` -> `OrderState::apply`.

use honba_entities::{Currency, ExecutionEvent, Trade};
use honba_messages::{
    Exchange, IllegalTransition, InstrumentId, OrderId, OrderSide, OrderState, OrderStatus,
    UnixNanos,
};

const OID: &str = "O-1";

fn oid() -> OrderId {
    OrderId::new(OID)
}
fn inst() -> InstrumentId {
    InstrumentId::new("X", Exchange::new("NSE"))
}
fn ts(n: u64) -> UnixNanos {
    UnixNanos::from_u64(n)
}

fn submitted(q: f64) -> ExecutionEvent {
    ExecutionEvent::Submitted {
        order_id: oid(),
        instrument_id: inst(),
        side: OrderSide::Buy,
        quantity: q,
        ts: ts(1),
    }
}
fn accepted(q: f64) -> ExecutionEvent {
    ExecutionEvent::Accepted {
        order_id: oid(),
        instrument_id: inst(),
        side: OrderSide::Buy,
        quantity: q,
        venue_order_id: None,
        ts: ts(2),
    }
}
fn fill(qty: f64, cum: f64, complete: bool) -> ExecutionEvent {
    let t = Trade::new(
        oid(),
        inst(),
        OrderSide::Buy,
        qty,
        10.0,
        Currency::Inr,
        ts(3),
        ts(3),
    );
    ExecutionEvent::Fill {
        trade: t,
        cum_qty: cum,
        complete,
        venue_order_id: None,
    }
}
fn tail(kind: &str, q: f64) -> ExecutionEvent {
    let (order_id, instrument_id, side, venue_order_id) = (oid(), inst(), OrderSide::Buy, None);
    match kind {
        "rejected" => ExecutionEvent::Rejected {
            order_id,
            instrument_id,
            side,
            quantity: q,
            reason: "rms".into(),
            venue_order_id,
            ts: ts(4),
        },
        "cancelled" => ExecutionEvent::Cancelled {
            order_id,
            instrument_id,
            side,
            quantity: q,
            venue_order_id,
            ts: ts(4),
        },
        _ => ExecutionEvent::Expired {
            order_id,
            instrument_id,
            side,
            quantity: q,
            venue_order_id,
            ts: ts(4),
        },
    }
}

fn run(events: &[ExecutionEvent]) -> OrderState {
    let mut st = OrderState::new();
    for ev in events {
        assert_eq!(ev.order_id(), &oid());
        st.apply(&ev.order_event()).expect("legal");
    }
    st
}

#[test]
fn market_flow_ends_filled() {
    let st = run(&[submitted(4.0), fill(4.0, 4.0, true)]);
    assert_eq!(st.status, OrderStatus::Filled);
    assert_eq!(st.filled_qty, 4.0);
}

#[test]
fn partial_then_complete_ends_filled() {
    let mid = run(&[submitted(4.0), accepted(4.0), fill(1.0, 1.0, false)]);
    assert_eq!(mid.status, OrderStatus::PartiallyFilled);
    let st = run(&[
        submitted(4.0),
        accepted(4.0),
        fill(1.0, 1.0, false),
        fill(3.0, 4.0, true),
    ]);
    assert_eq!(st.status, OrderStatus::Filled);
}

#[test]
fn reject_flows() {
    assert_eq!(
        run(&[submitted(4.0), tail("rejected", 4.0)]).status,
        OrderStatus::Rejected
    );
    // Pre-gate refusal: Initialized -> Rejected.
    assert_eq!(run(&[tail("rejected", 4.0)]).status, OrderStatus::Rejected);
}

#[test]
fn cancel_flow_clears_pending_cancel() {
    let cr = ExecutionEvent::CancelRequested {
        order_id: oid(),
        ts: ts(3),
    };
    let st = run(&[submitted(4.0), accepted(4.0), cr.clone()]);
    assert!(st.cancel_requested);
    let st = run(&[submitted(4.0), accepted(4.0), cr, tail("cancelled", 4.0)]);
    assert_eq!(st.status, OrderStatus::Cancelled);
    assert!(!st.cancel_requested);
}

#[test]
fn expiry_after_partial_ends_expired() {
    let st = run(&[submitted(4.0), fill(1.0, 1.0, false), tail("expired", 3.0)]);
    assert_eq!(st.status, OrderStatus::Expired);
    assert_eq!(st.filled_qty, 1.0);
}

#[test]
fn complete_flag_disagreeing_with_fsm_is_a_fill_mismatch() {
    let mut st = run(&[submitted(4.0)]);
    let err = st.apply(&fill(1.0, 1.0, true).order_event()).unwrap_err();
    assert!(
        matches!(err, IllegalTransition::FillMismatch { .. }),
        "{err:?}"
    );
    assert_eq!(st.status, OrderStatus::Submitted);
}
