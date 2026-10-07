//! `NextOpenSimulator`: binding of [`honba_sim::NextOpenSim`] (ADR 0016, chunk 3a).
//!
//! The module has two layers. The interpreter-free core ([`NativeSim`], [`BarIn`], [`OrderIn`],
//! [`FillOut`], [`RejectionOut`], [`SimError`], [`CostSpec`]) converts plain data to and from
//! the Rust types and is what the Rust tests drive. The `#[pyclass]` below is a thin shell that
//! reads Python dicts into the core's inputs and builds dicts from its outputs. Money crosses the
//! boundary as integer minor units (ADR 0011); prices and quantities are floats.
//!
//! Errors: every `AlgoError::Component` is a `ValueError`, except the settlement-cycle guard
//! (change after the first session), which is a `RuntimeError`, as in the Python reference.

#![allow(clippy::useless_conversion)]

use std::sync::{Arc, Mutex};

use honba_engine::{AlgoError, ExecutionEngine};
use honba_entities::{Currency, Money};
use honba_market::india::costs::NseCashEquitySchedule;
use honba_market::{CostSchedule, MarketSegment};
use honba_messages::{
    Bar, BarAggregation, BarSpecification, BarType, Exchange, InstrumentId, Order, OrderId,
    OrderSide, OrderType, PriceType, TimeInForce, UnixNanos,
};
use honba_sim::{FillCostFn, NextOpenSim};
use pyo3::exceptions::{PyRuntimeError, PyTypeError, PyValueError};
use pyo3::prelude::*;
use pyo3::types::{PyDict, PyList, PyModule, PyString, PyTuple};

/// Which Python exception a [`SimError`] becomes.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum SimErrorKind {
    /// `ValueError`: bad input or a rule violation.
    Value,
    /// `RuntimeError`: the settlement cycle was changed after the first session.
    Runtime,
}

/// An error of the binding core.
#[derive(Clone, Debug, PartialEq)]
pub struct SimError {
    /// The exception class it maps to.
    pub kind: SimErrorKind,
    /// The message.
    pub message: String,
}

impl SimError {
    fn value(message: impl Into<String>) -> Self {
        Self {
            kind: SimErrorKind::Value,
            message: message.into(),
        }
    }
}

impl From<AlgoError> for SimError {
    fn from(e: AlgoError) -> Self {
        let message = match e {
            AlgoError::Component(m) => m,
            other => other.to_string(),
        };
        SimError::value(message)
    }
}

type Res<T> = Result<T, SimError>;

/// One bar, as the vectors and the Python reference carry it.
#[derive(Clone, Debug, PartialEq)]
pub struct BarIn {
    /// Instrument symbol.
    pub symbol: String,
    /// Exchange code (`NSE`).
    pub exchange: String,
    /// The bar's timestamp, the session key.
    pub ts: u64,
    /// Open.
    pub open: f64,
    /// High.
    pub high: f64,
    /// Low.
    pub low: f64,
    /// Close.
    pub close: f64,
    /// Volume.
    pub volume: f64,
}

/// One order intent with its id and submit time.
#[derive(Clone, Debug, PartialEq)]
pub struct OrderIn {
    /// Order id.
    pub id: String,
    /// Instrument symbol.
    pub symbol: String,
    /// Exchange code.
    pub exchange: String,
    /// `buy` or `sell` (anything else is an order without a side, which is an error).
    pub side: String,
    /// `market`, `limit`, `stop_market` or `stop_limit`.
    pub kind: String,
    /// Quantity.
    pub qty: f64,
    /// Limit price.
    pub price: Option<f64>,
    /// Stop trigger price.
    pub trigger: Option<f64>,
    /// Submit time.
    pub ts: u64,
}

/// A fill, in the units of the Python `Trade`.
#[derive(Clone, Debug, PartialEq)]
pub struct FillOut {
    /// Order id.
    pub order_id: String,
    /// Instrument symbol.
    pub symbol: String,
    /// Exchange code.
    pub exchange: String,
    /// `buy` or `sell`.
    pub side: &'static str,
    /// Filled quantity.
    pub quantity: f64,
    /// Fill price (the bar open).
    pub price: f64,
    /// Fill time (the bar ts).
    pub ts: u64,
    /// Fill cost in minor units.
    pub costs: i64,
}

