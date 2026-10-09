#![allow(clippy::useless_conversion)]

use pyo3::prelude::*;
use pyo3::types::PyModule;

use honba_market::india::profile::IndiaMarketProfile;
use honba_messages::{
    Bar, BarAggregation, BarSpecification, BarType, Exchange as RustExchange,
    InstrumentId as RustInstrumentId, OrderSide as RustOrderSide, OrderType as RustOrderType,
    PriceType, QuoteTick as RustQuoteTick, TimeInForce as RustTimeInForce, UnixNanos,
};
use honba_strategy::OrderIntent as RustOrderIntent;

/// Rust-backed InstrumentId. Mirrors `honba_messages::InstrumentId`.
#[pyclass(name = "InstrumentId", module = "honba")]
#[derive(Clone, Debug, PartialEq, Eq, Hash)]
pub struct RInstrumentId {
    #[pyo3(get)]
    pub symbol: String,
    #[pyo3(get)]
    pub exchange: String,
}

#[pymethods]
impl RInstrumentId {
    #[new]
    #[pyo3(signature = (symbol, exchange="NSE"))]
    pub fn new(symbol: String, exchange: &str) -> Self {
        Self {
            symbol,
            exchange: exchange.to_string(),
        }
    }

    fn __repr__(&self) -> String {
        format!("{}.{}", self.symbol, self.exchange)
    }

    fn __str__(&self) -> String {
        self.__repr__()
    }
}

impl RInstrumentId {
    pub fn to_rust(&self) -> RustInstrumentId {
        RustInstrumentId::new(&self.symbol, RustExchange::new(&self.exchange))
    }

    pub fn from_rust(id: &RustInstrumentId) -> Self {
        Self {
            symbol: id.symbol().to_string(),
            exchange: id.exchange().as_str().to_string(),
        }
    }
}

/// Rust-backed QuoteTick (top-of-book market data).
#[pyclass(name = "QuoteTick", module = "honba")]
#[derive(Clone, Debug)]
pub struct RQuoteTick {
    #[pyo3(get)]
    pub symbol: String,
    #[pyo3(get)]
    pub exchange: String,
    #[pyo3(get)]
    pub bid_price: f64,
    #[pyo3(get)]
    pub ask_price: f64,
    #[pyo3(get)]
    pub bid_size: f64,
    #[pyo3(get)]
    pub ask_size: f64,
    #[pyo3(get)]
    pub ts: u64,
}

#[pymethods]
impl RQuoteTick {
    #[new]
    #[pyo3(signature = (symbol, bid_price, ask_price, bid_size=1.0, ask_size=1.0, ts=0, exchange="NSE"))]
    #[allow(clippy::too_many_arguments)]
    pub fn new(
        symbol: String,
        bid_price: f64,
        ask_price: f64,
        bid_size: f64,
        ask_size: f64,
        ts: u64,
        exchange: &str,
    ) -> Self {
        Self {
            symbol,
            exchange: exchange.to_string(),
            bid_price,
            ask_price,
            bid_size,
            ask_size,
            ts,
        }
    }

    #[getter]
    pub fn mid_price(&self) -> f64 {
        (self.bid_price + self.ask_price) / 2.0
    }

    fn __repr__(&self) -> String {
        format!(
            "<QuoteTick {}.{} bid={:.2} ask={:.2} ts={}>",
            self.symbol, self.exchange, self.bid_price, self.ask_price, self.ts
        )
    }
}

impl RQuoteTick {
    pub fn to_rust(&self) -> RustQuoteTick {
        let inst = RustInstrumentId::new(&self.symbol, RustExchange::new(&self.exchange));
        let t = UnixNanos::from_u64(self.ts);
        RustQuoteTick::new(
            inst,
            self.bid_price,
            self.ask_price,
            self.bid_size,
            self.ask_size,
            t,
            t,
        )
    }
}

/// Rust-backed bar. Mirrors `honba_messages::Bar`.
#[pyclass(name = "Bar", module = "honba")]
#[derive(Clone, Debug)]
pub struct RBar {
    #[pyo3(get, set)]
    pub symbol: String,
    #[pyo3(get, set)]
    pub exchange: String,
    #[pyo3(get, set)]
    pub ts: u64,
    #[pyo3(get, set)]
    pub open: f64,
    #[pyo3(get, set)]
    pub high: f64,
    #[pyo3(get, set)]
    pub low: f64,
    #[pyo3(get, set)]
    pub close: f64,
    #[pyo3(get, set)]
    pub volume: f64,
}

