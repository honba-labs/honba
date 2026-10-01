//! Generic transaction costs and named-charge fee schedules.

use chrono::NaiveDate;
use honba_messages::OrderSide;
use serde::{Deserialize, Serialize};
use std::fmt;

use crate::Result;

/// A named financial charge or tax line item on a transaction.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct Charge {
    /// Identifier or name of the charge (e.g., "stt", "exchange_fee", "brokerage", "sec_fee").
    pub name: String,
    /// Absolute calculated monetary amount in the settlement currency.
    pub amount: f64,
}

impl Charge {
    /// Creates a new charge.
    pub fn new(name: impl Into<String>, amount: f64) -> Self {
        Self {
            name: name.into(),
            amount,
        }
    }
}

impl fmt::Display for Charge {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "{}: {:.4}", self.name, self.amount)
    }
}

/// Generic itemized fee breakdown for a transaction or fill.
#[derive(Clone, Debug, PartialEq, Default, Serialize, Deserialize)]
pub struct FeeBreakdown {
    /// Itemized list of charges.
    pub charges: Vec<Charge>,
}

impl FeeBreakdown {
    /// Creates an empty fee breakdown.
    pub fn empty() -> Self {
        Self {
            charges: Vec::new(),
        }
    }

    /// Creates a breakdown from a list of charges.
    pub fn from_charges(charges: Vec<Charge>) -> Self {
        Self { charges }
    }

    /// Adds a named charge to the breakdown.
    pub fn add(&mut self, name: impl Into<String>, amount: f64) {
        self.charges.push(Charge::new(name, amount));
    }

    /// Computes the total sum of all charge line items.
    pub fn total(&self) -> f64 {
        self.charges.iter().map(|c| c.amount).sum()
    }

    /// Finds a charge by name.
    pub fn get(&self, name: &str) -> Option<f64> {
        self.charges
            .iter()
            .find(|c| c.name == name)
            .map(|c| c.amount)
    }
}

/// Generic market segment or product classification.
#[derive(Clone, Debug, PartialEq, Eq, Hash, Serialize, Deserialize)]
pub struct MarketSegment(String);

impl MarketSegment {
    /// Creates a market segment.
    pub fn new(segment: impl Into<String>) -> Self {
        Self(segment.into())
    }

    /// Returns the segment as a string slice.
    pub fn as_str(&self) -> &str {
        &self.0
    }
}

impl fmt::Display for MarketSegment {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(&self.0)
    }
}

impl From<&str> for MarketSegment {
    fn from(s: &str) -> Self {
        Self::new(s)
    }
}

impl From<String> for MarketSegment {
    fn from(s: String) -> Self {
        Self::new(s)
    }
}

/// Generic transaction cost schedule contract.
pub trait CostSchedule: Send + Sync {
    /// Computes the itemized transaction costs for a trade.
    fn compute_costs(
        &self,
        segment: &MarketSegment,
        side: OrderSide,
        notional: f64,
    ) -> FeeBreakdown;
}

/// A source of cost schedules indexed by effective date.
pub trait CostModelSource {
    /// Returns the cost schedule in force on the given date.
    fn schedule_for(&self, date: NaiveDate) -> Result<Box<dyn CostSchedule>>;
}
