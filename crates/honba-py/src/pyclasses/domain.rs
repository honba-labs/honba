#![allow(clippy::useless_conversion)]

use pyo3::prelude::*;
use pyo3::types::PyModule;

use honba_messages::{
    Bar, BarAggregation, BarSpecification, BarType, InstrumentId as RustInstrumentId,
    OrderSide as RustOrderSide, OrderType as RustOrderType, PriceType, QuoteTick as RustQuoteTick,
    TimeInForce as RustTimeInForce, UnixNanos, Venue as RustVenue,
};
use honba_strategy::OrderIntent as RustOrderIntent;

/// Rust-backed InstrumentId. Mirrors `honba_messages::InstrumentId`.
#[pyclass(name = "InstrumentId", module = "honba")]
#[derive(Clone, Debug, PartialEq, Eq, Hash)]
pub struct RInstrumentId {
    #[pyo3(get)]
    pub symbol: String,
    #[pyo3(get)]
    pub venue: String,
}

#[pymethods]
impl RInstrumentId {
    #[new]
    #[pyo3(signature = (symbol, venue="NSE"))]
    pub fn new(symbol: String, venue: &str) -> Self {
        Self {
            symbol,
            venue: venue.to_string(),
        }
    }

    fn __repr__(&self) -> String {
        format!("{}.{}", self.symbol, self.venue)
    }

    fn __str__(&self) -> String {
        self.__repr__()
    }
}

impl RInstrumentId {
    pub fn to_rust(&self) -> RustInstrumentId {
        RustInstrumentId::new(&self.symbol, RustVenue::new(&self.venue))
    }

