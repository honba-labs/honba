//! Tick types: top-of-book quotes and last-sale trades.

use serde::{Deserialize, Serialize};

use crate::events::timestamp::UnixNanos;
use crate::identifiers::{InstrumentId, TradeId};
use crate::validation::{finite, non_negative, serialize_finite, InvariantError};

crate::enum_with_all! {
    /// Which side initiated a trade.
    #[derive(Clone, Copy, Debug, PartialEq, Eq, Hash, Serialize, Deserialize)]
    #[serde(rename_all = "snake_case")]
    #[non_exhaustive]
    pub enum AggressorSide {
        /// The buyer was the aggressor.
        Buyer,
        /// The seller was the aggressor.
        Seller,
        /// The venue did not report an aggressor.
        NoAggressor,
    }
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
///
/// Invariants (checked by [`QuoteTick::validate`] and on deserialization):
/// prices and sizes are finite, `bid_price <= ask_price`, sizes are `>= 0`.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
#[serde(try_from = "QuoteTickRepr")]
pub struct QuoteTick {
    instrument_id: InstrumentId,
    #[serde(serialize_with = "serialize_finite")]
    bid_price: f64,
    #[serde(serialize_with = "serialize_finite")]
    ask_price: f64,
    #[serde(serialize_with = "serialize_finite")]
    bid_size: f64,
    #[serde(serialize_with = "serialize_finite")]
    ask_size: f64,
    ts_event: UnixNanos,
    ts_init: UnixNanos,
}

/// The raw wire form, validated into a [`QuoteTick`].
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct QuoteTickRepr {
    instrument_id: InstrumentId,
    bid_price: f64,
    ask_price: f64,
    bid_size: f64,
    ask_size: f64,
    ts_event: UnixNanos,
    ts_init: UnixNanos,
}

impl TryFrom<QuoteTickRepr> for QuoteTick {
    type Error = InvariantError;

    fn try_from(r: QuoteTickRepr) -> Result<Self, Self::Error> {
        let tick = QuoteTick {
            instrument_id: r.instrument_id,
            bid_price: r.bid_price,
            ask_price: r.ask_price,
            bid_size: r.bid_size,
            ask_size: r.ask_size,
            ts_event: r.ts_event,
            ts_init: r.ts_init,
        };
        tick.validate()?;
        Ok(tick)
    }
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
        let tick = Self {
            instrument_id,
            bid_price,
            ask_price,
            bid_size,
            ask_size,
            ts_event,
            ts_init,
        };
        debug_assert!(
            tick.validate().is_ok(),
            "invalid quote: {:?}",
            tick.validate()
        );
        tick
    }

    /// Checks the quote's invariants (see [`QuoteTick`]).
    pub fn validate(&self) -> Result<(), InvariantError> {
        finite("bid_price", self.bid_price)?;
        finite("ask_price", self.ask_price)?;
        non_negative("bid_size", self.bid_size)?;
        non_negative("ask_size", self.ask_size)?;
        if self.bid_price > self.ask_price {
            return Err(InvariantError::Crossed {
                lower: "bid_price",
                upper: "ask_price",
            });
        }
        Ok(())
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
///
/// Invariants (checked by [`TradeTick::validate`] and on deserialization):
/// `price` is finite and `size` is finite and `>= 0` (index feeds report
/// size 0).
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
#[serde(try_from = "TradeTickRepr")]
pub struct TradeTick {
    instrument_id: InstrumentId,
    #[serde(serialize_with = "serialize_finite")]
    price: f64,
    #[serde(serialize_with = "serialize_finite")]
    size: f64,
    aggressor_side: AggressorSide,
    trade_id: TradeId,
    ts_event: UnixNanos,
    ts_init: UnixNanos,
}

/// The raw wire form, validated into a [`TradeTick`].
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct TradeTickRepr {
    instrument_id: InstrumentId,
    price: f64,
    size: f64,
    aggressor_side: AggressorSide,
    trade_id: TradeId,
    ts_event: UnixNanos,
    ts_init: UnixNanos,
}

impl TryFrom<TradeTickRepr> for TradeTick {
    type Error = InvariantError;

    fn try_from(r: TradeTickRepr) -> Result<Self, Self::Error> {
        let tick = TradeTick {
            instrument_id: r.instrument_id,
            price: r.price,
            size: r.size,
            aggressor_side: r.aggressor_side,
            trade_id: r.trade_id,
            ts_event: r.ts_event,
            ts_init: r.ts_init,
        };
        tick.validate()?;
        Ok(tick)
    }
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
        let tick = Self {
            instrument_id,
            price,
            size,
            aggressor_side,
            trade_id,
            ts_event,
            ts_init,
        };
        debug_assert!(
            tick.validate().is_ok(),
            "invalid trade tick: {:?}",
            tick.validate()
        );
        tick
    }

    /// Checks the tick's invariants (see [`TradeTick`]).
    pub fn validate(&self) -> Result<(), InvariantError> {
        finite("price", self.price)?;
        non_negative("size", self.size)?;
        Ok(())
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

#[cfg(test)]
mod tests {
    use super::*;
    use crate::identifiers::Venue;
    use crate::validation::InvariantError::*;

    fn id() -> InstrumentId {
        InstrumentId::new("X", Venue::new("NSE"))
    }

    fn quote() -> QuoteTick {
        QuoteTick::new(id(), 10.0, 10.5, 1.0, 2.0, 1.into(), 1.into())
    }

    fn trade() -> TradeTick {
        TradeTick::new(
            id(),
            10.0,
            3.0,
            AggressorSide::Buyer,
            TradeId::new("T"),
            1.into(),
            1.into(),
        )
    }

    #[test]
    fn quote_validate_reports_typed_errors() {
        assert_eq!(quote().validate(), Ok(()));
        let crossed = QuoteTick {
            ask_price: 9.0,
            ..quote()
        };
        assert_eq!(
            crossed.validate(),
            Err(Crossed {
                lower: "bid_price",
                upper: "ask_price",
            })
        );
        let negative = QuoteTick {
            ask_size: -2.0,
            ..quote()
        };
        assert_eq!(
            negative.validate(),
            Err(Negative {
                field: "ask_size",
                value: -2.0,
            })
        );
        let json = serde_json::to_value(crossed).unwrap();
        assert!(serde_json::from_value::<QuoteTick>(json).is_err());
    }

    #[test]
    fn trade_tick_validate_reports_typed_errors() {
        assert_eq!(trade().validate(), Ok(()));
        let negative = TradeTick {
            size: -3.0,
            ..trade()
        };
        assert_eq!(
            negative.validate(),
            Err(Negative {
                field: "size",
                value: -3.0,
            })
        );
        let json = serde_json::to_value(negative).unwrap();
        assert!(serde_json::from_value::<TradeTick>(json).is_err());
    }

    #[test]
    fn non_finite_values_never_serialize_as_null() {
        let q = QuoteTick {
            bid_size: f64::NAN,
            ..quote()
        };
        assert!(serde_json::to_string(&q).is_err());
        let t = TradeTick {
            price: f64::NEG_INFINITY,
            ..trade()
        };
        assert!(serde_json::to_string(&t).is_err());
    }
}
