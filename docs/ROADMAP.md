# Honba roadmap (single forward-looking plan)

Status: v6, 2026-10-06. Supersedes `docs/archive/ROADMAP-borrowed-ideas.md` (v4, 2026-10-01) and merges `docs/archive/plan.md`
(2026-10-03) phases and epics E10 and E11 into one numbering-preserving structure. Story IDs (E0..E11) are unchanged so old references
still resolve. Nothing here has been filed as an issue. This file lives in the `honba` git repo at `docs/ROADMAP.md`; paths are relative
to the `honba/` root unless they name a sibling repo. Evidence is from the `honba/` tree and its git log, read-only; test baselines are
from `honba/.claude/HANDOFF.md` (cargo ~1045, python ~2413 passed, 8 skipped) and were not re-measured for v6.

v6 changes: E10-S7 DONE (commits edb4921, 91bff5b, 2c10630; ADR 0015 implemented); E3-S1a chunk 1 DONE (ADR 0016, commits
b12e5ba..1b2f6ce). Also done since v5 (see 1.1): cancel-time rule, rejection-queue hardening, IST timestamps, typed registry, SDK
capabilities/schema, WASM hardening, ADR 0014/0015, golden vectors validated against the schema, honba-docs chunks. The two superseded
documents are kept in `docs/archive/` for history and are not updated.

Story verdicts come from the 2026-10-06 audit (a scratch file, `ROADMAP_AUDIT.md`, model output, not kept in the repo) after a spot-check
against code. The audit's table
had 74 story rows (its prose said 70, its summary 69) and about 20 verdicts or evidence claims were corrected; see Appendix A. Treat the audit as a lead, this file
as the record.

---

## 1. Status snapshot (as of 2026-10-06)

Counts are over the 74 stories the audit tracked (E0-S1..S8, E1-S1..S6, E2-S1..S11, E3-S1..S7, E4-S1..S8, E5-S1..S9, E6-S5, E9-S1..S8,
E10-S1..S8, E11-S1..S8) after correction. E6 batches 1-2, E7 and E8 are tracked separately below.

| Epic | Done | Partial | Not started | One-line state |
|---|---|---|---|---|
| E0 Contract spine | 8 | 0 | 0 | Complete: wire types, codegen, versions, money, market packs, manifest, `honba verify` |
| E1 Instruments and adapters | 1 | 2 | 3 | Adapter Protocol and contract suite exist; no option metadata, instrument master impl, session or feed |
| E2 Safe engine | 1 | 4 | 6 | Kill switch, audit log and fill feedback loop work; no risk stage, no order FSM, no reconciliation |
| E3 Execution realism | 0 | 2 | 5 | Bar-fill (L1) simulator and Python next-open simulator only |
| E4 Research pipeline | 0 | 3 | 5 | Python `BacktestSession` and Rust `honba-sweep` exist; no journal, gates, look-ahead test, CLI or REST runner |
| E5 Agent and API | 0 | 2 | 7 | 20 MCP tool schemas and OpenAPI are generated; no MCP server, scopes, sandbox, approval queue |
| E6 Indicators | n/a | 0 | 1 | Batches 1-2 (O(1) rolling) done; batches 3-4 and render contract (S5) not started |
| E7 Scale and ops | 0 | 0 | 5 | Pull-based; E7-S4 is superseded by `honba-sweep` (E10-S5) |
| E8 Multi-leg | 0 | 0 | 4 | Optional; not scheduled (open decision D13) |
| E9 Frontend and AlgoDesigner | 0 | 1 | 7 | Greenfield; generated TS types only |
| E10 Async core | 6 | 1 | 1 | Ports, shell, engine output, Arc dataset, sweep, determinism test and Python runtime bridge (ADR 0015) shipped; connectors open |
| E11 Versioned API | 3 | 2 | 3 | Read API, SDK and WASM indicators shipped; streaming, writes, auth open |
| **Total (74)** | **19** | **17** | **38** | |

### 1.1 What is shipped (evidence)

| Area | Evidence |
|---|---|
| Layering and renames, PyO3 isolated in `honba-py` | ADRs 0001-0004, `scripts/dependency_graph.py` (also enforces async isolation and WASM purity), CI job |
| E0-S1 `honba._honba` crate + stubs | `crates/honba-py`, CI wheel/stubtest (handoff: stubtest 0 errors) |
| E0-S2 canonical wire types, golden vectors | ADR 0006 (decision 1 superseded by 0014), `schema/golden/`, commit 78f98f6 (128 vectors validated against the generated schema) |
| E0-S3 strategy contract parity | ADR 0008, `schema/conformance/strategy_contract.json`, commits 3d04774..0c888ac |
| E0-S4 config schema, E0-S5 versioning, codegen | ADR 0012, ADR 0014, `crates/honba-codegen`, `crates/honba-config`, `make check-codegen-ci` (CI blocking) |
| E0-S6 integer money | ADR 0011 (per-currency minor units, 2^63 guard), `entities` money |
| E0-S7 market packs | ADR 0005, `crates/honba-market` (`india` feature, `null` pack, shared contract suite) |
| E0-S8 manifest, IR, `honba verify` | `crates/honba-strategy/src/{manifest,ir}.rs`, `python/src/honba/strategies/{manifest,verify}.py`, `POST /strategies/verify`, `POST/GET /strategies` (commits 849fe80, f8ad58a) |
| E1-S1 adapter contract | ADR 0010, `python/src/honba/adapters/{contract,testing,registry}.py`, commit b0f4f0e (capability-typed registry). A second real adapter passing the suite is still open |
| E2-S5 command channel | `EngineOutput` applied by `Engine`, fills fed back as `OrderFilled` events (commit daf12d5, `crates/honba-engine/src/engine.rs`) |
| E10-S1 ports | `crates/honba-ports` (feed, execution, master, clock, sink, secret, snapshot, bar/quote/depth readers), commit 1ed5eb8 |
| E10-S2 async shell | `crates/honba-async` (`EngineHandle`, `LiveClock`, `HistoricClock`), commit 49ffb32 |
| E10-S3 engine output | commit daf12d5 |
| E10-S4 shared dataset (partial: Arc and columnar read only) | `honba-data` `Dataset`, `ColumnarSlice`, `DatasetFeed`, commit c7ef86b |
| E10-S5 sweep | `crates/honba-sweep` (one engine per trial, `spawn_blocking`, results in trial order), commit fb64146 |
| E10-S7 Python runtime bridge | ADR 0015 implemented: `honba.event_loop`, `honba.async_run`; commits edb4921, 91bff5b, 2c10630 |
| E3-S1a chunk 1 (next-open Rust port, part 1) | ADR 0016; commits b12e5ba..1b2f6ce. Chunks 2 and 3 remain (see section 4) |
| Cancel-time rule | ADR 0008 decision 13 addendum, commit 1c50fc7 |
| Rejection-queue hardening | commits 2620b12, e1f6b76, 081fd12, 53a3ef7 |
| IST timestamps | commit 31510cb |
| SDK capabilities and schema | commit f19a301 |
| WASM hardening | commit 101f81f |
| ADR 0014 (schema/codegen from Rust), ADR 0015 (explicit runtime) | `docs/adr/` |
| honba-docs chunks | documentation chunks in the `honba-docs` repo (not itemised here) |
| E10-S6 determinism under threads | `crates/honba-sweep/tests/sweep.rs` `determinism_under_threads_{one,four,sixteen}_worker(s)` compare journal and ranking across thread counts |
| E11-S1 envelope and registry | `crates/honba-api`, `crates/honba-messages/src/endpoints.rs` (22 routes, `WRITE_PATHS`), `GET /capabilities` (cf1366e) |
| E11-S2 timestamps as strings | ADR 0012 rule 4, commits 4006ff1, 9530251 |
| E11-S3 REST read API | ADR 0013; `instruments`, `quotes` (derived from bars), `bars` (cap 100k rows), `depth` (none), `strategies`, `screener/scan`; `honba serve`; error envelope for unknown routes (e24b806) |
| Python SDK | `honba.client` (`HttpTransport`, `InprocTransport` via `honba._honba.api_request`), parity tests `python/tests/integration/test_client_parity.py` |
| E11-S5 part 1 | `crates/honba-api-wasm`: `indicator_series`, `ohlc_indicator_series` (ATR), conformance vectors (d1c9db3, e9fd98e) |
| E6 batches 1-2 | commits 6372528, 510e0d4, efca4bd, 7c9a325, 6a91432 |
| Backtest foundation (Python) | `python/src/honba/session.py` `BacktestSession`/`BacktestResult`, `backtest/simulated.py` `NextOpenExecution`, date-aware India settlement (9cc171e), single-round costs (d38fe82) |