/// A rejection or cancellation of the remainder of an order.
#[derive(Clone, Debug, PartialEq)]
pub struct RejectionOut {
    /// Order id.
    pub order_id: String,
    /// Instrument symbol.
    pub symbol: String,
    /// Exchange code.
    pub exchange: String,
    /// `buy` or `sell`.
    pub side: &'static str,
    /// The remainder quantity.
    pub quantity: f64,
    /// Stable reason (`no_position`, `insufficient_funds`, `unsupported_order_type`, `cancelled`).
    pub reason: String,
    /// Event time.
    pub ts: u64,
    /// True for a cancellation.
    pub cancelled: bool,
}

/// How fills are costed.
pub enum CostSpec {
    /// No costs.
    None,
    /// The NSE equity delivery schedule.
    IndiaDelivery,
    /// The NSE equity intraday schedule.
    IndiaIntraday,
    /// A caller supplied `(side, quantity, price) -> cost` (minor units, never negative).
    Custom(FillCostFn),
}

impl CostSpec {
    /// Resolves a cost-pack name like Python `resolve_fill_costs` (trimmed, case-insensitive).
    pub fn named(name: &str) -> Res<Self> {
        match name.trim().to_lowercase().as_str() {
            "none" | "zero" => Ok(CostSpec::None),
            "india.equity" | "india.equity.delivery" => Ok(CostSpec::IndiaDelivery),
            "india.equity.intraday" => Ok(CostSpec::IndiaIntraday),
            _ => Err(SimError::value(format!(
                "unknown cost pack {name:?}; expected one of [\"india.equity\", \
                 \"india.equity.delivery\", \"india.equity.intraday\", \"none\", \"zero\"]"
            ))),
        }
    }

    fn into_fn(self, currency: Currency) -> Option<FillCostFn> {
        match self {
            CostSpec::None => None,
            CostSpec::IndiaDelivery => Some(schedule_cost_fn(
                Box::new(NseCashEquitySchedule::delivery()),
                MarketSegment::from("equity_delivery"),
                currency,
            )),
            CostSpec::IndiaIntraday => Some(schedule_cost_fn(
                Box::new(NseCashEquitySchedule::intraday()),
                MarketSegment::from("equity_intraday"),
                currency,
            )),
            CostSpec::Custom(f) => Some(f),
        }
    }
}

/// Adapts a [`CostSchedule`] to the simulator's fill-cost function: the notional is
/// `abs(quantity * price)`, every charge leg is rounded to minor units once (half away from zero)
/// and the legs are summed, like Python `nse_equity_delivery_fill_cost`.
pub fn schedule_cost_fn(
    schedule: Box<dyn CostSchedule>,
    segment: MarketSegment,
    currency: Currency,
) -> FillCostFn {
    Box::new(move |side, qty, price| {
        let fees = schedule.compute_costs(&segment, side, (qty * price).abs());
        let mut total = 0_i64;
        for charge in &fees.charges {
            let leg = Money::from_major_f64(charge.amount, currency)
                .map_err(|e| AlgoError::Component(format!("cost leg {}: {e}", charge.name)))?;
            total = total
                .checked_add(leg.minor())
                .ok_or_else(|| AlgoError::Component("fill cost overflow".into()))?;
        }
        Ok(Money::new(total, currency))
    })
}

fn parse_side(side: &str) -> OrderSide {
    match side {
        "buy" => OrderSide::Buy,
        "sell" => OrderSide::Sell,
        _ => OrderSide::NoOrderSide,
    }
}

fn side_str(side: OrderSide) -> &'static str {
    if side == OrderSide::Buy {
        "buy"
    } else {
        "sell"
    }
}

/// The cost of one fill under the named India pack, in minor units (INR): the function the
/// simulator calls, exposed for parity checks against the Python fill-cost functions.
pub fn india_fill_cost(pack: &str, side: &str, quantity: f64, price: f64) -> Res<i64> {
    let f = CostSpec::named(pack)?.into_fn(Currency::Inr);
    match f {
        None => Ok(0),
        Some(f) => Ok(f(parse_side(side), quantity, price)?.minor()),
    }
}

