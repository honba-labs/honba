# ADR 0013: REST Reads Go Through Domain-Owned Ports

Date: 2026-10-06. Status: accepted. Roadmap story: E11-S3 (part 1).

## Context

`honba-api-rest` answered `GET /instruments` and `GET /bars/{id}` with empty
placeholders and had an empty `AppState`. Wiring it to real data must not let
`honba-data` (Parquet, files) leak into handlers, and must not give the REST
crate its own idea of what an instrument or a bar query is.

## Decision

- **Ports live in `honba-ports` (L2).** The existing `InstrumentMaster` answers
  `GET /instruments[/{id}]`. A new read-only `BarReader` (+ `BarRequest`, a
  half-open `[from, to)` range that rejects an empty or inverted range at
  construction) answers `GET /bars/{id}`. Both are `Send + Sync`, held as
  `Arc<dyn ...>`.
- **The adapter is `honba_data::DatasetReader`** (L5 -> L2, an inward edge,
  added to `ALLOWED_PROD["honba-data"]`). It serves an immutable `Dataset`, so
  answers are deterministic: instruments in `InstrumentId` order, bars in
  ascending `ts_event`.
- **Query parsing and wire mapping are pure and live in `honba-api` (L6)**:
  `BarsQuery::resolve`, `InstrumentsQuery::matches`, `parse_instrument_id`,
  `instrument_json`. Every transport (REST now, MCP/WASM later) rejects a bad
  query with the same `validation_invalid_request` detail naming `context.field`.
- **`honba-api-rest` depends on `honba-ports` for the traits and on `honba-data`
  only in `AppState`'s constructors** (`from_reader`, `from_parquet_dir`). That
  is the composition root, which the layering allows (`honba-api-rest` was
  already permitted `honba-data`; `honba-ports` is added). Handlers see ports only.
- Instrument id in paths is `SYMBOL.EXCHANGE` (the `InstrumentId` display form),
  split on the last dot.

## Behaviour

| Case | Status | Envelope code |
|---|---|---|
| ok | 200 | - |
| unknown instrument | 404 | `instrument_not_found` |
| timeframe not held for the instrument (`PortError::Unsupported`) | 404 | `market_data_unavailable` |
| bad `tf`/`from`/`to`, empty range, unknown query key, malformed id | 422 | `validation_invalid_request` |
| port unavailable / timeout / transport | 503 / 504 / 502 | `market_data_unavailable` / `timeout` / `transport_error` |

Prices are `f64` observations, so bars carry no money; timestamps use the
existing `{iso, unix_nanos}` form. ADR 0011 is not engaged by these endpoints.

## Known limits

- Parquet files carry no reference data: instruments are derived as INR
  equities, lot 1, tick 0.05, and files are read as 1-minute last-price bars.
  A symbol master and per-file timeframes are follow-ups.
- No pagination or row cap on bars; the DTO has none yet.
- Quotes, depth and the other routes are still placeholders.