#[pymethods]
impl RBar {
    #[new]
    #[pyo3(signature = (symbol, ts, open, high, low, close, volume=0.0, exchange="NSE"))]
    #[allow(clippy::too_many_arguments)]
    pub fn new(
        symbol: String,
        ts: u64,
        open: f64,
        high: f64,
        low: f64,
        close: f64,
        volume: f64,
        exchange: &str,
    ) -> Self {
        Self {
            symbol,
            exchange: exchange.to_string(),
            ts,
            open,
            high,
            low,
            close,
            volume,
        }
    }

    fn __repr__(&self) -> String {
        format!("<Bar {} ts={} close={}>", self.symbol, self.ts, self.close)
    }
}

impl RBar {
    /// Convert to the native Rust `Bar`. Spec defaults to 1-minute Last.
    pub fn to_rust(&self) -> Bar {
        let instrument = RustInstrumentId::new(&self.symbol, RustExchange::new(&self.exchange));
        let spec = BarSpecification::new(1, BarAggregation::Minute, PriceType::Last);
        let bar_type = BarType::new(instrument, spec);
        let t = UnixNanos::from_u64(self.ts);
        Bar::new(
            bar_type,
            self.open,
            self.high,
            self.low,
            self.close,
            self.volume,
            t,
            t,
        )
    }
}

/// Rust-backed fill record.
#[pyclass(name = "Fill", module = "honba")]
#[derive(Clone, Debug)]
pub struct RFill {
    #[pyo3(get)]
    pub symbol: String,
    #[pyo3(get)]
    pub ts: u64,
    #[pyo3(get)]
    pub price: f64,
    #[pyo3(get)]
    pub qty: f64,
    #[pyo3(get)]
    pub side: String,
}

#[pymethods]
impl RFill {
    #[new]
    fn new(symbol: String, ts: u64, price: f64, qty: f64, side: String) -> Self {
        Self {
            symbol,
            ts,
            price,
            qty,
            side,
        }
    }

    fn __repr__(&self) -> String {
        format!(
            "<Fill {} {} {} @ {}>",
            self.side, self.qty, self.symbol, self.price
        )
    }
}

/// Rust-backed OrderIntent. Mirrors `honba_strategy::OrderIntent`.
/// Rust-backed OrderIntent. Mirrors `honba_strategy::OrderIntent`.
///
/// `price` is the limit price (limit and stop-limit orders); `trigger_price`
/// is the stop trigger (stop-market and stop-limit orders). The constructor
/// enforces the same invariants as `OrderIntent::validate` in Rust.
#[pyclass(name = "OrderIntent", module = "honba")]
#[derive(Clone, Debug)]
pub struct ROrderIntent {
    #[pyo3(get)]
    pub symbol: String,
    #[pyo3(get)]
    pub exchange: String,
    #[pyo3(get)]
    pub side: String,
    #[pyo3(get)]
    pub quantity: f64,
    #[pyo3(get)]
    pub order_type: String,
    #[pyo3(get)]
    pub price: Option<f64>,
    #[pyo3(get)]
    pub trigger_price: Option<f64>,
    #[pyo3(get)]
    pub trail_amount: Option<f64>,
    #[pyo3(get)]
    pub trail_percent: Option<f64>,
    #[pyo3(get)]
    pub time_in_force: String,
}

