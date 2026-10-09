//! The pre-trade risk stage for Python (ADR 0018 decision 10).
//!
//! `_honba.TradingState`, `_honba.RiskLimits`, `_honba.RiskDecision` and `_honba.RiskStage`
//! are thin shells over `honba-risk`; the parsing and shaping live in plain functions
//! (`parse_limits`, `parse_request`, `build_stage`, `decision_json`, `parse_run_risk`) so they
//! are unit-testable without an interpreter. Everything crossing the boundary is JSON-shaped,
//! so the rules and their numbers stay identical to the Rust conformance runner.

// PyO3 0.22 `#[pyclass]`/`#[pymethods]` expansion trips this lint on `PyResult` returns.
#![allow(clippy::useless_conversion)]

use std::collections::BTreeMap;
use std::sync::Arc;

use honba_entities::{Currency, Instrument};
use honba_market::{InstrumentRules, MarketRegistry, PriceBand};
use honba_messages::{InstrumentId, OrderId, OrderSide, TradingState, UnixNanos};
use honba_risk::{
    OrderRateLimit, ProfileRulesSource, RiskCheck, RiskDecision, RiskLimits, RiskRequest,
    RiskStage, RulesSource,
};
use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use pyo3::types::{PyDict, PyModule};
use serde_json::{json, Map, Value};

use super::run::parse_instrument;

const REQUEST_KEYS: [&str; 11] = [
    "order_id",
    "instrument_id",
    "side",
    "quantity",
    "price",
    "trigger_price",
    "reference_price",
    "adv",
    "position",
    "trading_state",
    "ts",
];
const INSTRUMENT_KEYS: [&str; 8] = [
    "instrument_id",
    "kind",
    "currency",
    "lot_size",
    "tick_size",
    "min_order_quantity",
    "max_order_quantity",
    "band",
];

fn req_err(msg: impl std::fmt::Display) -> String {
    format!("invalid risk request: {msg}")
}

/// Parses a `[risk]` table (`max_notional`, `order_rate = {max_orders, window_ms}`) with the
/// same serde parser the Rust config uses, then validates the values.
pub(crate) fn parse_limits(v: &Value) -> Result<RiskLimits, String> {
    // serde reads a struct from a sequence too; a `[risk]` table never means that.
    if v.get("order_rate")
        .is_some_and(|r| !r.is_object() && !r.is_null())
    {
        return Err("invalid risk limits: order_rate must be a table".to_string());
    }
    let limits: RiskLimits =
        serde_json::from_value(v.clone()).map_err(|e| format!("invalid risk limits: {e}"))?;
    limits
        .validate()
        .map_err(|e| format!("invalid risk limits: {e}"))?;
    Ok(limits)
}

fn parse_instrument_id(s: &str) -> Option<InstrumentId> {
    let (symbol, exchange) = s.rsplit_once('.')?;
    (!symbol.is_empty() && !exchange.is_empty())
        .then(|| InstrumentId::new(symbol, honba_messages::Exchange::new(exchange)))
}

fn parse_state(s: &str) -> Option<TradingState> {
    serde_json::from_value(Value::String(s.to_string())).ok()
}

/// Parses a request dict (as JSON): the shape of a golden-vector request. Absent optional keys
/// are `None`; unknown keys and a non-integer `ts` are refused.
pub(crate) fn parse_request(v: &Value) -> Result<RiskRequest, String> {
    let obj = v.as_object().ok_or_else(|| req_err("expected an object"))?;
    if let Some(k) = obj.keys().find(|k| !REQUEST_KEYS.contains(&k.as_str())) {
        return Err(req_err(format!("unknown key `{k}`")));
    }
    let get = |key: &str| {
        obj.get(key)
            .filter(|x| !x.is_null())
            .ok_or_else(|| req_err(format!("missing `{key}`")))
    };
    let number = |key: &str| {
        get(key)?
            .as_f64()
            .ok_or_else(|| req_err(format!("`{key}` must be a number")))
    };
    let optional = |key: &str| match obj.get(key) {
        None | Some(Value::Null) => Ok(None),
        Some(x) => x
            .as_f64()
            .map(Some)
            .ok_or_else(|| req_err(format!("`{key}` must be a number"))),
    };
    let text = |key: &str| {
        get(key)?
            .as_str()
            .ok_or_else(|| req_err(format!("`{key}` must be a string")))
    };
    let side = match text("side")? {
        "buy" => OrderSide::Buy,
        "sell" => OrderSide::Sell,
        other => {
            return Err(req_err(format!(
                "`side` must be buy or sell, got `{other}`"
            )))
        }
    };
    let instrument_id = parse_instrument_id(text("instrument_id")?)
        .ok_or_else(|| req_err("`instrument_id` must look like SYMBOL.EXCHANGE"))?;
    let state_text = text("trading_state")?;
    let trading_state = parse_state(state_text).ok_or_else(|| {
        req_err(format!(
            "`trading_state` must be active, reducing or halted, got `{state_text}`"
        ))
    })?;
    let ts = get("ts")?
        .as_u64()
        .ok_or_else(|| req_err("`ts` must be a non-negative integer of nanoseconds"))?;
    Ok(RiskRequest {
        order_id: OrderId::new(text("order_id")?),
        instrument_id,
        side,
        quantity: number("quantity")?,
        price: optional("price")?,
        trigger_price: optional("trigger_price")?,
        reference_price: optional("reference_price")?,
        adv: optional("adv")?,
        position: number("position")?,
        trading_state,
        ts: UnixNanos::new(ts),
        last_feed_ts: None,
    })
}

