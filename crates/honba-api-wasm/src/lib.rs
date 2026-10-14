#![allow(missing_docs)]
#![deny(rustdoc::broken_intra_doc_links)]
#![deny(unsafe_code)]

//! WASM compute surface for Honba frontend.
//!
//! Pure compute only: indicators (close-only and OHLC), screener evaluation, and Journal v1
//! backtest replay. No tokio, no filesystem, no network. Depends only on pure L0-L4 crates.
//!
//! The logic lives in plain Rust modules ([`indicators`], [`screener`], [`replay`]) that are
//! tested natively; this file is only the `wasm-bindgen` layer over them. Series cross the
//! boundary as `Float64Array`; a screen result and a journal replay cross as JSON strings.

use wasm_bindgen::prelude::*;

pub mod indicators;
pub mod replay;
pub mod screener;

/// Full indicator series over `closes`, same length as the input, `NaN` during warm-up.
///
/// `params_json` is a JSON object such as `{"period":14}`. Throws a JavaScript `Error` (a `JsError`
/// carrying a `reason: message` pair) on an unknown indicator, bad params, non-finite input, or
/// finite input whose result overflows `f64` (e.g. an SMA of values near `1e308`); it never
/// returns infinity. The `reason` is the stable [`IndicatorError::reason`] string.
#[wasm_bindgen]
pub fn indicator_series(
    name: &str,
    params_json: &str,
    closes: &[f64],
) -> Result<Vec<f64>, JsError> {
    indicators::indicator_series(name, params_json, closes)
        .map_err(|error| indicators::error_pair(&error))
}

/// Full OHLC-input indicator series (currently `atr`) over equal-length `high`, `low` and
/// `close` arrays, same length as the input, `NaN` during warm-up (`period - 1` values).
///
/// Additive companion to [`indicator_series`]: `params_json` is a JSON object such as
/// `{"period":14}`. Throws a JavaScript `Error` (a `JsError` carrying a `reason: message` pair)
/// on an unknown (or close-only) indicator, bad params (period must be an integer in
/// `1..=1000000`), unequal array lengths, non-finite input, or finite input whose result
/// overflows `f64`; it never returns infinity. Empty arrays give an empty result. The `reason` is
/// the stable [`IndicatorError::reason`] string.
#[wasm_bindgen]
pub fn ohlc_indicator_series(
    name: &str,
    params_json: &str,
    high: &[f64],
    low: &[f64],
    close: &[f64],
) -> Result<Vec<f64>, JsError> {
    indicators::ohlc_indicator_series(name, params_json, high, low, close)
        .map_err(|error| indicators::error_pair(&error))
}

/// JSON catalog of the available indicators: names, params, outputs and warm-up length.
#[wasm_bindgen]
pub fn list_indicators() -> String {
    indicators::list_indicators_json()
}

/// Evaluates one screener predicate over column-oriented bar arrays and reports the outcome.
///
/// `predicate_json` is a JSON `ScreenerFilterPredicate` (e.g.
/// `{"key":"close","op":"gt","value":10}`). The five arrays are equal-length chronological bars.
/// Returns the JSON string `{"result": bool, "metrics": {key: number|null}}` (`null` is a metric
/// still in warm-up). Throws a JavaScript `Error` on malformed predicate JSON, unequal lengths,
/// non-finite input, an inconsistent OHLC row, an unsupported metric, or an operator/operand
/// mismatch.
#[wasm_bindgen]
pub fn screener_scan_predicate(
    predicate_json: &str,
    open: &[f64],
    high: &[f64],
    low: &[f64],
    close: &[f64],
    volume: &[f64],
) -> Result<String, JsError> {
    screener::scan_predicate_json(predicate_json, open, high, low, close, volume)
        .map_err(|e| JsError::new(&e))
}

/// Evaluates an AND/OR (possibly nested) screener filter group over column-oriented bar arrays.
///
/// `group_json` is a JSON `ScreenerFilterGroup`. Returns the same JSON shape and throws the same
/// way as [`screener_scan_predicate`], plus on group shape/size/operator errors.
#[wasm_bindgen]
pub fn screener_scan_group(
    group_json: &str,
    open: &[f64],
    high: &[f64],
    low: &[f64],
    close: &[f64],
    volume: &[f64],
) -> Result<String, JsError> {
    screener::scan_group_json(group_json, open, high, low, close, volume)
        .map_err(|e| JsError::new(&e))
}

/// Parses a Journal v1 NDJSON stream and returns its provenance header as JSON.
///
/// `journal_ndjson` is the whole journal in memory (the first non-empty line is the header,
/// `type: "header"`; the rest are audit records). Returns the `JournalHeader` object as JSON.
/// Throws a JavaScript `Error` on an empty stream, a missing or malformed header, a malformed
/// record line, or an unsupported journal version.
#[wasm_bindgen]
pub fn journal_header(journal_ndjson: &str) -> Result<String, JsError> {
    replay::journal_header_json(journal_ndjson).map_err(|e| JsError::new(&e))
}

/// Replays a Journal v1 NDJSON stream and returns the reconstructed state as compact JSON.
///
/// `journal_ndjson` is the whole journal in memory. Returns the stable shape
/// `{"header": <JournalHeader>, "replay": {"trading_state", "dispatches", "positions", "orders",
/// "rejections"}}`, with `positions`/`orders` sorted and `rejections` keyed by order id, so the
/// frontend can show a backtest journal offline. Throws a JavaScript `Error` on the same inputs
/// as [`journal_header`].
#[wasm_bindgen]
pub fn replay_journal(journal_ndjson: &str) -> Result<String, JsError> {
    replay::replay_journal_json(journal_ndjson).map_err(|e| JsError::new(&e))
}

#[cfg(test)]
mod tests;
