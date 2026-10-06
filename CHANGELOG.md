# Changelog

## [Unreleased]

### Python adapters: capability-aware registry typing

- `AdapterRegistry.create_market_data(name, **config) -> MarketDataClient` and `create_execution(...) -> ExecutionClient`
  (plus `resolve_market_data_adapter` / `resolve_execution_adapter`) return adapters typed with their role methods
  (`instruments`, `search_instruments`, `quote`, `place_order`...), checked at runtime; an adapter without the role raises
  `AdapterError`. New `MarketDataClient` / `ExecutionClient` protocols. `create` / `resolve_adapter` are unchanged.
  Removed the unneeded `# type: ignore[attr-defined]` workarounds in `adapters/contract.py`.

### WASM: OHLC input validation

- `ohlc_indicator_series` now rejects bars where `high < low` (`IndicatorError::InvertedRange(index)`); added edge-case
  tests (flat series, minimal lengths, non-finite input) for every exposed indicator.

### Python SDK: capabilities and schema

- `Client.capabilities()` (`GET /capabilities`) returns a typed `CapabilityManifest` (crates, market packs, endpoints,
  the 501 `not_implemented` subset, toolsets, adapters, features); `Client.schema()` (`GET /schema`) returns the served
  schema info record as a plain dict. Both work on `HttpTransport` and `InprocTransport` (parity-tested); no Rust change.

### REST: unknown routes return an error envelope

- An unknown route is now a 404 `not_found` envelope (was an empty body, which the Python client reported as
  `InvalidResponseError`); a known path with an unsupported method is a 405 `unsupported` envelope (was empty).

### REST: screener scan (E11-S3 part 4)

- `GET /screener/scan` is built (no longer 501). Query: `universe` (JSON array of `SYMBOL.EXCHANGE`, required),
  `filters` (JSON filter group), `tf` (default `1d`), `as_of` (inclusive). Response `ScreenerResponse
  {rows: [{instrument_id, metrics}]}`, matches in instrument-id order. Evaluated in Rust over the bar dataset.
- New pure module `honba_indicators::screener` (`evaluate_predicate`, `evaluate_group`, `latest_metrics`, `validate_group`):
  all 13 operators, metrics `open/high/low/close/volume`, `price_52_week_high/low`, `SMA<N>`, `RSI`. Pinned to the Python
  evaluator by `schema/conformance/screener_scan.json` (65 predicate, 7 group, 5 unsupported, 2 divergence vectors), run by
  Rust and Python. `honba-indicators` now depends on `honba-entities`; `honba-api` now depends on `honba-indicators`
  (allowlist updated in `scripts/dependency_graph.py`).
- Metrics a bar dataset cannot compute (fundamentals, any `period`) are a 422 `unsupported_metric`, not an empty result.
  Differences from Python (documented in ADR 0013): that error, `invalid_operand` for ordering against a string (Python
  raises `TypeError`), and a case-insensitive 252-bar check on 52-week keys.
- Limits (422 `too_many_rows`): 1,000 instruments, 500 rows, 2,000,000 bars. Unknown instrument is a 404.
- New wire types `ScreenerQuery`, `ScreenerResponse`, `ScreenerResultRow`; the `screen` MCP tool now takes the scan query.
  OpenAPI, `.pyi`, MCP and JSON Schema regenerated. The frontend TypeScript (`make schema`) is not regenerated here.
- Python: `Client.screener_scan(universe, filters=None, *, tf=None, as_of=None)` returning `ScreenerResultRow`, on both
  transports.

### REST: compile and list strategies (E11-S3 part 3)

- `POST /strategies` and `GET /strategies` are built (no longer 501). "Compile" is the same step as
  `POST /strategies/verify`: a manifest in, its `StrategyIr` out. `POST` takes `{"manifest": ...}`, stores the result in a
  session-scoped in-memory catalog and returns `CompiledStrategy {id, ir}`; `GET` lists the catalog ordered by `id`.
