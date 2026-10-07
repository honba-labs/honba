//! Identifier newtypes used throughout the platform.
//!
//! These are deliberately opaque wrappers around [`String`]. They make it
//! impossible to pass an exchange name where an instrument symbol is expected.

use schemars::JsonSchema;
use serde::{Deserialize, Serialize};
use std::fmt;

/// A trading exchange, e.g. `NSE`, `BSE`, `NASDAQ`.
#[derive(
    Clone, Debug, PartialEq, Eq, Hash, PartialOrd, Ord, Serialize, Deserialize, JsonSchema,
)]
pub struct Exchange(String);

impl Exchange {
    /// Creates an exchange from any string-like value.
    ///
    /// ```
    /// use honba_messages::Exchange;
    ///
    /// let v = Exchange::new("NSE");
    /// assert_eq!(v.as_str(), "NSE");
    /// ```
    pub fn new(symbol: impl Into<String>) -> Self {
        Self(symbol.into())
    }

    /// Returns the exchange code as a string slice.
    pub fn as_str(&self) -> &str {
        &self.0
    }
}

impl fmt::Display for Exchange {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(&self.0)
    }
}

/// A trading instrument, combining a symbol and its exchange.
#[derive(
    Clone, Debug, PartialEq, Eq, Hash, PartialOrd, Ord, Serialize, Deserialize, JsonSchema,
)]
pub struct InstrumentId {
    symbol: String,
    exchange: Exchange,
}

impl InstrumentId {
    /// Creates an instrument id from a symbol and an exchange.
    ///
    /// ```
    /// use honba_messages::{InstrumentId, Exchange};
    ///
    /// let id = InstrumentId::new("RELIANCE", Exchange::new("NSE"));
    /// assert_eq!(id.symbol(), "RELIANCE");
    /// assert_eq!(id.exchange().as_str(), "NSE");
    /// ```
    pub fn new(symbol: impl Into<String>, exchange: Exchange) -> Self {
        Self {
            symbol: symbol.into(),
            exchange,
        }
    }

    /// Returns the instrument symbol.
    pub fn symbol(&self) -> &str {
        &self.symbol
    }

    /// Returns the exchange.
    pub fn exchange(&self) -> &Exchange {
        &self.exchange
    }
}

impl fmt::Display for InstrumentId {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "{}.{}", self.symbol, self.exchange)
    }
}

/// A client-assigned order identifier.
#[derive(
    Clone, Debug, PartialEq, Eq, Hash, PartialOrd, Ord, Serialize, Deserialize, JsonSchema,
)]
pub struct OrderId(String);

impl OrderId {
    /// Creates an order id.
    pub fn new(value: impl Into<String>) -> Self {
        Self(value.into())
    }

    /// Returns the order id as a string slice.
    pub fn as_str(&self) -> &str {
        &self.0
    }
}

impl From<&str> for OrderId {
    fn from(value: &str) -> Self {
        Self::new(value)
    }
}

impl From<String> for OrderId {
    fn from(value: String) -> Self {
        Self(value)
    }
}

impl fmt::Display for OrderId {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(&self.0)
    }
}

/// A venue-assigned order identifier (the exchange or broker's id, as opposed to the
/// client-assigned [`OrderId`]).
#[derive(
    Clone, Debug, PartialEq, Eq, Hash, PartialOrd, Ord, Serialize, Deserialize, JsonSchema,
)]
pub struct VenueOrderId(String);

impl VenueOrderId {
    /// Creates a venue order id.
    pub fn new(value: impl Into<String>) -> Self {
        Self(value.into())
    }

    /// Returns the venue order id as a string slice.
    pub fn as_str(&self) -> &str {
        &self.0
    }
}

impl fmt::Display for VenueOrderId {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(&self.0)
    }
}

/// A exchange-assigned trade identifier.
#[derive(
    Clone, Debug, PartialEq, Eq, Hash, PartialOrd, Ord, Serialize, Deserialize, JsonSchema,
)]
pub struct TradeId(String);

impl TradeId {
    /// Creates a trade id.
    pub fn new(value: impl Into<String>) -> Self {
        Self(value.into())
    }

    /// Returns the trade id as a string slice.
    pub fn as_str(&self) -> &str {
        &self.0
    }
}

impl fmt::Display for TradeId {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(&self.0)
    }
}
