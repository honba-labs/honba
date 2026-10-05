//! Completed trade records.

use honba_messages::validation::{positive, serialize_finite};
use honba_messages::{InstrumentId, InvariantError, OrderId, OrderSide, UnixNanos};
use schemars::JsonSchema;
use serde::{Deserialize, Serialize};

use crate::instrument::Money;

/// A completed fill, recorded after the exchange confirms execution.
///
/// `costs` is the total transaction cost of the fill (brokerage, taxes,
/// exchange fees) as [`Money`] in the settlement currency; it defaults to zero
/// and is set with [`Trade::with_costs`]. `quantity` and `price` stay `f64`:
/// they are observations, and [`Trade::notional`] rounds to minor units once,
/// at the point of use.
///
/// ```
/// use honba_entities::{Currency, Trade};
/// use honba_messages::{InstrumentId, OrderId, OrderSide, UnixNanos, Exchange};
///
/// let t = Trade::new(
///     OrderId::new("O-1"),
///     InstrumentId::new("NIFTY50", Exchange::new("NSE")),
///     OrderSide::Buy,
///     75.0,
///     22_000.0,
///     Currency::Inr,
///     UnixNanos::from_u64(1),
///     UnixNanos::from_u64(2),
/// );
/// assert_eq!(t.notional(), 75.0 * 22_000.0);
/// assert_eq!(t.costs().minor(), 0);
/// ```
///
/// Invariants (checked by [`Trade::validate`] and on deserialization): `side`
/// is buy or sell, `quantity` and `price` are finite and `> 0`. Costs are an
/// integer `Money` and so cannot be non-finite.
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
    pub(crate) costs: Money,
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
    costs: MoneyRepr,
    ts_event: UnixNanos,
    ts_init: UnixNanos,
}

/// Costs on the wire: the exact integer form, or a legacy float.
///
/// Older producers wrote `45.67`; readers round it to minor units once, at the
/// door, so an old journal still parses. New producers emit `4567`.
///
/// Shared by `Trade` and `Position`: `Position.realized_pnl` is a ledger entry
/// and reads the same two forms.
#[derive(Deserialize)]
#[serde(untagged)]
pub(crate) enum MoneyRepr {
    /// An exact [`Money`] value.
    Exact(Money),
    /// A legacy float in major units; rounded to minor units once.
    LegacyMajor(f64),
}

impl TryFrom<TradeRepr> for Trade {
    type Error = InvariantError;

    fn try_from(r: TradeRepr) -> std::result::Result<Self, Self::Error> {
        let costs = match r.costs {
            MoneyRepr::Exact(money) => money,
            MoneyRepr::LegacyMajor(major) => {
                // Older producers wrote `45.67`; round once, at the door.
                Money::from_major_f64(major, crate::instrument::Currency::Inr)
                    .map_err(|_| {
                        // The legacy path has no currency attached, so INR is
                        // assumed — the platform's market — and a non-finite
                        // value fails the fill rather than producing a NaN.
                        InvariantError::NonFinite { field: "costs" }
                    })?
            }
        };
        let trade = Trade {
            order_id: r.order_id,
            instrument_id: r.instrument_id,
            side: r.side,
            quantity: r.quantity,
            price: r.price,
            costs,
            ts_event: r.ts_event,
            ts_init: r.ts_init,
        };
        trade.validate()?;
        Ok(trade)
    }
}

/// A completed fill needs a settlement currency, so `Trade::new` takes the
/// costs' currency. Use [`Trade::from_legacy`] when reading a journal that
/// predates integer money.
impl Trade {
    /// Creates a trade record.
    #[allow(clippy::too_many_arguments)]
    pub fn new(
        order_id: OrderId,
        instrument_id: InstrumentId,
        side: OrderSide,
        quantity: f64,
        price: f64,
        costs_currency: crate::instrument::Currency,
        ts_event: UnixNanos,
        ts_init: UnixNanos,
    ) -> Self {
        let trade = Self {
            order_id,
            instrument_id,
            side,
            quantity,
            price,
            costs: Money::zero(costs_currency),
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

    /// Creates a trade record with a known legacy cost, for tests and for
    /// journals written before integer money.
    #[allow(clippy::too_many_arguments)]
    pub fn from_legacy(
        order_id: OrderId,
        instrument_id: InstrumentId,
        side: OrderSide,
        quantity: f64,
        price: f64,
        legacy_costs: f64,
        costs_currency: crate::instrument::Currency,
        ts_event: UnixNanos,
        ts_init: UnixNanos,
    ) -> Self {
        Self {
            order_id,
            instrument_id,
            side,
            quantity,
            price,
            costs: Money::from_major_f64(legacy_costs, costs_currency)
                .unwrap_or_else(|_| Money::zero(costs_currency)),
            ts_event,
            ts_init,
        }
    }

    /// Checks the trade's invariants (see [`Trade`]).
    pub fn validate(&self) -> std::result::Result<(), InvariantError> {
        if !matches!(self.side, OrderSide::Buy | OrderSide::Sell) {
            return Err(InvariantError::NotAllowed { field: "side" });
        }
        positive("quantity", self.quantity)?;
        positive("price", self.price)?;
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
    /// use honba_entities::{Currency, Money, Trade};
    /// use honba_messages::{InstrumentId, OrderId, OrderSide, UnixNanos, Exchange};
    ///
    /// let t = Trade::new(
    ///     OrderId::new("O-1"),
    ///     InstrumentId::new("NIFTY50", Exchange::new("NSE")),
    ///     OrderSide::Buy,
    ///     75.0,
    ///     22_000.0,
    ///     Currency::Inr,
    ///     UnixNanos::from_u64(1),
    ///     UnixNanos::from_u64(2),
    /// )
    /// .with_costs(Money::from_major_f64(45.67, Currency::Inr).unwrap());
    /// assert_eq!(t.costs().minor(), 4567);
    /// ```
    pub fn with_costs(mut self, costs: Money) -> Self {
        debug_assert_eq!(
            costs.currency(),
            self.costs.currency(),
            "costs currency must match the trade's currency"
        );
        self.costs = costs;
        self
    }

    /// Returns the total transaction costs in the settlement currency.
    pub fn costs(&self) -> Money {
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