/// Everything needed to build a [`NativeSim`].
pub struct SimConfig {
    /// Starting cash in minor units (not negative).
    pub cash: i64,
    /// Currency code (`INR`).
    pub currency: String,
    /// Settlement cycle in sessions.
    pub settlement_days: i64,
    /// Cap sells at the position held.
    pub long_only: bool,
    /// `(symbol, exchange, lot size)`.
    pub lot_sizes: Vec<(String, String, f64)>,
    /// Fill costs.
    pub costs: CostSpec,
}

impl SimConfig {
    /// Defaults: INR, no settlement lag, long only, no lots, no costs.
    pub fn new(cash: i64) -> Self {
        Self {
            cash,
            currency: "INR".into(),
            settlement_days: 0,
            long_only: true,
            lot_sizes: Vec::new(),
            costs: CostSpec::None,
        }
    }
}

fn iid(symbol: &str, exchange: &str) -> InstrumentId {
    InstrumentId::new(symbol, Exchange::new(exchange))
}

fn to_bar(b: &BarIn) -> Bar {
    let ts = UnixNanos::from_u64(b.ts);
    Bar::new(
        BarType::new(
            iid(&b.symbol, &b.exchange),
            BarSpecification::new(1, BarAggregation::Day, PriceType::Last),
        ),
        b.open,
        b.high,
        b.low,
        b.close,
        b.volume,
        ts,
        ts,
    )
}

/// The interpreter-free core of the binding: a [`NextOpenSim`] behind plain-data methods.
pub struct NativeSim {
    sim: NextOpenSim,
}

impl NativeSim {
    /// Builds the simulator; invalid cash, currency, settlement days or lot sizes are errors.
    pub fn new(cfg: SimConfig) -> Res<Self> {
        let currency: Currency =
            serde_json::from_value(serde_json::Value::String(cfg.currency.clone()))
                .map_err(|_| SimError::value(format!("unknown currency {:?}", cfg.currency)))?;
        let mut sim = NextOpenSim::new(Money::new(cfg.cash, currency))?
            .with_settlement_days(cfg.settlement_days)?
            .with_long_only(cfg.long_only);
        if let Some(f) = cfg.costs.into_fn(currency) {
            sim = sim.with_costs(f);
        }
        let mut out = Self { sim };
        for (symbol, exchange, lot) in &cfg.lot_sizes {
            out.set_lot_size(symbol, exchange, *lot)?;
        }
        Ok(out)
    }

    /// Opens session `ts` with `bars`.
    pub fn open_session(&mut self, ts: u64, bars: &[BarIn]) -> Res<()> {
        let bars: Vec<Bar> = bars.iter().map(to_bar).collect();
        Ok(self.sim.open_session(UnixNanos::from_u64(ts), &bars)?)
    }

    /// The session-open event (lenient bar rules afterwards).
    pub fn on_session_open(&mut self, ts: u64, bars: &[BarIn]) -> Res<()> {
        let bars: Vec<Bar> = bars.iter().map(to_bar).collect();
        Ok(self.sim.on_session_open(UnixNanos::from_u64(ts), &bars)?)
    }

    /// Observes one bar.
    pub fn on_bar(&mut self, bar: &BarIn) -> Res<()> {
        Ok(self.sim.on_bar(&to_bar(bar))?)
    }

    /// Submits an order.
    pub fn submit(&mut self, o: &OrderIn) -> Res<()> {
        let kind = match o.kind.as_str() {
            "market" => OrderType::Market,
            "limit" => OrderType::Limit,
            "stop_market" => OrderType::StopMarket,
            "stop_limit" => OrderType::StopLimit,
            other => return Err(SimError::value(format!("unknown order type {other:?}"))),
        };
        let ts = UnixNanos::from_u64(o.ts);
        let order = Order::new(
            OrderId::new(o.id.as_str()),
            iid(&o.symbol, &o.exchange),
            parse_side(&o.side),
            kind,
            o.qty,
            o.price,
            TimeInForce::Day,
            ts,
            ts,
        );
        let order = match o.trigger {
            Some(t) => order.with_trigger_price(t),
            None => order,
        };
        Ok(ExecutionEngine::submit(&mut self.sim, order)?)
    }