    pub fn from_rust(id: &RustInstrumentId) -> Self {
        Self {
            symbol: id.symbol().to_string(),
            venue: id.venue().as_str().to_string(),
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
    pub venue: String,
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
    #[pyo3(signature = (symbol, bid_price, ask_price, bid_size=1.0, ask_size=1.0, ts=0, venue="NSE"))]
    #[allow(clippy::too_many_arguments)]
    pub fn new(
        symbol: String,
        bid_price: f64,
        ask_price: f64,
        bid_size: f64,
        ask_size: f64,
        ts: u64,
        venue: &str,
    ) -> Self {
        Self {
            symbol,
            venue: venue.to_string(),
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
            self.symbol, self.venue, self.bid_price, self.ask_price, self.ts
        )
    }
}

impl RQuoteTick {
    pub fn to_rust(&self) -> RustQuoteTick {
        let inst = RustInstrumentId::new(&self.symbol, RustVenue::new(&self.venue));
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
    pub venue: String,
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
    #[pyo3(signature = (symbol, ts, open, high, low, close, volume=0.0, venue="NSE"))]
    #[allow(clippy::too_many_arguments)]
    pub fn new(
        symbol: String,
        ts: u64,
        open: f64,
        high: f64,
        low: f64,
        close: f64,
        volume: f64,
        venue: &str,
    ) -> Self {
        Self {
            symbol,
            venue: venue.to_string(),
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
        let instrument = RustInstrumentId::new(&self.symbol, RustVenue::new(&self.venue));
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
#[pyclass(name = "OrderIntent", module = "honba")]
#[derive(Clone, Debug)]
pub struct ROrderIntent {
    #[pyo3(get)]
    pub symbol: String,
    #[pyo3(get)]
    pub venue: String,
    #[pyo3(get)]
    pub side: String,
    #[pyo3(get)]
    pub quantity: f64,
    #[pyo3(get)]
    pub order_type: String,
    #[pyo3(get)]
    pub price: Option<f64>,
    #[pyo3(get)]
    pub time_in_force: String,
}

#[pymethods]
#[allow(clippy::useless_conversion)]
impl ROrderIntent {
    #[new]
    #[pyo3(signature = (symbol, side, quantity, order_type="market", price=None, time_in_force="day", venue="NSE"))]
    #[allow(clippy::too_many_arguments)]
    pub fn new(
        symbol: String,
        side: &str,
        quantity: f64,
        order_type: &str,
        price: Option<f64>,
        time_in_force: &str,
        venue: &str,
    ) -> PyResult<Self> {
        if quantity <= 0.0 {
            return Err(pyo3::exceptions::PyValueError::new_err(format!(
                "quantity must be positive, got {quantity}"
            )));
        }
        let norm_side = side.to_lowercase();
        if norm_side != "buy" && norm_side != "sell" {
            return Err(pyo3::exceptions::PyValueError::new_err(format!(
                "invalid side '{side}'; must be 'buy' or 'sell'"
            )));
        }
        let norm_type = order_type.to_lowercase();
        if (norm_type == "limit" || norm_type == "stop") && price.is_none() {
            return Err(pyo3::exceptions::PyValueError::new_err(format!(
                "{norm_type} order requires a price"
            )));
        }
        Ok(Self {
            symbol,
            venue: venue.to_string(),
            side: norm_side,
            quantity,
            order_type: norm_type,
            price,
            time_in_force: time_in_force.to_lowercase(),
        })
    }

    #[staticmethod]
    #[pyo3(signature = (symbol, quantity, venue="NSE"))]
    #[allow(clippy::useless_conversion)]
    pub fn market_buy(symbol: String, quantity: f64, venue: &str) -> PyResult<Self> {
        Self::new(symbol, "buy", quantity, "market", None, "day", venue)
    }

    #[staticmethod]
    #[pyo3(signature = (symbol, quantity, venue="NSE"))]
    #[allow(clippy::useless_conversion)]
    pub fn market_sell(symbol: String, quantity: f64, venue: &str) -> PyResult<Self> {
        Self::new(symbol, "sell", quantity, "market", None, "day", venue)
    }

    #[staticmethod]
    #[pyo3(signature = (symbol, quantity, price, venue="NSE"))]
    #[allow(clippy::useless_conversion)]
    pub fn limit_buy(symbol: String, quantity: f64, price: f64, venue: &str) -> PyResult<Self> {
        Self::new(symbol, "buy", quantity, "limit", Some(price), "day", venue)
    }

    #[staticmethod]
    #[pyo3(signature = (symbol, quantity, price, venue="NSE"))]
    #[allow(clippy::useless_conversion)]
    pub fn limit_sell(symbol: String, quantity: f64, price: f64, venue: &str) -> PyResult<Self> {
        Self::new(symbol, "sell", quantity, "limit", Some(price), "day", venue)
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
        let inst = RustInstrumentId::new(&self.symbol, RustVenue::new(&self.venue));
        let side = match self.side.as_str() {
            "buy" => RustOrderSide::Buy,
            "sell" => RustOrderSide::Sell,
            other => return Err(format!("unknown order side: {other}")),
        };
        let order_type = match self.order_type.as_str() {
            "market" => RustOrderType::Market,
            "limit" => RustOrderType::Limit,
            "stop" | "stop_market" => RustOrderType::StopMarket,
            "stop_limit" => RustOrderType::StopLimit,
            other => return Err(format!("unknown order type: {other}")),
        };
        let tif = match self.time_in_force.as_str() {
            "day" => RustTimeInForce::Day,
            "gtc" => RustTimeInForce::Gtc,
            "ioc" => RustTimeInForce::Ioc,
            other => return Err(format!("unknown time in force: {other}")),
        };
        Ok(RustOrderIntent {
            instrument_id: inst,
            side,
            quantity: self.quantity,
            order_type,
            price: self.price,
            trigger_price: None,
            time_in_force: tif,
        })
    }
}

pub fn register(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<RInstrumentId>()?;
    m.add_class::<RQuoteTick>()?;
    m.add_class::<RBar>()?;
    m.add_class::<RFill>()?;
    m.add_class::<ROrderIntent>()?;
    Ok(())
}
