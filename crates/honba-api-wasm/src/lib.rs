#![allow(missing_docs)]
#![deny(rustdoc::broken_intra_doc_links)]
#![deny(unsafe_code)]

//! WASM compute surface for Honba frontend.
//!
//! Pure compute only: indicators now; screener evaluation and backtest replay later. No tokio,
//! no filesystem, no network. Depends only on pure L0-L4 crates.
//!
//! The logic lives in plain Rust modules ([`indicators`]) that are tested natively; this file is
//! only the `wasm-bindgen` layer over them. Slices cross the boundary as `Float64Array`.

use wasm_bindgen::prelude::*;

pub mod indicators;

/// Full indicator series over `closes`, same length as the input, `NaN` during warm-up.
///
/// `params_json` is a JSON object such as `{"period":14}`. Throws a JavaScript `Error` (a `JsError`
/// carrying the message) on an unknown indicator, bad params, non-finite input, or finite input
/// whose result overflows `f64` (e.g. an SMA of values near `1e308`); it never returns infinity.
#[wasm_bindgen]
pub fn indicator_series(
    name: &str,
    params_json: &str,
    closes: &[f64],
) -> Result<Vec<f64>, JsError> {
    indicators::indicator_series(name, params_json, closes)
        .map_err(|e| JsError::new(&e.to_string()))
}

/// JSON catalog of the available indicators: names, params, outputs and warm-up length.
#[wasm_bindgen]
pub fn list_indicators() -> String {
    indicators::list_indicators_json()
}

#[cfg(test)]
mod tests;
