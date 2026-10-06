//! The strategy manifest: the single unit a backtest, sweep, live run, MCP
//! call, and frontend request all name (docs/archive/plan.md E0-S8).
//!
//! A strategy written in Python or TypeScript is turned into a manifest *once*,
//! by a verify step, and everything downstream consumes the manifest rather
//! than the source. That is what makes backtest and live the same run: they
//! read identical subscriptions, universe, and timeframe, so they cannot
//! silently diverge.
//!
//! The manifest is deliberately data, not behaviour. It carries no code and no
//! strategy parameters — those live in the strategy — so it can be hashed,
//! stored, diffed, and sent over a wire.

use std::collections::BTreeMap;

use schemars::JsonSchema;
use serde::{Deserialize, Serialize};

use honba_messages::{BarAggregation, InstrumentId};

/// Version of the strategy contract a manifest was compiled against.
///
/// Bumping this means the strategy's hook set or context changed. It is
/// separate from the wire `SCHEMA_VERSION`: a strategy can be rebuilt for a
/// newer contract without the envelope moving.
pub const STRATEGY_API_VERSION: &str = "1.0.0";

/// A verified, hashable description of a strategy, ready to be run.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct StrategyManifest {
    /// Strategy contract version this was compiled against.
    pub api_version: String,
    /// Strategy's declared name, used in journals and reports.
    pub name: String,
    /// Stable digest of the source that produced this manifest.
    ///
    /// Two runs with the same `source_hash` and the same data must produce a
    /// byte-identical journal; a changed hash is what tells a cache it cannot
    /// reuse a previous result.
    pub source_hash: String,
    /// The universe the strategy may trade, as a named set or explicit list.
    pub universe: Universe,
    /// What the strategy needs fed to it.
    pub subscriptions: Subscriptions,
    /// The bar timeframe that drives the strategy's clock.
    pub driving_timeframe: TimeframeSpec,
    /// Bars required before the first decision, so indicators converge.
    pub warmup_bars: u32,
    /// Optional wall-clock schedules (session open/close, rebalance times).
    #[serde(default, skip_serializing_if = "BTreeMap::is_empty")]
    pub schedules: BTreeMap<String, String>,
}

/// The set of instruments a strategy may trade.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize, JsonSchema)]
#[serde(rename_all = "snake_case", deny_unknown_fields)]
pub enum Universe {
    /// A named index or curated list resolved by the broker adapter.
    Named(String),
    /// An explicit list of instruments.
    Explicit(Vec<InstrumentId>),
}

/// The market data a strategy subscribes to.
///
/// A strategy that forgets a subscription gets no events for it, which in a
/// backtest looks like a strategy that never trades. Recording the set
/// explicitly is what lets the loader fail loudly instead.
#[derive(Clone, Debug, Default, PartialEq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct Subscriptions {
    /// Instruments whose bars this strategy consumes.
    pub instruments: Vec<InstrumentId>,
    /// Whether top-of-book quotes are delivered.
    #[serde(default)]
    pub quotes: bool,
    /// Whether trade prints are delivered.
    #[serde(default)]
    pub trades: bool,
}

impl Subscriptions {
    /// Returns true when this subscription set mentions `id` in any channel.
    pub fn covers(&self, id: &InstrumentId) -> bool {
        self.instruments.contains(id)
    }
}

/// A bar aggregation interval.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct TimeframeSpec {
    /// Number of units per bar, e.g. 5 in `5m`.
    pub interval: u32,
    /// The unit the interval counts.
    pub aggregation: BarAggregation,
}

impl TimeframeSpec {
    /// Builds a timeframe.
    pub const fn new(interval: u32, aggregation: BarAggregation) -> Self {
        Self {
            interval,
            aggregation,
        }
    }
}

