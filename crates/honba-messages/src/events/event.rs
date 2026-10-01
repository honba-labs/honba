//! The [`Event`] enum and the [`Message`] envelope.

use serde::{Deserialize, Serialize};

use crate::events::timestamp::UnixNanos;
use crate::identifiers::OrderId;
use crate::market_data::{Bar, QuoteTick, TradeTick};
use crate::orders::Order;
use crate::validation::{deserialize_positive, finite, serialize_finite};

fn last_qty<'de, D: serde::Deserializer<'de>>(d: D) -> Result<f64, D::Error> {
    deserialize_positive("last_qty", d)
}

fn last_px<'de, D: serde::Deserializer<'de>>(d: D) -> Result<f64, D::Error> {
    let value: f64 = Deserialize::deserialize(d)?;
    finite("last_px", value).map_err(serde::de::Error::custom)
}

/// Version of the JSON wire contract carried by every [`Message`].
///
/// Bump it on any breaking change to the serialized form of a message type,
/// together with the golden vectors in `schema/golden/` and the Python
/// constant `honba.entities.wire.SCHEMA_VERSION` (see ADR 006).
pub const SCHEMA_VERSION: u32 = 1;

/// Any typed event that can flow through the Honba event kernel.
///
/// Serialized as an internally tagged JSON object: `{"type": "order_filled", ...}`.
///
/// ```
/// use honba_messages::Event;
///
/// fn is_market_data(ev: &Event) -> bool {
///     matches!(ev, Event::Quote(_) | Event::Trade(_) | Event::Bar(_))
/// }
/// ```
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
#[serde(tag = "type", rename_all = "snake_case", deny_unknown_fields)]
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
        order_id: OrderId,
        /// The venue timestamp at which acceptance occurred.
        ts_event: UnixNanos,
    },
    /// The venue rejected an order.
    OrderRejected {
        /// The client order identifier.
        order_id: OrderId,
        /// A human-readable rejection reason.
        reason: String,
        /// The venue timestamp at which rejection occurred.
        ts_event: UnixNanos,
    },
    /// An order received a (possibly partial) fill.
    OrderFilled {
        /// The client order identifier.
        order_id: OrderId,
        /// The quantity filled in this event (finite, `> 0`).
        #[serde(serialize_with = "serialize_finite", deserialize_with = "last_qty")]
        last_qty: f64,
        /// The price at which this fill occurred (finite).
        #[serde(serialize_with = "serialize_finite", deserialize_with = "last_px")]
        last_px: f64,
        /// The venue timestamp at which the fill occurred.
        ts_event: UnixNanos,
    },
    /// An order was cancelled.
    OrderCancelled {
        /// The client order identifier.
        order_id: OrderId,
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
/// This is the versioned unit of the wire contract: its JSON form is
/// `{"schema_version": 1, "event": {...}, "ts_init": n}`, and deserializing a
/// message with any other `schema_version` fails.
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
/// assert_eq!(msg.schema_version(), honba_messages::SCHEMA_VERSION);
/// ```
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Message {
    schema_version: SchemaVersion,
    event: Event,
    ts_init: UnixNanos,
}

/// The `schema_version` field: always [`SCHEMA_VERSION`] once constructed.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(try_from = "u32", into = "u32")]
pub(crate) struct SchemaVersion;

impl TryFrom<u32> for SchemaVersion {
    type Error = String;

    fn try_from(value: u32) -> Result<Self, Self::Error> {
        if value == SCHEMA_VERSION {
            Ok(SchemaVersion)
        } else {
            Err(format!(
                "unsupported schema_version {value}; this build reads {SCHEMA_VERSION}"
            ))
        }
    }
}

impl From<SchemaVersion> for u32 {
    fn from(_: SchemaVersion) -> Self {
        SCHEMA_VERSION
    }
}

impl Message {
    /// Wraps an event with the time Honba created the envelope.
    pub fn new(event: Event, ts_init: UnixNanos) -> Self {
        Self {
            schema_version: SchemaVersion,
            event,
            ts_init,
        }
    }

    /// Returns the wire-contract version of this message.
    pub fn schema_version(&self) -> u32 {
        self.schema_version.into()
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