/// The decision as a JSON object: `approved`, `code`, `rule`, `context`.
pub(crate) fn decision_json(d: &RiskDecision) -> Value {
    match d {
        RiskDecision::Approved => {
            json!({"approved": true, "code": null, "rule": null, "context": {}})
        }
        RiskDecision::Refused(r) => json!({
            "approved": false,
            "code": r.error_code().as_str(),
            "rule": r.rule(),
            "context": r.context(),
        }),
    }
}

/// Per-instrument rule overrides carried next to the instrument metadata (what a venue profile
/// does not know: freeze quantity, minimum quantity, daily band).
#[derive(Clone, Copy, Default)]
struct Overrides {
    min_order_quantity: Option<f64>,
    max_order_quantity: Option<f64>,
    band: Option<PriceBand>,
}

/// [`ProfileRulesSource`] plus [`Overrides`].
struct OverrideRules {
    inner: ProfileRulesSource,
    overrides: BTreeMap<InstrumentId, Overrides>,
}

impl RulesSource for OverrideRules {
    fn rules(&self, id: &InstrumentId) -> Option<(InstrumentRules, Option<PriceBand>)> {
        let (mut rules, mut band) = self.inner.rules(id)?;
        if let Some(o) = self.overrides.get(id) {
            if let Some(min) = o.min_order_quantity {
                rules.min_order_quantity = min;
            }
            if o.max_order_quantity.is_some() {
                rules.max_order_quantity = o.max_order_quantity;
            }
            if o.band.is_some() {
                band = o.band;
            }
        }
        Some((rules, band))
    }
}

fn parse_overrides(v: &Value) -> Result<Overrides, String> {
    let obj = v
        .as_object()
        .ok_or_else(|| "invalid instrument: expected an object".to_string())?;
    if let Some(k) = obj.keys().find(|k| !INSTRUMENT_KEYS.contains(&k.as_str())) {
        return Err(format!("invalid instrument: unknown key `{k}`"));
    }
    let positive = |key: &str| -> Result<Option<f64>, String> {
        match obj.get(key) {
            None | Some(Value::Null) => Ok(None),
            Some(x) => match x.as_f64() {
                Some(n) if n.is_finite() && n > 0.0 => Ok(Some(n)),
                _ => Err(format!(
                    "invalid instrument: {key} must be a positive number"
                )),
            },
        }
    };
    let band = match obj.get("band") {
        None | Some(Value::Null) => None,
        Some(b) => {
            let bad = || "invalid instrument: band must be {lower, upper} with lower <= upper";
            let o = b.as_object().filter(|o| o.len() == 2).ok_or_else(bad)?;
            let lower = o.get("lower").and_then(Value::as_f64).ok_or_else(bad)?;
            let upper = o.get("upper").and_then(Value::as_f64).ok_or_else(bad)?;
            if !(lower.is_finite() && upper.is_finite() && lower <= upper) {
                return Err(bad().to_string());
            }
            Some(PriceBand::new(lower, upper))
        }
    };
    Ok(Overrides {
        min_order_quantity: positive("min_order_quantity")?,
        max_order_quantity: positive("max_order_quantity")?,
        band,
    })
}

