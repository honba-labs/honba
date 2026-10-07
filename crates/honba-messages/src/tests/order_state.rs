//! Unit tests for `crate::orders::state` (ADR 0019 transition table).

use crate::orders::order::OrderStatus::{self, *};
use crate::orders::state::*;

const QTY: f64 = 3.0;

/// How a table cell is expected to resolve.
#[derive(Clone, Copy, Debug)]
enum Cell {
    /// Illegal.
    X,
    /// Duplicate no-op.
    Noop,
    /// Transitions to `(status, cancel_requested)`.
    To(OrderStatus, bool),
}
use Cell::{Noop, To, X};

/// Rows: `(status, cancel_requested)`; columns follow `OrderEventKind::ALL`:
/// Submitted, Accepted, Rejected, Fill, CancelRequested, Cancelled, Expired.
/// The Fill column is exercised with a non-completing fill of 1 of 3.
#[rustfmt::skip]
fn table() -> Vec<((OrderStatus, bool), [Cell; 7])> {
    vec![
        ((Initialized, false),     [To(Submitted, false), X, To(Rejected, false), X, X, X, X]),
        ((Submitted, false),       [X, To(Accepted, false), To(Rejected, false), To(PartiallyFilled, false), To(Submitted, true), To(Cancelled, false), To(Expired, false)]),
        ((Submitted, true),        [X, To(Accepted, true), To(Rejected, false), To(PartiallyFilled, true), Noop, To(Cancelled, false), To(Expired, false)]),
        ((Accepted, false),        [X, Noop, To(Rejected, false), To(PartiallyFilled, false), To(Accepted, true), To(Cancelled, false), To(Expired, false)]),
        ((Accepted, true),         [X, Noop, To(Rejected, false), To(PartiallyFilled, true), Noop, To(Cancelled, false), To(Expired, false)]),
        ((PartiallyFilled, false), [X, Noop, To(Rejected, false), To(PartiallyFilled, false), To(PartiallyFilled, true), To(Cancelled, false), To(Expired, false)]),
        ((PartiallyFilled, true),  [X, Noop, To(Rejected, false), To(PartiallyFilled, true), Noop, To(Cancelled, false), To(Expired, false)]),
        ((Filled, false),          [X, X, X, X, X, X, X]),
        ((Cancelled, false),       [X, X, X, X, X, Noop, X]),
        ((Rejected, false),        [X, X, Noop, X, X, X, X]),
        ((Expired, false),         [X, X, X, X, X, X, Noop]),
    ]
}

fn event(kind: OrderEventKind) -> OrderEvent {
    match kind {
        OrderEventKind::Submitted => OrderEvent::Submitted { quantity: QTY },
        OrderEventKind::Accepted => OrderEvent::Accepted,
        OrderEventKind::Rejected => OrderEvent::Rejected,
        OrderEventKind::Fill => OrderEvent::Fill {
            last_qty: 1.0,
            complete: false,
        },
        OrderEventKind::CancelRequested => OrderEvent::CancelRequested,
        OrderEventKind::Cancelled => OrderEvent::Cancelled,
        OrderEventKind::Expired => OrderEvent::Expired,
    }
}

fn state_at(status: OrderStatus, cr: bool) -> OrderState {
    let (quantity, filled_qty) = match status {
        Initialized => (0.0, 0.0),
        PartiallyFilled => (QTY, 1.0),
        Filled => (QTY, QTY),
        _ => (QTY, 0.0),
    };
    OrderState {
        status,
        quantity,
        filled_qty,
        cancel_requested: cr,
    }
}

