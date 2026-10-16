//! Signal contract: an explainable buy/sell point (ADR 0031, ROADMAP E12).
//!
//! Where [`crate::screener`] holds the *filter* contract, this module holds the *signal*
//! contract: a bar-anchored buy/sell point carrying the **reasons** it fired, so the chart can
//! show the user why. Like the screener types, these live here in `honba-entities` (L1) so
//! `honba-codegen` and `honba-api` can name them without depending on the detector engine
//! (`honba-signals`, L4).
//!
//! The values are pure and closed under the engine: the engine (`honba-signals::compute`)
//! constructs them, and `honba-signals` is the only place that decides which reasons fire. This
//! module deliberately carries no evaluation logic.

use std::collections::BTreeMap;

use honba_messages::{InstrumentId, UnixNanos};
use schemars::JsonSchema;
use serde::{Deserialize, Serialize};

use crate::screener::Timeframe;

/// The trading horizon a signal was evaluated for (ADR 0031 §5).
#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash, Serialize, Deserialize, JsonSchema)]
#[serde(rename_all = "snake_case")]
pub enum SignalHorizon {
    /// Intraday: session VWAP, opening range, no overnight.
    Intraday,
    /// Positional: daily/weekly, multi-day holds.
    Swing,
}

impl SignalHorizon {
    /// The default ruleset id for this horizon (`signals/day:v1` / `signals/swing:v1`).
    pub const fn ruleset(self) -> &'static str {
        match self {
            Self::Intraday => "signals/day:v1",
            Self::Swing => "signals/swing:v1",
        }
    }
}

/// The direction a signal points.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash, Serialize, Deserialize, JsonSchema)]
#[serde(rename_all = "snake_case")]
pub enum SignalDirection {
    /// A long/entry bias.
    Buy,
    /// A short/exit bias.
    Sell,
}

/// The detector family a reason belongs to.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash, Serialize, Deserialize, JsonSchema)]
#[serde(rename_all = "snake_case")]
pub enum ReasonGroup {
    /// Moving-average and trend structure.
    Trend,
    /// Oscillators (RSI, MACD, stochastic).
    Momentum,
    /// Candlestick and market-structure patterns.
    PriceAction,
    /// Volume and participation.
    Volume,
    /// Volatility regimes (ATR, bands).
    Volatility,
    /// Support/resistance and prior extremes.
    Level,
    /// Session clock and intraday levels.
    Session,
    /// Relative strength versus an index or peer.
    RelativeStrength,
}

/// The direction a single reason argues for.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash, Serialize, Deserialize, JsonSchema)]
#[serde(rename_all = "snake_case")]
pub enum ReasonPolarity {
    /// Argues for a buy.
    Bullish,
    /// Argues for a sell.
    Bearish,
}

impl ReasonPolarity {
    /// The signal direction this polarity argues for.
    pub const fn direction(self) -> SignalDirection {
        match self {
            Self::Bullish => SignalDirection::Buy,
            Self::Bearish => SignalDirection::Sell,
        }
    }
}

/// One detector firing, shown to the user verbatim (ADR 0031 §1).
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize, JsonSchema)]
pub struct Reason {
    /// Stable code from the frozen ontology (ADR 0031 §4), e.g. `ema_cross_up`.
    pub code: String,
    /// The detector family.
    pub group: ReasonGroup,
    /// Which way this reason argues.
    pub polarity: ReasonPolarity,
    /// The positive term this detector contributed to the signal's `strength`.
    pub weight: f64,
    /// Short human text, e.g. `20-EMA crosses above 50-EMA`.
    pub label: String,
    /// One sentence citing the numbers.
    pub detail: String,
}

