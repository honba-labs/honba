//! Pure indicator-series compute, free of any wasm-bindgen types.
//!
//! Everything here runs natively, so it is unit- and conformance-tested without a wasm runtime.
//! The `#[wasm_bindgen]` layer in [`crate`] only forwards to these functions.
//!
//! The indicators are the streaming implementations from `honba-indicators`, driven over a whole
//! series: `out[i]` is the indicator after consuming `closes[0..=i]`, and is `NaN` while the
//! indicator is warming up. The output always has the same length as the input.

use std::fmt;

use honba_indicators::{Atr, BollingerBands, Ema, Indicator, Macd, Rsi, Sma};
use serde::Deserialize;
use serde_json::json;

/// Largest accepted period; bounds the memory an untrusted `params_json` can request.
pub const MAX_PERIOD: usize = 1_000_000;

/// Why an indicator request was refused.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum IndicatorError {
    /// The indicator name is not in the catalog.
    UnknownIndicator(String),
    /// `params_json` is malformed, incomplete or out of range.
    InvalidParams(String),
    /// The input contains a `NaN` or infinity at this index.
    NonFiniteInput(usize),
    /// Finite input overflowed: the output is infinite, or `NaN` after warm-up, at this index.
    NonFiniteOutput(usize),
    /// The `high`, `low` and `close` series do not have the same length.
    LengthMismatch {
        /// Length of `high`.
        high: usize,
        /// Length of `low`.
        low: usize,
        /// Length of `close`.
        close: usize,
    },
}

impl fmt::Display for IndicatorError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::UnknownIndicator(n) => write!(f, "unknown indicator `{n}`"),
            Self::InvalidParams(m) => write!(f, "invalid params: {m}"),
            Self::NonFiniteInput(i) => write!(f, "non-finite input at index {i}"),
            Self::NonFiniteOutput(i) => {
                write!(f, "numeric overflow: non-finite output at index {i}")
            }
            Self::LengthMismatch { high, low, close } => write!(
                f,
                "series lengths differ: high={high}, low={low}, close={close}"
            ),
        }
    }
}