#[test]
fn transition_table_exhaustive() {
    let rows = table();
    assert_eq!(rows.len(), 11);
    assert_eq!(OrderEventKind::ALL.len(), 7);
    for ((status, cr), cells) in rows {
        for (kind, cell) in OrderEventKind::ALL.iter().zip(cells) {
            let mut s = state_at(status, cr);
            let before = s.clone();
            let got = s.apply(&event(*kind));
            let ctx = format!("{status:?}+cr={cr} x {kind:?}");
            match cell {
                X => {
                    assert!(got.is_err(), "{ctx}: {got:?}");
                    assert_eq!(s, before, "{ctx}: state must be untouched on Err");
                    assert!(!OrderState::can_transition((status, cr), *kind), "{ctx}");
                }
                Noop => {
                    assert_eq!(got, Ok(false), "{ctx}");
                    assert_eq!(s, before, "{ctx}");
                    assert!(OrderState::can_transition((status, cr), *kind), "{ctx}");
                }
                To(st, c) => {
                    assert_eq!(got, Ok(true), "{ctx}");
                    assert_eq!((s.status, s.cancel_requested), (st, c), "{ctx}");
                    assert!(OrderState::can_transition((status, cr), *kind), "{ctx}");
                }
            }
        }
    }
}

#[test]
fn illegal_transition_is_typed() {
    let mut s = OrderState::new();
    assert_eq!(
        s.apply(&OrderEvent::Accepted),
        Err(IllegalTransition::Transition {
            status: Initialized,
            cancel_requested: false,
            event: OrderEventKind::Accepted
        })
    );
    assert!(IllegalTransition::Transition {
        status: Initialized,
        cancel_requested: false,
        event: OrderEventKind::Accepted
    }
    .to_string()
    .contains("accepted"));
}

#[test]
fn new_state_is_initialized_and_empty() {
    let s = OrderState::new();
    assert_eq!(
        (s.status, s.quantity, s.filled_qty, s.cancel_requested),
        (Initialized, 0.0, 0.0, false)
    );
    assert_eq!(s, OrderState::default());
}

#[test]
fn submitted_sets_quantity_and_validates_it() {
    let mut s = OrderState::new();
    assert_eq!(s.apply(&OrderEvent::Submitted { quantity: 5.0 }), Ok(true));
    assert_eq!(s.quantity, 5.0);
    for bad in [0.0, -1.0, f64::NAN, f64::INFINITY] {
        let mut s = OrderState::new();
        assert!(matches!(
            s.apply(&OrderEvent::Submitted { quantity: bad }),
            Err(IllegalTransition::InvalidQuantity { .. })
        ));
        assert_eq!(s, OrderState::new());
    }
}

#[test]
fn terminal_is_final() {
    for terminal in [Filled, Cancelled, Rejected, Expired] {
        for kind in OrderEventKind::ALL {
            let mut s = state_at(terminal, false);
            let before = s.clone();
            let r = s.apply(&event(*kind));
            assert_eq!(s, before);
            // Only the exact duplicate of the terminal event may succeed (as a no-op).
            assert!(matches!(r, Err(_) | Ok(false)), "{terminal:?} {kind:?}");
        }
    }
}

#[test]
fn duplicate_terminal_is_noop() {
    let cases = [
        (Cancelled, OrderEvent::Cancelled),
        (Rejected, OrderEvent::Rejected),
        (Expired, OrderEvent::Expired),
    ];
    for (status, ev) in cases {
        let mut s = state_at(status, false);
        assert_eq!(s.apply(&ev), Ok(false));
        assert_eq!(s.status, status);
    }
    // Filled has no duplicate no-op: a repeated completing fill is an overfill.
    let mut s = state_at(Filled, false);
    assert!(matches!(
        s.apply(&OrderEvent::Fill {
            last_qty: 1.0,
            complete: true
        }),
        Err(IllegalTransition::Overfill { .. })
    ));
}

#[test]
fn overfill_rejected() {
    let mut s = state_at(Accepted, false);
    let r = s.apply(&OrderEvent::Fill {
        last_qty: 3.5,
        complete: true,
    });
    assert_eq!(
        r,
        Err(IllegalTransition::Overfill {
            quantity: 3.0,
            filled_qty: 0.0,
            last_qty: 3.5
        })
    );
    assert_eq!(s, state_at(Accepted, false));
    // Within the 1e-9 tolerance is not an overfill.
    let mut s = state_at(Accepted, false);
    assert_eq!(
        s.apply(&OrderEvent::Fill {
            last_qty: 3.0 + 5e-10,
            complete: true
        }),
        Ok(true)
    );
    assert_eq!(s.status, Filled);
}