- `id` is `sha256:` plus the digest of the canonical manifest JSON: deterministic, and the same manifest is stored once.
- Breaking wire change: `StrategiesRequest` is now `{manifest}` (was `{name?, code?}`) and `StrategiesResponse.strategies`
  is `[CompiledStrategy]` (was untyped values). Source code is refused: a body with `code` or `source` is a 422 with
  `context.reason = source_unsupported`. A manifest that does not verify gives verify's 422 and reason code.
- The catalog holds at most 1,000 strategies (`MAX_COMPILED_STRATEGIES`); a new strategy past that is a 422 with
  `reason = catalog_full` and `limit`, never an eviction. Re-submitting a held manifest still succeeds.
- New `honba_api::strategies` (`compile_strategy`, `list_strategies`, `parse_compile_request`, `StrategyCatalog`), held in
  `AppState.strategies`; new `honba-api -> sha2` dependency. Two new MCP tools, `compile_strategy` and `list_strategies`.
  OpenAPI, `.pyi`, MCP and JSON Schema regenerated (`CompiledStrategy` added).
- Python: `Client.strategies()` and `Client.compile_strategy(manifest)` returning the new `CompiledStrategy` model, on both
  transports. `/capabilities` `not_implemented` no longer lists the two routes. The catalog is lost on restart.

### NSE bhavcopy timestamps

- Bhavcopy bar timestamps are now built as 09:15 IST explicitly (`honba.markets.india.calendar.IST`) instead of via the
  machine-local timezone; identical on IST machines, previously off by the local UTC offset elsewhere.

### WASM indicator surface

- New export `ohlc_indicator_series(name, params_json, high, low, close)` (additive; `indicator_series` is unchanged) with
  `atr` (Wilder, `period`). Equal-length arrays are required (`IndicatorError::LengthMismatch`); other conventions as
  `indicator_series`. `list_indicators()` gains an `atr` entry (`"input":"ohlc"`, `"export":"ohlc_indicator_series"`); the
  catalog name list is now `sma,ema,rsi,macd,bollinger,atr` (deliberate extension of the existing catalog assertions).
- New shared fixture `schema/conformance/ohlc_series.json` (10 ATR vectors, generated from Python `Atr(include_first_bar=True)`),
  run by Rust, Python and the wasm/node harness. Python's default `include_first_bar=False` differs from Rust (see README).
- `honba_indicators::Atr::update_hlc(high, low, close)` feeds raw prices without a `Bar` (`update` now delegates to it).

- `indicator_series` refuses finite input whose result overflows `f64` (infinite output, or `NaN` after warm-up) with a
  thrown error (`IndicatorError::NonFiniteOutput`) instead of returning `Infinity`/`NaN`; leading warm-up `NaN`s are unchanged.
- Shared `indicator_series` conformance fixture gains 14 edge vectors (empty input, single bar, bollinger period 1, k=0,
  flat-price RSI); Rust, Python and the wasm/node harness agree on all 28.
- `honba-api-wasm` drops unused dependencies (`honba-messages/entities/market/engine/strategy/sim`, `serde-wasm-bindgen`,
  `wasm-bindgen-test`, the deprecated `serde-serialize` feature) and the leftover `hello_wasm` export; its
  `dependency_graph.py` allowlist is now `honba-indicators` only.
- Docs: `indicator_series` errors are documented as a thrown JS `Error` (`JsError`), not a string, in `lib.rs` and the README.

### REST read API hardening

- `GET /bars/{id}` is capped at `MAX_BAR_ROWS` (100,000) bars: a larger selection is a 422
  `validation_invalid_request` with `context.reason = too_many_rows` and `context.limit`; narrow `from`/`to`.
  No pagination (ADR 0013).
- CORS is no longer permissive: no CORS headers by default; `honba serve --cors-origin <origin>` (repeatable, via
  `ApiConfig`) allow-lists explicit origins. `honba serve` warns on stderr when `--addr` is not loopback (no auth/TLS).