/// An explainable buy/sell point at one closed bar (ADR 0031 §1).
///
/// `reasons` is never empty: a signal with no reason is a bug, not a weaker signal. Construct
/// through the engine, or call [`Signal::validate`] after building one by hand.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize, JsonSchema)]
pub struct Signal {
    /// The instrument the signal belongs to.
    pub instrument: InstrumentId,
    /// The bar timeframe the signal was evaluated on (e.g. `5m`, `1d`).
    pub timeframe: String,
    /// The horizon ruleset that produced the signal.
    pub horizon: SignalHorizon,
    /// Close time of the confirming bar.
    pub t: UnixNanos,
    /// The direction suggested.
    pub direction: SignalDirection,
    /// Confluence strength in `0..=1`: the winning side's share of fired weight.
    pub strength: f64,
    /// The bar close, or the trigger level for a level break.
    pub anchor_price: f64,
    /// The ruleset id, e.g. `signals/day:v1`.
    pub ruleset: String,
    /// The reasons that fired at this bar; never empty.
    pub reasons: Vec<Reason>,
    /// Detector codes that fired, e.g. `["ema_cross", "rsi", "rel_volume"]`.
    pub tags: Vec<String>,
    /// Numbers the reason text cites, for inspection. Omitted when empty.
    #[serde(default, skip_serializing_if = "BTreeMap::is_empty")]
    pub evidence: BTreeMap<String, f64>,
}

/// Why a [`Signal`] is malformed.
#[derive(Clone, Debug, PartialEq)]
pub enum SignalError {
    /// `reasons` was empty.
    NoReasons,
    /// A field was not finite, or `strength` fell outside `0..=1`.
    OutOfRange {
        /// The offending field.
        field: &'static str,
        /// The offending value.
        value: f64,
    },
}

impl std::fmt::Display for SignalError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::NoReasons => write!(f, "signal has no reasons"),
            Self::OutOfRange { field, value } => {
                write!(f, "signal field `{field}` out of range: {value}")
            }
        }
    }
}

impl std::error::Error for SignalError {}

impl Signal {
    /// Checks the invariants the engine guarantees: at least one reason, finite prices, and
    /// `strength` within `0..=1`.
    ///
    /// # Errors
    ///
    /// [`SignalError`] describing the first violation.
    pub fn validate(&self) -> Result<(), SignalError> {
        if self.reasons.is_empty() {
            return Err(SignalError::NoReasons);
        }
        if !self.strength.is_finite() || !(0.0..=1.0).contains(&self.strength) {
            return Err(SignalError::OutOfRange {
                field: "strength",
                value: self.strength,
            });
        }
        if !self.anchor_price.is_finite() {
            return Err(SignalError::OutOfRange {
                field: "anchor_price",
                value: self.anchor_price,
            });
        }
        Ok(())
    }

    /// The detector codes that fired, de-duplicated and sorted.
    pub fn reason_codes(&self) -> Vec<&str> {
        let mut codes: Vec<&str> = self.reasons.iter().map(|r| r.code.as_str()).collect();
        codes.sort_unstable();
        codes.dedup();
        codes
    }
}

/// Request payload for the signal scan / `GET /signals` (ADR 0031 §6).
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize, JsonSchema)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct SignalScanRequest {
    /// Instruments to evaluate, as `EXCHANGE:SYMBOL` (e.g. `NSE:RELIANCE`).
    pub universe: Vec<String>,
    /// The bar timeframe to evaluate.
    pub timeframe: Timeframe,
    /// The horizon ruleset to apply.
    pub horizon: SignalHorizon,
    /// Drop signals weaker than this (`0..=1`); defaults to `0.0`.
    #[serde(default)]
    pub min_strength: f64,
    /// Restrict to these detector codes; empty means every detector in the ruleset.
    #[serde(default)]
    pub detectors: Vec<String>,
}

/// Response to a signal scan: the fired signals across the universe, newest last.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize, JsonSchema)]
pub struct SignalScanResponse {
    /// Count of signals returned.
    pub total: usize,
    /// The signals, ordered by `t` then instrument.
    pub signals: Vec<Signal>,
}