/// Builds the stage over the market profile named `market` (`"null"`, `"nse_bse"`) and the
/// given instruments (the shape of `parse_instrument`, plus the optional rule keys
/// `min_order_quantity`, `max_order_quantity`, `band`).
pub(crate) fn build_stage(
    limits: RiskLimits,
    currency: &str,
    market: &str,
    instruments: &[Value],
) -> Result<RiskStage, String> {
    let currency: Currency = serde_json::from_value(Value::String(currency.to_string()))
        .map_err(|e| format!("invalid currency `{currency}`: {e}"))?;
    let registry = MarketRegistry::default_registry();
    let profile = registry.get(market).map_err(|_| {
        format!(
            "unknown market `{market}`; expected one of {:?}",
            registry.available_markets()
        )
    })?;
    let mut parsed = Vec::with_capacity(instruments.len());
    let mut overrides = BTreeMap::new();
    for raw in instruments {
        let instrument: Instrument = parse_instrument(raw)?;
        overrides.insert(instrument.id().clone(), parse_overrides(raw)?);
        parsed.push(instrument);
    }
    let rules = OverrideRules {
        inner: ProfileRulesSource::new(profile, parsed),
        overrides,
    };
    RiskStage::new(limits, currency, Arc::new(rules))
        .map_err(|e| format!("invalid risk limits: {e}"))
}

/// The `risk` argument of `run_strategy*`: limits, seed positions, initial trading state and
/// scheduled state changes in event time.
#[derive(Debug)]
pub(crate) struct RunRisk {
    pub(crate) limits: RiskLimits,
    pub(crate) market: String,
    pub(crate) positions: Vec<(InstrumentId, f64)>,
    pub(crate) trading_state: TradingState,
    /// `(ts_init in ns, state)`, non-decreasing: applied before the first event at or after it.
    pub(crate) state_changes: Vec<(u64, TradingState)>,
}

fn spec_err(msg: impl std::fmt::Display) -> String {
    format!("invalid risk spec: {msg}")
}

fn strict_keys(obj: &Map<String, Value>, allowed: &[&str], what: &str) -> Result<(), String> {
    match obj.keys().find(|k| !allowed.contains(&k.as_str())) {
        Some(k) => Err(spec_err(format!("unknown key `{k}` in {what}"))),
        None => Ok(()),
    }
}

fn state_of(v: &Value, what: &str) -> Result<TradingState, String> {
    v.as_str()
        .and_then(parse_state)
        .ok_or_else(|| spec_err(format!("{what} must be active, reducing or halted")))
}

/// Parses the `risk` JSON of `run_strategy*`.
pub(crate) fn parse_run_risk(text: &str) -> Result<RunRisk, String> {
    let v: Value = serde_json::from_str(text).map_err(spec_err)?;
    let obj = v
        .as_object()
        .ok_or_else(|| spec_err("expected a JSON object"))?;
    strict_keys(
        obj,
        &[
            "limits",
            "market",
            "positions",
            "trading_state",
            "state_changes",
        ],
        "the risk spec",
    )?;
    let limits = match obj.get("limits") {
        None | Some(Value::Null) => RiskLimits::default(),
        Some(l) => parse_limits(l)?,
    };
    let market = match obj.get("market") {
        None => "null".to_string(),
        Some(m) => m
            .as_str()
            .ok_or_else(|| spec_err("market must be a string"))?
            .to_string(),
    };
    let trading_state = match obj.get("trading_state") {
        None => TradingState::Active,
        Some(s) => state_of(s, "trading_state")?,
    };
    let mut positions = Vec::new();
    for p in obj
        .get("positions")
        .map(|p| {
            p.as_array()
                .ok_or_else(|| spec_err("positions must be a list"))
        })
        .transpose()?
        .into_iter()
        .flatten()
    {
        let o = p
            .as_object()
            .ok_or_else(|| spec_err("a position must be an object"))?;
        strict_keys(o, &["instrument_id", "quantity"], "a position")?;
        let id: InstrumentId =
            serde_json::from_value(o.get("instrument_id").cloned().unwrap_or(Value::Null))
                .map_err(|e| spec_err(format!("position instrument_id: {e}")))?;
        let qty = o
            .get("quantity")
            .and_then(Value::as_f64)
            .filter(|q| q.is_finite())
            .ok_or_else(|| spec_err("a position needs a finite numeric quantity"))?;
        positions.push((id, qty));
    }
    let mut state_changes: Vec<(u64, TradingState)> = Vec::new();
    for c in obj
        .get("state_changes")
        .map(|c| {
            c.as_array()
                .ok_or_else(|| spec_err("state_changes must be a list"))
        })
        .transpose()?
        .into_iter()
        .flatten()
    {
        let o = c
            .as_object()
            .ok_or_else(|| spec_err("a state change must be an object"))?;
        strict_keys(o, &["ts_init", "state"], "a state change")?;
        let ts = o
            .get("ts_init")
            .and_then(Value::as_u64)
            .ok_or_else(|| spec_err("state change ts_init must be an integer of nanoseconds"))?;
        let state = state_of(
            o.get("state").unwrap_or(&Value::Null),
            "a state change state",
        )?;
        if state_changes.last().is_some_and(|(prev, _)| *prev > ts) {
            return Err(spec_err("state_changes must be ordered by ts_init"));
        }
        state_changes.push((ts, state));
    }
    Ok(RunRisk {
        limits,
        market,
        positions,
        trading_state,
        state_changes,
    })
}