Not shipped despite appearing in earlier drafts: any `honba-risk` crate (`crates/honba-engine/src/risk/mod.rs` is a 9-byte stub), a
`not_modelled` assumptions list (only an untyped `assumptions: Option<Value>` on `BacktestResponse`), `FillModel`/`LatencyModel` traits,
Deflated Sharpe/PBO/Monte Carlo/walk-forward (`honba-analytics/src/{monte_carlo,walk_forward}.rs` are 1-line orphans), `honba.event_loop`,
`Instrument` option metadata (expiry, strike), an `OrderState` FSM or client order ids, any streaming route.

---

## 2. Principles carried forward

1. **Contract first.** Every story starts with the artifact (Rust trait or message, schema, `.pyi`, ADR), then contract tests and golden
   vectors, then the reference implementation. Rust `honba-codegen` is the single generator of JSON Schema, OpenAPI, TS, `.pyi`, MCP
   schemas (ADR 0014); generated files are never edited; drift is a blocking CI check.
2. **Rust core, Python-first surface.** Rust owns deterministic hot paths (engine, simulator, risk, analytics, sweeps, indicators); Python
   is the user, SDK, adapter and research surface. Anything user-facing added in Rust ships a Python binding and stub in the same story.
   The Python API does not change when logic moves to Rust (next-open port is the live example).
3. **Sync kernel, async shell.** The event loop is synchronous and single-threaded; async and threads live at the edges and across runs
   (`docs/archive/plan.md` section 1, `honba-async`, `honba-sweep`). Same seed and data give a byte-identical journal at any thread count.
4. **Determinism.** Fixed seeds, simulated clock, recorded fixtures, no network or wall clock in tests; shared golden vectors for
   Rust/Python parity; backtest, paper and live share one event flow and one order-state machine (E2-S6 hard requirement).
5. **Workspace rules R1-R3 (root `CLAUDE.md`).** R1 TDD (failing test first, bug fixes start with a regression test, never weaken a test).
   R2 DDD (domain pure; infrastructure behind ports; adapters translate at the boundary; deps point inward, enforced by
   `scripts/dependency_graph.py`). R3 every change ships a named unit test and a named integration test (Rust `src/tests/` and `tests/`;
   Python `tests/unit/` and `tests/integration/`), plus the shared contract test for any port change.
6. **Research and AI first.** Typed, serializable, seeded, journaled outputs; reachable by library, CLI, REST and MCP with the same
   envelope and stable error codes; LLM and broker text is untrusted input, verified before use; AI-generated strategies pass the same
   validation as hand-written ones.
7. **Market-neutral core.** India rules live only in the `india` pack of `honba-market`; no core crate names a pack.
8. **Honesty over breadth.** Unbuilt routes answer 501 `not_implemented`; known gaps are `#[ignore = "known gap: ..."]` tests with reasons.

Licences: OpenAlgo is AGPL-3.0 (clean-room only, behaviour not code); QuantDinger backend Apache-2.0, its frontends have separate
licences (ideas only); Nautilus LGPL-3.0, barter MIT, arbkit Apache-2.0 or MIT (verify before any reuse). A clean-room log for
OpenAlgo-inspired stories is still unwritten (D15).

---

## 3. Re-evaluated borrowed ideas

Decisions: **ADOPT** (build, scheduled), **KEEP** (already adopted and shipped), **DEFER** (premise holds, not now), **DROP** (premise no
longer holds). What changed since v4: the core is async-ported with a sync kernel; REST and WASM exist; the Python SDK has two
transports; schemas and MCP tool metadata are generated from Rust; adapters stay Python (ADR 0010); the simulator and costs are still
thin; no live feed or broker exists in Rust.

### 3.1 OpenAlgo (AGPL, behaviour only)

