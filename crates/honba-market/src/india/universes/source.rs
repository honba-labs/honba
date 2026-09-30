//! Traits for index universes and their data sources.

use chrono::NaiveDate;

use crate::Result;

/// A point-in-time set of instrument symbols.
pub trait Universe: Send + Sync {
    /// Returns the effective date of this snapshot.
    fn as_of(&self) -> NaiveDate;

    /// Returns the constituent symbols.
    fn symbols(&self) -> &[String];

    /// Returns the number of constituents.
    fn len(&self) -> usize {
        self.symbols().len()
    }

    /// Returns `true` if there are no constituents.
    fn is_empty(&self) -> bool {
        self.symbols().is_empty()
    }

    /// Returns `true` if the symbol is in the universe.
    fn contains(&self, symbol: &str) -> bool {
        self.symbols().iter().any(|s| s == symbol)
    }

    /// Returns the index of the symbol, if present.
    fn position(&self, symbol: &str) -> Option<usize> {
        self.symbols().iter().position(|s| s == symbol)
    }
}

/// A source of constituent lists.
pub trait UniverseSource {
    /// Loads the constituent symbols effective on `as_of`.
    fn load(&self, as_of: NaiveDate) -> Result<Vec<String>>;
}
