//! Instrument metadata and monetary amounts.

use std::fmt;

use honba_messages::InstrumentId;
use schemars::JsonSchema;
use serde::{Deserialize, Serialize};

honba_messages::enum_with_all! {
    /// A currency in which instruments are denominated.
    ///
    /// ```
    /// use honba_entities::Currency;
    ///
    /// assert_eq!(Currency::Inr.code(), "INR");
    /// assert_eq!(Currency::Inr.to_string(), "INR");
    /// ```
    #[derive(Clone, Copy, Debug, PartialEq, Eq, Hash, PartialOrd, Ord, Serialize, Deserialize, JsonSchema)]
    #[serde(rename_all = "UPPERCASE")]
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

/// A monetary amount in a specific currency, in integer minor units.
///
/// Amounts are `i64` minor units — paise for INR, cents for the rest — so the
/// ledger is exact: `0.1 + 0.2` is 30 paise, not a float that is off by a
/// fraction that compounds across a run. Construction from major units rounds
/// half away from zero and rejects non-finite input; serialization emits the
/// integer, never a JSON float.
///
/// Prices (`Trade.price`, bar OHLC, marks) and statistics (Sharpe, drawdown)
/// stay `f64` — they are observations and ratios, not settled amounts.
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct Money {
    amount: i64,
    currency: Currency,
}

/// The wire form: an integer, or (for older writers) a JSON number.
///
/// Legacy producers emitted floats. Reading them is kept, and the value is
/// rounded to minor units once, at the door — so an old stream does not fail a
/// whole run, but nothing new depends on float input.
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct MoneyRepr {
    amount: MoneyAmount,
    currency: Currency,
}

/// A money value as it arrives on the wire: an exact integer, or a legacy float.
#[derive(Clone, Copy, Debug)]
enum MoneyAmount {
    Minor(i64),
    LegacyMajor(f64),
}

impl<'de> Deserialize<'de> for MoneyAmount {
    fn deserialize<D>(deserializer: D) -> Result<Self, D::Error>
    where
        D: serde::Deserializer<'de>,
    {
        struct Visitor;
        impl serde::de::Visitor<'_> for Visitor {
            type Value = MoneyAmount;

            fn expecting(&self, formatter: &mut std::fmt::Formatter) -> std::fmt::Result {
                formatter.write_str("an integer minor-unit amount, or a legacy float")
            }

            fn visit_i64<E>(self, value: i64) -> Result<Self::Value, E> {
                Ok(MoneyAmount::Minor(value))
            }

            fn visit_u64<E>(self, value: u64) -> Result<Self::Value, E>
            where
                E: serde::de::Error,
            {
                i64::try_from(value)
                    .map(MoneyAmount::Minor)
                    .map_err(|_| E::custom("amount exceeds i64 minor units"))
            }

            fn visit_f64<E>(self, value: f64) -> Result<Self::Value, E> {
                Ok(MoneyAmount::LegacyMajor(value))
            }
        }
        deserializer.deserialize_any(Visitor)
    }
}

impl std::fmt::Display for Money {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "{} {:.2}", self.currency, self.to_major_f64())
    }
}

impl Serialize for Money {
    fn serialize<S>(&self, serializer: S) -> Result<S::Ok, S::Error>
    where
        S: serde::Serializer,
    {
        use serde::ser::SerializeStruct;
        let mut state = serializer.serialize_struct("Money", 2)?;
        state.serialize_field("amount", &self.amount)?;
        state.serialize_field("currency", &self.currency)?;
        state.end()
    }
}

impl<'de> Deserialize<'de> for Money {
    fn deserialize<D>(deserializer: D) -> Result<Self, D::Error>
    where
        D: serde::Deserializer<'de>,
    {
        let repr = MoneyRepr::deserialize(deserializer)?;
        match repr.amount {
            MoneyAmount::Minor(minor) => Ok(Self {
                amount: minor,
                currency: repr.currency,
            }),
            MoneyAmount::LegacyMajor(major) => {
                round_major_to_minor(major, repr.currency).map_err(serde::de::Error::custom)
            }
        }
    }
}