// ---------------------------------------------------------------------------------------
// Python classes
// ---------------------------------------------------------------------------------------

fn value_err(msg: String) -> PyErr {
    PyValueError::new_err(msg)
}

/// JSON text of any Python object; objects with `to_dict()` (`RiskLimits`) and enum members
/// (`TradingState`, via `str()`) are accepted anywhere inside it.
pub(crate) fn json_text(obj: &Bound<'_, PyAny>) -> PyResult<String> {
    if let Ok(s) = obj.extract::<String>() {
        return Ok(s);
    }
    let py = obj.py();
    let default = py.eval_bound(
        "lambda o: o.to_dict() if hasattr(o, 'to_dict') else str(o)",
        None,
        None,
    )?;
    let kwargs = PyDict::new_bound(py);
    kwargs.set_item("default", default)?;
    py.import_bound("json")?
        .call_method("dumps", (obj,), Some(&kwargs))?
        .extract()
}

fn to_value(obj: &Bound<'_, PyAny>, what: &str) -> PyResult<Value> {
    let text = json_text(obj)?;
    serde_json::from_str(&text).map_err(|e| value_err(format!("invalid {what}: {e}")))
}

fn to_py(py: Python<'_>, v: &Value) -> PyResult<PyObject> {
    Ok(py
        .import_bound("json")?
        .call_method1("loads", (v.to_string(),))?
        .unbind())
}

/// The engine's trading state; `str()` is the wire spelling.
#[pyclass(name = "TradingState", module = "honba", eq, eq_int, frozen, hash)]
#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash)]
pub enum PyTradingState {
    /// Normal trading.
    #[pyo3(name = "ACTIVE")]
    Active,
    /// Winding down: only reduce-only orders pass.
    #[pyo3(name = "REDUCING")]
    Reducing,
    /// Stopped: every order is refused.
    #[pyo3(name = "HALTED")]
    Halted,
}

impl From<PyTradingState> for TradingState {
    fn from(s: PyTradingState) -> Self {
        match s {
            PyTradingState::Active => TradingState::Active,
            PyTradingState::Reducing => TradingState::Reducing,
            PyTradingState::Halted => TradingState::Halted,
        }
    }
}

#[pymethods]
impl PyTradingState {
    fn __str__(&self) -> &'static str {
        match self {
            Self::Active => "active",
            Self::Reducing => "reducing",
            Self::Halted => "halted",
        }
    }

    fn __repr__(&self) -> String {
        format!("TradingState.{}", self.__str__().to_uppercase())
    }
}

/// Per-run risk limits (`max_notional` in major units, `order_rate` as `(max_orders,
/// window_ms)`); `None` switches a rule off.
#[pyclass(name = "RiskLimits", module = "honba", eq, frozen)]
#[derive(Clone, Debug, PartialEq)]
pub struct PyRiskLimits {
    limits: RiskLimits,
}

impl PyRiskLimits {
    pub(crate) fn inner(&self) -> &RiskLimits {
        &self.limits
    }
}

#[pymethods]
impl PyRiskLimits {
    #[new]
    #[pyo3(signature = (max_notional=None, order_rate=None, max_participation=None, stale_after_ms=None))]
    fn new(
        max_notional: Option<f64>,
        order_rate: Option<(u32, u64)>,
        max_participation: Option<f64>,
        stale_after_ms: Option<u64>,
    ) -> PyResult<Self> {
        let limits = RiskLimits {
            max_notional,
            order_rate: order_rate.map(|(max_orders, window_ms)| OrderRateLimit {
                max_orders,
                window_ms,
            }),
            max_participation,
            stale_after_ms,
        };
        limits
            .validate()
            .map_err(|e| value_err(format!("invalid risk limits: {e}")))?;
        Ok(Self { limits })
    }