- `GET /capabilities` derives `endpoints` from the endpoint registry (all 22 rows as `METHOD /path`, e.g. `GET /bars/{id}`)
  and adds `not_implemented` (additive, defaults to empty) listing the routes that answer 501. OpenAPI, `.pyi`, MCP
  and domain schemas regenerated.

### CLI exchange selection

- `honba data load <file> <SYMBOL>` no longer hardcodes NSE: `SYMBOL` is bare (exchange from `--exchange NSE|BSE`,
  default NSE) or TradingView-style `NSE:INFY` / `BSE:INFY`; a conflicting qualifier and flag, or an unknown exchange,
  is an error. The summary prints the qualified instrument. Parsing lives in
  `honba_market::india::exchange` (`IndiaExchange`, `resolve_instrument`).

### Python SDK client (E11-S3, part 3)

- New `honba.client`: `Client` with typed methods mirroring the served routes (`health`, `instruments`, `instrument`,
  `bars`, `quotes`, `depth`, `verify_strategy`) returning the existing wire models (`Bar`, `QuoteTick` with `UnixNanos`
  timestamps) plus `InstrumentInfo`, `Depth`, `Health` records that ignore unknown fields (ADR 0012). Arguments the
  server would reject (timeframe, time format, empty range, depth range, instrument id) raise
  `RequestValidationError(field, reason)` before anything is sent.
- Failures raise a typed `ApiError` hierarchy built from the `ErrorDetail` envelope (`code`, `category`, `retryable`,
  `context`, `status`): `ValidationApiError` (422), `NotFoundApiError` (404), `MarketDataUnavailableApiError`,
  `NotImplementedApiError` (501, a subclass of `UnsupportedApiError`), `TransportApiError`, and so on. An unknown code
  stays a base `ApiError`; a body that is not an envelope is `InvalidResponseError`.
- Two interchangeable transports behind a `Transport` protocol. `HttpTransport(base_url, timeout=...)` uses httpx (already
  a dependency); retries are off by default and, when a bounded `RetryPolicy` is given, only a `retryable` envelope or a
  connection failure is retried. `InprocTransport(data_dir)` runs the same Rust router without a socket.
- `honba._honba.api_request(data_dir, method, path, query_json, body_json) -> (status, body_json)` and
  `honba_api_rest::dispatch` back the in-process transport: `honba-py` builds the router from `AppState::from_parquet_dir`
  (cached per directory) and drives it with tower's `oneshot`; no handler is reimplemented in Python. New layering edge
  `honba-py -> honba-api-rest` (both L7; `honba-cli` already has it).
- Parity tests run every scenario, and the 404/422/501 envelopes, through both transports against a real `honba serve`
  and require identical results. Not in the client yet: the routes that answer 501 (`/backtests`, `/sweeps`, `/orders`, `/positions/close`, `/screener/scan`, `/journals`) plus `/capabilities` and `/schema`.
- `InprocTransport` wraps native `OSError`/`ValueError` (e.g. data directory removed after construction) into a
  non-retryable `TransportApiError`, so `except ApiError` behaves the same on both transports.
- Request builders reject times past the server's `i64` nanosecond range (after 2262-04-11T23:47:16.854775807Z) client-side
  with `RequestValidationError(field, 'invalid_time')`, like the server, before any round trip.
- Datetime subclasses exposing `.nanosecond` (e.g. `pandas.Timestamp`) keep their sub-microsecond part in request bounds.

### WASM indicator surface (E11-S5, part 1)

- `honba-api-wasm` now exports `indicator_series(name, params_json, closes: Float64Array) -> Float64Array`
  (full series, input length, `NaN` warm-up) and `list_indicators() -> JSON` for `sma`, `ema`, `rsi`, `macd` and
  `bollinger`, computed by the existing `honba-indicators` types. The logic is a pure native module
  (`honba_api_wasm::indicators`); the wasm-bindgen layer only forwards to it. Bad params, unknown names and
  non-finite input are errors (no panics).
- Removed the demo `sma(values, period)` export (it returned only the last-window mean and had no importers).
  Breaking for anything that called it; use `indicator_series("sma", ...)`.