    /// Cancels a working order; unknown ids are a no-op. `now` stamps the cancellation.
    pub fn cancel(&mut self, order_id: &str, now: u64) -> Res<()> {
        Ok(ExecutionEngine::cancel(
            &mut self.sim,
            order_id,
            UnixNanos::from_u64(now),
        )?)
    }

    /// Changes the settlement cycle: `RuntimeError` after the first session, `ValueError` when
    /// negative (the same order of checks as Python).
    pub fn set_settlement_days(&mut self, days: i64) -> Res<()> {
        self.sim.set_settlement_days(days).map_err(|e| {
            let mut e = SimError::from(e);
            if days >= 0 {
                // the only other failure is the first-session guard
                e.kind = SimErrorKind::Runtime;
            }
            e
        })
    }

    /// Sets the funding-cut lot size of an instrument.
    pub fn set_lot_size(&mut self, symbol: &str, exchange: &str, lot: f64) -> Res<()> {
        Ok(self.sim.set_lot_size(&iid(symbol, exchange), lot)?)
    }

    /// Settlement cycle in sessions.
    pub fn settlement_days(&self) -> i64 {
        self.sim.settlement_days()
    }

    /// Booked cash, minor units.
    pub fn cash(&self) -> i64 {
        self.sim.cash().minor()
    }

    /// Costs paid so far, minor units.
    pub fn fees(&self) -> i64 {
        self.sim.fees().minor()
    }

    /// Sum of fill notionals, minor units.
    pub fn traded_notional(&self) -> i64 {
        self.sim.traded_notional().minor()
    }

    /// Sale proceeds not yet available, minor units.
    pub fn unsettled(&self) -> i64 {
        self.sim.unsettled().minor()
    }

    /// Cash that may fund a buy now, minor units.
    pub fn available_cash(&self) -> i64 {
        self.sim.available_cash().minor()
    }

    /// Pending proceeds `(due session index, amount in minor units)`.
    pub fn receivables(&self) -> Vec<(i64, i64)> {
        self.sim
            .receivables()
            .into_iter()
            .map(|(d, m)| (d, m.minor()))
            .collect()
    }

    /// Non-flat positions `(symbol, exchange, quantity)` in the order first opened.
    pub fn positions(&self) -> Vec<(String, String, f64)> {
        self.sim
            .positions()
            .into_iter()
            .map(|(i, q)| (i.symbol().to_string(), i.exchange().as_str().to_string(), q))
            .collect()
    }

    /// Ids of working orders in submission order.
    pub fn working_orders(&self) -> Vec<String> {
        self.sim.working_orders()
    }

    /// Returns and clears the fills.
    pub fn drain_fills(&mut self) -> Vec<FillOut> {
        let fills = ExecutionEngine::drain_fills(&mut self.sim).unwrap_or_default();
        fills
            .iter()
            .map(|f| FillOut {
                order_id: f.order_id().as_str().to_string(),
                symbol: f.instrument_id().symbol().to_string(),
                exchange: f.instrument_id().exchange().as_str().to_string(),
                side: side_str(f.side()),
                quantity: f.quantity(),
                price: f.price(),
                ts: f.ts_event().as_u64(),
                costs: f.costs().minor(),
            })
            .collect()
    }

    /// Returns and clears the rejections and cancellations.
    pub fn drain_rejections(&mut self) -> Vec<RejectionOut> {
        let rejections = ExecutionEngine::drain_rejections(&mut self.sim).unwrap_or_default();
        rejections
            .iter()
            .map(|r| RejectionOut {
                order_id: r.order_id.as_str().to_string(),
                symbol: r.instrument_id.symbol().to_string(),
                exchange: r.instrument_id.exchange().as_str().to_string(),
                side: side_str(r.side),
                quantity: r.quantity,
                reason: r.reason.clone(),
                ts: r.ts.as_u64(),
                cancelled: r.is_cancelled(),
            })
            .collect()
    }
}

// -- Python shell ---------------------------------------------------------------------------

/// First error raised by a Python cost callable, kept so it propagates unchanged.
type ErrorSlot = Arc<Mutex<Option<PyErr>>>;