#[pymethods]
#[allow(clippy::useless_conversion)]
impl ROrderIntent {
    #[new]
    #[pyo3(signature = (symbol, side, quantity, order_type="market", price=None, time_in_force="day", exchange="NSE", trigger_price=None, trail_amount=None, trail_percent=None))]
    #[allow(clippy::too_many_arguments)]
    pub fn new(
        symbol: String,
        side: &str,
        quantity: f64,
        order_type: &str,
        price: Option<f64>,
        time_in_force: &str,
        exchange: &str,
        trigger_price: Option<f64>,
        trail_amount: Option<f64>,
        trail_percent: Option<f64>,
    ) -> PyResult<Self> {
        let norm_type = match order_type.to_lowercase().as_str() {
            "stop" => "stop_market".to_string(),
            other => other.to_string(),
        };
        let intent = Self {
            symbol,
            exchange: exchange.to_string(),
            side: side.to_lowercase(),
            quantity,
            order_type: norm_type,
            price,
            trigger_price,
            trail_amount,
            trail_percent,
            time_in_force: time_in_force.to_lowercase(),
        };
        let rust = intent
            .to_rust()
            .map_err(pyo3::exceptions::PyValueError::new_err)?;
        rust.validate()
            .map_err(|e| pyo3::exceptions::PyValueError::new_err(e.to_string()))?;
        Ok(intent)
    }

    #[staticmethod]
    #[pyo3(signature = (symbol, quantity, exchange="NSE"))]
    pub fn market_buy(symbol: String, quantity: f64, exchange: &str) -> PyResult<Self> {
        Self::new(
            symbol, "buy", quantity, "market", None, "day", exchange, None, None, None,
        )
    }

    #[staticmethod]
    #[pyo3(signature = (symbol, quantity, exchange="NSE"))]
    pub fn market_sell(symbol: String, quantity: f64, exchange: &str) -> PyResult<Self> {
        Self::new(
            symbol, "sell", quantity, "market", None, "day", exchange, None, None, None,
        )
    }

    #[staticmethod]
    #[pyo3(signature = (symbol, quantity, price, exchange="NSE"))]
    pub fn limit_buy(symbol: String, quantity: f64, price: f64, exchange: &str) -> PyResult<Self> {
        Self::new(
            symbol,
            "buy",
            quantity,
            "limit",
            Some(price),
            "day",
            exchange,
            None,
            None,
            None,
        )
    }

    #[staticmethod]
    #[pyo3(signature = (symbol, quantity, price, exchange="NSE"))]
    pub fn limit_sell(symbol: String, quantity: f64, price: f64, exchange: &str) -> PyResult<Self> {
        Self::new(
            symbol,
            "sell",
            quantity,
            "limit",
            Some(price),
            "day",
            exchange,
            None,
            None,
            None,
        )
    }

    #[staticmethod]
    #[pyo3(signature = (symbol, quantity, trigger_price, exchange="NSE"))]
    pub fn stop_buy(
        symbol: String,
        quantity: f64,
        trigger_price: f64,
        exchange: &str,
    ) -> PyResult<Self> {
        let t = Some(trigger_price);
        Self::new(
            symbol,
            "buy",
            quantity,
            "stop_market",
            None,
            "day",
            exchange,
            t,
            None,
            None,
        )
    }

    #[staticmethod]
    #[pyo3(signature = (symbol, quantity, trigger_price, exchange="NSE"))]
    pub fn stop_sell(
        symbol: String,
        quantity: f64,
        trigger_price: f64,
        exchange: &str,
    ) -> PyResult<Self> {
        let t = Some(trigger_price);
        Self::new(
            symbol,
            "sell",
            quantity,
            "stop_market",
            None,
            "day",
            exchange,
            t,
            None,
            None,
        )
    }

    #[staticmethod]
    #[pyo3(signature = (symbol, quantity, trigger_price, limit_price, exchange="NSE"))]
    pub fn stop_limit_buy(
        symbol: String,
        quantity: f64,
        trigger_price: f64,
        limit_price: f64,
        exchange: &str,
    ) -> PyResult<Self> {
        let (p, t) = (Some(limit_price), Some(trigger_price));
        Self::new(symbol, "buy", quantity, "stop_limit", p, "day", exchange, t, None, None)
    }

    #[staticmethod]
    #[pyo3(signature = (symbol, quantity, trigger_price, limit_price, exchange="NSE"))]
    pub fn stop_limit_sell(
        symbol: String,
        quantity: f64,
        trigger_price: f64,
        limit_price: f64,
        exchange: &str,
    ) -> PyResult<Self> {
        let (p, t) = (Some(limit_price), Some(trigger_price));
        Self::new(
            symbol,
            "sell",
            quantity,
            "stop_limit",
            p,
            "day",
            exchange,
            t,
            None,
            None,
        )
    }

