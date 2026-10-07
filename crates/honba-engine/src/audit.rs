//! The engine's audit trail.

use honba_messages::{IllegalTransition, OrderEventKind, VenueOrderId};

use crate::state::TradingState;

/// One entry of an [`AuditLog`].
///
/// `seq` is monotonic and starts at zero, so a log can be compared and
/// diffed across runs without depending on wall-clock time.
#[derive(Debug, Clone, PartialEq)]
pub struct AuditRecord {
    /// The position of this record in the log, counting from zero.
    pub seq: u64,
    /// What happened.
    pub kind: AuditKind,
}

/// What the engine did, and in what order it did it.
///
/// The engine records one entry per dispatched message and one entry per
/// command it applied, so the log is the ordered account of a run: what was
/// dispatched, what was submitted, refused, cancelled or filled, and how the
/// trading state moved.
#[derive(Debug, Clone, PartialEq)]
pub enum AuditKind {
    /// A queued message was popped and dispatched to the handlers.
    EventDispatched {
        /// `ts_event` of the dispatched message.
        ts_event: u64,
    },
    /// The execution sink accepted an order.
    OrderSubmitted {
        /// The client order identifier.
        order_id: String,
        /// The instrument, rendered as `"{symbol}.{exchange}"`.
        instrument: String,
        /// The side, rendered as `"buy"`, `"sell"` or `"no_order_side"`.
        side: String,
    },
    /// The engine refused to submit an order (a pre-gate refusal, ADR 0019
    /// decision 5); `reason` is the `ErrorCode` wire spelling, also carried by
    /// the `ExecutionEvent::Rejected` the engine enqueues.
    OrderRejected {
        /// The client order identifier.
        order_id: String,
        /// Why it was refused.
        reason: String,
    },
    /// A cancel was asked for and routed to the execution sink (formerly
    /// `OrderCancelled`, which never meant the venue had cancelled). Recorded
    /// once per order: repeated cancels are no-ops (ADR 0019 decision 6).
    CancelRequested {
        /// The client order identifier.
        order_id: String,
    },
    /// The execution sink reported a non-fill lifecycle event (accepted,
    /// rejected, cancelled, expired) that the engine applied and translated.
    /// Fills are [`AuditKind::FillProduced`]; the submitter's own
    /// `Submitted`/`CancelRequested` and pre-gate rejections have their own
    /// records.
    OrderLifecycle {
        /// The client order identifier.
        order_id: String,
        /// What happened.
        event: OrderEventKind,
        /// The kernel clock value stamped on the translated message.
        ts_event: u64,
    },
    /// An event the order-state machine refused: the state was not changed
    /// and no message was emitted (an illegal fill is still booked into the
    /// position map). In sims and tests this is a bug; live, it is flagged for
    /// reconciliation (E2-S7).
    IllegalTransition {
        /// The client order identifier.
        order_id: String,
        /// Why the transition was refused.
        error: IllegalTransition,
    },
    /// An event named a different venue order id than the one first recorded
    /// for the order; the recorded one is kept (ADR 0019 decision 3).
    VenueOrderIdDrift {
        /// The client order identifier.
        order_id: String,
        /// The id recorded first.
        recorded: VenueOrderId,
        /// The different id the event carried.
        received: VenueOrderId,
    },
    /// The execution sink produced a fill, which the engine turned back into
    /// an [`Event::OrderPartiallyFilled`](honba_messages::Event::OrderPartiallyFilled)
    /// or [`Event::OrderFilled`](honba_messages::Event::OrderFilled) message.
    FillProduced {
        /// The client order identifier.
        order_id: String,
        /// The filled quantity.
        quantity: f64,
        /// The fill price.
        price: f64,
        /// The kernel clock value stamped on the message.
        ts_event: u64,
    },
    /// The trading state changed.
    StateChanged {
        /// The state the engine left.
        from: TradingState,
        /// The state the engine entered.
        to: TradingState,
    },
}

/// An in-memory, append-only audit trail.
///
/// Recording is synchronous and order-preserving: [`AuditLog::record`] assigns
/// the next sequence number and appends, so the log reads back in exactly the
/// order things happened.
#[derive(Debug, Clone, Default, PartialEq)]
pub struct AuditLog {
    records: Vec<AuditRecord>,
    next_seq: u64,
}

impl AuditLog {
    /// Creates an empty log.
    pub fn new() -> Self {
        Self::default()
    }

    /// Appends a record and returns the sequence number assigned to it.
    ///
    /// The first record of a log gets sequence number `0`, and every later
    /// record gets the next one, whatever its [`AuditKind`].
    pub fn record(&mut self, kind: AuditKind) -> u64 {
        let seq = self.next_seq;
        self.next_seq = self.next_seq.wrapping_add(1);
        self.records.push(AuditRecord { seq, kind });
        seq
    }

    /// Returns the records in the order they were recorded.
    pub fn records(&self) -> &[AuditRecord] {
        &self.records
    }

    /// Returns the number of records.
    pub fn len(&self) -> usize {
        self.records.len()
    }

    /// Returns `true` if nothing has been recorded yet.
    pub fn is_empty(&self) -> bool {
        self.records.is_empty()
    }
}