    /// Parses a `[risk]` table with the Rust config parser (unknown keys are refused).
    #[staticmethod]
    fn from_dict(d: &Bound<'_, PyAny>) -> PyResult<Self> {
        let v = to_value(d, "risk limits")?;
        Ok(Self {
            limits: parse_limits(&v).map_err(value_err)?,
        })
    }

    /// The limits in the `[risk]` table shape.
    fn to_dict(&self, py: Python<'_>) -> PyResult<PyObject> {
        let v = json!({
            "max_notional": self.limits.max_notional,
            "order_rate": self.limits.order_rate.map(|r| json!({
                "max_orders": r.max_orders, "window_ms": r.window_ms
            })),
            "max_participation": self.limits.max_participation,
            "stale_after_ms": self.limits.stale_after_ms,
        });
        to_py(py, &v)
    }

    /// Per-order notional ceiling, or `None`.
    #[getter]
    fn max_notional(&self) -> Option<f64> {
        self.limits.max_notional
    }

    /// `(max_orders, window_ms)`, or `None`.
    #[getter]
    fn order_rate(&self) -> Option<(u32, u64)> {
        self.limits.order_rate.map(|r| (r.max_orders, r.window_ms))
    }

    /// Maximum order quantity as a fraction of ADV, or `None`.
    #[getter]
    fn max_participation(&self) -> Option<f64> {
        self.limits.max_participation
    }

    /// Maximum quote/feed age in milliseconds before an order is refused, or `None`.
    #[getter]
    fn stale_after_ms(&self) -> Option<u64> {
        self.limits.stale_after_ms
    }

    /// Raises `ValueError` unless both limits are set (the guard for a live run).
    fn require_live(&self) -> PyResult<()> {
        self.limits
            .require_live()
            .map_err(|e| value_err(e.to_string()))
    }

    fn __repr__(&self) -> String {
        format!(
            "RiskLimits(max_notional={:?}, order_rate={:?}, max_participation={:?})",
            self.limits.max_notional,
            self.order_rate(),
            self.max_participation()
        )
    }
}

/// The outcome of a risk check.
#[pyclass(name = "RiskDecision", module = "honba", frozen)]
pub struct PyRiskDecision {
    value: Value,
}

#[pymethods]
impl PyRiskDecision {
    /// Whether every rule passed.
    #[getter]
    fn approved(&self) -> bool {
        self.value["approved"] == json!(true)
    }

    /// The wire error code of the refusal, or `None`.
    #[getter]
    fn code(&self) -> Option<String> {
        self.value["code"].as_str().map(str::to_string)
    }

    /// The name of the refusing rule, or `None`.
    #[getter]
    fn rule(&self) -> Option<String> {
        self.value["rule"].as_str().map(str::to_string)
    }

    /// The numbers behind the refusal (a fresh dict; empty when approved).
    #[getter]
    fn context(&self, py: Python<'_>) -> PyResult<PyObject> {
        to_py(py, &self.value["context"])
    }

    fn __repr__(&self) -> String {
        match self.code() {
            None => "RiskDecision(approved)".to_string(),
            Some(code) => format!("RiskDecision(refused, code={code:?})"),
        }
    }
}

/// The pre-trade risk stage: fixed-order rules, first refusal wins. With an order-rate limit
/// `check` is not idempotent (an approval consumes a slot).
#[pyclass(name = "RiskStage", module = "honba")]
pub struct PyRiskStage {
    stage: RiskStage,
}

#[pymethods]
impl PyRiskStage {
    #[new]
    fn new(
        limits: &PyRiskLimits,
        currency: &str,
        market: &str,
        instruments: Vec<Bound<'_, PyAny>>,
    ) -> PyResult<Self> {
        let raw = instruments
            .iter()
            .map(|i| to_value(i, "instrument"))
            .collect::<PyResult<Vec<_>>>()?;
        let stage =
            build_stage(limits.inner().clone(), currency, market, &raw).map_err(value_err)?;
        Ok(Self { stage })
    }

    /// Checks one order request (a dict; see the golden vectors) and returns the decision.
    fn check(&mut self, request: &Bound<'_, PyAny>) -> PyResult<PyRiskDecision> {
        let v = to_value(request, "risk request")?;
        let req = parse_request(&v).map_err(value_err)?;
        Ok(PyRiskDecision {
            value: decision_json(&self.stage.check(&req)),
        })
    }
}

pub fn register(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<PyTradingState>()?;
    m.add_class::<PyRiskLimits>()?;
    m.add_class::<PyRiskDecision>()?;
    m.add_class::<PyRiskStage>()?;
    Ok(())
}