#[test]
fn fill_mismatch() {
    let mut s = state_at(Accepted, false);
    assert_eq!(
        s.apply(&OrderEvent::Fill {
            last_qty: 3.0,
            complete: false
        }),
        Err(IllegalTransition::FillMismatch {
            claimed_complete: false,
            derived_complete: true
        })
    );
    assert_eq!(
        s.apply(&OrderEvent::Fill {
            last_qty: 1.0,
            complete: true
        }),
        Err(IllegalTransition::FillMismatch {
            claimed_complete: true,
            derived_complete: false
        })
    );
    assert_eq!(s, state_at(Accepted, false));
}

#[test]
fn invalid_fill_quantity() {
    for bad in [0.0, -1.0, f64::NAN, f64::INFINITY] {
        let mut s = state_at(Accepted, false);
        assert!(matches!(
            s.apply(&OrderEvent::Fill {
                last_qty: bad,
                complete: false
            }),
            Err(IllegalTransition::InvalidQuantity { .. })
        ));
        assert_eq!(s, state_at(Accepted, false));
    }
}

#[test]
fn partial_fill_sequence() {
    let mut s = OrderState::new();
    s.apply(&OrderEvent::Submitted { quantity: 3.0 }).unwrap();
    s.apply(&OrderEvent::Accepted).unwrap();
    for (i, expect) in [
        (1.0, PartiallyFilled),
        (1.0, PartiallyFilled),
        (1.0, Filled),
    ]
    .into_iter()
    .enumerate()
    {
        let complete = expect.1 == Filled;
        assert_eq!(
            s.apply(&OrderEvent::Fill {
                last_qty: expect.0,
                complete
            }),
            Ok(true),
            "fill {i}"
        );
        assert_eq!(s.status, expect.1);
        assert!((s.filled_qty - (i as f64 + 1.0)).abs() < 1e-12);
    }
}

#[test]
fn cancel_requested_set_once() {
    let mut s = state_at(Accepted, false);
    assert_eq!(s.apply(&OrderEvent::CancelRequested), Ok(true));
    assert!(s.cancel_requested);
    assert_eq!(s.apply(&OrderEvent::CancelRequested), Ok(false));
    assert!(s.cancel_requested);
}

#[test]
fn cancel_requested_cleared_on_terminal() {
    for ev in [
        OrderEvent::Cancelled,
        OrderEvent::Rejected,
        OrderEvent::Expired,
        OrderEvent::Fill {
            last_qty: 2.0,
            complete: true,
        },
    ] {
        let mut s = state_at(PartiallyFilled, true);
        assert_eq!(s.apply(&ev), Ok(true));
        assert!(!s.cancel_requested, "{ev:?}");
    }
}

#[test]
fn partial_fill_keeps_cancel_requested() {
    let mut s = state_at(Accepted, true);
    s.apply(&OrderEvent::Fill {
        last_qty: 1.0,
        complete: false,
    })
    .unwrap();
    assert_eq!((s.status, s.cancel_requested), (PartiallyFilled, true));
}

#[test]
fn expired_transitions() {
    for status in [Submitted, Accepted, PartiallyFilled] {
        let mut s = state_at(status, false);
        assert_eq!(s.apply(&OrderEvent::Expired), Ok(true));
        assert_eq!(s.status, Expired);
    }
    for status in [Initialized, Filled, Cancelled, Rejected] {
        assert!(state_at(status, false).apply(&OrderEvent::Expired).is_err());
    }
}

#[test]
fn event_kind_mapping() {
    for kind in OrderEventKind::ALL {
        assert_eq!(event(*kind).kind(), *kind);
    }
}

#[test]
fn serde_round_trip() {
    for kind in OrderEventKind::ALL {
        let e = event(*kind);
        let j = serde_json::to_string(&e).unwrap();
        assert_eq!(serde_json::from_str::<OrderEvent>(&j).unwrap(), e, "{j}");
    }
    let s = state_at(PartiallyFilled, true);
    let j = serde_json::to_string(&s).unwrap();
    assert_eq!(serde_json::from_str::<OrderState>(&j).unwrap(), s);
    let e = IllegalTransition::Overfill {
        quantity: 1.0,
        filled_qty: 1.0,
        last_qty: 1.0,
    };
    assert!(!e.to_string().is_empty());
}