impl std::error::Error for IndicatorError {}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct PeriodParams {
    period: usize,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct BollingerParams {
    period: usize,
    #[serde(default = "default_k")]
    k: f64,
    #[serde(default = "default_middle")]
    output: String,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct MacdParams {
    #[serde(default = "default_fast")]
    fast: usize,
    #[serde(default = "default_slow")]
    slow: usize,
    #[serde(default = "default_signal")]
    signal: usize,
    #[serde(default = "default_macd")]
    output: String,
}

fn default_k() -> f64 {
    2.0
}
fn default_middle() -> String {
    "middle".into()
}
fn default_fast() -> usize {
    12
}
fn default_slow() -> usize {
    26
}
fn default_signal() -> usize {
    9
}
fn default_macd() -> String {
    "macd".into()
}

fn parse<'a, T: Deserialize<'a>>(params_json: &'a str) -> Result<T, IndicatorError> {
    let text = if params_json.trim().is_empty() {
        "{}"
    } else {
        params_json
    };
    serde_json::from_str(text).map_err(|e| IndicatorError::InvalidParams(e.to_string()))
}

fn period(name: &str, v: usize) -> Result<usize, IndicatorError> {
    if v == 0 || v > MAX_PERIOD {
        return Err(IndicatorError::InvalidParams(format!(
            "`{name}` must be in 1..={MAX_PERIOD}, got {v}"
        )));
    }
    Ok(v)
}

fn pick(output: &str, allowed: &[&str]) -> Result<usize, IndicatorError> {
    allowed.iter().position(|a| *a == output).ok_or_else(|| {
        IndicatorError::InvalidParams(format!(
            "`output` must be one of {allowed:?}, got {output:?}"
        ))
    })
}

fn run<I>(mut ind: I, closes: &[f64], f: impl Fn(I::Output) -> f64) -> Vec<f64>
where
    I: for<'a> Indicator<Input<'a> = f64>,
{
    closes
        .iter()
        .map(|&c| ind.update(c).map_or(f64::NAN, &f))
        .collect()
}

/// Leading NaNs are warm-up. An infinity, or a NaN after the first value, means finite input
/// overflowed f64 (e.g. a running sum of 1e308 values); refuse rather than return garbage.
fn check_output(out: &[f64]) -> Result<(), IndicatorError> {
    let first = out.iter().position(|v| !v.is_nan()).unwrap_or(out.len());
    match out
        .iter()
        .enumerate()
        .position(|(i, v)| v.is_infinite() || (i > first && v.is_nan()))
    {
        Some(i) => Err(IndicatorError::NonFiniteOutput(i)),
        None => Ok(()),
    }
}

/// Computes the full series of indicator `name` over `closes`.
///
/// `params_json` is a JSON object (see [`list_indicators_json`]); blank means `{}`. Unknown
/// fields are rejected. Multi-output indicators (`macd`, `bollinger`) select one output with the
/// `output` param.
///
/// # Errors
///
/// [`IndicatorError`] for an unknown name, bad params, non-finite input, or finite input whose
/// result overflows `f64` (infinite output, or `NaN` after warm-up).
pub fn indicator_series(
    name: &str,
    params_json: &str,
    closes: &[f64],
) -> Result<Vec<f64>, IndicatorError> {
    // Validate params before inspecting the data so a bad request fails the same way on any input.
    enum Plan {
        Sma(usize),
        Ema(usize),
        Rsi(usize),
        Bollinger(usize, f64, usize),
        Macd(usize, usize, usize, usize),
    }
    let plan = match name {
        "sma" => Plan::Sma(period(
            "period",
            parse::<PeriodParams>(params_json)?.period,
        )?),
        "ema" => Plan::Ema(period(
            "period",
            parse::<PeriodParams>(params_json)?.period,
        )?),
        "rsi" => Plan::Rsi(period(
            "period",
            parse::<PeriodParams>(params_json)?.period,
        )?),
        "bollinger" => {
            let p: BollingerParams = parse(params_json)?;
            if !p.k.is_finite() || p.k < 0.0 {
                return Err(IndicatorError::InvalidParams(format!(
                    "`k` must be finite and >= 0, got {}",
                    p.k
                )));
            }
            let out = pick(&p.output, &["middle", "upper", "lower"])?;
            Plan::Bollinger(period("period", p.period)?, p.k, out)
        }
        "macd" => {
            let p: MacdParams = parse(params_json)?;
            let (fast, slow, signal) = (
                period("fast", p.fast)?,
                period("slow", p.slow)?,
                period("signal", p.signal)?,
            );
            if fast >= slow {
                return Err(IndicatorError::InvalidParams(format!(
                    "`fast` ({fast}) must be less than `slow` ({slow})"
                )));
            }
            let out = pick(&p.output, &["macd", "signal", "histogram"])?;
            Plan::Macd(fast, slow, signal, out)
        }
        other => return Err(IndicatorError::UnknownIndicator(other.to_string())),
    };
    if let Some(i) = closes.iter().position(|c| !c.is_finite()) {
        return Err(IndicatorError::NonFiniteInput(i));
    }
    let out = match plan {
        Plan::Sma(n) => run(Sma::new(n), closes, |v| v),
        Plan::Ema(n) => run(Ema::new(n), closes, |v| v),
        Plan::Rsi(n) => run(Rsi::new(n), closes, |v| v),
        Plan::Bollinger(n, k, out) => run(BollingerBands::new(n, k), closes, |v| {
            [v.middle, v.upper, v.lower][out]
        }),
        Plan::Macd(f, s, g, out) => run(Macd::new(f, s, g), closes, |v| {
            [v.macd, v.signal, v.histogram][out]
        }),
    };
    check_output(&out)?;
    Ok(out)
}

/// Computes the full series of OHLC-input indicator `name` over equal-length `high`, `low` and
/// `close` series (currently `atr`: Wilder, `period` required).
///
/// Same conventions as [`indicator_series`]: params are validated first (unknown fields rejected,
/// `period` in `1..=MAX_PERIOD`), then lengths, then finiteness; the output has the input's
/// length with `period - 1` leading `NaN`s (the first bar's true range is `high - low`); empty
/// input gives empty output; infinite output from finite input is [`IndicatorError::NonFiniteOutput`].
///
/// # Errors
///
/// [`IndicatorError`] for an unknown (or close-only) name, bad params, unequal lengths,
/// non-finite input, or overflow.
pub fn ohlc_indicator_series(
    name: &str,
    params_json: &str,
    high: &[f64],
    low: &[f64],
    close: &[f64],
) -> Result<Vec<f64>, IndicatorError> {
    let n = match name {
        "atr" => period("period", parse::<PeriodParams>(params_json)?.period)?,
        other => return Err(IndicatorError::UnknownIndicator(other.to_string())),
    };
    if high.len() != low.len() || high.len() != close.len() {
        return Err(IndicatorError::LengthMismatch {
            high: high.len(),
            low: low.len(),
            close: close.len(),
        });
    }
    for series in [high, low, close] {
        if let Some(i) = series.iter().position(|v| !v.is_finite()) {
            return Err(IndicatorError::NonFiniteInput(i));
        }
    }
    let mut atr = Atr::new(n);
    let out: Vec<f64> = high
        .iter()
        .zip(low)
        .zip(close)
        .map(|((&h, &l), &c)| atr.update_hlc(h, l, c).unwrap_or(f64::NAN))
        .collect();
    check_output(&out)?;
    Ok(out)
}

/// Describes the available indicators as JSON: names, inputs, params, outputs and warm-up.
///
/// Shape: `{"warmup_value":"NaN","indicators":[{"name","input","params":[{"name","type",
/// "required","default"?,"min"?}],"outputs":[..],"warmup":".."}]}`. `input` is `close` for
/// [`indicator_series`] indicators and `ohlc` for [`ohlc_indicator_series`] ones (those also carry
/// `"export":"ohlc_indicator_series"`). The `warmup` text is the
/// number of leading `NaN` values.
pub fn list_indicators_json() -> String {
    let period = json!({"name": "period", "type": "integer", "required": true, "min": 1,
        "max": MAX_PERIOD});
    let int = |n: &str, d: usize| {
        json!({"name": n, "type": "integer", "required": false, "default": d, "min": 1,
            "max": MAX_PERIOD})
    };
    let output = |opts: &[&str]| {
        json!({"name": "output", "type": "string", "required": false, "default": opts[0],
            "values": opts})
    };
    json!({
        "warmup_value": "NaN",
        "indicators": [
            {"name": "sma", "input": "close", "params": [period], "outputs": ["value"],
             "warmup": "period - 1"},
            {"name": "ema", "input": "close", "params": [period], "outputs": ["value"],
             "warmup": "period - 1"},
            {"name": "rsi", "input": "close", "params": [period], "outputs": ["value"],
             "warmup": "period"},
            {"name": "macd", "input": "close",
             "params": [int("fast", 12), int("slow", 26), int("signal", 9),
                        output(&["macd", "signal", "histogram"])],
             "outputs": ["macd", "signal", "histogram"], "warmup": "slow + signal - 2"},
            {"name": "bollinger", "input": "close",
             "params": [period, {"name": "k", "type": "number", "required": false,
                                 "default": 2.0, "min": 0},
                        output(&["middle", "upper", "lower"])],
             "outputs": ["middle", "upper", "lower"], "warmup": "period - 1"},
            {"name": "atr", "input": "ohlc", "export": "ohlc_indicator_series",
             "params": [period], "outputs": ["value"], "warmup": "period - 1"},
        ],
    })
    .to_string()
}
