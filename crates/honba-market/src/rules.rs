//! Generic instrument trading rules, price bands, and symbol grammar.

use honba_entities::Instrument;
use honba_messages::InstrumentId;
use serde::{Deserialize, Serialize};

use crate::{MarketError, Result};

/// Price band (circuit limits) for an instrument on a trading day.
#[derive(Clone, Copy, Debug, PartialEq, Serialize, Deserialize)]
pub struct PriceBand {
    /// Lower circuit / floor limit price.
    pub lower: f64,
    /// Upper circuit / ceiling limit price.
    pub upper: f64,
}

impl PriceBand {
    /// Creates a new price band.
    pub fn new(lower: f64, upper: f64) -> Self {
        debug_assert!(upper >= lower, "upper band must be >= lower band");
        Self { lower, upper }
    }

    /// Checks if a price is within the band.
    pub fn contains(&self, price: f64) -> bool {
        price >= self.lower && price <= self.upper
    }
}

/// Trading rules governing lot sizes, tick sizes, freeze quantities, and price limits.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct InstrumentRules {
    /// Minimum tradable lot size.
    pub lot_size: f64,
    /// Minimum price increment.
    pub tick_size: f64,
    /// Minimum allowed order quantity.
    pub min_order_quantity: f64,
    /// Maximum allowed single order quantity (freeze / max clip size). None if unlimited.
    pub max_order_quantity: Option<f64>,
}

impl InstrumentRules {
    /// Creates basic instrument rules from lot size and tick size.
    pub fn new(lot_size: f64, tick_size: f64) -> Self {
        Self {
            lot_size,
            tick_size,
            min_order_quantity: lot_size,
            max_order_quantity: None,
        }
    }

    /// Sets the maximum single order quantity.
    pub fn with_max_quantity(mut self, max_quantity: f64) -> Self {
        self.max_order_quantity = Some(max_quantity);
        self
    }

    /// Validates whether an order quantity complies with lot size and quantity limits.
    pub fn validate_quantity(&self, quantity: f64) -> Result<()> {
        if quantity < self.min_order_quantity {
            return Err(MarketError::RuleViolation(format!(
                "quantity {} below minimum {}",
                quantity, self.min_order_quantity
            )));
        }

        if let Some(max_qty) = self.max_order_quantity {
            if quantity > max_qty {
                return Err(MarketError::RuleViolation(format!(
                    "quantity {} exceeds maximum freeze limit {}",
                    quantity, max_qty
                )));
            }
        }

        let rem = (quantity / self.lot_size).fract();
        if rem.abs() > 1e-6 && (1.0 - rem.abs()) > 1e-6 {
            return Err(MarketError::RuleViolation(format!(
                "quantity {} is not an exact multiple of lot size {}",
                quantity, self.lot_size
            )));
        }

        Ok(())
    }

    /// Validates whether an order price complies with the tick size.
    pub fn validate_price(&self, price: f64) -> Result<()> {
        if price <= 0.0 {
            return Err(MarketError::RuleViolation(format!(
                "price {} must be positive",
                price
            )));
        }

        let ticks = price / self.tick_size;
        let rem = ticks.fract();
        if rem.abs() > 1e-4 && (1.0 - rem.abs()) > 1e-4 {
            return Err(MarketError::RuleViolation(format!(
                "price {} does not conform to tick size {}",
                price, self.tick_size
            )));
        }

        Ok(())
    }
}

/// Generic provider of instrument rules for symbols.
pub trait InstrumentRulesProvider: Send + Sync {
    /// Returns the trading rules for a given instrument.
    fn rules_for(&self, instrument: &Instrument) -> InstrumentRules {
        InstrumentRules::new(instrument.lot_size(), instrument.tick_size())
    }

    /// Returns the daily price band (circuits) if applicable.
    fn price_band(&self, _id: &InstrumentId) -> Option<PriceBand> {
        None
    }
}

/// Symbol grammar defining naming, normalization, and ticker parsing.
pub trait SymbolGrammar: Send + Sync {
    /// Normalizes an input symbol to the exchange canonical representation.
    fn normalize(&self, raw: &str) -> String {
        raw.trim().to_uppercase()
    }

    /// Validates whether a raw symbol conforms to market grammar.
    fn validate(&self, raw: &str) -> Result<()> {
        if raw.trim().is_empty() {
            Err(MarketError::InvalidSymbol {
                symbol: raw.to_string(),
                reason: "symbol cannot be empty".to_string(),
            })
        } else {
            Ok(())
        }
    }
}
