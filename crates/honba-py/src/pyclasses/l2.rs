//! `L2Simulator`: binding of [`honba_sim::L2Sim`] (ADR 0030, chunk E3-S1b-2).
//!
//! Same two layers as [`super::next_open`]: an interpreter-free core ([`NativeL2`], [`QuoteIn`],
//! [`TradeIn`], [`L2OrderIn`], [`L2Config`]) that the Rust tests drive, and a thin `#[pyclass]`
//! shell reading Python dicts. Money is integer minor units (ADR 0011); prices, sizes and
//! quantities are floats; times are integer nanoseconds of event time. The cost, error and
//! drain conventions are those of `NextOpenSimulator`, whose helper types this module reuses.

#![allow(clippy::useless_conversion)]

use std::sync::Arc;

use honba_engine::ExecutionEngine;
use honba_entities::{Currency, Money};
use honba_messages::{
    AggressorSide, Order, OrderId, OrderType, QuoteTick, TimeInForce, TradeId, TradeTick, UnixNanos,
};
use honba_sim::L2Sim;
use pyo3::exceptions::PyTypeError;
use pyo3::prelude::*;
use pyo3::types::{PyDict, PyList, PyModule, PyString, PyTuple};

use super::next_open::{
    callable_cost_fn, iid, lot_key, optional, parse_side, required, side_str, to_py_err, CostSpec,
    ErrorSlot, EventOut, FillOut, RejectionOut, Res, SimError,
};

/// One top-of-book quote.
#[derive(Clone, Debug, PartialEq)]
pub struct QuoteIn {
    /// Instrument symbol.
    pub symbol: String,
    /// Exchange code.
    pub exchange: String,
    /// Event time, nanoseconds.
    pub ts: u64,
    /// Best bid price.
    pub bid: f64,
    /// Best ask price.
    pub ask: f64,
    /// Size at the bid.
    pub bid_size: f64,
    /// Size at the ask.
    pub ask_size: f64,
}

/// One trade print.
#[derive(Clone, Debug, PartialEq)]
pub struct TradeIn {
    /// Instrument symbol.
    pub symbol: String,
    /// Exchange code.
    pub exchange: String,
    /// Event time, nanoseconds.
    pub ts: u64,
    /// Print price.
    pub price: f64,
    /// Print size.
    pub size: f64,
    /// `buyer` (default), `seller` or `none`; it does not affect matching.
    pub aggressor: String,
}

/// One order with its id, time in force and submit time.
#[derive(Clone, Debug, PartialEq)]
pub struct L2OrderIn {
    /// Order id.
    pub id: String,
    /// Instrument symbol.
    pub symbol: String,
    /// Exchange code.
    pub exchange: String,
    /// `buy` or `sell` (anything else is an order without a side, which is an error).
    pub side: String,
    /// `market`, `limit`, `stop_market`, `stop_limit` or `trailing_stop` (refused).
    pub kind: String,
    /// Quantity.
    pub qty: f64,
    /// Limit price.
    pub price: Option<f64>,
    /// Stop trigger price.
    pub trigger: Option<f64>,
    /// `day`, `gtc`, `ioc`, `fok` or `gtd`.
    pub tif: String,
    /// Submit time, nanoseconds.
    pub ts: u64,
}

/// Everything needed to build a [`NativeL2`].
pub struct L2Config {
    /// Starting cash in minor units (not negative).
    pub cash: i64,
    /// Currency code (`INR`).
    pub currency: String,
    /// Cap sells at the position held.
    pub long_only: bool,
    /// `(symbol, exchange, lot size)`.
    pub lot_sizes: Vec<(String, String, f64)>,
    /// Fill costs.
    pub costs: CostSpec,
}

impl L2Config {
    /// Defaults: INR, long only, no lots, no costs.
    pub fn new(cash: i64) -> Self {
        Self {
            cash,
            currency: "INR".into(),
            long_only: true,
            lot_sizes: Vec::new(),
            costs: CostSpec::None,
        }
    }
}

fn parse_tif(tif: &str) -> Res<TimeInForce> {
    match tif {
        "day" => Ok(TimeInForce::Day),
        "gtc" => Ok(TimeInForce::Gtc),
        "ioc" => Ok(TimeInForce::Ioc),
        "fok" => Ok(TimeInForce::Fok),
        "gtd" => Ok(TimeInForce::Gtd),
        other => Err(SimError::value(format!("unknown time in force {other:?}"))),
    }
}

fn finite(name: &str, v: f64) -> Res<()> {
    if v.is_finite() {
        Ok(())
    } else {
        Err(SimError::value(format!("{name} must be finite, got {v}")))
    }
}

/// The interpreter-free core of the binding: an [`L2Sim`] behind plain-data methods.
pub struct NativeL2 {
    sim: L2Sim,
}

