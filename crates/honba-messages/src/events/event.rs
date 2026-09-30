//! The [`Event`] enum and the [`Message`] envelope.

use crate::events::timestamp::UnixNanos;
use crate::market_data::{Bar, QuoteTick, TradeTick};
use crate::orders::Order;

/// Any typed event that can flow through the Honba event kernel.
///
/// ```
/// use honba_messages::Event;
///
/// fn is_market_data(ev: &Event) -> bool {
///     matches!(ev, Event::Quote(_) | Event::Trade(_) | Event::Bar(_))
/// }
/// ```
#[derive(Clone, Debug, PartialEq)]
#[non_exhaustive]
pub enum Event {
    /// A quote (top-of-book) update.
    Quote(QuoteTick),
    /// A trade (last-sale) update.
    Trade(TradeTick),
    /// An aggregated bar.
    Bar(Bar),
    /// A new order that has been submitted.
    Order(Order),
    /// The venue accepted an order.
    OrderAccepted {
        /// The client order identifier.
        order_id: String,
        /// The venue timestamp at which acceptance occurred.
        ts_event: UnixNanos,
    },
    /// The venue rejected an order.
    OrderRejected {
        /// The client order identifier.
        order_id: String,
        /// A human-readable rejection reason.
        reason: String,
        /// The venue timestamp at which rejection occurred.
        ts_event: UnixNanos,
    },
    /// An order received a (possibly partial) fill.
    OrderFilled {
        /// The client order identifier.
        order_id: String,
        /// The quantity filled in this event.
        last_qty: f64,
        /// The price at which this fill occurred.
        last_px: f64,
        /// The venue timestamp at which the fill occurred.
        ts_event: UnixNanos,
    },
    /// An order was cancelled.
    OrderCancelled {
        /// The client order identifier.
        order_id: String,
        /// The venue timestamp at which cancellation occurred.
        ts_event: UnixNanos,
    },
}

impl Event {
    /// Returns the timestamp at which the venue observed this event.
    pub fn ts_event(&self) -> UnixNanos {
        match self {
            Event::Quote(q) => q.ts_event(),
            Event::Trade(t) => t.ts_event(),
            Event::Bar(b) => b.ts_event(),
            Event::Order(o) => o.ts_event(),
            Event::OrderAccepted { ts_event, .. }
            | Event::OrderRejected { ts_event, .. }
            | Event::OrderFilled { ts_event, .. }
            | Event::OrderCancelled { ts_event, .. } => *ts_event,
        }
    }

    /// Returns `true` if this event carries market data.
    pub fn is_market_data(&self) -> bool {
        matches!(self, Event::Quote(_) | Event::Trade(_) | Event::Bar(_))
    }
}

/// The envelope that carries an [`Event`] plus Honba-side metadata.
///
/// ```
/// use honba_messages::{Event, Message, UnixNanos, QuoteTick, InstrumentId, Venue};
///
/// let quote = QuoteTick::new(
///     InstrumentId::new("NIFTY50", Venue::new("NSE")),
///     22_000.0, 22_001.0, 50.0, 75.0,
///     UnixNanos::from_u64(1),
///     UnixNanos::from_u64(1),
/// );
/// let msg = Message::new(Event::Quote(quote), UnixNanos::from_u64(2));
/// assert!(msg.event().is_market_data());
/// ```
#[derive(Clone, Debug, PartialEq)]
pub struct Message {
    event: Event,
    ts_init: UnixNanos,
}

impl Message {
    /// Wraps an event with the time Honba created the envelope.
    pub fn new(event: Event, ts_init: UnixNanos) -> Self {
        Self { event, ts_init }
    }

    /// Returns the wrapped event.
    pub fn event(&self) -> &Event {
        &self.event
    }

    /// Consumes the message and returns the wrapped event.
    pub fn into_event(self) -> Event {
        self.event
    }

    /// Returns the time Honba created the envelope.
    pub fn ts_init(&self) -> UnixNanos {
        self.ts_init
    }
}