    #[staticmethod]
    #[pyo3(signature = (symbol, quantity, *, trail_amount=None, trail_percent=None, exchange="NSE"))]
    pub fn trailing_stop_buy(
        symbol: String,
        quantity: f64,
        trail_amount: Option<f64>,
        trail_percent: Option<f64>,
        exchange: &str,
    ) -> PyResult<Self> {
        Self::new(
            symbol,
            "buy",
            quantity,
            "trailing_stop",
            None,
            "day",
            exchange,
            None,
            trail_amount,
            trail_percent,
        )
    }

    #[staticmethod]
    #[pyo3(signature = (symbol, quantity, *, trail_amount=None, trail_percent=None, exchange="NSE"))]
    pub fn trailing_stop_sell(
        symbol: String,
        quantity: f64,
        trail_amount: Option<f64>,
        trail_percent: Option<f64>,
        exchange: &str,
    ) -> PyResult<Self> {
        Self::new(
            symbol,
            "sell",
            quantity,
            "trailing_stop",
            None,
            "day",
            exchange,
            None,
            trail_amount,
            trail_percent,
        )
    }

    fn __repr__(&self) -> String {
        format!(
            "<OrderIntent {} {} {} type={}>",
            self.side, self.quantity, self.symbol, self.order_type
        )
    }
}

impl ROrderIntent {
    pub fn to_rust(&self) -> Result<RustOrderIntent, String> {
        let inst = RustInstrumentId::new(&self.symbol, RustExchange::new(&self.exchange));
        let side = match self.side.as_str() {
            "buy" => RustOrderSide::Buy,
            "sell" => RustOrderSide::Sell,
            other => return Err(format!("invalid side '{other}'; must be 'buy' or 'sell'")),
        };
        let order_type = match self.order_type.as_str() {
            "market" => RustOrderType::Market,
            "limit" => RustOrderType::Limit,
            "stop" | "stop_market" => RustOrderType::StopMarket,
            "stop_limit" => RustOrderType::StopLimit,
            "trailing_stop" => RustOrderType::TrailingStop,
            other => return Err(format!("unknown order type: {other}")),
        };
        let tif = match self.time_in_force.as_str() {
            "day" => RustTimeInForce::Day,
            "gtc" => RustTimeInForce::Gtc,
            "ioc" => RustTimeInForce::Ioc,
            "fok" => RustTimeInForce::Fok,
            "gtd" => RustTimeInForce::Gtd,
            other => return Err(format!("unknown time in force: {other}")),
        };
        Ok(RustOrderIntent {
            instrument_id: inst,
            side,
            quantity: self.quantity,
            order_type,
            price: self.price,
            trigger_price: self.trigger_price,
            trail_amount: self.trail_amount,
            trail_percent: self.trail_percent,
            time_in_force: tif,
        })
    }
}

/// India (NSE/BSE) equity delivery settlement cycle in days.
///
/// With no `as_of` this is the cycle in force today (T+1); with an ISO date
/// (`"YYYY-MM-DD"`) it is the cycle on that date (T+2 before 2023-01-27, T+1 from then).
/// Source of truth is the India market pack in `honba-market`
/// (`IndiaMarketProfile::equity_settlement_days_as_of`); Python wraps this in
/// `honba.markets.india.settlement_days_for` and can override per run with
/// `settlement_days`.
#[pyfunction]
#[pyo3(signature = (as_of=None))]
pub fn nse_equity_settlement_days(as_of: Option<&str>) -> PyResult<usize> {
    match as_of {
        None => Ok(IndiaMarketProfile::equity_settlement_days()),
        Some(text) => {
            let date = chrono::NaiveDate::parse_from_str(text, "%Y-%m-%d").map_err(|e| {
                pyo3::exceptions::PyValueError::new_err(format!(
                    "as_of must be an ISO date (YYYY-MM-DD), got {text:?}: {e}"
                ))
            })?;
            Ok(IndiaMarketProfile::equity_settlement_days_as_of(date))
        }
    }
}

pub fn register(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<RInstrumentId>()?;
    m.add_class::<RQuoteTick>()?;
    m.add_class::<RBar>()?;
    m.add_class::<RFill>()?;
    m.add_class::<ROrderIntent>()?;
    m.add_function(wrap_pyfunction!(nse_equity_settlement_days, m)?)?;
    Ok(())
}