impl NativeL2 {
    /// Builds the simulator; invalid cash, currency or lot sizes are errors.
    pub fn new(cfg: L2Config) -> Res<Self> {
        let currency: Currency =
            serde_json::from_value(serde_json::Value::String(cfg.currency.clone()))
                .map_err(|_| SimError::value(format!("unknown currency {:?}", cfg.currency)))?;
        let mut sim = L2Sim::new(Money::new(cfg.cash, currency))?.with_long_only(cfg.long_only);
        if let Some(f) = cfg.costs.into_fn(currency) {
            sim = sim.with_costs(f);
        }
        let mut out = Self { sim };
        for (symbol, exchange, lot) in &cfg.lot_sizes {
            out.set_lot_size(symbol, exchange, *lot)?;
        }
        Ok(out)
    }

    /// Observes one quote; `ValueError` for a non-finite, crossed or negative-size quote.
    pub fn on_quote(&mut self, q: &QuoteIn) -> Res<()> {
        finite("bid_price", q.bid)?;
        finite("ask_price", q.ask)?;
        finite("bid_size", q.bid_size)?;
        finite("ask_size", q.ask_size)?;
        if q.bid > q.ask {
            return Err(SimError::value("bid_price must be <= ask_price"));
        }
        if q.bid_size < 0.0 || q.ask_size < 0.0 {
            return Err(SimError::value("sizes must be >= 0"));
        }
        let ts = UnixNanos::from_u64(q.ts);
        let tick = QuoteTick::new(
            iid(&q.symbol, &q.exchange),
            q.bid,
            q.ask,
            q.bid_size,
            q.ask_size,
            ts,
            ts,
        );
        Ok(self.sim.on_quote(&tick)?)
    }

    /// Observes one print; `ValueError` for a non-finite price or a negative size.
    pub fn on_trade(&mut self, t: &TradeIn) -> Res<()> {
        finite("price", t.price)?;
        finite("size", t.size)?;
        if t.size < 0.0 {
            return Err(SimError::value("size must be >= 0"));
        }
        let aggressor = match t.aggressor.as_str() {
            "buyer" => AggressorSide::Buyer,
            "seller" => AggressorSide::Seller,
            "none" | "no_aggressor" => AggressorSide::NoAggressor,
            other => return Err(SimError::value(format!("unknown aggressor {other:?}"))),
        };
        let ts = UnixNanos::from_u64(t.ts);
        let tick = TradeTick::new(
            iid(&t.symbol, &t.exchange),
            t.price,
            t.size,
            aggressor,
            TradeId::new(format!("T-{}", t.ts)),
            ts,
            ts,
        );
        Ok(self.sim.on_trade(&tick)?)
    }

