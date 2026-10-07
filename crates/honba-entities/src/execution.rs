//! Port-level execution events (ADR 0019 decision 4).
//!
//! `ExecutionEvent` is the single ordered vocabulary an execution engine
//! drains. It lives here, not in `honba-messages`, because a fill carries a
//! [`Trade`]. The FSM never sees a trade: [`ExecutionEvent::order_event`]
//! projects each event onto the quantity-only [`OrderEvent`].

use honba_messages::{InstrumentId, OrderEvent, OrderId, OrderSide, UnixNanos, VenueOrderId};

use crate::Trade;

/// An event drained from an execution engine, in order.
///
/// `quantity` is the open quantity on `Accepted` and the unfilled remainder
/// released on `Rejected`/`Cancelled`/`Expired`. `venue_order_id` is `None` for
/// sims and pre-gate refusals.
#[derive(Clone, Debug, PartialEq)]
#[non_exhaustive]
pub enum ExecutionEvent {
    /// Submitter-synthesised: the order was handed to the gateway.
    Submitted {
        /// The client order id.
        order_id: OrderId,
        /// The instrument.
        instrument_id: InstrumentId,
        /// The side.
        side: OrderSide,
        /// The order quantity.
        quantity: f64,
        /// Event time.
        ts: UnixNanos,
    },
    /// The venue acknowledged the order.
    Accepted {
        /// The client order id.
        order_id: OrderId,
        /// The instrument.
        instrument_id: InstrumentId,
        /// The side.
        side: OrderSide,
        /// The open quantity.
        quantity: f64,
        /// The venue's id for the order, if known.
        venue_order_id: Option<VenueOrderId>,
        /// Event time.
        ts: UnixNanos,
    },
    /// The order was rejected (pre-gate or venue).
    Rejected {
        /// The client order id.
        order_id: OrderId,
        /// The instrument.
        instrument_id: InstrumentId,
        /// The side.
        side: OrderSide,
        /// The unfilled remainder released.
        quantity: f64,
        /// Venue text or an `ErrorCode` wire spelling.
        reason: String,
        /// The venue's id for the order, if known.
        venue_order_id: Option<VenueOrderId>,
        /// Event time.
        ts: UnixNanos,
    },
    /// A fill.
    Fill {
        /// The executed trade.
        trade: Trade,
        /// Cumulative filled quantity after this fill.
        cum_qty: f64,
        /// The producer's claim that this fill completes the order.
        complete: bool,
        /// The venue's id for the order, if known.
        venue_order_id: Option<VenueOrderId>,
    },
    /// Submitter-synthesised: a cancel was asked for.
    CancelRequested {
        /// The client order id.
        order_id: OrderId,
        /// Event time.
        ts: UnixNanos,
    },
    /// The order was cancelled.
    Cancelled {
        /// The client order id.
        order_id: OrderId,
        /// The instrument.
        instrument_id: InstrumentId,
        /// The side.
        side: OrderSide,
        /// The unfilled remainder released.
        quantity: f64,
        /// The venue's id for the order, if known.
        venue_order_id: Option<VenueOrderId>,
        /// Event time.
        ts: UnixNanos,
    },
    /// The order expired by time-in-force.
    Expired {
        /// The client order id.
        order_id: OrderId,
        /// The instrument.
        instrument_id: InstrumentId,
        /// The side.
        side: OrderSide,
        /// The unfilled remainder released.
        quantity: f64,
        /// The venue's id for the order, if known.
        venue_order_id: Option<VenueOrderId>,
        /// Event time.
        ts: UnixNanos,
    },
}

impl ExecutionEvent {
    /// The client order id this event concerns.
    pub fn order_id(&self) -> &OrderId {
        match self {
            Self::Submitted { order_id, .. }
            | Self::Accepted { order_id, .. }
            | Self::Rejected { order_id, .. }
            | Self::CancelRequested { order_id, .. }
            | Self::Cancelled { order_id, .. }
            | Self::Expired { order_id, .. } => order_id,
            Self::Fill { trade, .. } => trade.order_id(),
        }
    }

    /// Projects onto the FSM vocabulary; a `Fill` maps to its trade quantity.
    pub fn order_event(&self) -> OrderEvent {
        match self {
            Self::Submitted { quantity, .. } => OrderEvent::Submitted {
                quantity: *quantity,
            },
            Self::Accepted { .. } => OrderEvent::Accepted,
            Self::Rejected { .. } => OrderEvent::Rejected,
            Self::Fill {
                trade, complete, ..
            } => OrderEvent::Fill {
                last_qty: trade.quantity(),
                complete: *complete,
            },
            Self::CancelRequested { .. } => OrderEvent::CancelRequested,
            Self::Cancelled { .. } => OrderEvent::Cancelled,
            Self::Expired { .. } => OrderEvent::Expired,
        }
    }
}
