//! Completed trade records.

use honba_messages::{InstrumentId, OrderSide, OrderId, UnixNanos};

/// A completed fill, recorded after the venue confirms execution.
///
/// ```
/// use honba_entities::Trade;
/// use honba_messages::{InstrumentId, OrderId, OrderSide, UnixNanos, Venue};
///
/// let t = Trade::new(
///     OrderId::new("O-1"),
///     InstrumentId::new("NIFTY50", Venue::new("NSE")),
///     OrderSide::Buy,
///     75.0,
///     22_000.0,
///     UnixNanos::from_u64(1),
///     UnixNanos::from_u64(2),
/// );
/// assert_eq!(t.notional(), 75.0 * 22_000.0);
/// ```
#[derive(Clone, Debug, PartialEq)]
pub struct Trade {
    order_id: OrderId,
    instrument_id: InstrumentId,
    side: OrderSide,
    quantity: f64,
    price: f64,
    ts_event: UnixNanos,
    ts_init: UnixNanos,
}

impl Trade {
    /// Creates a trade record.
    #[allow(clippy::too_many_arguments)]
    pub fn new(
        order_id: OrderId,
        instrument_id: InstrumentId,
        side: OrderSide,
        quantity: f64,
        price: f64,
        ts_event: UnixNanos,
        ts_init: UnixNanos,
    ) -> Self {
        debug_assert!(quantity > 0.0, "trade quantity must be positive");
        debug_assert!(price > 0.0, "trade price must be positive");
        Self { order_id, instrument_id, side, quantity, price, ts_event, ts_init }
    }

    /// Returns the order id.
    pub fn order_id(&self) -> &OrderId { &self.order_id }

    /// Returns the instrument id.
    pub fn instrument_id(&self) -> &InstrumentId { &self.instrument_id }

    /// Returns the side.
    pub fn side(&self) -> OrderSide { self.side }

    /// Returns the fill quantity.
    pub fn quantity(&self) -> f64 { self.quantity }

    /// Returns the fill price.
    pub fn price(&self) -> f64 { self.price }

    /// Returns the venue timestamp.
    pub fn ts_event(&self) -> UnixNanos { self.ts_event }

    /// Returns the Honba timestamp.
    pub fn ts_init(&self) -> UnixNanos { self.ts_init }

    /// Returns `quantity * price`.
    pub fn notional(&self) -> f64 { self.quantity * self.price }
}