impl JsonSchema for Money {
    fn schema_name() -> String {
        "Money".to_string()
    }

    fn json_schema(_generator: &mut schemars::gen::SchemaGenerator) -> schemars::schema::Schema {
        schemars::schema::Schema::Object(schemars::schema::SchemaObject {
            instance_type: Some(schemars::schema::InstanceType::Object.into()),
            object: Some(Box::new(schemars::schema::ObjectValidation {
                properties: [
                    (
                        "amount".to_string(),
                        schemars::schema::Schema::Object(schemars::schema::SchemaObject {
                            instance_type: Some(schemars::schema::InstanceType::Integer.into()),
                            format: Some("int64".to_string()),
                            ..Default::default()
                        }),
                    ),
                    (
                        "currency".to_string(),
                        schemars::schema::Schema::Object(schemars::schema::SchemaObject {
                            instance_type: Some(
                                schemars::schema::InstanceType::String.into(),
                            ),
                            ..Default::default()
                        }),
                    ),
                ]
                .into_iter()
                .collect(),
                required: ["amount".to_string(), "currency".to_string()]
                    .into_iter()
                    .collect(),
                ..Default::default()
            })),
            metadata: Some(
                schemars::schema::Metadata {
                    description: Some(
                        "Monetary amount in integer minor units (paise/cents).".to_string(),
                    ),
                    ..Default::default()
                }
                .into(),
            ),
            ..Default::default()
        })
    }
}

/// Minor units per major unit. Fixed at 100: paise for INR, cents for
/// USD/EUR/GBP. A currency that needs finer granularity is a new `Money`
/// representation, not a field, because rounding would change everywhere.
const MINOR_PER_MAJOR: f64 = 100.0;

/// Rounds a major-unit value to integer minor units, half away from zero.
///
/// Rejects NaN, infinities, and anything that cannot be represented exactly.
fn round_major_to_minor(value: f64, currency: Currency) -> Result<Money, MoneyError> {
    if !value.is_finite() {
        return Err(MoneyError::NonFinite);
    }
    let scaled = value * MINOR_PER_MAJOR;
    if scaled.abs() > i64::MAX as f64 {
        return Err(MoneyError::Overflow);
    }
    Ok(Money {
        amount: scaled.round() as i64,
        currency,
    })
}

/// Why a money value was rejected.
#[derive(Clone, Debug, PartialEq, Eq)]
#[non_exhaustive]
pub enum MoneyError {
    /// NaN or infinite input, which has no minor-unit representation.
    NonFinite,
    /// A value whose minor units do not fit `i64`.
    Overflow,
    /// Two operands had incompatible currencies.
    CurrencyMismatch {
        /// The left operand's currency.
        left: Currency,
        /// The right operand's currency.
        right: Currency,
    },
    /// A non-finite quantity met a finite price (or vice versa).
    InvalidQuantity,
}

impl std::fmt::Display for MoneyError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::NonFinite => write!(f, "money amount must be finite"),
            Self::Overflow => write!(f, "money amount exceeds i64 minor units"),
            Self::CurrencyMismatch { left, right } => {
                write!(f, "currency mismatch: {left} vs {right}")
            }
            Self::InvalidQuantity => write!(f, "quantity must be finite"),
        }
    }
}

impl std::error::Error for MoneyError {}

impl From<MoneyError> for crate::EntitiesError {
    fn from(value: MoneyError) -> Self {
        match value {
            MoneyError::NonFinite => crate::EntitiesError::InvalidMoney("non-finite".into()),
            MoneyError::Overflow => crate::EntitiesError::InvalidMoney("overflow".into()),
            MoneyError::CurrencyMismatch { left, right } => crate::EntitiesError::CurrencyMismatch {
                left: left.to_string(),
                right: right.to_string(),
            },
            MoneyError::InvalidQuantity => {
                crate::EntitiesError::InvalidMoney("non-finite quantity".into())
            }
        }
    }
}

