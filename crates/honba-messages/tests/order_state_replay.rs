//! Replays a scripted event sequence through `OrderState` using only the public API.

use honba_messages::{IllegalTransition, OrderEvent, OrderState, OrderStatus};

fn replay(events: &[OrderEvent]) -> (OrderState, Vec<Result<bool, IllegalTransition>>) {
    let mut s = OrderState::new();
    let results = events.iter().map(|e| s.apply(e)).collect();
    (s, results)
}

#[test]
fn partial_fill_then_cancel_race() {
    let (s, r) = replay(&[
        OrderEvent::Submitted { quantity: 3.0 },
        OrderEvent::Accepted,
        OrderEvent::Fill {
            last_qty: 1.0,
            complete: false,
        },
        OrderEvent::CancelRequested,
        OrderEvent::CancelRequested,
        OrderEvent::Cancelled,
        OrderEvent::Cancelled,
        OrderEvent::Fill {
            last_qty: 1.0,
            complete: false,
        },
    ]);
    let ok: Vec<_> = r.iter().map(|x| x.as_ref().ok().copied()).collect();
    assert_eq!(
        ok,
        vec![
            Some(true),
            Some(true),
            Some(true),
            Some(true),
            Some(false),
            Some(true),
            Some(false),
            None
        ]
    );
    assert!(matches!(r[7], Err(IllegalTransition::Transition { .. })));
    assert_eq!(s.status, OrderStatus::Cancelled);
    assert_eq!(s.filled_qty, 1.0);
    assert!(!s.cancel_requested);
}

#[test]
fn l1_path_without_ack_fills_to_completion() {
    let (s, r) = replay(&[
        OrderEvent::Submitted { quantity: 2.0 },
        OrderEvent::Fill {
            last_qty: 1.0,
            complete: false,
        },
        OrderEvent::Accepted, // late ack: no-op
        OrderEvent::Fill {
            last_qty: 1.0,
            complete: true,
        },
        OrderEvent::Fill {
            last_qty: 1.0,
            complete: true,
        }, // re-delivery
    ]);
    assert_eq!(r[2], Ok(false));
    assert!(matches!(r[4], Err(IllegalTransition::Overfill { .. })));
    assert_eq!((s.status, s.filled_qty), (OrderStatus::Filled, 2.0));
}
