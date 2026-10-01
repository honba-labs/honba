//! Aggregated OHLCV bars and their specifications.

use serde::{Deserialize, Serialize};

use crate::events::timestamp::UnixNanos;
use crate::identifiers::InstrumentId;

/// How a bar aggregates its underlying data.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
#[non_exhaustive]
pub enum BarAggregation {
    /// Aggregate every N ticks.
    Tick,
    /// Aggregate every N seconds.
    Second,
    /// Aggregate every N minutes.
    Minute,
    /// Aggregate every N hours.
    Hour,
    /// Aggregate every N days.
    Day,
    /// Aggregate every N weeks.
    Week,
    /// Aggregate every N calendar months.
    Month,
}

/// Which price of the underlying data feeds the bar.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
#[non_exhaustive]
pub enum PriceType {
    /// Use the bid price.
    Bid,
    /// Use the ask price.
    Ask,
    /// Use the midpoint.
    Mid,
    /// Use the last traded price.
    Last,
}

/// Describes how a [`Bar`] aggregates its inputs.
///
/// ```
/// use honba_messages::{BarAggregation, BarSpecification, PriceType};
///
/// let spec = BarSpecification::new(1, BarAggregation::Minute, PriceType::Last);
/// assert_eq!(spec.step(), 1);
/// assert_eq!(spec.aggregation(), BarAggregation::Minute);
/// ```
#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct BarSpecification {
    step: usize,
    aggregation: BarAggregation,
    price_type: PriceType,
}

impl BarSpecification {
    /// Creates a new specification.
    pub const fn new(step: usize, aggregation: BarAggregation, price_type: PriceType) -> Self {
        Self {
            step,
            aggregation,
            price_type,
        }
    }

    /// Returns the step size.
    pub const fn step(&self) -> usize {
        self.step
    }

    /// Returns the aggregation.
    pub const fn aggregation(&self) -> BarAggregation {
        self.aggregation
    }

    /// Returns the price type.
    pub const fn price_type(&self) -> PriceType {
        self.price_type
    }
}

/// Fully identifies a bar: which instrument, and how it aggregates.
#[derive(Clone, Debug, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct BarType {
    instrument_id: InstrumentId,
    spec: BarSpecification,
}

impl BarType {
    /// Creates a bar type.
    pub fn new(instrument_id: InstrumentId, spec: BarSpecification) -> Self {
        Self {
            instrument_id,
            spec,
        }
    }

    /// Returns the instrument.
    pub fn instrument_id(&self) -> &InstrumentId {
        &self.instrument_id
    }

    /// Returns the specification.
    pub fn spec(&self) -> BarSpecification {
        self.spec
    }
}

/// An aggregated OHLCV bar.
///
/// ```
/// use honba_messages::{
///     Bar, BarAggregation, BarSpecification, BarType, InstrumentId, PriceType,
///     UnixNanos, Venue,
/// };
///
/// let bar_type = BarType::new(
///     InstrumentId::new("NIFTY50", Venue::new("NSE")),
///     BarSpecification::new(1, BarAggregation::Minute, PriceType::Last),
/// );
/// let bar = Bar::new(
///     bar_type,
///     22_000.0, 22_050.0, 21_980.0, 22_020.0, 15_000.0,
///     UnixNanos::from_u64(1_700_000_060_000_000_000),
///     UnixNanos::from_u64(1_700_000_060_000_000_000),
/// );
/// assert_eq!(bar.open(), 22_000.0);
/// assert_eq!(bar.close(), 22_020.0);
/// ```
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Bar {
    bar_type: BarType,
    open: f64,
    high: f64,
    low: f64,
    close: f64,
    volume: f64,
    ts_event: UnixNanos,
    ts_init: UnixNanos,
}

impl Bar {
    /// Creates a bar from its components.
    #[allow(clippy::too_many_arguments)]
    pub fn new(
        bar_type: BarType,
        open: f64,
        high: f64,
        low: f64,
        close: f64,
        volume: f64,
        ts_event: UnixNanos,
        ts_init: UnixNanos,
    ) -> Self {
        debug_assert!(high >= low, "bar high ({high}) must be >= low ({low})");
        Self {
            bar_type,
            open,
            high,
            low,
            close,
            volume,
            ts_event,
            ts_init,
        }
    }

    /// Returns the bar type.
    pub fn bar_type(&self) -> &BarType {
        &self.bar_type
    }

    /// Returns the open price.
    pub fn open(&self) -> f64 {
        self.open
    }

    /// Returns the high price.
    pub fn high(&self) -> f64 {
        self.high
    }

    /// Returns the low price.
    pub fn low(&self) -> f64 {
        self.low
    }

    /// Returns the close price.
    pub fn close(&self) -> f64 {
        self.close
    }

    /// Returns the volume.
    pub fn volume(&self) -> f64 {
        self.volume
    }

    /// Returns the venue timestamp.
    pub fn ts_event(&self) -> UnixNanos {
        self.ts_event
    }

    /// Returns the Honba timestamp.
    pub fn ts_init(&self) -> UnixNanos {
        self.ts_init
    }
}