    /// Submits an order.
    pub fn submit(&mut self, o: &L2OrderIn) -> Res<()> {
        let kind = match o.kind.as_str() {
            "market" => OrderType::Market,
            "limit" => OrderType::Limit,
            "stop_market" => OrderType::StopMarket,
            "stop_limit" => OrderType::StopLimit,
            "trailing_stop" => OrderType::TrailingStop,
            other => return Err(SimError::value(format!("unknown order type {other:?}"))),
        };
        let tif = parse_tif(&o.tif)?;
        let ts = UnixNanos::from_u64(o.ts);
        let order = Order::new(
            OrderId::new(o.id.as_str()),
            iid(&o.symbol, &o.exchange),
            parse_side(&o.side),
            kind,
            o.qty,
            o.price,
            tif,
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

    /// Moves event time to `ts` with no market event; due Day orders expire at their close.
    pub fn advance_to(&mut self, ts: u64) -> Res<()> {
        Ok(self.sim.advance_to(UnixNanos::from_u64(ts))?)
    }

    /// Explicit session close: every working Day order expires, stamped `ts`.
    pub fn on_session_close(&mut self, ts: u64) -> Res<()> {
        Ok(self.sim.on_session_close(UnixNanos::from_u64(ts))?)
    }

    /// Sets the funding-cut lot size of an instrument.
    pub fn set_lot_size(&mut self, symbol: &str, exchange: &str, lot: f64) -> Res<()> {
        Ok(self.sim.set_lot_size(&iid(symbol, exchange), lot)?)
    }

    /// Seeds or overwrites the position in an instrument (zero removes it).
    pub fn set_position(&mut self, symbol: &str, exchange: &str, quantity: f64) -> Res<()> {
        Ok(self.sim.set_position(&iid(symbol, exchange), quantity)?)
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

    /// The top of book `(bid, ask, bid_size, ask_size)` of an instrument, once quoted.
    pub fn book(&self, symbol: &str, exchange: &str) -> Option<(f64, f64, f64, f64)> {
        self.sim
            .book(&iid(symbol, exchange))
            .map(|b| (b.bid(), b.ask(), b.bid_size(), b.ask_size()))
    }

    /// Returns and clears the ordered execution events (ADR 0019 decision 4).
    pub fn drain_events(&mut self) -> Vec<EventOut> {
        ExecutionEngine::drain_events(&mut self.sim)
            .unwrap_or_default()
            .iter()
            .map(EventOut::from_event)
            .collect()
    }

    /// Returns and clears the fills.
    pub fn drain_fills(&mut self) -> Vec<FillOut> {
        ExecutionEngine::drain_fills(&mut self.sim)
            .unwrap_or_default()
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

    /// Returns and clears the rejections, cancellations and expiries.
    pub fn drain_rejections(&mut self) -> Vec<RejectionOut> {
        ExecutionEngine::drain_rejections(&mut self.sim)
            .unwrap_or_default()
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

fn quote_from_py(d: &Bound<'_, PyDict>) -> PyResult<QuoteIn> {
    Ok(QuoteIn {
        symbol: required(d, "symbol")?,
        exchange: optional(d, "exchange")?.unwrap_or_else(|| "NSE".into()),
        ts: required(d, "ts")?,
        bid: required(d, "bid")?,
        ask: required(d, "ask")?,
        bid_size: required(d, "bid_size")?,
        ask_size: required(d, "ask_size")?,
    })
}

fn trade_from_py(d: &Bound<'_, PyDict>) -> PyResult<TradeIn> {
    Ok(TradeIn {
        symbol: required(d, "symbol")?,
        exchange: optional(d, "exchange")?.unwrap_or_else(|| "NSE".into()),
        ts: required(d, "ts")?,
        price: required(d, "price")?,
        size: required(d, "size")?,
        aggressor: optional(d, "aggressor")?.unwrap_or_else(|| "buyer".into()),
    })
}

fn order_from_py(d: &Bound<'_, PyDict>) -> PyResult<L2OrderIn> {
    Ok(L2OrderIn {
        id: required(d, "id")?,
        symbol: required(d, "symbol")?,
        exchange: optional(d, "exchange")?.unwrap_or_else(|| "NSE".into()),
        side: required(d, "side")?,
        kind: optional(d, "type")?.unwrap_or_else(|| "market".into()),
        qty: required(d, "qty")?,
        price: optional(d, "price")?,
        trigger: optional(d, "trigger")?,
        tif: optional(d, "tif")?.unwrap_or_else(|| "day".into()),
        ts: required(d, "ts")?,
    })
}

/// L2 top-of-book execution simulator (ADR 0030): orders match against a `QuoteTick` /
/// `TradeTick` stream at nanosecond event time. Money is integer minor units; quotes, trades and
/// orders are dicts.
///
/// `costs` is `None`, a pack name (`"india.equity.delivery"`, `"india.equity.intraday"`,
/// `"none"`) or a callable `(side: str, quantity: float, price: float) -> int` of minor units.
/// A callable's exception propagates unchanged; a negative result is a `ValueError`.
#[pyclass(module = "honba._honba")]
pub struct L2Simulator {
    core: NativeL2,
    errors: ErrorSlot,
}

impl L2Simulator {
    fn finish<T>(&self, result: Res<T>) -> PyResult<T> {
        let stored = self.errors.lock().unwrap().take();
        match result {
            Ok(v) => Ok(v),
            Err(e) => Err(stored.unwrap_or_else(|| to_py_err(e))),
        }
    }
}

#[pymethods]
impl L2Simulator {
    #[new]
    #[pyo3(signature = (cash, currency="INR", *, long_only=true, lot_sizes=None, costs=None))]
    fn py_new(
        cash: i64,
        currency: &str,
        long_only: bool,
        lot_sizes: Option<Bound<'_, PyDict>>,
        costs: Option<Bound<'_, PyAny>>,
    ) -> PyResult<Self> {
        let errors: ErrorSlot = Arc::default();
        let cur: Currency = serde_json::from_value(serde_json::Value::String(currency.into()))
            .map_err(|_| {
                pyo3::exceptions::PyValueError::new_err(format!("unknown currency {currency:?}"))
            })?;
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
        let mut cfg = L2Config::new(cash);
        cfg.currency = currency.into();
        cfg.long_only = long_only;
        cfg.costs = spec;
        if let Some(lots) = lot_sizes {
            for (k, v) in lots.iter() {
                let (symbol, exchange) = lot_key(&k)?;
                cfg.lot_sizes.push((symbol, exchange, v.extract()?));
            }
        }
        let core = NativeL2::new(cfg).map_err(to_py_err)?;
        Ok(Self { core, errors })
    }

    /// Observes a quote (dict: symbol, ts, bid, ask, bid_size, ask_size[, exchange]); matches
    /// working orders. `ValueError` for a non-finite, crossed or negative-size quote.
    fn on_quote(&mut self, quote: &Bound<'_, PyDict>) -> PyResult<()> {
        let q = quote_from_py(quote)?;
        let r = self.core.on_quote(&q);
        self.finish(r)
    }

    /// Observes a print (dict: symbol, ts, price, size[, aggressor, exchange]).
    fn on_trade(&mut self, trade: &Bound<'_, PyDict>) -> PyResult<()> {
        let t = trade_from_py(trade)?;
        let r = self.core.on_trade(&t);
        self.finish(r)
    }

    /// Submits an order (dict: id, symbol, side, qty, ts[, type, price, trigger, tif,
    /// exchange]). `ValueError` for a missing side or an id that is already working; a trailing
    /// stop is reported as an `unsupported_order_type` rejection.
    fn submit(&mut self, order: &Bound<'_, PyDict>) -> PyResult<()> {
        let o = order_from_py(order)?;
        let r = self.core.submit(&o);
        self.finish(r)
    }

    /// Cancels a working order, stamped `now`; unknown ids are a no-op.
    fn cancel(&mut self, order_id: &str, now: u64) -> PyResult<()> {
        let r = self.core.cancel(order_id, now);
        self.finish(r)
    }

    /// Moves event time to `ts` with no market event; Day orders past their close expire.
    fn advance_to(&mut self, ts: u64) -> PyResult<()> {
        let r = self.core.advance_to(ts);
        self.finish(r)
    }

    /// Explicit session close: every working Day order expires, stamped `ts`.
    fn on_session_close(&mut self, ts: u64) -> PyResult<()> {
        let r = self.core.on_session_close(ts);
        self.finish(r)
    }

    /// Sets the funding-cut lot size of `symbol` (default exchange `NSE`).
    #[pyo3(signature = (symbol, lot_size, exchange="NSE"))]
    fn set_lot_size(&mut self, symbol: &str, lot_size: f64, exchange: &str) -> PyResult<()> {
        let r = self.core.set_lot_size(symbol, exchange, lot_size);
        self.finish(r)
    }

    /// Seeds or overwrites the position in `symbol` (default exchange `NSE`); zero removes it.
    #[pyo3(signature = (symbol, quantity, exchange="NSE"))]
    fn set_position(&mut self, symbol: &str, quantity: f64, exchange: &str) -> PyResult<()> {
        let r = self.core.set_position(symbol, exchange, quantity);
        self.finish(r)
    }

    /// The top of book `(bid, ask, bid_size, ask_size)` of `symbol`, `None` before a quote.
    #[pyo3(signature = (symbol, exchange="NSE"))]
    fn book(&self, symbol: &str, exchange: &str) -> Option<(f64, f64, f64, f64)> {
        self.core.book(symbol, exchange)
    }

    /// Returns and clears the ordered execution events (ADR 0019): dicts tagged by `kind`
    /// (`rejected`, `fill`, `cancel_requested`, `cancelled`, `expired`) with order_id, symbol,
    /// exchange, side, quantity, ts, venue_order_id and, per kind, reason (`rejected`) or price,
    /// costs (minor units), cum_qty, complete (`fill`).
    fn drain_events<'py>(&mut self, py: Python<'py>) -> PyResult<Bound<'py, PyList>> {
        let out = PyList::empty_bound(py);
        for e in self.core.drain_events() {
            let d = PyDict::new_bound(py);
            d.set_item("kind", e.kind)?;
            d.set_item("order_id", e.order_id)?;
            d.set_item("symbol", e.symbol)?;
            d.set_item("exchange", e.exchange)?;
            d.set_item("side", e.side)?;
            d.set_item("quantity", e.quantity)?;
            d.set_item("ts", e.ts)?;
            d.set_item("venue_order_id", e.venue_order_id)?;
            match e.kind {
                "fill" => {
                    d.set_item("price", e.price)?;
                    d.set_item("costs", e.costs)?;
                    d.set_item("cum_qty", e.cum_qty)?;
                    d.set_item("complete", e.complete)?;
                }
                "rejected" => d.set_item("reason", e.reason)?,
                _ => {}
            }
            out.append(d)?;
        }
        Ok(out)
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

    /// Returns and clears the rejections, cancellations and expiries: dicts with order_id,
    /// symbol, exchange, side, quantity, reason, ts, cancelled.
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

    /// Ids of orders not yet filled, rejected, cancelled or expired, in submission order.
    #[getter]
    fn working_orders(&self) -> Vec<String> {
        self.core.working_orders()
    }
}

pub fn register(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<L2Simulator>()?;
    Ok(())
}
