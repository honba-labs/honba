#![allow(missing_docs)]
#![deny(rustdoc::broken_intra_doc_links)]
#![deny(unsafe_code)]

//! WASM compute surface for Honba frontend.
//!
//! Pure compute only: indicators, screener evaluation, backtest replay over local data.
//! No tokio, no filesystem, no network. Depends only on pure L0–L4 crates.

use wasm_bindgen::prelude::*;

#[wasm_bindgen]
extern "C" {
    #[wasm_bindgen(js_namespace = console)]
    fn log(s: &str);
}

#[wasm_bindgen]
pub fn hello_wasm() -> String {
    "Honba WASM ready".to_string()
}

/// Compute a simple moving average over a slice of values (WASM demo).
#[wasm_bindgen]
pub fn sma(values: &[f64], period: usize) -> f64 {
    if values.is_empty() || period == 0 {
        return 0.0;
    }
    let start = if values.len() >= period {
        values.len() - period
    } else {
        0
    };
    let slice = &values[start..];
    let sum: f64 = slice.iter().sum();
    sum / slice.len() as f64
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_sma_basic() {
        let values = vec![1.0, 2.0, 3.0, 4.0, 5.0];
        assert_eq!(sma(&values, 5), 3.0);
    }
}