fn to_py_err(e: SimError) -> PyErr {
    match e.kind {
        SimErrorKind::Value => PyValueError::new_err(e.message),
        SimErrorKind::Runtime => PyRuntimeError::new_err(e.message),
    }
}

fn required<'py, T: FromPyObject<'py>>(d: &Bound<'py, PyDict>, key: &str) -> PyResult<T> {
    match d.get_item(key)? {
        Some(v) if !v.is_none() => v.extract(),
        _ => Err(PyValueError::new_err(format!("missing key {key:?}"))),
    }
}

fn optional<'py, T: FromPyObject<'py>>(d: &Bound<'py, PyDict>, key: &str) -> PyResult<Option<T>> {
    match d.get_item(key)? {
        Some(v) if !v.is_none() => v.extract().map(Some),
        _ => Ok(None),
    }
}

fn bar_from_py(d: &Bound<'_, PyDict>) -> PyResult<BarIn> {
    Ok(BarIn {
        symbol: required(d, "symbol")?,
        exchange: optional(d, "exchange")?.unwrap_or_else(|| "NSE".into()),
        ts: required(d, "ts")?,
        open: required(d, "open")?,
        high: required(d, "high")?,
        low: required(d, "low")?,
        close: required(d, "close")?,
        volume: optional(d, "volume")?.unwrap_or(1000.0),
    })
}

fn order_from_py(d: &Bound<'_, PyDict>) -> PyResult<OrderIn> {
    Ok(OrderIn {
        id: required(d, "id")?,
        symbol: required(d, "symbol")?,
        exchange: optional(d, "exchange")?.unwrap_or_else(|| "NSE".into()),
        side: required(d, "side")?,
        kind: optional(d, "type")?.unwrap_or_else(|| "market".into()),
        qty: required(d, "qty")?,
        price: optional(d, "price")?,
        trigger: optional(d, "trigger")?,
        ts: required(d, "ts")?,
    })
}

fn bars_from_py(bars: Vec<Bound<'_, PyDict>>) -> PyResult<Vec<BarIn>> {
    bars.iter().map(bar_from_py).collect()
}

/// A Python callable `(side: str, quantity: float, price: float) -> int` as a fill-cost function.
/// The callable runs under the GIL the calling method already holds; its exception is stored in
/// `slot` and re-raised unchanged by the method that failed.
fn callable_cost_fn(callable: Py<PyAny>, currency: Currency, slot: ErrorSlot) -> FillCostFn {
    Box::new(move |side, qty, price| {
        Python::with_gil(|py| {
            let result = callable
                .call1(py, (side_str(side), qty, price))
                .and_then(|v| v.extract::<i64>(py));
            match result {
                Ok(minor) => Ok(Money::new(minor, currency)),
                Err(e) => {
                    let msg = format!("cost function failed: {e}");
                    *slot.lock().unwrap() = Some(e);
                    Err(AlgoError::Component(msg))
                }
            }
        })
    })
}

fn lot_key(key: &Bound<'_, PyAny>) -> PyResult<(String, String)> {
    if let Ok(symbol) = key.extract::<String>() {
        return Ok((symbol, "NSE".into()));
    }
    key.extract::<(String, String)>().map_err(|_| {
        PyTypeError::new_err("lot_sizes keys must be a symbol or a (symbol, exchange) pair")
    })
}

/// Next-open execution simulator (ADR 0016): market orders fill at the open of the instrument's
/// first bar in a later session. Money is integer minor units; bars and orders are dicts.
///
/// `costs` is `None`, a pack name (`"india.equity.delivery"`, `"india.equity.intraday"`,
/// `"none"`) or a callable `(side: str, quantity: float, price: float) -> int` of minor units.
/// A callable's exception propagates unchanged; a negative result is a `ValueError`.
#[pyclass(module = "honba._honba")]
pub struct NextOpenSimulator {
    core: NativeSim,
    errors: ErrorSlot,
}

impl NextOpenSimulator {
    fn finish<T>(&self, result: Res<T>) -> PyResult<T> {
        let stored = self.errors.lock().unwrap().take();
        match result {
            Ok(v) => Ok(v),
            Err(e) => Err(stored.unwrap_or_else(|| to_py_err(e))),
        }
    }
}

