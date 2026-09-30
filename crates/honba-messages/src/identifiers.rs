//! Identifier newtypes used throughout the platform.
//!
//! These are deliberately opaque wrappers around [`String`]. They make it
//! impossible to pass a venue name where an instrument symbol is expected.

use std::fmt;

/// A trading venue, e.g. `NSE`, `BSE`, `NASDAQ`.
#[derive(Clone, Debug, PartialEq, Eq, Hash, PartialOrd, Ord)]
pub struct Venue(String);

impl Venue {
    /// Creates a venue from any string-like value.
    ///
    /// ```
    /// use honba_messages::Venue;
    ///
    /// let v = Venue::new("NSE");
    /// assert_eq!(v.as_str(), "NSE");
    /// ```
    pub fn new(symbol: impl Into<String>) -> Self {
        Self(symbol.into())
    }

    /// Returns the venue code as a string slice.
    pub fn as_str(&self) -> &str {
        &self.0
    }
}

impl fmt::Display for Venue {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(&self.0)
    }
}

/// A trading instrument, combining a symbol and its venue.
#[derive(Clone, Debug, PartialEq, Eq, Hash, PartialOrd, Ord)]
pub struct InstrumentId {
    symbol: String,
    venue: Venue,
}

impl InstrumentId {
    /// Creates an instrument id from a symbol and a venue.
    ///
    /// ```
    /// use honba_messages::{InstrumentId, Venue};
    ///
    /// let id = InstrumentId::new("RELIANCE", Venue::new("NSE"));
    /// assert_eq!(id.symbol(), "RELIANCE");
    /// assert_eq!(id.venue().as_str(), "NSE");
    /// ```
    pub fn new(symbol: impl Into<String>, venue: Venue) -> Self {
        Self { symbol: symbol.into(), venue }
    }

    /// Returns the instrument symbol.
    pub fn symbol(&self) -> &str {
        &self.symbol
    }

    /// Returns the venue.
    pub fn venue(&self) -> &Venue {
        &self.venue
    }
}

impl fmt::Display for InstrumentId {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "{}.{}", self.symbol, self.venue)
    }
}

/// A client-assigned order identifier.
#[derive(Clone, Debug, PartialEq, Eq, Hash, PartialOrd, Ord)]
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

impl fmt::Display for OrderId {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(&self.0)
    }
}

/// A venue-assigned trade identifier.
#[derive(Clone, Debug, PartialEq, Eq, Hash, PartialOrd, Ord)]
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