impl StrategyManifest {
    /// Starts a manifest, filling in the current contract version.
    pub fn new(
        name: impl Into<String>,
        source_hash: impl Into<String>,
        universe: Universe,
        driving_timeframe: TimeframeSpec,
    ) -> Self {
        Self {
            api_version: STRATEGY_API_VERSION.to_string(),
            name: name.into(),
            source_hash: source_hash.into(),
            universe,
            subscriptions: Subscriptions::default(),
            driving_timeframe,
            warmup_bars: 0,
            schedules: BTreeMap::new(),
        }
    }

    /// Sets the instruments and channels this strategy consumes.
    #[must_use]
    pub fn with_subscriptions(mut self, subscriptions: Subscriptions) -> Self {
        self.subscriptions = subscriptions;
        self
    }

    /// Sets the number of warm-up bars.
    #[must_use]
    pub fn with_warmup_bars(mut self, warmup_bars: u32) -> Self {
        self.warmup_bars = warmup_bars;
        self
    }

    /// Adds one named schedule entry.
    #[must_use]
    pub fn with_schedule(mut self, key: impl Into<String>, value: impl Into<String>) -> Self {
        self.schedules.insert(key.into(), value.into());
        self
    }

    /// Every instrument this strategy may act on.
    ///
    /// The union of the explicit subscription list and, for a named universe,
    /// whatever the loader resolved the name to. Verification uses this to
    /// reject a strategy that subscribes to something outside its universe.
    pub fn instruments(&self) -> Vec<InstrumentId> {
        let mut out = self.subscriptions.instruments.clone();
        if let Universe::Explicit(list) = &self.universe {
            for id in list {
                if !out.contains(id) {
                    out.push(id.clone());
                }
            }
        }
        out.sort();
        out.dedup();
        out
    }

    /// Checks the manifest's internal consistency.
    ///
    /// Run before a run starts, so an unusable manifest is rejected at the door
    /// rather than producing an empty journal that looks like a strategy that
    /// did nothing.
    pub fn validate(&self) -> Result<(), ManifestError> {
        if self.name.trim().is_empty() {
            return Err(ManifestError::EmptyName);
        }
        if self.source_hash.trim().is_empty() {
            return Err(ManifestError::EmptySourceHash);
        }
        if self.api_version != STRATEGY_API_VERSION {
            return Err(ManifestError::UnsupportedApiVersion {
                found: self.api_version.clone(),
            });
        }
        if self.driving_timeframe.interval == 0 {
            return Err(ManifestError::ZeroInterval);
        }
        // A named universe with no resolved instruments would silently trade
        // nothing; the loader must fill the subscription list first.
        if matches!(self.universe, Universe::Named(_)) && self.subscriptions.instruments.is_empty()
        {
            return Err(ManifestError::NamedUniverseUnresolved);
        }
        Ok(())
    }
}

/// Why a manifest was rejected.
#[derive(Clone, Debug, PartialEq, Eq)]
#[non_exhaustive]
pub enum ManifestError {
    /// The strategy has no name.
    EmptyName,
    /// The source digest is missing, so runs cannot be keyed to code.
    EmptySourceHash,
    /// The manifest targets a contract version this build does not implement.
    UnsupportedApiVersion {
        /// The version found in the manifest.
        found: String,
    },
    /// A bar timeframe of zero would never produce a bar.
    ZeroInterval,
    /// A named universe was not resolved to concrete instruments.
    NamedUniverseUnresolved,
}

impl std::fmt::Display for ManifestError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::EmptyName => write!(f, "strategy name must not be empty"),
            Self::EmptySourceHash => write!(f, "source_hash must not be empty"),
            Self::UnsupportedApiVersion { found } => {
                write!(f, "unsupported strategy api_version: {found}")
            }
            Self::ZeroInterval => write!(f, "timeframe interval must be > 0"),
            Self::NamedUniverseUnresolved => {
                write!(
                    f,
                    "named universe must be resolved to instruments before running"
                )
            }
        }
    }
}

impl std::error::Error for ManifestError {}
