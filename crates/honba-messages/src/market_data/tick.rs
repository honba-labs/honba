//! Tick types: top-of-book quotes and last-sale trades.

use crate::events::timestamp::UnixNanos;
use crate::identifiers::{InstrumentId, TradeId};

/// Which side initiated a trade.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash)]
#[non_exhaustive]
pub enum AggressorSide {
    /// The buyer was the aggressor.
    Buyer,
    /// The seller was the aggressor.
    Seller,
    /// The venue did not report an aggressor.
    NoAggressor,
}

/// A top-of-book quote update.
///
/// ```
/// use honba_messages::{InstrumentId, QuoteTick, UnixNanos, Venue};
///
/// let tick = QuoteTick::new(
///     InstrumentId::new("NIFTY50", Venue::new("NSE")),
///     22_000.0, 22_001.0, 50.0, 75.0,
///     UnixNanos::from_u64(1),
///     UnixNanos::from_u64(1),
/// );
/// assert_eq!(tick.bid_price(), 22_000.0);
/// assert_eq!(tick.ask_price(), 22_001.0);
/// ```
#[derive(Clone, Debug, PartialEq)]
pub struct QuoteTick {
    instrument_id: InstrumentId,
    bid_price: f64,
    ask_price: f64,
    bid_size: f64,
    ask_size: f64,
    ts_event: UnixNanos,
    ts_init: UnixNanos,
}

impl QuoteTick {
    /// Creates a quote tick.
    #[allow(clippy::too_many_arguments)]
    pub fn new(
        instrument_id: InstrumentId,
        bid_price: f64,
        ask_price: f64,
        bid_size: f64,
        ask_size: f64,
        ts_event: UnixNanos,
        ts_init: UnixNanos,
    ) -> Self {
        debug_assert!(
            ask_price >= bid_price,
            "ask ({ask_price}) must be >= bid ({bid_price})"
        );
        Self {
            instrument_id,
            bid_price,
            ask_price,
            bid_size,
            ask_size,
            ts_event,
            ts_init,
        }
    }

    /// Returns the instrument.
    pub fn instrument_id(&self) -> &InstrumentId {
        &self.instrument_id
    }

    /// Returns the bid price.
    pub fn bid_price(&self) -> f64 {
        self.bid_price
    }

    /// Returns the ask price.
    pub fn ask_price(&self) -> f64 {
        self.ask_price
    }

    /// Returns the bid size.
    pub fn bid_size(&self) -> f64 {
        self.bid_size
    }

    /// Returns the ask size.
    pub fn ask_size(&self) -> f64 {
        self.ask_size
    }

    /// Returns the mid price.
    pub fn mid_price(&self) -> f64 {
        (self.bid_price + self.ask_price) / 2.0
    }

    /// Returns the venue timestamp.
    pub fn ts_event(&self) -> UnixNanos {
        self.ts_event
    }

    /// Returns the Honba timestamp.
    pub fn ts_init(&self) -> UnixNanos {
        self.ts_init
    }
}

/// A last-sale trade update.
///
/// ```
/// use honba_messages::{AggressorSide, InstrumentId, TradeId, TradeTick, UnixNanos, Venue};
///
/// let tick = TradeTick::new(
///     InstrumentId::new("NIFTY50", Venue::new("NSE")),
///     22_001.0, 25.0,
///     AggressorSide::Buyer,
///     TradeId::new("T-1"),
///     UnixNanos::from_u64(1),
///     UnixNanos::from_u64(1),
/// );
/// assert_eq!(tick.price(), 22_001.0);
/// assert_eq!(tick.aggressor_side(), AggressorSide::Buyer);
/// ```
#[derive(Clone, Debug, PartialEq)]
pub struct TradeTick {
    instrument_id: InstrumentId,
    price: f64,
    size: f64,
    aggressor_side: AggressorSide,
    trade_id: TradeId,
    ts_event: UnixNanos,
    ts_init: UnixNanos,
}

impl TradeTick {
    /// Creates a trade tick.
    #[allow(clippy::too_many_arguments)]
    pub fn new(
        instrument_id: InstrumentId,
        price: f64,
        size: f64,
        aggressor_side: AggressorSide,
        trade_id: TradeId,
        ts_event: UnixNanos,
        ts_init: UnixNanos,
    ) -> Self {
        Self {
            instrument_id,
            price,
            size,
            aggressor_side,
            trade_id,
            ts_event,
            ts_init,
        }
    }

    /// Returns the instrument.
    pub fn instrument_id(&self) -> &InstrumentId {
        &self.instrument_id
    }

    /// Returns the trade price.
    pub fn price(&self) -> f64 {
        self.price
    }

    /// Returns the trade size.
    pub fn size(&self) -> f64 {
        self.size
    }

    /// Returns the aggressor side.
    pub fn aggressor_side(&self) -> AggressorSide {
        self.aggressor_side
    }

    /// Returns the venue trade id.
    pub fn trade_id(&self) -> &TradeId {
        &self.trade_id
    }

    /// Returns the venue timestamp.
    pub fn ts_event(&self) -> UnixNanos {
        self.ts_event
    }

    /// Returns the Honba timestamp.
    pub fn ts_init(&self) -> UnixNanos {
        self.ts_init
    }
}

/// A typed union of tick kinds.
#[derive(Clone, Debug, PartialEq)]
#[non_exhaustive]
pub enum Tick {
    /// A quote tick.
    Quote(QuoteTick),
    /// A trade tick.
    Trade(TradeTick),
}

impl Tick {
    /// Returns the venue timestamp.
    pub fn ts_event(&self) -> UnixNanos {
        match self {
            Tick::Quote(q) => q.ts_event(),
            Tick::Trade(t) => t.ts_event(),
        }
    }
}