impl Money {
    /// Creates an amount from integer minor units.
    ///
    /// ```
    /// use honba_entities::{Currency, Money};
    ///
    /// let m = Money::new(12_345, Currency::Inr);
    /// assert_eq!(m.minor(), 12_345);
    /// assert_eq!(m.to_major_f64(), 123.45);
    /// ```
    pub const fn new(minor: i64, currency: Currency) -> Self {
        Self {
            amount: minor,
            currency,
        }
    }

    /// Creates an amount from major units, rounding half away from zero.
    ///
    /// Prefer [`Money::new`] at rest; this is the boundary constructor, for
    /// values that arrive as floats from configuration or older producers.
    pub fn from_major_f64(major: f64, currency: Currency) -> Result<Self, MoneyError> {
        round_major_to_minor(major, currency)
    }

    /// Returns a zero amount in the given currency.
    pub const fn zero(currency: Currency) -> Self {
        Self { amount: 0, currency }
    }

    /// Returns the amount in minor units.
    pub const fn minor(&self) -> i64 {
        self.amount
    }

    /// Returns the amount in major units, as an exact division.
    pub fn to_major_f64(&self) -> f64 {
        self.amount as f64 / MINOR_PER_MAJOR
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

    /// Multiplies a quantity by a price and rounds to minor units.
    ///
    /// The product of two floats is rounded once, to the nearest paise, which
    /// is the value that settles. Negative quantities (sell notionals) round
    /// symmetrically with positive ones.
    pub fn mul_qty(quantity: f64, price: f64, currency: Currency) -> Result<Self, MoneyError> {
        if !quantity.is_finite() || !price.is_finite() {
            return Err(MoneyError::InvalidQuantity);
        }
        round_major_to_minor(quantity * price, currency)
    }
}
impl std::ops::Add for Money {
    type Output = crate::Result<Money>;

    /// Adds two amounts in the same currency, checking for i64 overflow.
    ///
    /// Overflow is an error, not a wrap: a wrapped ledger balance would report
    /// the wrong sign on a real account.
    fn add(self, rhs: Self) -> Self::Output {
        let (left, right) = self.checked_currency(rhs)?;
        left.amount
            .checked_add(right.amount)
            .map(|amount| Money { amount, currency: left.currency })
            .ok_or_else(|| crate::EntitiesError::Arithmetic("money addition overflowed".into()))
    }
}

impl std::ops::Sub for Money {
    type Output = crate::Result<Money>;

    /// Subtracts two amounts in the same currency, checking for i64 overflow.
    fn sub(self, rhs: Self) -> Self::Output {
        let (left, right) = self.checked_currency(rhs)?;
        left.amount
            .checked_sub(right.amount)
            .map(|amount| Money { amount, currency: left.currency })
            .ok_or_else(|| crate::EntitiesError::Arithmetic("money subtraction overflowed".into()))
    }
}

impl Money {
    /// Returns both operands when they share a currency, or the mismatch.
    fn checked_currency(&self, rhs: Self) -> crate::Result<(Money, Money)> {
        if self.currency != rhs.currency {
            return Err(crate::EntitiesError::CurrencyMismatch {
                left: self.currency.to_string(),
                right: rhs.currency.to_string(),
            });
        }
        Ok((*self, rhs))
    }
}

/// The kind of instrument.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash)]
#[non_exhaustive]
pub enum InstrumentKind {
    /// A cash equity.
    Equity,
    /// An exchange-traded fund.
    Etf,
    /// A bond or fixed-income security.
    Bond,
    /// An initial public offering listing.
    Ipo,
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
/// use honba_messages::{InstrumentId, Exchange};
///
/// let inst = Instrument::new(
///     InstrumentId::new("RELIANCE", Exchange::new("NSE")),
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
