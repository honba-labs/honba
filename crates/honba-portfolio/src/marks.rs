//! Mark prices used to value positions.

use std::collections::BTreeMap;

use honba_messages::InstrumentId;

/// The last price used to value each instrument (ADR 0032 §3).
///
/// Marks come from the same last quote or close the chart uses; the engine never reaches for
/// one. A `BTreeMap` keeps iteration deterministic, so two equal `Marks` behave alike.
#[derive(Clone, Debug, Default)]
pub struct Marks {
    prices: BTreeMap<InstrumentId, f64>,
}

impl Marks {
    /// Returns an empty set of marks.
    pub fn new() -> Self {
        Self::default()
    }

    /// Records the mark for an instrument, replacing any previous one.
    pub fn set(&mut self, instrument_id: InstrumentId, price: f64) {
        self.prices.insert(instrument_id, price);
    }

    /// Returns a copy with one more mark, for chaining.
    pub fn with(mut self, instrument_id: InstrumentId, price: f64) -> Self {
        self.set(instrument_id, price);
        self
    }

    /// Returns the mark for an instrument, if one was set.
    pub fn get(&self, instrument_id: &InstrumentId) -> Option<f64> {
        self.prices.get(instrument_id).copied()
    }

    /// Returns the number of marks.
    pub fn len(&self) -> usize {
        self.prices.len()
    }

    /// Returns `true` if no mark was set.
    pub fn is_empty(&self) -> bool {
        self.prices.is_empty()
    }
}
