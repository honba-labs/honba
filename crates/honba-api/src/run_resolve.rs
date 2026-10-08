//! `BacktestRequest::resolve` and `SweepRequest::resolve` (ADR 0017 decision 6).
//!
//! Pure, like `BarsQuery::resolve`: the wire DTOs keep every field optional, and the
//! strictness lives here. A missing or invalid field is `422 validation_invalid_request`
//! with `context.field`. Strategy lookup (catalog or registered factory) is a submit-time
//! step in the REST layer; [`unknown_strategy`] is its shared error.

use honba_messages::{ErrorDetail, UnixNanos};
use serde::{Deserialize, Serialize};
use serde_json::Value;

use crate::market::{invalid, parse_bound, parse_timeframe};
use crate::requests::{BacktestRequest, SweepRequest};

/// Bar specification used when `bar_spec` is omitted.
pub const DEFAULT_BAR_SPEC: &str = "1d";

/// Initial capital used when `initial_capital` is omitted.
///
/// Mirrors `honba_config::AccountConfig::default().starting_cash`; `honba-api` sits above
/// `honba-config` in the layering and cannot read it, so the REST crate pins the two equal.
pub const DEFAULT_INITIAL_CAPITAL: f64 = 1_000_000.0;

/// A [`BacktestRequest`] with every required field present and valid.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct ResolvedBacktest {
    /// Strategy content id or registered name (not yet looked up).
    pub strategy: String,
    /// Universe of instruments.
    pub universe: String,
    /// Inclusive start.
    pub start: UnixNanos,
    /// Exclusive end, strictly after `start`.
    pub end: UnixNanos,
    /// Bar specification text, e.g. `1d`.
    pub bar_spec: String,
    /// Initial capital, finite and positive.
    pub initial_capital: f64,
    /// Seed, non-zero.
    pub seed: u64,
}

/// A [`SweepRequest`] with every required field present and valid.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct ResolvedSweep {
    /// Strategy content id or registered name (not yet looked up).
    pub strategy: String,
    /// Parameter ranges: a non-empty JSON object.
    pub params: Value,
    /// Number of trials, at least one.
    pub trials: u64,
    /// Seed, non-zero.
    pub seed: u64,
}

/// A resolved run request of either kind (what a manifest pins).
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
#[serde(tag = "kind", rename_all = "snake_case")]
pub enum ResolvedRequest {
    /// A single backtest.
    Backtest(ResolvedBacktest),
    /// A parameter sweep.
    Sweep(ResolvedSweep),
}

/// The error for a `strategy` that is neither a catalog content id nor a registered name.
pub fn unknown_strategy(strategy: &str) -> ErrorDetail {
    invalid(
        "strategy",
        "unknown_strategy",
        format!("strategy {strategy:?} is not in the catalog or the registered strategies"),
    )
}

fn required<'a>(field: &str, value: &'a Option<String>) -> Result<&'a str, ErrorDetail> {
    match value.as_deref().map(str::trim) {
        Some(text) if !text.is_empty() => Ok(text),
        Some(_) => Err(invalid(
            field,
            "empty",
            format!("{field} must not be empty"),
        )),
        None => Err(invalid(field, "missing", format!("{field} is required"))),
    }
}

fn seed(value: Option<u64>) -> Result<u64, ErrorDetail> {
    match value {
        None => Err(invalid("seed", "missing", "seed is required")),
        Some(0) => Err(invalid("seed", "zero_seed", "seed must be non-zero")),
        Some(seed) => Ok(seed),
    }
}

impl BacktestRequest {
    /// Resolves a submit: required fields present, times parsed, defaults applied.
    pub fn resolve(&self) -> Result<ResolvedBacktest, ErrorDetail> {
        let seed = seed(self.seed)?;
        let strategy = required("strategy", &self.strategy)?.to_owned();
        let universe = required("universe", &self.universe)?.to_owned();
        let start = parse_bound("start", required("start", &self.start)?)?;
        let end = parse_bound("end", required("end", &self.end)?)?;
        if start >= end {
            return Err(invalid(
                "end",
                "empty_range",
                "`start` must be strictly before `end`",
            ));
        }
        let bar_spec = self
            .bar_spec
            .as_deref()
            .map_or(DEFAULT_BAR_SPEC, str::trim)
            .to_owned();
        parse_timeframe(&bar_spec)
            .map_err(|e| invalid("bar_spec", "invalid_bar_spec", e.message))?;
        let initial_capital = self.initial_capital.unwrap_or(DEFAULT_INITIAL_CAPITAL);
        if !(initial_capital.is_finite() && initial_capital > 0.0) {
            return Err(invalid(
                "initial_capital",
                "not_positive",
                "initial_capital must be a finite positive number",
            ));
        }
        Ok(ResolvedBacktest {
            strategy,
            universe,
            start,
            end,
            bar_spec,
            initial_capital,
            seed,
        })
    }
}

impl SweepRequest {
    /// Resolves a submit: required fields present and valid.
    pub fn resolve(&self) -> Result<ResolvedSweep, ErrorDetail> {
        let seed = seed(self.seed)?;
        let strategy = required("strategy", &self.strategy)?.to_owned();
        let params = match &self.params {
            None => return Err(invalid("params", "missing", "params is required")),
            Some(Value::Object(map)) if !map.is_empty() => Value::Object(map.clone()),
            Some(_) => {
                return Err(invalid(
                    "params",
                    "not_an_object",
                    "params must be a non-empty JSON object of name to [low, high, step]",
                ))
            }
        };
        let trials = match self.trials {
            None => return Err(invalid("trials", "missing", "trials is required")),
            Some(0) => return Err(invalid("trials", "zero", "trials must be at least 1")),
            Some(n) => n,
        };
        Ok(ResolvedSweep {
            strategy,
            params,
            trials,
            seed,
        })
    }
}