#[pymethods]
impl NextOpenSimulator {
    #[new]
    #[pyo3(signature = (
        cash, currency="INR", *, settlement_days=0, long_only=true, lot_sizes=None, costs=None
    ))]
    fn py_new(
        cash: i64,
        currency: &str,
        settlement_days: i64,
        long_only: bool,
        lot_sizes: Option<Bound<'_, PyDict>>,
        costs: Option<Bound<'_, PyAny>>,
    ) -> PyResult<Self> {
        let errors: ErrorSlot = Arc::default();
        let cur: Currency = serde_json::from_value(serde_json::Value::String(currency.into()))
            .map_err(|_| PyValueError::new_err(format!("unknown currency {currency:?}")))?;
        let spec = match costs {
            None => CostSpec::None,
            Some(c) if c.is_none() => CostSpec::None,
            Some(c) if c.is_instance_of::<PyString>() => {
                CostSpec::named(&c.extract::<String>()?).map_err(to_py_err)?
            }
            Some(c) if c.is_callable() => {
                CostSpec::Custom(callable_cost_fn(c.unbind(), cur, errors.clone()))
            }
            Some(_) => {
                return Err(PyTypeError::new_err(
                    "costs must be None, a cost pack name or a callable",
                ))
            }
        };
        let mut cfg = SimConfig::new(cash);
        cfg.currency = currency.into();
        cfg.settlement_days = settlement_days;
        cfg.long_only = long_only;
        cfg.costs = spec;
        if let Some(lots) = lot_sizes {
            for (k, v) in lots.iter() {
                let (symbol, exchange) = lot_key(&k)?;
                cfg.lot_sizes.push((symbol, exchange, v.extract()?));
            }
        }
        let core = NativeSim::new(cfg).map_err(to_py_err)?;
        Ok(Self { core, errors })
    }

    /// Opens session `ts` with `bars` (dicts: symbol, ts, open, high, low, close[, volume,
    /// exchange]). `ValueError` unless `ts` follows the current session.
    fn open_session(&mut self, ts: u64, bars: Vec<Bound<'_, PyDict>>) -> PyResult<()> {
        let bars = bars_from_py(bars)?;
        let r = self.core.open_session(ts, &bars);
        self.finish(r)
    }

    /// The session-open event: like `open_session`, then lenient bar rules for good.
    fn on_session_open(&mut self, ts: u64, bars: Vec<Bound<'_, PyDict>>) -> PyResult<()> {
        let bars = bars_from_py(bars)?;
        let r = self.core.on_session_open(ts, &bars);
        self.finish(r)
    }

    /// Observes one bar (a dict); `ValueError` for a non-monotonic or duplicate bar.
    fn on_bar(&mut self, bar: &Bound<'_, PyDict>) -> PyResult<()> {
        let bar = bar_from_py(bar)?;
        let r = self.core.on_bar(&bar);
        self.finish(r)
    }

    /// Submits an order (a dict: id, symbol, side, qty, ts[, type, price, trigger, exchange]).
    /// `ValueError` for a missing side or an id that is already working; non-market types are
    /// reported as `unsupported_order_type` rejections.
    fn submit(&mut self, order: &Bound<'_, PyDict>) -> PyResult<()> {
        let order = order_from_py(order)?;
        let r = self.core.submit(&order);
        self.finish(r)
    }

    /// Cancels a working order, stamped `now`; unknown ids are a no-op.
    fn cancel(&mut self, order_id: &str, now: u64) -> PyResult<()> {
        let r = self.core.cancel(order_id, now);
        self.finish(r)
    }

    /// Changes the settlement cycle: `ValueError` if negative, `RuntimeError` after the first
    /// session.
    fn set_settlement_days(&mut self, days: i64) -> PyResult<()> {
        let r = self.core.set_settlement_days(days);
        self.finish(r)
    }

    /// Sets the funding-cut lot size of `symbol` (default exchange `NSE`).
    #[pyo3(signature = (symbol, lot_size, exchange="NSE"))]
    fn set_lot_size(&mut self, symbol: &str, lot_size: f64, exchange: &str) -> PyResult<()> {
        let r = self.core.set_lot_size(symbol, exchange, lot_size);
        self.finish(r)
    }

    /// Returns and clears the fills: dicts with order_id, symbol, exchange, side, quantity,
    /// price, ts, costs (minor units).
    fn drain_fills<'py>(&mut self, py: Python<'py>) -> PyResult<Bound<'py, PyList>> {
        let out = PyList::empty_bound(py);
        for f in self.core.drain_fills() {
            let d = PyDict::new_bound(py);
            d.set_item("order_id", f.order_id)?;
            d.set_item("symbol", f.symbol)?;
            d.set_item("exchange", f.exchange)?;
            d.set_item("side", f.side)?;
            d.set_item("quantity", f.quantity)?;
            d.set_item("price", f.price)?;
            d.set_item("ts", f.ts)?;
            d.set_item("costs", f.costs)?;
            out.append(d)?;
        }
        Ok(out)
    }

    /// Returns and clears the rejections and cancellations: dicts with order_id, symbol,
    /// exchange, side, quantity, reason, ts, cancelled.
    fn drain_rejections<'py>(&mut self, py: Python<'py>) -> PyResult<Bound<'py, PyList>> {
        let out = PyList::empty_bound(py);
        for r in self.core.drain_rejections() {
            let d = PyDict::new_bound(py);
            d.set_item("order_id", r.order_id)?;
            d.set_item("symbol", r.symbol)?;
            d.set_item("exchange", r.exchange)?;
            d.set_item("side", r.side)?;
            d.set_item("quantity", r.quantity)?;
            d.set_item("reason", r.reason)?;
            d.set_item("ts", r.ts)?;
            d.set_item("cancelled", r.cancelled)?;
            out.append(d)?;
        }
        Ok(out)
    }

    /// Booked cash in minor units.
    #[getter]
    fn cash(&self) -> i64 {
        self.core.cash()
    }

    /// Transaction costs paid so far, minor units.
    #[getter]
    fn fees(&self) -> i64 {
        self.core.fees()
    }

    /// Sum of the notional of every fill, minor units.
    #[getter]
    fn traded_notional(&self) -> i64 {
        self.core.traded_notional()
    }

    /// Sale proceeds booked but not yet available, minor units.
    #[getter]
    fn unsettled(&self) -> i64 {
        self.core.unsettled()
    }

    /// Cash that may fund a buy now, minor units.
    #[getter]
    fn available_cash(&self) -> i64 {
        self.core.available_cash()
    }

    /// The settlement cycle in sessions.
    #[getter]
    fn settlement_days(&self) -> i64 {
        self.core.settlement_days()
    }

    /// Pending sale proceeds as `(due session index, amount in minor units)`.
    #[getter]
    fn receivables(&self) -> Vec<(i64, i64)> {
        self.core.receivables()
    }

    /// Non-flat positions as `(symbol, exchange, quantity)` in the order first opened.
    #[getter]
    fn positions<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyList>> {
        let out = PyList::empty_bound(py);
        for (s, e, q) in self.core.positions() {
            out.append(PyTuple::new_bound(
                py,
                [s.into_py(py), e.into_py(py), q.into_py(py)],
            ))?;
        }
        Ok(out)
    }

    /// Ids of orders not yet filled, rejected or cancelled, in submission order.
    #[getter]
    fn working_orders(&self) -> Vec<String> {
        self.core.working_orders()
    }
}

/// Cost in minor units of one fill under a named India pack (`"india.equity.delivery"`,
/// `"india.equity.intraday"`, `"none"`); the function `NextOpenSimulator` applies, for parity
/// checks against the Python fill-cost functions. `side` is `"buy"` or `"sell"`.
#[pyfunction]
#[pyo3(name = "next_open_fill_cost")]
pub fn py_next_open_fill_cost(pack: &str, side: &str, quantity: f64, price: f64) -> PyResult<i64> {
    india_fill_cost(pack, side, quantity, price).map_err(to_py_err)
}

pub fn register(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<NextOpenSimulator>()?;
    m.add_function(wrap_pyfunction!(py_next_open_fill_cost, m)?)?;
    Ok(())
}