| Idea | Decision | Reason | Lands |
|---|---|---|---|
| Adapter Protocol, capability manifest, lazy registry, contract suite | KEEP | Built (ADR 0010, typed registry b0f4f0e); second-adapter proof still owed | E1-S1 |
| Canonical typed models, shared golden vectors | KEEP | Core of E0; 128 vectors validated against generated schema | E0-S2 |
| Strict symbol grammar with round-trip tests | ADOPT | Trait exists but `india::fno` is an 8-byte stub and `Instrument` has no expiry/strike; needed before options and the instrument master | E1-S2 |
| Instrument master with dated snapshots | ADOPT | Only the `InstrumentMaster` port exists; backtests need resolve-as-of; build over the Parquet catalog, not a broker table | E1-S3 |
| Table-driven product and price-type mapping | DEFER | Adapters are Python and map their own types; revisit when the first real adapter passes the suite, then fold into the contract suite | E1-S4 |
| Smart order (target position, per-symbol lock) | DEFER | Needs lot splitter and a live gateway; backtests already use intents | E3-S5 |
| Lot and freeze-quantity splitter | ADOPT | `NextOpenExecution` floors to lot only; real splitter needs instrument master rules | E3-S6 |
| Sandbox behind the same interface (no global mode flag) | ADOPT | Principle kept; implementation waits for one order FSM and L1/L2 simulator parity | E3-S7 |
| Webhook pipeline (stages, idempotency, cool-off) | DEFER | Premise (hosted order ingestion) needs auth (E11-S8) and risk stage first; no demand signal | E5-S6 |
| MCP metadata, toolsets, read-only kill switch, scope map | ADOPT (changed) | Tool schemas are now generated from the endpoint registry (`schema/mcp/mcp_tools.json`, `readOnlyHint` from `WRITE_PATHS`); remaining work is scope map plus drift test and a thin Python MCP server | E5-S1, E5-S3 |
| Trust envelope for untrusted broker text | ADOPT | Nothing built; do with MCP server | E5-S2 |
| Approval queue for placements | ADOPT | Gated on risk stage; cancel/modify bypass | E5-S4 |
| Market-data normaliser and silent-feed watchdog | DEFER | No feed exists; ports only | E1-S6 |
| Visual flow builder with code twin (clean-room) | DEFER | Frontend greenfield; needs E4-S3 runner and E5-S5 verifier first | E9-S1..S4 |
| 36-broker breadth, Flask/ZeroMQ, key in body, global analyzer flag | DROP | Unchanged | none |

### 3.2 Nautilus (LGPL, ideas only)

| Idea | Decision | Reason | Lands |
|---|---|---|---|
| Order request/ack state machine, client order ids | ADOPT | Only `OrderStatus` enum and an `OrderRejection` queue exist (ADR 0008 decision 13); no FSM, no ack events | E2-S6 |
| Reconciliation on start/reconnect | DEFER | Needs live adapter, cache (E2-S9), FSM | E2-S7 |
| Component lifecycle FSM | DROP | `EngineHandle` supervisor and `Command::Stop` in `honba-async` cover start/stop/health; no second consumer | was E2-S8 |
| Pluggable seeded fill/latency/margin models | ADOPT | Only config enum `FillModel::BarFill` and flat/bps costs exist; no traits | E3-S2, E3-S4 |
| Data catalog with streaming Parquet batches | ADOPT (smaller) | `Dataset`/`ColumnarSlice` cover shared reads; remaining value is chunked streaming and dataset ids | E4-S7 |
| Cache persistence, crash-only restart | DEFER | No live sessions yet | E7-S3 |
| Message bus and engine replacement | DROP | Unchanged; recorder tap is `AuditLog` plus streaming (E11-S4) | E7-S5 |

### 3.3 barter-rs

| Idea | Decision | Reason | Lands |
|---|---|---|---|
| Audit stream with replicable state | KEEP | `AuditLog` records every dispatch, fill, state change; replay test still missing | E2-S4 |
| `Clock` trait live vs historic | KEEP | `honba-ports::Clock`, `LiveClock`, `HistoricClock`; wall-clock-in-domain check still missing | E2-S3 |
| Trading-state kill switch | KEEP | `TradingState`, Halted refuses orders and is audited; Reducing not enforced, no Python/CLI exposure | E2-S1 |
| Risk stage with typed refusals | ADOPT | Not built; top priority, gates every write path | E2-S2 |
| Command channel and `EngineOutput` | KEEP | Shipped (daf12d5) | E2-S5 |
| Terminal/Unrecoverable errors, reconnecting stream | ADOPT | Typed port error taxonomy exists (1ed5eb8); reconnect/backoff waits for first feed | E1-S6 |
| Indexed instrument state | DEFER | No profile justifies it | E7-S2 |

### 3.4 QuantDinger (Apache backend; frontend ideas only)

| Idea | Decision | Reason | Lands |
|---|---|---|---|
| Strategy manifest compiled by `verify` | KEEP | Shipped; `CompiledStrategy.id` is `sha256` of canonical manifest | E0-S8 |
| One-line param declarations, `hyperparameters()` | ADOPT | Manifest has no range/sweep space yet; sweep takes hand-built `TrialParams` | E4-S2 |
| Target-position intents with reason, client order id | ADOPT | With the FSM | E2-S6, E3-S5 |
| `not_modelled` assumptions list | ADOPT | Field exists untyped on `BacktestResponse`; Python `BacktestResult` has none | E4-S3 |
| Cost-stress rerun, block-bootstrap Monte Carlo, DSR, PBO, holdout | ADOPT | Nothing compiled; build in Rust `honba-analytics` over the journal | E4-S6 |
| Backtest cache keyed by hash | ADOPT | Hash pattern proven in strategy catalog; needs journal and cost-model version | E4-S1 |
| Evaluation never submits orders (intents only) | KEEP | True of `Strategy` contract; enforced end to end once risk stage lands | E2-S2 |
| Computed vs broker PnL reconciliation, drift tracker | DEFER | Needs live | E2-S7 |
| AI decision filter (veto/size-down), report builder | DEFER | Needs verifier, journal and trust envelope first | E5-S7, E5-S9 |
| Per-source circuit breaker, rate limiter | DEFER | No remote sources yet; Python adapters own it when built | E1-S5 |
| AST/allowlist/subprocess verification of AI code | ADOPT | `ai/verification/` is empty; blocks any AI authoring path | E5-S5 |
| Indicator render contract | DEFER | Premise is charting; start with AlgoDesigner/chart work (WASM indicators already serve values) | E6-S5 |
| Ops console, per-run mode | DEFER | Needs risk, streaming, run lifecycle | E9-S6 |
| Celery/Redis/Kafka, billing, multi-tenant SaaS, crypto breadth, Vue | DROP | Unchanged | none |

