use pyo3::prelude::*;
use pyo3::types::PyModule;

use super::domain::RBar;

/// Rust-backed SMA crossover. Same semantics as the Python `SmaCrossover`,
/// but the rolling window and position state live in Rust.
#[pyclass(name = "RustSmaCrossover", module = "honba")]
pub struct RustSmaCrossover {
    fast: usize,
    slow: usize,
    qty: f64,
    position: f64,
    prices: Vec<f64>,
    prev_diff: Option<f64>,
    intents: Vec<(String, f64)>, // (side, qty)
}

#[pymethods]
impl RustSmaCrossover {
    #[new]
    #[pyo3(signature = (fast=3, slow=8, qty=1.0))]
    fn new(fast: usize, slow: usize, qty: f64) -> Self {
        Self {
            fast,
            slow,
            qty,
            position: 0.0,
            prices: Vec::new(),
            prev_diff: None,
            intents: Vec::new(),
        }
    }

    /// Feed a bar. Returns `(side, qty)` if a signal fired, else `None`.
    fn on_bar(&mut self, bar: &RBar) -> Option<(String, f64)> {
        self.prices.push(bar.close);
        if self.prices.len() < self.slow {
            return None;
        }
        let fast = sma(&self.prices, self.fast)?;
        let slow = sma(&self.prices, self.slow)?;
        let diff = fast - slow;

        let mut fired: Option<(String, f64)> = None;
        if let Some(prev) = self.prev_diff {
            if prev <= 0.0 && diff > 0.0 && self.position == 0.0 {
                self.position = self.qty;
                fired = Some(("buy".to_string(), self.qty));
            } else if prev >= 0.0 && diff < 0.0 && self.position > 0.0 {
                let q = self.position;
                self.position = 0.0;
                fired = Some(("sell".to_string(), q));
            }
        }
        self.prev_diff = Some(diff);

        if let Some(ref f) = fired {
            self.intents.push(f.clone());
        }
        fired
    }

    /// Feed a plain close price instead of an `RBar`.
    fn on_close(&mut self, close: f64) -> Option<(String, f64)> {
        let synthetic = RBar::new("".to_string(), 0, close, close, close, close, 0.0, "NSE");
        self.on_bar(&synthetic)
    }

    #[getter]
    fn position(&self) -> f64 {
        self.position
    }

    #[getter]
    fn intent_count(&self) -> usize {
        self.intents.len()
    }

    fn intents(&self) -> Vec<(String, f64)> {
        self.intents.clone()
    }
}

fn sma(v: &[f64], n: usize) -> Option<f64> {
    if n == 0 || v.len() < n {
        return None;
    }
    Some(v[v.len() - n..].iter().sum::<f64>() / n as f64)
}

pub fn register(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<RustSmaCrossover>()?;
    Ok(())
}
