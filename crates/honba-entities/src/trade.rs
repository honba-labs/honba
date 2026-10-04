//! Completed trade records.

use honba_messages::validation::{finite, positive, serialize_finite};
use honba_messages::{InstrumentId, InvariantError, OrderId, OrderSide, UnixNanos};
use schemars::JsonSchema;
use serde::{Deserialize, Serialize};

/// A completed fill, recorded after the exchange confirms execution.
///
/// `costs` is the total transaction cost of the fill (brokerage, taxes,
/// exchange fees) in the settlement currency; it defaults to zero and is set
/// with [`Trade::with_costs`].
///
/// ```
/// use honba_entities::Trade;
/// use honba_messages::{InstrumentId, OrderId, OrderSide, UnixNanos, Exchange};
///
/// let t = Trade::new(
///     OrderId::new("O-1"),
///     InstrumentId::new("NIFTY50", Exchange::new("NSE")),
///     OrderSide::Buy,
///     75.0,
///     22_000.0,
///     UnixNanos::from_u64(1),
///     UnixNanos::from_u64(2),
/// );
/// assert_eq!(t.notional(), 75.0 * 22_000.0);
/// assert_eq!(t.costs(), 0.0);
/// ```
///
/// Invariants (checked by [`Trade::validate`] and on deserialization): `side`
/// is buy or sell, `quantity` and `price` are finite and `> 0`, `costs` is
/// finite.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize, JsonSchema)]
#[serde(try_from = "TradeRepr")]
pub struct Trade {
    pub(crate) order_id: OrderId,
    pub(crate) instrument_id: InstrumentId,
    pub(crate) side: OrderSide,
    #[serde(serialize_with = "serialize_finite")]
    pub(crate) quantity: f64,
    #[serde(serialize_with = "serialize_finite")]
    pub(crate) price: f64,
    #[serde(serialize_with = "serialize_finite")]
    pub(crate) costs: f64,
    pub(crate) ts_event: UnixNanos,
    pub(crate) ts_init: UnixNanos,
}

/// The raw wire form, validated into a [`Trade`].
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct TradeRepr {
    order_id: OrderId,
    instrument_id: InstrumentId,
    side: OrderSide,
    quantity: f64,
    price: f64,
    costs: f64,
    ts_event: UnixNanos,
    ts_init: UnixNanos,
}

impl TryFrom<TradeRepr> for Trade {
    type Error = InvariantError;

    fn try_from(r: TradeRepr) -> std::result::Result<Self, Self::Error> {
        let trade = Trade {
            order_id: r.order_id,
            instrument_id: r.instrument_id,
            side: r.side,
            quantity: r.quantity,
            price: r.price,
            costs: r.costs,
            ts_event: r.ts_event,
            ts_init: r.ts_init,
        };
        trade.validate()?;
        Ok(trade)
    }
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
        let trade = Self {
            order_id,
            instrument_id,
            side,
            quantity,
            price,
            costs: 0.0,
            ts_event,
            ts_init,
        };
        debug_assert!(
            trade.validate().is_ok(),
            "invalid trade: {:?}",
            trade.validate()
        );
        trade
    }

    /// Checks the trade's invariants (see [`Trade`]).
    pub fn validate(&self) -> std::result::Result<(), InvariantError> {
        if !matches!(self.side, OrderSide::Buy | OrderSide::Sell) {
            return Err(InvariantError::NotAllowed { field: "side" });
        }
        positive("quantity", self.quantity)?;
        positive("price", self.price)?;
        finite("costs", self.costs)?;
        Ok(())
    }

    /// Returns the order id.
    pub fn order_id(&self) -> &OrderId {
        &self.order_id
    }

    /// Returns the instrument id.
    pub fn instrument_id(&self) -> &InstrumentId {
        &self.instrument_id
    }

    /// Returns the side.
    pub fn side(&self) -> OrderSide {
        self.side
    }

    /// Returns the fill quantity.
    pub fn quantity(&self) -> f64 {
        self.quantity
    }

    /// Returns the fill price.
    pub fn price(&self) -> f64 {
        self.price
    }

    /// Sets the total transaction costs of the fill, returning `self`.
    ///
    /// ```
    /// use honba_entities::Trade;
    /// use honba_messages::{InstrumentId, OrderId, OrderSide, UnixNanos, Exchange};
    ///
    /// let t = Trade::new(
    ///     OrderId::new("O-1"),
    ///     InstrumentId::new("NIFTY50", Exchange::new("NSE")),
    ///     OrderSide::Buy,
    ///     75.0,
    ///     22_000.0,
    ///     UnixNanos::from_u64(1),
    ///     UnixNanos::from_u64(2),
    /// )
    /// .with_costs(45.67);
    /// assert_eq!(t.costs(), 45.67);
    /// ```
    pub fn with_costs(mut self, costs: f64) -> Self {
        debug_assert!(costs.is_finite(), "trade costs must be finite");
        self.costs = costs;
        self
    }

    /// Returns the total transaction costs in the settlement currency.
    pub fn costs(&self) -> f64 {
        self.costs
    }

    /// Returns the exchange timestamp.
    pub fn ts_event(&self) -> UnixNanos {
        self.ts_event
    }

    /// Returns the Honba timestamp.
    pub fn ts_init(&self) -> UnixNanos {
        self.ts_init
    }

    /// Returns `quantity * price`.
    pub fn notional(&self) -> f64 {
        self.quantity * self.price
    }
}