### 3.5 Jesse

| Idea | Decision | Reason | Lands |
|---|---|---|---|
| Declarative `hyperparameters()` + seeded sweep + fitness | ADOPT | Sweep runner exists with `SharpeFitness` only (Calmar/Sortino/Omega absent) | E4-S2, E4-S5 |
| Look-ahead protection by construction + property test | ADOPT | No such test; matters most for AI-generated strategies | E4-S4 |
| Optuna as dependency | DEFER | Rust seeded sampler fits the sync-kernel design; Optuna only as optional Python extra (D9) | E4-S5 |
| Multi-timeframe aggregation | DEFER | No demand; needs calendar bucketing | E4-S8 |

### 3.6 arbkit

| Idea | Decision | Reason | Lands |
|---|---|---|---|
| Integer money, conservative rounding | KEEP | ADR 0011 shipped | E0-S6 |
| Costs netted into price first (`edge_after_costs`) | ADOPT | Rust `CostSchedule` is not used by `honba-sim`; Python path uses `CostModel` via `fill_costs_from_model` | E3-S3 |
| Depth survival discount shared by signal and fill | DEFER | Needs L2 matching and depth data (`/depth` returns none) | E3-S2 |
| Feed staleness breaker | DEFER | Needs feed and risk stage | E2-S10 |
| Durable risk state, idempotent fill ledger | DEFER | Needs live and FSM | E2-S11 |
| Opportunity contract, multi-leg sizing/execution, reference arb strategies | DEFER | Arbitrage is not a decided product goal (D13); only E3-S3 and E2-S10 ideas stay in scope | E8 |

### 3.7 Matching-core study (differential oracle, L3 book)

| Idea | Decision | Reason | Lands |
|---|---|---|---|
| Naive reference matcher vs optimised sim | DEFER | Do with L2; L1 has simple semantics covered by shared vectors | E3-S1d |
| L3 full-book replay | DROP | No full-depth data source; broker feeds give a few levels; would add false precision | none |

---

## 4. Milestones re-sequenced

Done work is removed. Sizes: S (<=1 day), M (2-4 days), L (split before start). Owner repo is `honba` unless stated.

| Milestone | Theme | Stories | Exit criteria |
|---|---|---|---|
| R1 Safe writes | Nothing can place an order without a gate | E2-S2, E2-S1 rest, E2-S3 rest, E2-S6(a) | Risk stage with typed refusals, Reducing enforced, wall-clock lint, `OrderState` FSM in `honba-messages` |
| R2 Runs | A backtest is a first-class server object | ADR-Run, E3-S1a (next-open Rust port), E4-S3, E4-S1, E11-S3 rest (backtests, sweeps, journals), E4-S5 | `POST /backtests` returns `run_id`; result carries `assumptions.not_modelled`; sweep over REST; journal v1 |
| R3 Live-shaped runtime | Python and Rust share one runtime; push updates | E11-S4 (E10-S7 done) | `honba.event_loop` owns one runtime (done); SSE streams run progress with resume |
| R4 Trustworthy research | Results that resist self-deception | E4-S2, E4-S4, E4-S6, E4-S7, E6 batches 3-4 | Look-ahead property test; DSR/PBO/holdout/MC/cost-stress gates in result |
| R5 Agent surfaces | Safe LLM and automation access | E5-S5, E5-S2, E5-S1 server, E5-S3, E5-S4, E5-S7, E11-S7 | MCP server from generated schemas; scope drift test; verifier; write routes gated |
| R6 Live-ready | Broker, paper on live data | E1-S2..S6, E10-S8, E2-S7, E2-S9..S11, E3-S2..S7 | Instrument master, feed connector, reconciliation, L2 simulator, sandbox adapter |
| R7 Frontend | Capability-driven apps | E11-S5/S6 rest, E9 | WASM replay, generated client, AlgoDesigner |
| R8 Optional | Scale and arbitrage | E7, E8 | Only if profiling or product decision demands |

R6 and R7 can run in parallel with R4 once R2 exits. R5 needs R1 for write tools and R2 for runs.

**Critical path:** E2-S2 -> E11-S7 (write endpoints) and E5-S4; ADR-Run -> E4-S3 -> `POST /backtests` -> E4-S5 -> E11-S4 -> E9-S7;
E3-S1a (next-open Rust) -> E3-S1b/S2 -> E3-S7; E10-S7 (done) gates Python callers of any async Rust API; E2-S6(a) -> E2-S6(b..d) -> E2-S7 -> E3-S7.

### E1 Instruments and adapters

| ID | Scope | Size | Deps | Acceptance (unit / integration) | Owner |
|---|---|---|---|---|---|
| E1-S1b | Second adapter passes the contract suite on recorded fixtures; migration note per adapter | M | E1-S1 | `test_contract_suite[dhan]` / `test_adapter_wiring_recorded_fixture` | honba-adapters (ask first) |
| E1-S2 | `Instrument` gains optional expiry, strike, option type, underlying; `india::fno` grammar parse/format (equity, futures, options, index) | M | E0-S7 | `symbol_round_trip_property`, `ce_suffix_ambiguity`, `weekly_vs_monthly` / `catalog_resolves_fno_symbol` through market registry; Python binding + stub | honba |
| E1-S3 | `InstrumentMaster` impl over Parquet catalog: `snapshot(as_of)`, effective-dated lot/tick/freeze | M | E1-S2 | `resolve_as_of_old_symbol` / `backtest_resolves_expired_contract` | honba |
| E1-S4 | Product/price-type/TIF table mapping per adapter | S | first real adapter | `mapping_round_trip_property` / `adapter_rejects_unsupported_combo` | honba-adapters |
| E1-S5 | `SessionProvider`, rate limiter, secret store impl (Python side) | M | E1-S1b | `token_expiry_fake_clock`, `bucket_refill` / `limits_hold_across_workers`, `no_secret_in_logs` | honba (+adapters) |
| E1-S6 | Feed connector contract: reconnect, backoff, gap markers, watchdog, Terminal/Unrecoverable | M | E10-S1, E2-S3 | `backoff_schedule`, `terminal_error_stops` / `simulated_feed_drop_emits_gap_marker` | honba |

### E2 Safe engine

