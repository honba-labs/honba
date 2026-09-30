//! Instrument metadata and monetary amounts.

use std::fmt;

use honba_messages::InstrumentId;
use serde::{Deserialize, Serialize};

/// A currency in which instruments are denominated.
///
/// ```
/// use honba_entities::Currency;
///
/// assert_eq!(Currency::Inr.code(), "INR");
/// assert_eq!(Currency::Inr.to_string(), "INR");
/// ```
#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash, PartialOrd, Ord, Serialize, Deserialize)]
#[non_exhaustive]
pub enum Currency {
    /// Indian rupee.
    Inr,
    /// United States dollar.
    Usd,
    /// Euro.
    Eur,
    /// Pound sterling.
    Gbp,
}

impl Currency {
    /// Returns the ISO 4217 currency code.
    pub const fn code(&self) -> &'static str {
        match self {
            Currency::Inr => "INR",
            Currency::Usd => "USD",
            Currency::Eur => "EUR",
            Currency::Gbp => "GBP",
        }
    }
}

impl fmt::Display for Currency {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(self.code())
    }
}

/// A monetary amount in a specific currency.
///
/// Amounts are stored as `f64` to match the rest of the platform's price
/// representation. For settlement and accounting that require exact decimal
/// arithmetic, convert to minor units before persisting.
///
/// ```
/// use honba_entities::{Currency, Money};
///
/// let a = Money::new(100.0, Currency::Inr);
/// let b = Money::new(50.0, Currency::Inr);
/// assert_eq!((a + b).unwrap().amount(), 150.0);
/// ```
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct Money {
    amount: f64,
    currency: Currency,
}

impl Money {
    /// Creates a monetary amount.
    pub const fn new(amount: f64, currency: Currency) -> Self {
        Self { amount, currency }
    }

    /// Returns a zero amount in the given currency.
    pub const fn zero(currency: Currency) -> Self {
        Self {
            amount: 0.0,
            currency,
        }
    }

    /// Returns the amount.
    pub const fn amount(&self) -> f64 {
        self.amount
    }

    /// Returns the currency.
    pub const fn currency(&self) -> Currency {
        self.currency
    }

    /// Returns the negated amount.
    pub fn neg(&self) -> Self {
        Self {
            amount: -self.amount,
            currency: self.currency,
        }
    }
}

impl std::ops::Add for Money {
    type Output = crate::Result<Money>;

    fn add(self, rhs: Self) -> Self::Output {
        if self.currency != rhs.currency {
            return Err(crate::EntitiesError::CurrencyMismatch {
                left: self.currency.to_string(),
                right: rhs.currency.to_string(),
            });
        }
        Ok(Money {
            amount: self.amount + rhs.amount,
            currency: self.currency,
        })
    }
}

impl std::ops::Sub for Money {
    type Output = crate::Result<Money>;

    fn sub(self, rhs: Self) -> Self::Output {
        if self.currency != rhs.currency {
            return Err(crate::EntitiesError::CurrencyMismatch {
                left: self.currency.to_string(),
                right: rhs.currency.to_string(),
            });
        }
        Ok(Money {
            amount: self.amount - rhs.amount,
            currency: self.currency,
        })
    }
}

/// The kind of instrument.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash)]
#[non_exhaustive]
pub enum InstrumentKind {
    /// A cash equity.
    Equity,
    /// A futures contract.
    Future,
    /// An options contract.
    Option,
    /// A spot currency pair.
    Fx,
    /// An index (non-tradable).
    Index,
    /// A mutual fund unit.
    MutualFund,
}

/// Static metadata for a tradable instrument.
///
/// ```
/// use honba_entities::{Currency, Instrument, InstrumentKind};
/// use honba_messages::{InstrumentId, Venue};
///
/// let inst = Instrument::new(
///     InstrumentId::new("RELIANCE", Venue::new("NSE")),
///     InstrumentKind::Equity,
///     Currency::Inr,
///     1.0,
///     0.05,
/// );
/// assert_eq!(inst.kind(), InstrumentKind::Equity);
/// assert_eq!(inst.tick_size(), 0.05);
/// ```
#[derive(Clone, Debug, PartialEq)]
pub struct Instrument {
    id: InstrumentId,
    kind: InstrumentKind,
    currency: Currency,
    lot_size: f64,
    tick_size: f64,
}

impl Instrument {
    /// Creates instrument metadata.
    pub fn new(
        id: InstrumentId,
        kind: InstrumentKind,
        currency: Currency,
        lot_size: f64,
        tick_size: f64,
    ) -> Self {
        debug_assert!(lot_size > 0.0, "lot_size must be positive");
        debug_assert!(tick_size > 0.0, "tick_size must be positive");
        Self {
            id,
            kind,
            currency,
            lot_size,
            tick_size,
        }
    }

    /// Returns the instrument id.
    pub fn id(&self) -> &InstrumentId {
        &self.id
    }

    /// Returns the instrument kind.
    pub fn kind(&self) -> InstrumentKind {
        self.kind
    }

    /// Returns the settlement currency.
    pub fn currency(&self) -> Currency {
        self.currency
    }

    /// Returns the minimum tradable quantity.
    pub fn lot_size(&self) -> f64 {
        self.lot_size
    }

    /// Returns the minimum price increment.
    pub fn tick_size(&self) -> f64 {
        self.tick_size
    }
}
