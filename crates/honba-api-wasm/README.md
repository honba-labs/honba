# honba-api-wasm

WASM compute surface for the Honba frontend. Pure compute only: no tokio, no filesystem, no
network; depends on the pure L0-L4 crates (`scripts/dependency_graph.py` enforces this).

Logic lives in plain Rust modules that are tested natively; `src/lib.rs` is a thin `wasm-bindgen`
layer over them.

## Indicators

| Export | Signature |
|---|---|
| `indicator_series(name, params_json, closes)` | `(string, string, Float64Array) -> Float64Array`; throws a JS `Error` on failure |
| `ohlc_indicator_series(name, params_json, high, low, close)` | `(string, string, Float64Array, Float64Array, Float64Array) -> Float64Array`; throws a JS `Error` on failure |
| `list_indicators()` | `() -> string` (JSON catalog: names, params, outputs, warm-up) |

`indicator_series` returns one value per input close; the leading warm-up values are `NaN`
(`list_indicators` states the count per indicator). Inputs must be finite; a call throws an `Error` (message text) on bad input or params. If finite input overflows `f64` (e.g. SMA of values near `1e308`) the call throws rather than return infinity. Params are a JSON object
and unknown fields are rejected, e.g. `{"period":14}`.

| name | params (default) | warm-up `NaN`s |
|---|---|---|
| `sma`, `ema` | `period` (required) | `period - 1` |
| `rsi` (Wilder) | `period` (required) | `period` |
| `macd` | `fast` (12), `slow` (26), `signal` (9), `output` = `macd`/`signal`/`histogram` | `slow + signal - 2` |
| `bollinger` | `period` (required), `k` (2.0), `output` = `middle`/`upper`/`lower` | `period - 1` |

### OHLC indicators

`ohlc_indicator_series` is the additive companion for indicators that need high/low/close. It
follows the same rules as `indicator_series` (params JSON validated first with unknown fields
rejected, finite input only, `NaN` warm-up kept, a thrown `Error` instead of infinity, empty input
gives empty output) plus: the three arrays must have equal length, otherwise it throws
(`series lengths differ: high=..`). A close-only name such as `sma` is an unknown indicator here, and
`atr` is unknown to `indicator_series`; the catalog marks it with `"input":"ohlc"` and
`"export":"ohlc_indicator_series"`.

| name | params | warm-up `NaN`s |
|---|---|---|
| `atr` (Wilder) | `period` (required, integer in `1..=1000000`) | `period - 1` |

ATR uses the first bar's `high - low` as its first true range, then `max(high - low,
|high - prev_close|, |low - prev_close|)`; the first value is the mean of the first `period` true
ranges, then Wilder smoothing. This equals the Python `Atr(include_first_bar=True)`; the Python
default (`include_first_bar=False`) skips the first bar and warms up one bar longer.

The values come straight from `honba-indicators`; nothing is reimplemented here.

## Tests and checks

- Unit: `cargo test -p honba-api-wasm` (`src/tests/`).
- Conformance: `schema/conformance/indicator_series.json` is run by
  `tests/indicator_conformance.rs` (native Rust), by
  `python/tests/integration/test_indicator_series_conformance.py` (Python indicators) and by
  `make test-wasm` (the real wasm build under node; needs `wasm-pack`, `node`).
  Tolerance: `null` is `NaN`; integer-valued expectations are exact; others `1e-12` relative.
- OHLC conformance: `schema/conformance/ohlc_series.json` (ATR vectors generated once from the Python
  `Atr`) is run by `tests/ohlc_conformance.rs`, `python/tests/integration/test_ohlc_series_conformance.py`
  and the same node harness (`tests/js/indicator_conformance.mjs`).
- Build: `make check-wasm` (`cargo check -p honba-api-wasm --target wasm32-unknown-unknown`).
