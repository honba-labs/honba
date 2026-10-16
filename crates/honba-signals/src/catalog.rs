//! The frozen reason ontology, the detector families, the horizon rulesets and the detector
//! parameters (ADR 0031 §4, §5).
//!
//! Weights and labels live here, in one auditable place: a detector decides only *when* a
//! [`ReasonCode`] fires and cites its numbers; the engine reads the group, polarity and weight
//! from this table. Adding a code is an additive change; renaming or reusing one is not.

use honba_entities::{ReasonGroup, ReasonPolarity, SignalHorizon};

/// A frozen reason code: the stable identity of one detector firing (ADR 0031 §4).
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct ReasonCode {
    /// The stable wire code, e.g. `ema_cross_up`.
    pub code: &'static str,
    /// The detector family.
    pub group: ReasonGroup,
    /// Which way this reason argues.
    pub polarity: ReasonPolarity,
    /// The positive term this code contributes to a signal's `strength`.
    pub weight: f64,
    /// Short human label shown in the reasons panel.
    pub label: &'static str,
}

/// Every reason code the engine can emit, in catalogue order.
pub const REASON_CODES: &[ReasonCode] = &[
    // --- Trend ---
    ReasonCode {
        code: "ema_cross_up",
        group: ReasonGroup::Trend,
        polarity: ReasonPolarity::Bullish,
        weight: 0.45,
        label: "Fast EMA crossed above slow EMA",
    },
    ReasonCode {
        code: "ema_cross_down",
        group: ReasonGroup::Trend,
        polarity: ReasonPolarity::Bearish,
        weight: 0.45,
        label: "Fast EMA crossed below slow EMA",
    },
    // --- Momentum ---
    ReasonCode {
        code: "rsi_oversold_turn",
        group: ReasonGroup::Momentum,
        polarity: ReasonPolarity::Bullish,
        weight: 0.35,
        label: "RSI turned up out of oversold",
    },
    ReasonCode {
        code: "rsi_overbought_turn",
        group: ReasonGroup::Momentum,
        polarity: ReasonPolarity::Bearish,
        weight: 0.35,
        label: "RSI turned down out of overbought",
    },
    ReasonCode {
        code: "macd_cross_up",
        group: ReasonGroup::Momentum,
        polarity: ReasonPolarity::Bullish,
        weight: 0.35,
        label: "MACD crossed above its signal line",
    },
    ReasonCode {
        code: "macd_cross_down",
        group: ReasonGroup::Momentum,
        polarity: ReasonPolarity::Bearish,
        weight: 0.35,
        label: "MACD crossed below its signal line",
    },
    // --- Volume ---
    ReasonCode {
        code: "rel_volume_spike_up",
        group: ReasonGroup::Volume,
        polarity: ReasonPolarity::Bullish,
        weight: 0.25,
        label: "Relative-volume spike on an up bar",
    },
    ReasonCode {
        code: "rel_volume_spike_down",
        group: ReasonGroup::Volume,
        polarity: ReasonPolarity::Bearish,
        weight: 0.25,
        label: "Relative-volume spike on a down bar",
    },
];

/// Looks up a reason code by its wire string.
pub fn reason_code(code: &str) -> Option<&'static ReasonCode> {
    REASON_CODES.iter().find(|c| c.code == code)
}

/// A detector family and the codes it can emit.
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct DetectorSpec {
    /// Catalogue name, e.g. `ema_cross`.
    pub name: &'static str,
    /// The codes this detector may fire.
    pub codes: &'static [&'static str],
}

/// Every detector the engine knows, in catalogue order.
pub const DETECTORS: &[DetectorSpec] = &[
    DetectorSpec {
        name: "ema_cross",
        codes: &["ema_cross_up", "ema_cross_down"],
    },
    DetectorSpec {
        name: "rsi_turn",
        codes: &["rsi_oversold_turn", "rsi_overbought_turn"],
    },
    DetectorSpec {
        name: "macd_cross",
        codes: &["macd_cross_up", "macd_cross_down"],
    },
    DetectorSpec {
        name: "relative_volume",
        codes: &["rel_volume_spike_up", "rel_volume_spike_down"],
    },
];