| ID | Scope | Size | Deps | Acceptance | Owner |
|---|---|---|---|---|---|
| E2-S1 rest | Enforce Reducing as reduce-only; expose `TradingState` and `Command::State` in Python and CLI; state change visible in audit | S | none | `reducing_refuses_position_increasing_order` / `python_halt_blocks_orders_in_run` | honba |
| E2-S2 | New `honba-risk` crate (pure): `RiskCheck` -> approved plus typed refusals; rules max notional, order rate, lot size, price band from `MarketProfile`; refusals are events and audited; config schema | M | E2-S1, E0-S7 | `max_notional_refused`, `price_band_from_profile` (+ property tests) / `strategy_engine_risk_fills_refusal_in_audit` | honba |
| E2-S3 rest | Wall-clock-free domain check (lint or test) in CI | S | none | `no_system_time_in_domain_crates` / dependency-graph job | honba |
| E2-S4 rest | Replay: rebuild final state from `AuditLog`; journal writer | M | E2-S3 | `audit_replay_matches_state` / `run_then_replay_identical_positions` | honba |
| E2-S6 | (a) `OrderState` FSM and client order ids in `honba-messages`; (b) `ExecutionEngine` emits ack/reject/fill/cancel-ack events replacing `drain_*`; (c) adapter contract emits same; (d) sim and paper identical. Split before start; ADR first (breaking) | L | E0-S2 | `illegal_transition_rejected`, `partial_fill_sequence` / one conformance scenario set (market, limit, partial, reject, cancel race, expiry) run on every gateway | honba (+adapters) |
| E2-S7 | Reconciliation on startup/reconnect | M | E2-S4, E2-S6, E2-S9 | `missed_fill_detected` / recorded fixture scenarios | honba |
| E2-S9 | Cache/state store (`engine/src/cache/` is a stub): orders, positions, instruments, last quotes; query trait for context | M | E2-S6(a) | `cache_apply_event` / `context_reads_from_cache` | honba |
| E2-S10 | Feed staleness breaker on the simulated clock | S | E2-S2, E1-S6 | `stale_after_blocks_orders` / `stale_feed_scenario` | honba |
| E2-S11 | Durable risk state, idempotent fill ledger | M | E2-S2, E2-S6, E2-S7 | `fingerprint_dedup`, `atomic_snapshot` / `reconnect_replay_no_double_count` | honba |
| E2-S8 | DROPPED (see 3.2) | | | | |

### E3 Execution realism

| ID | Scope | Size | Deps | Acceptance | Owner |
|---|---|---|---|---|---|
| E3-S1a | Rust port of the next-open simulator behind `ExecutionEngine`, Python `NextOpenExecution` API unchanged; harden L1 stop/limit (3 `#[ignore]` known-gap tests). **Chunk 1 DONE** (ADR 0016, commits b12e5ba..1b2f6ce). Remaining per ADR 0016: chunk 2 settlement + costs + `SessionOpen`; chunk 3 PyO3 binding and Python delegation. Note: the Python reference never fills limit or stop orders (they are rejected `unsupported_order_type`), so "harden L1 stop/limit" needs a product decision before it can be scheduled | L -> split (port, order types, partial by volume) | ADR D1 | `next_open_fills_match_vectors` / `schema/conformance/next_open.json` run by Rust, Python and `honba._honba` and asserted equal | honba |
| E3-S1b | L2 quote/tick matching: marketable orders walk top of book, resting limits, stops from ticks, IOC/FOK/day | L -> split | E2-S6, E3-S2, tick data decision | `limit_fills_on_trade_through` / L1 vs L2 conformance on same stream | honba |
| E3-S1d | Differential oracle: naive reference matcher vs optimised sim | M | E3-S1b | `oracle_agrees_seeded_streams` / same | honba |
| E3-S2 | `FillModel`, `LatencyModel` traits, seeded; slippage, ack delay, queue position; depth survival discount | M | E3-S1a | `seeded_slippage_reproducible` / `same_seed_byte_identical_fills` | honba |
| E3-S3 | Wire Rust `CostSchedule` into fills (named charges on `Trade`); `edge_after_costs` | M | E0-S7 | `charges_effective_dated` / `india_costs_in_backtest_match_python_costmodel` golden | honba |
| E3-S4 | Margin model and MIS square-off from `MarketProfile` | M | E3-S3, E2-S3 | `square_off_at_session_end` / `mis_run_flat_by_close` | honba |
| E3-S5 | Target-position (smart order) with per-instrument serialisation | M | E3-S6, E2-S6 | `target_delta_computed` / `target_position_run` | honba |
| E3-S6 | Lot and freeze-quantity splitter, lot-aware sizing | S | E1-S3 | `slice_above_freeze` / `order_sliced_in_sim` | honba |
| E3-S7 | `SandboxAdapter`: paper on live data, same matching core and FSM | M | E1-S6, E2-S6, E3-S1..S4 | `virtual_funds_isolated` / `sandbox_vs_backtest_same_fills` | honba |

### E4 Research pipeline (core in Rust analytics/sweep; orchestration in Python)

| ID | Scope | Size | Deps | Acceptance | Owner |
|---|---|---|---|---|---|
| E4-S1 | Journal schema v1 (seed, config hash, dataset id, git rev, trial count, schema_version) from `AuditLog`; result cache keyed by hash(manifest, params, dataset id, cost-model version) | M | E2-S4, ADR D3 | `journal_schema_roundtrip`, `cache_key_changes_with_costs` / `rerun_hits_cache` | honba |
| E4-S2 | `hyperparameters()` and one-line param declarations drive sweep space; defaults checked by `verify` | S | E0-S8 | `param_range_to_schema` / `sweep_space_from_manifest` | honba (+strategies) |
| E4-S3 | Finish runner: `honba backtest` CLI, `POST/GET /backtests` with `run_id`, typed `assumptions.not_modelled` and timing rule, manifest-driven | M | ADR D1, E3-S1a | `result_has_not_modelled`, `cli_exit_codes` / `rest_backtest_roundtrip_matches_session` | honba |
| E4-S4 | Look-ahead guarantee: truncating future data never changes earlier decisions; vectorized vs event-driven parity | M | E4-S3 | `truncation_property` / `prefilter_vs_event_driven_fixture` | honba |
| E4-S5 rest | REST `POST /sweeps`, `GET /sweeps/{id}`; more fitness (Calmar, Sortino, Omega); journal per trial | M | E4-S1, E4-S3 | `fitness_ranking_stable` / `sweep_rest_equals_library` | honba |
| E4-S6 | Gates in Rust `honba-analytics`: DSR, PBO, walk-forward efficiency, one-shot holdout (reads trial count), block-bootstrap MC, 2x cost-stress | L -> split | E4-S1, E4-S5, E3-S3 | `dsr_known_values`, `bootstrap_seeded` / `result_carries_validation_block` | honba |
| E4-S7 | Chunked streaming batches and dataset ids | M | E10-S4 | `chunks_cover_slice` / `sweep_over_streamed_dataset` | honba |
| E4-S8 | Multi-timeframe aggregation on session calendar | M | E4-S4 | `htf_bar_only_when_complete` / `mtf_strategy_run` | honba |
| E10-S4 rest | Streaming Parquet half of E10-S4 is E4-S7 | | | | |

