use pyo3::prelude::*;
use pyo3::types::PyModule;

use honba_messages::{
    Bar, BarAggregation, BarSpecification, BarType, InstrumentId, PriceType, UnixNanos, Venue,
};

/// Rust-backed bar. Mirrors `honba_messages::Bar`.
#[pyclass(name = "Bar", module = "honba")]
#[derive(Clone, Debug)]
pub struct RBar {
    #[pyo3(get, set)] pub symbol: String,
    #[pyo3(get, set)] pub venue: String,
    #[pyo3(get, set)] pub ts: u64,
    #[pyo3(get, set)] pub open: f64,
    #[pyo3(get, set)] pub high: f64,
    #[pyo3(get, set)] pub low: f64,
    #[pyo3(get, set)] pub close: f64,
    #[pyo3(get, set)] pub volume: f64,
}

#[pymethods]
impl RBar {
    #[new]
    #[pyo3(signature = (symbol, ts, open, high, low, close, volume=0.0, venue="NSE"))]
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
        format!(
            "<Bar {} ts={} close={}>",
            self.symbol, self.ts, self.close
        )
    }
}

impl RBar {
    /// Convert to the native Rust `Bar`. Spec defaults to 1-minute Last.
    pub fn to_rust(&self) -> Bar {
        let instrument = InstrumentId::new(&self.symbol, Venue::new(&self.venue));
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
    #[pyo3(get)] pub symbol: String,
    #[pyo3(get)] pub ts: u64,
    #[pyo3(get)] pub price: f64,
    #[pyo3(get)] pub qty: f64,
    #[pyo3(get)] pub side: String,
}

#[pymethods]
impl RFill {
    #[new]
    fn new(symbol: String, ts: u64, price: f64, qty: f64, side: String) -> Self {
        Self { symbol, ts, price, qty, side }
    }

    fn __repr__(&self) -> String {
        format!(
            "<Fill {} {} {} @ {}>",
            self.side, self.qty, self.symbol, self.price
        )
    }
}

pub fn register(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<RBar>()?;
    m.add_class::<RFill>()?;
    Ok(())
}
