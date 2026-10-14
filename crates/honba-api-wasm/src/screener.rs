//! Pure screener evaluation, free of any wasm-bindgen types.
//!
//! Everything here runs natively, so it is conformance-tested without a wasm runtime (see
//! `tests/screener_conformance.rs`). The `#[wasm_bindgen]` layer in [`crate`] only forwards to
//! [`scan_predicate_json`] and [`scan_group_json`].
//!
//! The evaluator itself is `honba_indicators::screener` (L3); this module is the thin adaptation
//! from the frontend's column-oriented arrays (`open`/`high`/`low`/`close`/`volume`) to the
//! chronological [`Bar`] history the evaluator reads, plus the JSON encoding of the outcome.
//! Nothing is reimplemented: a metric, operator or filter error surfaces as the evaluator's own
//! message.
//!
//! # Wire contract
//!
//! A scan returns the JSON string `{"result": bool, "metrics": {key: number|null}}`. `null` is a
//! metric still in warm-up (the evaluator returns `None`), which is distinct from a missing key.
//!
//! Invalid input is refused with a message rather than answered, matching the indicator exports: a
//! malformed filter JSON, unequal series lengths, a non-finite value, an inconsistent OHLC row, an
//! unsupported metric, an operator/operand mismatch, or an out-of-limits filter tree all throw a
//! JavaScript `Error` carrying the message. In particular a bar-only dataset never reports an
//! unsupported (e.g. fundamental) metric as a non-match.

use std::collections::BTreeMap;
use std::fmt;

use honba_entities::{ScreenerFilterGroup, ScreenerFilterPredicate};
use honba_indicators::screener::{
    evaluate_group, evaluate_predicate, group_metric_keys, latest_metrics, ScreenerError,
};
use honba_messages::{
    Bar, BarAggregation, BarSpecification, BarType, ErrorCode, Exchange, InstrumentId, PriceType,
    UnixNanos,
};
use serde_json::{json, Value};

/// Nanoseconds in one day; the bars carry a synthetic one-day-apart timestamp.
const DAY_NANOS: u64 = 86_400_000_000_000;

/// Why a screener request over column-oriented arrays was refused.
///
/// [`reason`](Self::reason) is the stable machine-readable half of the refusal.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum WasmScreenerError {
    /// A predicate or group JSON document is malformed (unknown fields, bad types, ...).
    InvalidJson {
        /// Which document failed to parse: `"predicate"` or `"group"`.
        what: &'static str,
        /// The underlying parser message.
        message: String,
    },
    /// The five input series do not all have the same length.
    LengthMismatch {
        /// Length of `open`.
        open: usize,
        /// Length of `high`.
        high: usize,
        /// Length of `low`.
        low: usize,
        /// Length of `close`.
        close: usize,
        /// Length of `volume`.
        volume: usize,
    },
    /// A series holds a `NaN` or infinity at this index.
    NonFiniteInput {
        /// Which series: `"open"`, `"high"`, `"low"`, `"close"` or `"volume"`.
        series: &'static str,
        /// Index of the offending value.
        index: usize,
    },
    /// A row cannot be a valid bar (`low > high`, `open`/`close` outside the range, negative
    /// volume). Kept out of [`Bar::new`], whose debug assertion would otherwise panic.
    InvalidBar {
        /// Index of the offending row.
        index: usize,
        /// What is wrong.
        message: &'static str,
    },
    /// The filter itself cannot be evaluated; the evaluator's own message is reused.
    Screener(ScreenerError),
}

impl WasmScreenerError {
    /// A stable machine-readable reason in a `reason: message` pair (ADR 0012 rule 4).
    ///
    /// Caller-facing input failures (malformed predicate or group JSON, unequal series lengths,
    /// non-finite input, an inconsistent OHLC row) all reuse the REST
    /// [`ErrorCode::ValidationInvalidRequest`] code, matching `POST /screener/scan`; evaluator
    /// failures reuse the evaluator's own reason (`unsupported_metric`, `invalid_operand`,
    /// `invalid_filter`).
    #[must_use]
    pub fn reason(&self) -> &'static str {
        match self {
            Self::InvalidJson { .. }
            | Self::LengthMismatch { .. }
            | Self::NonFiniteInput { .. }
            | Self::InvalidBar { .. } => ErrorCode::ValidationInvalidRequest.as_str(),
            Self::Screener(error) => error.reason(),
        }
    }
}