### E5 Agent and API surfaces

| ID | Scope | Size | Deps | Acceptance | Owner |
|---|---|---|---|---|---|
| E5-S5 | AI strategy verifier: AST, import allowlist, subprocess with rlimits, no network, deterministic smoke, look-ahead check, red-team tests; ADR D7 | M | E4-S4, ADR | `ast_rejects_forbidden_import` / `generated_strategy_passes_same_suite_as_handwritten` | honba |
| E5-S2 | Trust envelope: broker/LLM/instrument text wrapped and marked untrusted | S | none | `wraps_untrusted_text` / `mcp_tool_output_marked` | honba |
| E5-S1 | Thin Python MCP server over generated `mcp_tools.json` (20 tools); toolset filter; read-only kill switch | M | E5-S2 | `toolset_filter` / `mcp_call_matches_rest_response` | honba |
| E5-S3 | Scope map (read:market, read:account, write:orders, research) with CI drift test; audit by token id | S | E5-S1, E11-S8 min | `scope_for_every_endpoint` / `drift_test_fails_on_new_route` | honba |
| E5-S4 | Approval queue for placements; cancel/modify bypass | M | E2-S2, E5-S3 | `placement_held`, `cancel_bypasses` / `approve_flows_through_risk_to_sim` | honba |
| E5-S7 | Typed LLM decisions (veto/size-down only), journaled | S | E5-S5, E4-S1 | `schema_rejects_increase` / `decision_in_journal` | honba |
| E5-S8 rest | Write routes gated (E11-S7) | S | E2-S2, E5-S4 | see E11-S7 | honba |
| E5-S6, E5-S9 | Deferred (webhook, report builder) | | | | |

### E6, E7, E8, E9

| ID | Scope | Size | Deps | Acceptance | Owner |
|---|---|---|---|---|---|
| E6 b3 | Monotonic-deque indicators (donchian, williams_r, stochastic, ...); batch 4 plain sums | M each | none | oracle vs old O(n) plus Fraction reference / shared vectors | honba |
| E6-S5 | Indicator render contract (pane, plots, zones) | S/M | E9 start | `output_len_matches_input` / chart consumes via WASM | honba |
| E7-S1 | Docs from schemas | S | continuous | docs build | honba-docs |
| E7-S2/S3/S5 | Indexed state, cache persistence, recorder tap | M | pull-based | n/a | honba |
| E8-S1..S4 | Multi-leg; not scheduled | | D13 | n/a | honba |
| E9-S8 | Generated TS client from OpenAPI (types already generated) | S | E11-S6 | `client_types_compile` / contract test against `honba serve` | honba-frontend (approved) |
| E9-S1..S7 | StrategyGraph editor, graph to code, code to graph, verify/run, AI assistant, ops console, result views | M each | E0-S8, E4-S3, E5-S5, E11-S4 | per old roadmap section E9 | honba-frontend |

### E10 Async core (remaining; E10-S7 done)

| ID | Scope | Size | Deps | Acceptance | Owner |
|---|---|---|---|---|---|
| E10-S8 | Rust-native connectors (NSE bhavcopy first, broker REST later) as `honba-ports` impls; decide Python-adapter bridge (D10) | L -> split | E10-S1, D10 | `bhavcopy_connector_parses_fixture` / `feed_into_engine_run` | honba |

### E11 Versioned API (remaining)

| ID | Scope | Size | Deps | Acceptance | Owner |
|---|---|---|---|---|---|
| E11-S3 rest | Backtests, sweeps, journals, orders routes (currently 501, 10 routes) built in order of R2; known limits in ADR 0013 stand (no real bid/ask, no pagination, no auth) | M each | E4-S3, E4-S5 | route unit tests / `test_client_parity` extended | honba |
| E11-S4 | SSE for run progress and journals (resume-from-sequence), WS for events later; schema for streamed events | M | ADR D6, E4-S3 | `resume_from_seq`, `event_schema` / `sse_backtest_progress_inproc_and_http` | honba |
| E11-S5 rest | WASM: screener eval export (Rust evaluator exists in `honba-indicators`, not exported), backtest replay; wasm-pack CI step never exercised | M | E4-S3 | conformance vectors `screener_scan.json` / browser-free node run | honba |
| E11-S6 | Frontend client from OpenAPI, TS drift check in CI (currently local only) | S | none | `ts_matches_openapi` / frontend build | honba + honba-frontend |
| E11-S7 | Write endpoints behind risk and approval queue | M | E2-S2, E5-S4 | `order_refused_by_risk` / `post_order_flows_to_sim_only` | honba |
| E11-S8 | Auth, scopes, secrets, audit (minimum viable before non-loopback `serve`) | L | ADR D11 | `token_scope_enforced` / `write_needs_second_factor` | honba |

---

## 5. Open decisions and ADRs to write

