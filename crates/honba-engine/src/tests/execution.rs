//! Unit tests for `crate::execution`.

use honba_entities::Trade;
use honba_messages::{OrderId, OrderSide, UnixNanos};

use super::any_instrument;

use crate::{ExecutionEngine, OrderRejection, Result};

/// An engine written before the rejection queue: only the required methods.
struct Legacy;

impl ExecutionEngine for Legacy {
    fn submit(&mut self, _order: honba_messages::Order) -> Result<()> {
        Ok(())
    }
    fn cancel(&mut self, _order_id: &str, _now: UnixNanos) -> Result<()> {
        Ok(())
    }
    fn drain_fills(&mut self) -> Result<Vec<Trade>> {
        Ok(Vec::new())
    }
}

#[test]
fn engines_without_a_rejection_queue_report_none() {
    assert!(Legacy.drain_rejections().unwrap().is_empty());
}

#[test]
fn rejection_carries_the_unfilled_remainder() {
    let r = OrderRejection::rejected(
        OrderId::new("s-0"),
        any_instrument(),
        OrderSide::Buy,
        6.0,
        "insufficient_funds",
        UnixNanos::from_u64(7),
    );
    assert_eq!(r.order_id.as_str(), "s-0");
    assert_eq!(r.quantity, 6.0);
    assert_eq!(r.reason, "insufficient_funds");
    assert_eq!(r.ts.as_u64(), 7);
    assert!(!r.cancelled);
}

#[test]
fn cancellation_is_a_rejection_with_the_cancelled_flag() {
    let r = OrderRejection::cancelled(
        OrderId::new("s-1"),
        any_instrument(),
        OrderSide::Sell,
        3.0,
        UnixNanos::from_u64(9),
    );
    assert!(r.cancelled);
    assert_eq!(r.reason, OrderRejection::CANCELLED);
    assert_eq!(r.reason, "cancelled");
    assert_eq!(r.side, OrderSide::Sell);
}
