//! Execution engine trait.

use honba_entities::Trade;
use honba_messages::{InstrumentId, Order, OrderId, OrderSide, UnixNanos};

use crate::error::Result;

/// An order, or the part of one, that will never fill (ADR 008, decision 13).
///
/// Either the venue or engine refused it, or it was cancelled: a cancellation is
/// exactly a rejection whose `reason` is [`OrderRejection::CANCELLED`], so
/// [`Self::is_cancelled`] is derived from the reason and the two cannot disagree.
/// `quantity` is the unfilled remainder: the amount the strategy's context must
/// release, so a partly filled order reports only what is left. The Python
/// mirror is `honba.strategies.execution.OrderRejection`.
#[derive(Clone, Debug, PartialEq)]
pub struct OrderRejection {
    /// The order the remainder belongs to.
    pub order_id: OrderId,
    /// The instrument of the order.
    pub instrument_id: InstrumentId,
    /// The side of the order.
    pub side: OrderSide,
    /// The quantity that will never fill.
    pub quantity: f64,
    /// Why, in a stable machine-readable form (`insufficient_funds`,
    /// `no_position`, `cancelled`, ...).
    pub reason: String,
    /// The engine's time for the event.
    pub ts: UnixNanos,
}

impl OrderRejection {
    /// The reason string of a cancellation.
    pub const CANCELLED: &'static str = "cancelled";

    /// A rejection of `quantity` of an order, for `reason`.
    pub fn rejected(
        order_id: OrderId,
        instrument_id: InstrumentId,
        side: OrderSide,
        quantity: f64,
        reason: impl Into<String>,
        ts: UnixNanos,
    ) -> Self {
        Self {
            order_id,
            instrument_id,
            side,
            quantity,
            reason: reason.into(),
            ts,
        }
    }

    /// True for a cancellation (reason [`Self::CANCELLED`]), false for a rejection.
    pub fn is_cancelled(&self) -> bool {
        self.reason == Self::CANCELLED
    }

    /// A cancellation of the unfilled `quantity` of an order.
    pub fn cancelled(
        order_id: OrderId,
        instrument_id: InstrumentId,
        side: OrderSide,
        quantity: f64,
        ts: UnixNanos,
    ) -> Self {
        Self::rejected(order_id, instrument_id, side, quantity, Self::CANCELLED, ts)
    }
}

/// Receives orders and produces fills.
///
/// Implementations range from a paper-trading simulator (fills against
/// observed prices) to a live adapter (routes to a broker).
pub trait ExecutionEngine: Send {
    /// Submits an order.
    fn submit(&mut self, order: Order) -> Result<()>;

    /// Cancels an order by id, at time `now`.
    ///
    /// An engine holding the order reports the unfilled remainder through
    /// [`Self::drain_rejections`] as a cancellation (`is_cancelled()`). Cancelling an
    /// unknown or finished order is a no-op. The cancellation is stamped with
    /// `now`, the engine time at which the cancel is processed, not the
    /// order's original `ts_event` (ADR 008, decision 13 addendum).
    ///
    /// Fills the engine has produced but not yet drained are not an ordering
    /// hazard: the cancelled remainder excludes whatever already filled, and
    /// the context's pending count only ever decreases by `filled + released`,
    /// which sums to the ordered quantity whichever is booked first. The
    /// remainder must never include a quantity that is also in a fill.
    fn cancel(&mut self, order_id: &str, now: UnixNanos) -> Result<()>;

    /// Drains any fills produced since the last call.
    fn drain_fills(&mut self) -> Result<Vec<Trade>>;

    /// Drains the orders (or parts of orders) that will never fill, rejected
    /// or cancelled since the last call.
    ///
    /// The default reports none: an engine that fills everything it accepts
    /// needs no override, so engines written before this method compile
    /// unchanged. Once every working order is cancelled, `filled + released ==
    /// ordered` for each order.
    fn drain_rejections(&mut self) -> Result<Vec<OrderRejection>> {
        Ok(Vec::new())
    }
}