impl fmt::Display for WasmScreenerError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::InvalidJson { what, message } => write!(f, "invalid {what}: {message}"),
            Self::LengthMismatch {
                open,
                high,
                low,
                close,
                volume,
            } => write!(
                f,
                "series lengths differ: open={open}, high={high}, low={low}, close={close}, \
                 volume={volume}"
            ),
            Self::NonFiniteInput { series, index } => {
                write!(f, "non-finite {series} input at index {index}")
            }
            Self::InvalidBar { index, message } => {
                write!(f, "invalid bar at index {index}: {message}")
            }
            Self::Screener(e) => write!(f, "{e}"),
        }
    }
}

impl From<ScreenerError> for WasmScreenerError {
    fn from(e: ScreenerError) -> Self {
        Self::Screener(e)
    }
}

/// Renders an error as a `reason: message` pair (ADR 0012 rule 4) for the `Result<_, String>`
/// adaptation fns below.
fn error_pair(error: impl Into<WasmScreenerError>) -> String {
    let error = error.into();
    format!("{}: {error}", error.reason())
}

/// The stable identity of the synthetic bars: instrument `X` on `NSE`, one-day `Last` bars.
///
/// The evaluator derives `price_52_week_high`/`price_52_week_low` from a 252-bar window count, so
/// the exact timestamps do not matter; this identity matches the native evaluator's conformance
/// test so both surfaces read the same bars.
fn bar_type() -> BarType {
    BarType::new(
        InstrumentId::new("X", Exchange::new("NSE")),
        BarSpecification::new(1, BarAggregation::Day, PriceType::Last),
    )
}

fn finite_series(name: &'static str, series: &[f64]) -> Result<(), WasmScreenerError> {
    match series.iter().position(|v| !v.is_finite()) {
        Some(index) => Err(WasmScreenerError::NonFiniteInput {
            series: name,
            index,
        }),
        None => Ok(()),
    }
}

/// Builds a chronological bar history from the column-oriented input arrays.
///
/// Rejects unequal lengths, any non-finite value (in `open`, `high`, `low`, `close` order, then
/// `volume`), and rows that violate the `Bar` invariants (`low <= high`, `open`/`close` inside
/// `[low, high]`, non-negative volume). Empty arrays are allowed and give an empty history.
fn bars_from_arrays(
    open: &[f64],
    high: &[f64],
    low: &[f64],
    close: &[f64],
    volume: &[f64],
) -> Result<Vec<Bar>, WasmScreenerError> {
    let lengths = [open.len(), high.len(), low.len(), close.len(), volume.len()];
    if lengths.iter().any(|&n| n != open.len()) {
        return Err(WasmScreenerError::LengthMismatch {
            open: open.len(),
            high: high.len(),
            low: low.len(),
            close: close.len(),
            volume: volume.len(),
        });
    }
    for (name, series) in [
        ("open", open),
        ("high", high),
        ("low", low),
        ("close", close),
        ("volume", volume),
    ] {
        finite_series(name, series)?;
    }
    let bar_type = bar_type();
    let mut bars = Vec::with_capacity(open.len());
    for i in 0..open.len() {
        let (o, h, l, c, v) = (open[i], high[i], low[i], close[i], volume[i]);
        let message = if l > h {
            Some("low is above high")
        } else if o < l || o > h {
            Some("open is outside the high/low range")
        } else if c < l || c > h {
            Some("close is outside the high/low range")
        } else if v < 0.0 {
            Some("volume is negative")
        } else {
            None
        };
        if let Some(message) = message {
            return Err(WasmScreenerError::InvalidBar { index: i, message });
        }
        let ts = UnixNanos::from_u64(i as u64 * DAY_NANOS);
        bars.push(Bar::new(bar_type.clone(), o, h, l, c, v, ts, ts));
    }
    Ok(bars)
}

fn parse_predicate(predicate_json: &str) -> Result<ScreenerFilterPredicate, WasmScreenerError> {
    serde_json::from_str(predicate_json).map_err(|e| WasmScreenerError::InvalidJson {
        what: "predicate",
        message: e.to_string(),
    })
}

fn parse_group(group_json: &str) -> Result<ScreenerFilterGroup, WasmScreenerError> {
    serde_json::from_str(group_json).map_err(|e| WasmScreenerError::InvalidJson {
        what: "group",
        message: e.to_string(),
    })
}