/// A horizon ruleset: which detectors run, and the confluence/cooldown gates.
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct Ruleset {
    /// Stated on every signal this ruleset emits, e.g. `signals/day:v1`.
    pub id: &'static str,
    /// The horizon.
    pub horizon: SignalHorizon,
    /// Drop a signal whose winning-side weight is below this (`0..=1`).
    pub min_strength: f64,
    /// Suppress the same side for this many bars after it fires (`0` disables).
    pub cooldown_bars: usize,
    /// The detector names enabled (each must match a [`DETECTORS`] entry).
    pub detectors: &'static [&'static str],
}

/// The shipped rulesets, one per horizon (ADR 0031 §5).
pub const RULESETS: &[Ruleset] = &[
    Ruleset {
        id: "signals/day:v1",
        horizon: SignalHorizon::Intraday,
        min_strength: 0.4,
        cooldown_bars: 3,
        detectors: &["ema_cross", "rsi_turn", "macd_cross", "relative_volume"],
    },
    Ruleset {
        id: "signals/swing:v1",
        horizon: SignalHorizon::Swing,
        min_strength: 0.4,
        cooldown_bars: 0,
        detectors: &["ema_cross", "rsi_turn", "macd_cross", "relative_volume"],
    },
];

/// Looks up a ruleset by id.
pub fn ruleset(id: &str) -> Option<&'static Ruleset> {
    RULESETS.iter().find(|r| r.id == id)
}

/// The shipped ruleset for a horizon.
pub fn ruleset_for(horizon: SignalHorizon) -> &'static Ruleset {
    RULESETS
        .iter()
        .find(|r| r.horizon == horizon)
        .expect("every horizon has a ruleset")
}

/// Detector parameters: the indicator windows and thresholds one evaluation uses.
///
/// These differ by horizon ([`Params::for_horizon`]) but the machinery does not: the same
/// detectors run with different numbers.
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct Params {
    /// Fast EMA period for the trend detector.
    pub ema_fast: usize,
    /// Slow EMA period for the trend detector.
    pub ema_slow: usize,
    /// RSI period.
    pub rsi_period: usize,
    /// RSI level the market must leave for an oversold turn.
    pub rsi_oversold: f64,
    /// RSI level the market must leave for an overbought turn.
    pub rsi_overbought: f64,
    /// MACD fast EMA period.
    pub macd_fast: usize,
    /// MACD slow EMA period.
    pub macd_slow: usize,
    /// MACD signal EMA period.
    pub macd_signal: usize,
    /// Volume moving-average window.
    pub vol_window: usize,
    /// Multiple of the average volume that counts as a spike.
    pub rel_volume: f64,
}

impl Params {
    /// The default parameters for a horizon.
    pub fn for_horizon(horizon: SignalHorizon) -> Self {
        match horizon {
            SignalHorizon::Intraday => Self {
                ema_fast: 9,
                ema_slow: 21,
                rsi_period: 14,
                rsi_oversold: 30.0,
                rsi_overbought: 70.0,
                macd_fast: 12,
                macd_slow: 26,
                macd_signal: 9,
                vol_window: 20,
                rel_volume: 1.5,
            },
            SignalHorizon::Swing => Self {
                ema_fast: 20,
                ema_slow: 50,
                rsi_period: 14,
                rsi_oversold: 30.0,
                rsi_overbought: 70.0,
                macd_fast: 12,
                macd_slow: 26,
                macd_signal: 9,
                vol_window: 20,
                rel_volume: 1.2,
            },
        }
    }
}

impl Default for Params {
    fn default() -> Self {
        Self::for_horizon(SignalHorizon::Swing)
    }
}