| # | Question | Needed for | Recommendation |
|---|---|---|---|
| D1 | **Run lifecycle and `run_id`**: a `Run` is a server-side object (`queued/running/completed/failed/cancelled`), journal addressable by id, retention, restart behaviour. Types exist (`BacktestResponse`, `RunStatus`); routes are 501 | E4-S3, E11-S3/S4, E9-S6 | ADR before R2; in-memory plus on-disk journal first |
| D2 | **Cancel timestamp rule**: DECIDED 2026-10-06 (ADR 0008 decision 13 addendum, commit 1c50fc7): a cancel is stamped with the time of the cancel (`ExecutionEngine::cancel(order_id, now)`), identical in Rust and Python. Still open: L2 and live may need the venue-ack time | E2-S6, E3-S1b | State the per-fidelity rule for L2/live in the FSM ADR |
| D3 | Journal schema v1 and cache key | E4-S1 | ADR with golden journal |
| D4 | Risk stage: crate placement (`honba-risk` at L2), Reducing semantics, rule config schema | E2-S2 | ADR before code |
| D5 | `OrderState` FSM, event set, `drain_*` replacement (breaking for adapters, Python ports, bindings) | E2-S6 | ADR, then split stories |
| D6 | Streaming protocol: SSE vs WS, sequence and resume | E11-S4 | SSE first |
| D7 | Verifier sandbox strength: subprocess rlimits vs container | E5-S5 | Subprocess; revisit for hosted |
| D8 | Holdout policy: fixed ranges, who resets consumption | E4-S6 | ADR with E4-S6 |
| D9 | Sampler: Rust seeded sampler vs Optuna extra; pinned version | E4-S5 | Rust sampler; Optuna optional |
| D10 | Python adapters (ADR 0010) vs Rust `ExecutionGateway`/`MarketDataFeed` ports: who bridges, which side owns the loop | E10-S8, E3-S7 | ADR before E10-S8 |
| D11 | Minimal auth before non-loopback serve | E11-S8, E5-S3 | Bearer token plus scope map |
| D12 | ADR 0009 (agentic MCP and local LLM) is still **Proposed**; reconcile with ADR 0014 (MCP tools generated from Rust) and accept or amend | E5 | Amend then accept |
| D13 | Is arbitrage a product goal (E8)? | E8 | Unscheduled unless yes |
| D14 | Tick/depth data source for L2 (bars only today; `/depth` empty) | E3-S1b | Decide before L2; else stay L1 |
| D15 | Root CLAUDE.md wording for the pack pattern; clean-room log for OpenAlgo/QuantDinger-inspired work; `honba-python` split re-evaluation (needs `honba-py` on crates.io) | housekeeping | Owner approval |
| D16 | `StrategyGraph` stored form: Python file plus sidecar graph | E9 | Per old roadmap item 18 |
| D17 | NSE settlement default and intraday cycle (counts bars, not sessions) in simulators | E3-S1a | Settle in the port ADR |

Already decided: pyo3 single cdylib (ADR 0014 decision, `honba-py`), Strategy ABC with shim (ADR 0008), schema from Rust (ADR 0014),
integer money (0011), versioning and nanosecond strings (0012), REST read ports (0013), explicit runtime (0015, implemented in E10-S7), next-open Rust port split into three chunks (0016, chunk 1 done).

---

## 6. Cross-repo impact register

| Repo | Impact and action | Triggered by |
|---|---|---|
| `honba-adapters` | 8 adapters + shared still unmigrated to the E1-S1 contract (migration note owed); will need FSM events (E2-S6), reconcile reports (E2-S7), session/rate-limit (E1-S5). Ask before touching | E1-S1b, E2-S6, D10 |
| `honba-strategies` | Catalog passes `verify` and the loader; add `hyperparameters()` (E4-S2); `backtest_result.json` are placeholders with no regen script; alpha strategies excluded from the next-open replay test; 72 pre-existing ruff errors; `ai_generated/` must route through E5-S5 | E4-S2, E4-S3, E5-S5 |
| `honba-examples` | **Another agent owns it: ask first, new files only below.** Existing follow-ups (use core `NextOpenExecution`/loader, `*_minor` names, settlement default T+1) are theirs, not part of this roadmap | R2, R4, R5 |
| `honba-frontend` | No consumers of generated types, no test runner or lint; needs generated client (E11-S6), SSE consumer (E11-S4), WASM loader (E11-S5), then E9. TS drift check is local only | E11-S6, E9 |
| `honba-docs` | `docs/architecture/money.md` never built (`make build`); missing pages: reject/cancel contract, quotes/depth/serve, WASM, SDK; strategy pages still describe APIs that never existed (`on_stop` intents) | every story; docs ticket per story |

### 6.1 honba-examples E4/E5 demonstrations (new example files only; do not edit existing)

Existing numbering: `backtesting/01..06`, `ai_research/01..06` (several are one-line stubs); `basic/`, `candles/`, `options/` also exist.

| New file (proposed) | Demonstrates | Lands with |
|---|---|---|
| `backtesting/07_run_via_client.py` | Same strategy over `InprocTransport` and `HttpTransport`, `run_id`, assumptions | E4-S3, E11-S3 |
| `backtesting/08_journal_and_cache.py` | Write/read journal v1; cache hit on rerun | E4-S1 |
| `backtesting/09_seeded_sweep.py` | Sweep from manifest params, fitness ranking, determinism | E4-S2, E4-S5 |
| `backtesting/10_lookahead_guard.py` | Truncation parity and prefilter-vs-event check | E4-S4 |
| `backtesting/11_overfitting_gates.py` | Read DSR/PBO/holdout/cost-stress from result | E4-S6 |
| `ai_research/07_verify_generated_strategy.py` | LLM-written strategy through verifier then backtest | E5-S5 |
| `ai_research/08_mcp_tools_from_schema.py` | List and call MCP tools generated from `mcp_tools.json` | E5-S1 |
| `ai_research/09_typed_llm_decision.py` | Veto/size-down schema, decision journaled | E5-S7 |
| `ai_research/10_gated_order_flow.py` | Approval queue plus risk refusal on simulated orders | E5-S4, E2-S2 |

Each example ships with its own test under `honba-examples/tests/` (unit for helpers, integration running the script on synthetic data).

---

## 7. Definition of done

A story is done only when all of the following hold (merges roadmap v4 DoD, `docs/archive/plan.md` section 10, root R1-R3):

1. Contract merged first (trait, ABC, schema, `.pyi`, ADR if `needs-adr` or breaking) with docs.
2. Failing test seen first (R1); bug fixes start with a regression test; no test deleted, skipped or loosened without a recorded reason.
3. Unit tests and integration tests present and named in the story (R3), plus the shared contract test for any port change; or an explicit
   note why a level does not apply (docs-only, config-only, generated code, pure rename).
4. Deterministic: seeds, recorded fixtures, no network, no wall clock; cross-language behavior covered by shared golden vectors.
5. Reference implementation merged; Python binding and `.pyi` for any user-facing Rust behavior; Python API unchanged when logic moves to Rust.
6. Codegen artifacts regenerated, no drift (`make check-codegen-ci`); `schema_version` or `api_version` bumped per ADR 0012 when the wire changes;
   every error crossing a surface carries a stable code.