/// Every metric key a predicate reads: its left side, then a right-side metric reference.
fn predicate_keys(pred: &ScreenerFilterPredicate) -> Vec<String> {
    let mut keys = vec![pred.key.clone()];
    if let Some(reference) = pred.metric_ref() {
        keys.push(reference.key);
    }
    keys
}

fn f64_value(x: f64) -> Value {
    serde_json::Number::from_f64(x).map_or(Value::Null, Value::Number)
}

/// Renders `{"result": bool, "metrics": {key: number|null}}`.
fn scan_json(result: bool, metrics: BTreeMap<String, Option<f64>>) -> String {
    let metrics: serde_json::Map<String, Value> = metrics
        .into_iter()
        .map(|(key, value)| (key, value.map_or(Value::Null, f64_value)))
        .collect();
    json!({ "result": result, "metrics": Value::Object(metrics) }).to_string()
}

/// Evaluates one predicate JSON document against a bar history built from the input arrays.
///
/// `predicate_json` is the wire form of a `ScreenerFilterPredicate`, e.g.
/// `{"key":"close","op":"gt","value":10}`.
///
/// # Errors
///
/// A `reason: message` pair for malformed predicate JSON, unequal lengths, non-finite input, an
/// inconsistent OHLC row, or an [`ScreenerError`] (unknown/unsupported metric, bad operand, bad
/// filter tree). The `reason` is the stable [`WasmScreenerError::reason`] string.
pub fn evaluate_predicate_json(
    predicate_json: &str,
    open: &[f64],
    high: &[f64],
    low: &[f64],
    close: &[f64],
    volume: &[f64],
) -> Result<bool, String> {
    let pred = parse_predicate(predicate_json).map_err(error_pair)?;
    let bars = bars_from_arrays(open, high, low, close, volume).map_err(error_pair)?;
    evaluate_predicate(&pred, &bars).map_err(error_pair)
}

/// Evaluates one filter group JSON document (AND/OR, possibly nested) against the input arrays.
///
/// # Errors
///
/// As [`evaluate_predicate_json`], plus group shape/size/operator errors.
pub fn evaluate_group_json(
    group_json: &str,
    open: &[f64],
    high: &[f64],
    low: &[f64],
    close: &[f64],
    volume: &[f64],
) -> Result<bool, String> {
    let group = parse_group(group_json).map_err(error_pair)?;
    let bars = bars_from_arrays(open, high, low, close, volume).map_err(error_pair)?;
    evaluate_group(&group, &bars).map_err(error_pair)
}

/// Evaluates a predicate and reports both its `result` and the latest `metrics` it reads.
///
/// Returns the JSON string `{"result": bool, "metrics": {key: number|null}}`, where a `null`
/// metric is still in warm-up. For a single predicate the metrics are its key and any right-hand
/// metric reference.
///
/// # Errors
///
/// As [`evaluate_predicate_json`].
pub fn scan_predicate_json(
    predicate_json: &str,
    open: &[f64],
    high: &[f64],
    low: &[f64],
    close: &[f64],
    volume: &[f64],
) -> Result<String, String> {
    let pred = parse_predicate(predicate_json).map_err(error_pair)?;
    let bars = bars_from_arrays(open, high, low, close, volume).map_err(error_pair)?;
    let result = evaluate_predicate(&pred, &bars).map_err(error_pair)?;
    let metrics = latest_metrics(&predicate_keys(&pred), &bars).map_err(error_pair)?;
    Ok(scan_json(result, metrics))
}

/// Evaluates a group and reports both its `result` and the latest value of every metric it reads
/// (in first-seen order, without repeats).
///
/// Returns the same JSON shape as [`scan_predicate_json`].
///
/// # Errors
///
/// As [`evaluate_group_json`].
pub fn scan_group_json(
    group_json: &str,
    open: &[f64],
    high: &[f64],
    low: &[f64],
    close: &[f64],
    volume: &[f64],
) -> Result<String, String> {
    let group = parse_group(group_json).map_err(error_pair)?;
    let bars = bars_from_arrays(open, high, low, close, volume).map_err(error_pair)?;
    let result = evaluate_group(&group, &bars).map_err(error_pair)?;
    let keys = group_metric_keys(&group).map_err(error_pair)?;
    let metrics = latest_metrics(&keys, &bars).map_err(error_pair)?;
    Ok(scan_json(result, metrics))
}