- Golden vectors `schema/conformance/indicator_series.json` run natively in Rust, in Python against the Python
  indicators, and in the real wasm build under node (`make test-wasm`). `make check-wasm` and CI check the
  `wasm32-unknown-unknown` build.

### REST read API, part 2 (E11-S3, ADR 0013)

- `GET /quotes?symbols=A,B[&venue=NSE][&as_of=<time>]` and `GET /depth/{id}[?depth=N]` are served through two new
  `honba-ports` read ports, `QuoteReader` and `DepthReader`. `QuotesQuery` gains an optional `as_of` (OpenAPI, domain
  schema and `.pyi` regenerated).
- The Parquet-backed dataset has bars only, so a quote is derived: bid and ask are the close of the latest bar at or
  before `as_of` (inclusive), sizes are `0`. Depth is `404 market_data_unavailable` (no order book is invented).
- `honba serve --data-dir DIR [--addr 127.0.0.1:8080]` runs the API over `SYMBOL.EXCHANGE.parquet` files, prints
  `listening on http://<addr>` and stops gracefully on ctrl-c. `honba_api_rest::serve` is the embeddable form.
- Placeholder routes no longer fake success. Strategy listing/compile, backtests, sweeps, orders, `positions/close`,
  screener and journals answer `501` with the new error code `not_implemented` (category `unsupported`, not
  retryable). Breaking for anything that read their old empty or made-up bodies. `honba-frontend` generated
  `ErrorCode` needs the new member (`make check-schema-ts`).

### Backtest metrics