7. `cargo fmt`, `cargo clippy --workspace --all-targets -D warnings`, `cargo test --workspace`, `cargo doc -D warnings`, `ruff check` and
   `ruff format --check`, `pytest -m "not slow"`, stubtest, and `scripts/dependency_graph.py` (layering, async isolation, WASM purity,
   pyo3 containment, no `india` in core) all green.
8. For research-affecting changes: journal output defined, seeds recorded, and same seed and data give a byte-identical journal at 1, 4 and 16 worker threads.
9. Independent review done (one reviewer subagent per area, read-only); CHANGELOG entry for behavior changes.
10. Affected consumers flagged or updated per section 6 (adapters, strategies, examples, frontend, docs); docs ticket for new public behavior.
11. Commits: by explicit path, no trailer, one logical step each, tests committed with the code they drive.

Architecture-level done (`docs/archive/plan.md` section 10), still open: (a) one strategy runs unchanged in-process, over REST and in `honba-examples`
(section 6.1); (b) frontend computes indicators, screener and replay in WASM with no Python (replay and screener export open); (c) five
codegen artifacts with blocking drift checks (met except TS check is local only); (d) every surface error carries a stable code (met for
REST); (e) determinism at 1/4/16 threads (met for sweeps, extend to runs).

---

## Appendix A: Corrections to the audit (spot-check, 2026-10-06)

Checked about 25 verdicts against code, git log and ls/grep (read-only). Counts: the audit's table holds 74 rows (8 DONE, 37 PARTIAL,
29 NOT STARTED) while its prose said 70 and its summary 10/31/28 = 69. Corrected totals were 18/18/38 at v5; v6 moved E10-S7 to done (19/17/38, see section 1).

| Story | Audit said | Code shows | Corrected |
|---|---|---|---|
| E10-S6 | NOT STARTED | `crates/honba-sweep/tests/sweep.rs` runs `determinism_under_threads` at 1, 4, 16 workers comparing journal and ranking | DONE |
| E2-S2 | PARTIAL ("`honba-risk` skeleton, `RiskCheck` stub") | No `honba-risk` crate; `engine/src/risk/mod.rs` is 9 bytes; no `RiskCheck` anywhere | NOT STARTED |
| E4-S3 | PARTIAL, "`not_modelled` list exists (CHANGELOG)" | No `not_modelled` in code, schema or CHANGELOG; only `assumptions: Option<Value>` on the DTO; `cli/backtest.py` is 0 bytes; `/backtests` unbuilt | PARTIAL (Python `BacktestSession` only; claim removed) |
| E11-S4 | NOT STARTED, "WS/SSE routes return 501" | Verdict right; no stream route is registered at all (22 routes in `endpoints.rs`, none streaming) | NOT STARTED (evidence fixed) |
| E10-S3 | PARTIAL, "feedback unverified" | `Engine::apply_output` applies orders, cancels, state; fills re-enter queue as `OrderFilled`; commit daf12d5 | DONE (E2-S5 likewise DONE) |
| E10-S2 | PARTIAL | `honba-async` `EngineHandle`, clocks, command channel, tests, commit 49ffb32 | DONE |
| E10-S4 | PARTIAL, "not implemented" | `Dataset`, `ColumnarSlice`, `DatasetFeed` (c7ef86b); only chunked streaming missing | PARTIAL (narrower, moved to E4-S7) |
| E10-S5 | PARTIAL | Runner, report, tests complete; only REST route missing, tracked in E4-S5 | DONE |
| E10-S7 | PARTIAL, "bridges visible" | No `honba.event_loop`; `auto-initialize` already absent and `pyo3-async-runtimes` present | PARTIAL (ADR 0015 plus dependency only) |
| E2-S6 | PARTIAL, "(a) OrderState FSM and client ids done; simulator emits ack events" | Only `OrderStatus` enum and `drain_rejections`/`drain_fills`; no FSM, no `client_order_id`, no ack events | PARTIAL (only reject/cancel queue) |
| E1-S2 | PARTIAL, "Instrument has option fields; round-trip tests" | `Instrument` has no expiry/strike; `india/fno/mod.rs` is 8 bytes | PARTIAL (trait only) |
| E3-S2 | PARTIAL, "FillModel/LatencyModel traits" | Only a config enum in `honba-config`; `sim/{latency,cost,simulator}` are stubs | NOT STARTED |
| E2-S9 | PARTIAL, "StateCache stub in execution.rs" | `engine/src/cache/mod.rs` is a 10-byte stub; `execution.rs` has no cache | NOT STARTED |
| E4-S2 | PARTIAL, "manifest params with `range=a:b:step`" | No range or hyperparameter code in `strategies/manifest.py` or `base.py` | NOT STARTED |
| E4-S6 | PARTIAL, "DSR/PBO/MC in CHANGELOG" | `monte_carlo.rs` and `walk_forward.rs` are 1-line uncompiled files; no DSR/PBO anywhere | NOT STARTED |
| E4-S1 | PARTIAL, "BacktestResult wire model (commit 166c0bd)" | No journal schema or cache; `AuditLog` is in-memory | NOT STARTED |
| E4-S5 | PARTIAL, "fitness enum Sharpe, Calmar, Sortino, Omega" | Only `SharpeFitness` implements the `Fitness` trait | PARTIAL (corrected) |
| E2-S1 | PARTIAL, "enforcement unknown" | Halted refuses and audits (`engine.rs:261`); Reducing accepted without restriction; no Python or CLI exposure found | PARTIAL (clarified) |
| E0-S4/S5/S6/S8 | PARTIAL | Handoff and commits: schema export, versioning, money, manifest+IR+`verify` all shipped with tests | DONE |
| E11-S3 | PARTIAL | Read routes, screener and strategies built and tested; remaining 501 routes belong to E4/E5 | DONE |
| ADR 0009 | "Approved" | File says Status: Proposed | Open decision D12 |
| E3-S3 | PARTIAL, "cost model wired to fills" | Rust `CostSchedule` unused by `honba-sim`; only Python `fill_costs_from_model` path | PARTIAL (Python only) |

Verdicts confirmed by the spot-check: E3-S1 (L1 only; sim stubs), E11-S5 (indicators and ATR only; screener evaluator not exported to WASM),
E5-S1 (20 tool schemas, MCP server files 0 bytes), E1-S1, E0-S7, E0-S1..S3, E11-S1/S2, E10-S1, E9 (all not started), E5-S4/S5/S6/S7.
