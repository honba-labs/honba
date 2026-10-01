//! Completed trade records.

use honba_messages::{InstrumentId, OrderId, OrderSide, UnixNanos};
use serde::{Deserialize, Serialize};

/// A completed fill, recorded after the venue confirms execution.
///
/// `costs` is the total transaction cost of the fill (brokerage, taxes,
/// exchange fees) in the settlement currency; it defaults to zero and is set
/// with [`Trade::with_costs`].
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
/// assert_eq!(t.costs(), 0.0);
/// ```
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Trade {
    order_id: OrderId,
    instrument_id: InstrumentId,
    side: OrderSide,
    quantity: f64,
    price: f64,
    costs: f64,
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
        Self {
            order_id,
            instrument_id,
            side,
            quantity,
            price,
            costs: 0.0,
            ts_event,
            ts_init,
        }
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

    /// Returns the venue timestamp.
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

#[cfg(test)]
mod tests {
    use super::*;
    use honba_messages::Venue;

    fn trade() -> Trade {
        Trade::new(
            OrderId::new("O-9"),
            InstrumentId::new("X", Venue::new("NSE")),
            OrderSide::Sell,
            2.0,
            10.0,
            UnixNanos::from_u64(1),
            UnixNanos::from_u64(1),
        )
    }

    #[test]
    fn costs_default_to_zero_and_do_not_change_notional() {
        let t = trade();
        assert_eq!(t.costs(), 0.0);
        let t = t.with_costs(1.5);
        assert_eq!(t.costs(), 1.5);
        assert_eq!(t.notional(), 20.0);
        assert_eq!(t.order_id().as_str(), "O-9");
    }

    #[test]
    fn trade_json_requires_costs_field() {
        let mut json = serde_json::to_value(trade()).unwrap();
        json.as_object_mut().unwrap().remove("costs");
        assert!(serde_json::from_value::<Trade>(json).is_err());
    }
}
