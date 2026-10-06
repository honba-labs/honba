# honba-api-wasm

WASM compute surface for the Honba frontend. Pure compute only: no tokio, no filesystem, no
network; depends on the pure L0-L4 crates (`scripts/dependency_graph.py` enforces this).

Logic lives in plain Rust modules that are tested natively; `src/lib.rs` is a thin `wasm-bindgen`
layer over them.

## Indicators

| Export | Signature |
|---|---|
| `indicator_series(name, params_json, closes)` | `(string, string, Float64Array) -> Float64Array`; throws on error |
| `list_indicators()` | `() -> string` (JSON catalog: names, params, outputs, warm-up) |

`indicator_series` returns one value per input close; the leading warm-up values are `NaN`
(`list_indicators` states the count per indicator). Inputs must be finite. Params are a JSON object
and unknown fields are rejected, e.g. `{"period":14}`.

| name | params (default) | warm-up `NaN`s |
|---|---|---|
| `sma`, `ema` | `period` (required) | `period - 1` |
| `rsi` (Wilder) | `period` (required) | `period` |
| `macd` | `fast` (12), `slow` (26), `signal` (9), `output` = `macd`/`signal`/`histogram` | `slow + signal - 2` |
| `bollinger` | `period` (required), `k` (2.0), `output` = `middle`/`upper`/`lower` | `period - 1` |

The values come straight from `honba-indicators`; nothing is reimplemented here. ATR needs OHLC
bars and is not exposed yet.

## Tests and checks

- Unit: `cargo test -p honba-api-wasm` (`src/tests/`).
- Conformance: `schema/conformance/indicator_series.json` is run by
  `tests/indicator_conformance.rs` (native Rust), by
  `python/tests/integration/test_indicator_series_conformance.py` (Python indicators) and by
  `make test-wasm` (the real wasm build under node; needs `wasm-pack`, `node`).
  Tolerance: `null` is `NaN`; integer-valued expectations are exact; others `1e-12` relative.
- Build: `make check-wasm` (`cargo check -p honba-api-wasm --target wasm32-unknown-unknown`).
