# ADR 0013: REST Reads Go Through Domain-Owned Ports

Date: 2026-10-06. Status: accepted. Roadmap story: E11-S3 (parts 1 and 2).

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
- **Quotes and depth are two more read ports in `honba-ports`**: `QuoteReader`
  (`read_quote(id, as_of) -> Option<QuoteTick>`, inclusive `as_of`, `None` = latest) and
  `DepthReader` (`read_depth(id, levels) -> DepthSnapshot`). They are separate from `BarReader` because a
  bar store can derive a last-price quote but cannot have a book, while a tick store could answer both
  truthfully. `DatasetReader` implements both; `AppState` holds `quotes` and `depth` beside `instruments`
  and `bars`.
- **Quote derivation is documented, not hidden**: bid = ask = close of the latest bar stamped at or before
  `as_of`, bid and ask size `0`, `ts_event == ts_init ==` the bar timestamp. Zero sizes say "last price,
  no book". Depth from a bar dataset is `PortError::Unsupported`, which is the existing 404
  `market_data_unavailable`; no levels are fabricated.
- `QuotesQuery::resolve` (symbols, venue, `as_of`) and `DepthQuery::levels` (default 5, `1..=50`) are pure
  and live in `honba-api` with the other resolvers.
- **`honba serve`** (`--data-dir`, `--addr`, default `127.0.0.1:8080`) loads the directory once, then calls
  `honba_api_rest::serve(listener, state, shutdown)`, where the CLI's shutdown future is ctrl-c. The
  listener and stop signal belong to the caller so tests bind `127.0.0.1:0`. `honba-cli` is allowed to
  depend on `honba-api-rest` (both L7; added to `ALLOWED_PROD`).
- **Placeholders are honest**: a route in the contract whose behaviour is not built answers 501 with
  `ErrorCode::NotImplemented` (`not_implemented`, category `unsupported`) inside the envelope. A request
  body that does not parse is still 422 first.
- Instrument id in paths is `SYMBOL.EXCHANGE` (the `InstrumentId` display form),
  split on the last dot.

## Behaviour

| Case | Status | Envelope code |
|---|---|---|
| ok | 200 | - |
| unknown instrument | 404 | `instrument_not_found` |
| `/quotes` without `symbols`, empty entries, bad `as_of`, unknown key | 422 | `validation_invalid_request` |
| `/quotes` symbol matching no instrument (given `venue`) | 404 | `instrument_not_found` |
| `/quotes` instrument with no bar at or before `as_of` | 404 | `market_data_unavailable` |
| `/depth/{id}` over a bar dataset; `depth` outside 1..=50 | 404 / 422 | `market_data_unavailable` / `validation_invalid_request` |
| route not built yet (see below) | 501 | `not_implemented` |
| timeframe not held for the instrument (`PortError::Unsupported`) | 404 | `market_data_unavailable` |
| bad `tf`/`from`/`to`, empty range, unknown query key, malformed id | 422 | `validation_invalid_request` |
| `/bars/{id}` selection over 100,000 bars (`reason: too_many_rows`) | 422 | `validation_invalid_request` |
| port unavailable / timeout / transport | 503 / 504 / 502 | `market_data_unavailable` / `timeout` / `transport_error` |

Prices are `f64` observations, so bars carry no money; timestamps use the
existing `{iso, unix_nanos}` form. ADR 0011 is not engaged by these endpoints.

## Known limits

- Parquet files carry no reference data: instruments are derived as INR
  equities, lot 1, tick 0.05, and files are read as 1-minute last-price bars.
  A symbol master and per-file timeframes are follow-ups.
- No pagination on bars. A hard cap of `MAX_BAR_ROWS` (100,000) applies: a selection with more bars is a 422
  `validation_invalid_request` with `context.reason = too_many_rows` and `context.limit`, telling the caller to narrow
  `from`/`to`. The check runs after the read port answers, so it bounds the response, not the port's memory.
- A quote request is all-or-nothing: if one matched instrument has no quote at that time the whole request is
  404, rather than a silently shorter list.
- Not built, answering 501: `GET/POST /strategies`, `POST /backtests`, `GET /backtests/{id}[/journal]`,
  `POST /sweeps`, `GET /sweeps/{id}`, `GET/POST /orders`, `DELETE /orders/{id}`, `POST /positions/close`,
  `GET /screener/scan`, `GET /journals/{id}`. `POST /strategies/verify` is real (ADR 0012).
- No auth, TLS or rate limiting on `honba serve`; it is a loopback read API by default, and `honba serve` prints a
  warning on stderr when `--addr` is not loopback.
- **CORS is off by default** (no CORS headers). `ApiConfig::with_cors_origins` / `honba serve --cors-origin <origin>`
  (repeatable) opt in explicit origins; `*` and invalid header values are rejected. `api_router_with` keeps the
  default config; `api_router_with_config` / `serve_with_config` take an `ApiConfig`.