- `BacktestResult.equity_curve` now has one `(ts, equity)` point per bar session, marking every instrument at its last
  known close (was two points, last bar's instrument only). It ends on `final_equity`.
- `max_drawdown_pct` is the peak-to-trough fall as a positive percent of the peak; `0.0` (not NaN) when the curve has fewer
  than two points or never falls.
- `n_trades` is now the number of round trips (position returned to flat; an open trade is not counted); `n_fills` is
  unchanged. Before, `n_trades == n_fills`.

### Simulator and cost nits

- India fill costs (`nse_equity_*_fill_cost`) now round each leg to paise once from unrounded legs (before: 4 dp
  first, then paise, which could add a paisa; e.g. sell 215 @ 1611.93 STT 346.56495 gave 346.57). The itemised
  `*_breakdown` still shows 4 dp. Rust cost code already rounds once (`honba-sim` `BarFillCosts`); nothing to change there.
- `NextOpenExecution`: a funding cut floors to the instrument's lot size (`lot_sizes=` / `set_lot_size`; the session
  passes the provider's `Instrument.lot_size`) and bisects instead of stepping down one unit at a time; positions or
  sells within 1e-9 of each other count as equal, so `0.1 + 0.1 + 0.1` bought then `0.3` sold leaves no phantom position
  and no `no_position` rejection.

### Post-hoc CostModel reaches the ledger

Bug fix: a `CostModel` passed as `costs=` to `Honba.backtest` was applied to the result's `fills` only, so
cash, equity and metrics came from a zero-cost ledger. It is now adapted (`fill_costs_from_model`) into the
simulator's per-fill cost function, so fills, `ctx.cash()`, `final_cash`, `final_equity` and
`total_return_pct` agree. Only the model's returned `costs` is used (it cannot change price or quantity) and
it sees a placeholder instrument. `BacktestSession` no longer takes `cost_model` (it was only set by `Honba.backtest`).

### Date-aware India settlement (ADR 0005 decision 4)

Behavior change: the default equity settlement cycle depends on the trade date. NSE/BSE equities settle
T+2 until 2023-01-26 and T+1 from 2023-01-27, so backtests over data from 2023-01-27 now settle T+1
(previously T+2 for every date). Explicit `settlement_days` still wins everywhere.

- Rust: `SettlementRules::settlement_days_as_of`, `SettlementSchedule`, `IndiaMarketProfile::equity_settlement_days_as_of`;
  `IndiaMarketProfile::equity_settlement_days()` and `settlement_rules().settlement_days()` now report T+1 (today).
- Python: `honba._honba.nse_equity_settlement_days(as_of=None)`; `settlement_days_for(exchange, as_of=...)`;
  `make_simulator(..., as_of=, timeframe=)`; `Honba.backtest` resolves the default from the first session's date.
- Intraday timeframes (`5m`, `1h`, ...) now raise `ValueError` unless `settlement_days` is passed, because the simulator
  counts bars, not trading days.

### Catalog loader and next-open simulator fixes

- Breaking for provenance: `CatalogStrategy.source_sha256` now hashes the whole strategy directory
  (sorted relative paths, length-prefixed names and contents; hidden files and `__pycache__` ignored)
  instead of `strategy.py` + `config.toml` concatenated. All digest values change; re-record any
  stored ones.
- Loader: strategy modules are registered in `sys.modules` during import (dataclasses with
  `from __future__ import annotations` load); registry paths must stay inside the catalog; hidden and
  vendor dirs are not scanned; malformed registries raise `CatalogError`; `sys.path` is restored and
  sibling modules imported from the strategy dir are dropped after load.
- `NextOpenExecution`: `SessionOpen` keys need not be timestamps; unfundable buys wait only while sale
  proceeds are unsettled; bars with a non-finite or non-positive open never fill; plain `Bar` mode raises
  `ValueError` on non-monotonic or duplicate bars.

### Rust order-rejection queue (ADR 0008, decision 13)

Non-breaking; closes the ADR 0008 known gap. No wire change.

- `honba_engine::OrderRejection` and `ExecutionEngine::drain_rejections()`. The trait method has a default
  (returns nothing), so existing engines compile unchanged; no trait signature changed.
- `StrategyRunner` drains rejections after each event's fills, releases the unfilled remainder in the ledger
  context (`LedgerContext::release_remainder`) and records them (`order_rejections()`); new
  `StrategyRunner::cancel(order_id)`. Reasons match Python: `insufficient_funds`, `no_position`, `cancelled`.
- `honba_sim::ScriptedExecution` / `Behavior`: reference engine that rejects, partly fills or holds orders.
- Shared vectors `schema/conformance/order_rejections.json`, run by Rust and Python; shared `ExecutionEngine`
  contract test in `honba-sim`. Python needed no change (`OrderRejection`, `drain_rejections` and
  `RunResult.order_rejections` already existed).

### Currency-aware minor units (ADR 0011)

Non-breaking. No wire change (`SCHEMA_VERSION` stays 3; amounts are unchanged for
INR/USD/EUR/GBP, which all have exponent 2; only the `Money` JSON-Schema description text
changed).

- Rust: `Currency::minor_exponent()`, `Currency::minor_unit()` (`MinorUnit { singular, plural }`,
  re-exported from `honba_entities`) and `Money::format_minor()` (`1,250 paise`, `1 cent`,
  `300 pence`). `Money` conversions, `Money` display, position average-price rounding and
  round-trip fees now scale by `10^minor_exponent` of the currency instead of a fixed 100.
- Python: `honba._honba.currency_minor_units()`; `Currency.minor_exponent`,
  `Currency.minor_per_major`, `Currency.minor_unit` and `Money.format_minor()`.
  `honba.domain.money.MINOR_PER_MAJOR` is **deprecated** (still importable, emits
  `DeprecationWarning`, returns 100); use `Currency.minor_per_major`. The private helpers
  `_scaled`, `_snapped`, `_round_major_to_minor` and `position._round_to_minor` now take the
  exponent/currency.
- Shared conformance vector `schema/conformance/currency_minor_units.json`, read by Rust and
  Python tests.
- Internal renames: generic code, tests and docs say "minor unit" instead of "paise"
  (`honba-entities`, `honba-sim`, `honba-sweep`, `honba-strategy`, `wire.py`, `adapters/models.py`,
  `domain/money.py`). India cost code keeps paise. No public identifier was renamed.

### Wire contract v3: integer money and timestamp objects (E0-S6, E11-S2, ADR 0011)

`SCHEMA_VERSION` is now **3** (single owner `honba_messages::SCHEMA_VERSION`; mirrored by
`honba.entities.wire.SCHEMA_VERSION`, the generated `.pyi`, `domain_schema.json`, the golden
vectors and the strategy conformance fixture).

What changed on the wire:

- **Money is integer minor units.** `Money` serializes as `{"amount": <i64>, "currency": "INR"}`
  where `amount` is paise (cents for USD/EUR/GBP), never a JSON float. `Trade.costs` and
  `Position.realized_pnl` are `Money` objects (they were bare floats).
- **Timestamps are objects.** Every `UnixNanos` (`ts_event`, `ts_init`, `now`) serializes as
  `{"iso": "2023-11-14T22:14:20.000000000Z", "unix_nanos": "1700000060000000000"}`.
  `unix_nanos` is a decimal string (a u64 exceeds JS `Number.MAX_SAFE_INTEGER`) and is the
  value readers use; `iso` is informational.

Legacy-float read path (kept for old journals and fixtures, Rust and Python behave the same):

- A float `Money.amount` is read as **major** units and rounded once to minor units, half away
  from zero (`45.676` -> `4568`). Integers are taken as minor units.
- A bare number for `Trade.costs` is read as INR major units (`45.67` -> `{"amount": 4567,
  "currency": "INR"}`); a bare number for `Position.realized_pnl` takes the position's
  currency. Writers only ever emit the integer object form.
- There is **no** legacy read path for integer timestamps or for the envelope: a `Message`
  with `schema_version` 2 (or anything other than 3) is rejected with "unsupported
  schema_version". Re-export v2 journals with a v3 writer, or rewrite `schema_version`,
  timestamps and money fields, before replaying them.

Migration for code:

- Rust: `Money::new` takes `i64` minor units; use `Money::from_major_f64` at float boundaries,
  `Money::payout_from_major_f64` (floor) / `Money::stake_from_major_f64` (ceil) where money
  leaves the model, `Instrument::stake_quantity` (round up to lots) and
  `Instrument::settle_notional` (rejects off-tick prices with `MoneyError::OffTick`).
  `LedgerContext::apply_fill` now returns `Result` and refuses a fill it cannot book.
- Python: `StrategyContext.cash()` returns `honba.entities.Money` (was `float`); use
  `.to_major()` to display or size. `Trade.costs`, `Position.realized_pnl` and `Account.cash`
  are `Money`; a plain number for `Trade(costs=...)` is treated as legacy INR major units.
  `Money.from_major` now rounds half away from zero (it used Python's half-to-even `round`).
  `honba.entities.wire.UnixNanos.from_ns(n)` / `.to_ns()` convert timestamps.
- Consumers in other repos (honba-strategies, honba-examples, honba-frontend generated types)
  that read `cash()` as a float or timestamps/costs as numbers need the same update.

### Adapter contract (E1-S1, ADR 0010)

- `honba.adapters` package: `Adapter` (facade, capabilities, lifecycle), `MarketDataAdapter`
  and `ExecutionAdapter` (async Protocols, runtime-checkable), canonical value types
  (`OrderReport`, `Funds`, `Holding`, `MarginReport`, `MarketDepth`, `SessionInfo`,
  `Product`, `RunMode`, `StreamMode`, `Subscription`, `StreamEvent`/`StreamCallback`).
- `AdapterCapabilities` descriptor: venues, products, order types, time-in-force, price types,
  stream modes, capability features (`place_order` … `instrument_master`). `require_*`
  helpers raise `CapabilityError` when a declared capability is missing.
- `AdapterRegistry` (mirrors `honba_market::MarketRegistry`): explicit register/unregister,
  `create(name, **config)`, sorted `available()`, one-shot lazy entry-point discovery under
  group `"honba.adapters"`.
- Shared contract suite `verify_adapter_contract`: connects, refuses before connect, probes
  every unsupported method for `CapabilityError`, every supported method for its canonical
  type, places a probe order, retrieves it by id, idempotency under repeated
  `client_order_id`, cancels a resting order, and verifies fills reach the trade and
  position books. Pytest-free, deterministic, no network.
- `FakeAdapter`: in-memory reference implementation (reduced capabilities on purpose) that
  certifies the suite; not a paper adapter (E3-S7).
- Boundary rule: `find_boundary_violations(root, *, forbidden, adapter_roots)` asserts no
  broker SDK or adapter package is imported outside an adapter package.
- Cross-repo: `honba-adapters/shared` ships the contract wrapper, fixture reader and the
  boundary test; broker packages register via the entry point when they have code.

### Strategy contract (E0-S3, ADR 008)

- Python `Strategy` is an ABC with `on_start`, `on_bar`, `on_quote`, `on_trade`, `on_fill` and
  `on_stop` (the new hooks default to no-ops) and acts through `self.ctx`, a `StrategyContext`
  (clock, positions, cash, busy, instrument lookup, submit). New: `LedgerContext`,
  `StrategyRunner`, `honba.strategies.reference`, `honba.strategies.testing.BarCloseFills`,
  domain `QuoteTick` / `TradeTick` / `Instrument`, `wire.loads_many`.
- Compatibility (until 0.3): existing subclasses work unchanged, with or without
  `super().__init__()`; overriding `drain_intents`, `handle_fill` or `handle_rejected` emits a
  `DeprecationWarning` and is still honoured.
- Breaking (Rust): `Strategy` hooks take `ctx: &mut dyn StrategyContext`, market-data hooks lose
  `ts_init`, and `drain_intents` is gone (use `ctx.submit`). New: `LedgerContext`,
  `ContractProbe`, `StrategyRunner::{with_context, context, submitted}`.
- `honba._honba.run_strategy` runs a Rust reference strategy over JSON wire messages.
- `schema/conformance/strategy_contract.json` is run by the Rust, Python and cross-language suites.

### Performance

- Streaming indicators are now O(1) per update instead of O(period): Bollinger bands, z-score,
  `RollingStd` (volatility helpers), WMA, correlation, beta, covariance, LSMA and linear
  regression. They share the helpers in `honba.strategies.indicators._rolling`
  (`RollingSum`, `RollingMoments`, `RollingPairMoments`, `RollingLinReg`): shifted Welford with
  removal, an exactly carried position moment for regressions, and exact `fsum` rebuilds (every
  `max(1000, 4 * period)` updates, and on guard conditions such as an outlier leaving the window
  or drift of the newest value). Guard rebuilds are rate limited so adversarial input cannot
  force O(period) work on every update. Public APIs, warm-up lengths, `reset()` and degenerate
  results (zero variance gives 0.0) are unchanged.
- The steady-state update path of these helpers is inlined (plain-tuple `update_raw`, lazily
  topped-up rebuild credit, closed-form period-2 paths); at periods >= 20 every converted
  indicator is faster than the old O(period) code, and several times faster at period 100-300.

### Changed (behavior)

- A non-finite value (NaN or infinity), or a value with `|x| > 1e150`, in the window now makes
  the affected indicator report NaN until it has left the window; the running state is then
  rebuilt exactly, so a bad bar never poisons later results.
- The old correlation returned 1.0, and the old beta 0.0 (when only the benchmark window held the
  bad value), for some windows containing NaN; both now return NaN.
- Indicators that previously raised `OverflowError` on huge values (`|x| > 1e150` squares or
  sums overflowing) now return NaN instead.
- A window of identical values is exactly degenerate (zero variance, zero covariance, exactly flat
  regression line). The old two-pass code returned float noise when the mean of the constant
  window was not exactly representable (for example a constant 0.1).
- Linear regression residual std is computed from running sums, so a near-perfect fit is accurate
  to about 3e-8 window standard deviations (was exact two-pass); lsma, slope and value keep a
  relative error around 1e-12.

### Tests

- New `slow` pytest marker (registered in `python/pyproject.toml`) for long accuracy and
  amortised-cost sweeps; `pytest -m "not slow"` is the fast set.
