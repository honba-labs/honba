Archived 2026-10-06; superseded by docs/ROADMAP.md. Kept for history; do not update.

# Honba roadmap: contract-first delivery, borrowing from Nautilus, OpenAlgo, barter-rs, QuantDinger (and Jesse)

Status: DRAFT v4, 2026-10-01 (v4: deeper QuantDinger read folded in; new epic E9 for the frontend, with AlgoDesigner as a flow graph AND
code; v3: market-pack pattern and crate layout added; arbkit folded in). Nothing here has been filed on GitHub; the `gh` script in
Appendix B was not run.

Evidence base
- Honba baseline: a read-only inventory of `honba/` (Rust crates, Python packages, configs, CI, git).
- OpenAlgo: a read-only study of the local checkout at `~/tmp/openalgo` (outside the honba repo, read at the
  owner's explicit request).
- Nautilus, barter-rs, Jesse: doc and README level review only. Details marked "unverified" below
  were not read in source. Treat every borrowed idea as a lead to confirm in refinement.
- QuantDinger (`github.com/OpenByteInc/QuantDinger`, 2026-10-01): README, repo tree, `STRATEGY_DEV_GUIDE`, `INDICATOR_DEV_GUIDE`,
  `MODULE_BOUNDARIES`, `AGENTS.md`, `ROADMAP.md` and a description of the separate `QuantDinger-Vue` frontend, all read through a
  summarising fetch (not raw source). Not read: `events/`, `strategy_evolution`, `execution_streams`, `ai_decision_filter` internals, the
  frontend source (source-available, non-commercial licence: ideas only). The agent docs were thin on scopes and rate limits.
- honba-frontend (read-only, 2026-10-01): `Design.md` and `MODERNIZATION_PLAN.md`; `algodesigner-app.tsx` is a 2-line placeholder
  (`mountPlaceholder('algodesigner')`), so AlgoDesigner has no existing code to migrate.
- arbkit (`github.com/harlanljones/arbkit`): read README plus `arbkit-core` (arb.rs, fee.rs, fill.rs) and
  `arbkit-exec/state.rs` only; `arbkit-sim`, `-match`, `-feed`, `-engine`, docs and tests were NOT read. It is a Rust
  workspace for sports/prediction-market arbitrage (Kalshi, Polymarket), very immature (0 stars, 32 commits; test and latency
  numbers are README claims, unverified). Its value to honba is the multi-leg, cost-first, fail-safe design, not its markets.

Licences (borrow ideas, not code)
- OpenAlgo is AGPL-3.0: clean-room only. Do not copy code, mapping tables, or schemas. Describe behaviour, then implement from
  our own spec and tests.
- QuantDinger backend is Apache-2.0 (frontend and mobile clients have separate licences: do not copy those).
- Nautilus is LGPL-3.0. barter-rs is MIT (verify). arbkit is Apache-2.0 OR MIT per its README (verify the LICENSE files).
  Check honba's own LICENSE before any code reuse.

---

## 1. Design pillars (rules every story must obey)

**P1 Interface-first.** The contract is the first deliverable of every story: Rust trait or message type,
Python ABC/Protocol/pydantic model, `.pyi` stub, JSON Schema, config schema. Order of work is always
(1) contract + docs, (2) contract test suite and golden vectors, (3) reference implementation, (4) more implementations.

**P2 API-first.** One source of truth per surface, generated outward, never hand-copied:
- domain types (Rust) -> serde JSON -> Python models -> JSON Schema -> MCP tool schemas -> OpenAPI;
- config: pydantic models -> JSON Schema -> `configs/*.toml` validation;
- every capability reachable by library, CLI and MCP/HTTP with the same typed request/response and stable error codes;
- machine-readable outputs (JSON/Parquet), versioned (`schema_version`), with a compatibility test.

**P3 Python-first.** Behaviour is specified and prototyped in pure Python with full type annotations and tests.
The public Python API does not change when something moves to Rust. Anything user-facing added in Rust must land with
a Python binding and stub in the same milestone.

**P4 Rust-core.** Rust owns the deterministic hot path and the contracts that must be identical in backtest and live:
events, engine loop, clock, risk, execution state machine, simulator, indicators (already partly), analytics, sweeps.
Rust must not know about brokers' wire formats, HTTP, or LLMs (those sit at the edges behind ports).

**Cross-cutting rules**
- One event flow for backtest, paper and live; strategies depend only on the interface (never on the mode).
- Determinism: fixed seeds, simulated clock, recorded fixtures, no network or wall clock in tests.
- Mode is a per-run/per-adapter property, never a global flag (OpenAlgo pitfall).
- Symbols are display/alias grammar; identity is `InstrumentId` (venue + native token). No stringly-typed primary keys.
- LLM and broker text is untrusted input (trust envelope, verification sandbox).
- **Market-neutral core, market-specific packs.** The core (engine, sim, risk, strategy, research) knows only generic market
  contracts (`MarketCalendar`, `CostSchedule`, `InstrumentRules`, `SymbolGrammar`, `ExpiryRules`, `MarginModel`, `SettlementRules`,
  bundled as a `MarketProfile`). India (NSE/BSE calendar, STT/GST/stamp duty, expiry cycles, lot/freeze sizes) is one pack: a
  feature-gated module of `honba-market` (`india`; Python `honba.markets.india`), selected by run config and reached only via
  the registry. Nothing in the core names a pack. A trivial "null market"
  pack must pass the same contract tests, so the traits are proven not to be India-shaped.
- Cross-repo impact (`honba-adapters`, `honba-strategies`, `honba-examples`, `honba-docs`) is flagged in the story and
  updated or ticketed in the same milestone.

---

## 2. Verified baseline (what exists vs what is a stub)

Rust (11 crates, about 7.2k lines): a working event kernel with thin contracts.
- Real: `honba-messages` (Venue, InstrumentId, Order*, Bar, ticks, non-exhaustive `Event`), `honba-entities` (Instrument,
  Position, Account, Portfolio, Trade), `honba-algo` (Engine 105 lines, EventQueue, `Clock` concrete and monotonic,
  `Handler`, `DataFeed`, `ExecutionEngine` trait: submit/cancel/drain_fills), `honba-algo-strategies` (Strategy trait,
  StrategyRunner, OrderIntent market/limit only), `honba-algo-indicators` (Sma, Ema, Rsi, Macd, Atr, Bollinger),
  `honba-algo-testing` (VecFeed, BarFillEngine fills at last close, PaperExecution fixed price), `honba-algo-analytics`
  (EquityStats, RoundTrip, TradeStats), export (csv/json/markdown), import (parquet bars), `honba-india`
  (TradingCalendar, HolidaySource, NseCalendar, effective-dated CostModel STT/GST/stamp/SEBI/brokerage, Nifty50 universe), `honba-cli`.
- Stubs (1 line): `risk/`, `cache/`, `connector/`, simulator, latency, cost-in-backtest, concurrent sweep, backtest_node,
  result, monte_carlo, walk_forward, tearsheet, regime, `fno/`, `options/`, `equities/`, parquet export, broker/nse/bse import.
- Handlers cannot emit events back into the queue, so there is no command/ack feedback loop.
- India `CostModel` exists but is not wired into any fill engine.
- No option/future metadata on `Instrument` (expiry, strike, underlying); no lot/symbol parser; no holiday or cost data files.

Python (about 7.2k lines): real parts are `Strategy` (plain class, not an ABC; hooks on_start/on_bar/on_fill/on_stop),
frozen-dataclass entities, `StrategyConfig` (pydantic), `IndicatorBank`, 74 registered indicators, `strategies/testing.replay`,
`honba indicators list/show` CLI. Everything else is 0-byte: `adapters/*`, `core/*`, `backtest/*`, `research/*`,
`ai/{mcp,llm,autoresearch,verification,rl}`, `india/*`, most of `cli/*`, all 7 `configs/*.toml`, `docs/*`.

Build and CI
- **No `honba._honba` extension.** `pyproject.toml` declares maturin with that module name, but no cdylib crate exists;
  `_lib/__init__.pyi` is empty; pyo3 is embedded only in the `honba-cli` binary with a 539-line Python shim.
- CI: Rust job is strict (fmt, clippy `-D warnings`, tests, docs); the Python job is `continue-on-error`.
- `scripts/dependency_graph.py` `ALLOWED` map is out of sync with `Cargo.toml` (cli unlisted; strategies and india under-listed).
  The file is uncommitted-dirty in the working tree, so this needs an owner decision before anyone edits it.
- No JSON Schema, OpenAPI, MCP tool definitions, journal schema, or docs content exists anywhere.

---

## 2b. Crate arrangement (proposed; from inventory, verify with `cargo metadata` before acting)

Problems today: `honba-algo-testing` mixes test helpers with product code (BarFillEngine, PaperExecution, stubbed simulator, latency,
cost, backtest_node); crate naming is inconsistent (`honba-algo-*` vs unprefixed); risk/cache/connector stubs live inside the kernel
and `honba-strategies` has its own risk stub; pyo3 sits in the CLI binary so `honba._honba` has no crate; the allowed-dependency map in
`scripts/dependency_graph.py` disagrees with `Cargo.toml`; generic market traits live in `honba-india` (see E0-S7).

Target layering (owner-confirmed split, with `honba-india` renamed `honba-market`; dependencies point inward; a crate may depend only on
lower layers; enforced in CI):
```
L0  honba-messages                  ids, events, orders, bars, audit/journal schema (serde)
L1  honba-entities                  instruments, position, account, portfolio
L2  honba-market (was honba-india)  generic market contracts + registry (calendar, costs, instrument rules, symbol grammar, expiry,
                                    margin, settlement, instrument master); India is a feature-gated module (`india`), plus a `null` test pack
    honba-ports                     traits: MarketDataFeed, ExecutionGateway, InstrumentMaster, Clock
    honba-risk                      pure rule traits + pipeline stage; market rules injected from the MarketProfile, not imported
L3  honba-engine (was honba-algo)   event loop, queue, handler/output, state cache, audit sink
    honba-indicators                pure compute
L4  honba-sim                       simulator, fill/latency/margin models, paper execution (from -testing)
    honba-strategy                  Strategy trait, context, runner, sample strategies
L5  honba-analytics                 stats, Monte Carlo, walk-forward
    honba-data                      catalog, Parquet import/export, loaders (merges -import and -export)
L6  honba-backtest                  runner wiring engine + sim + strategy + data (from backtest_node)
    honba-testing                   VecFeed, fixtures, assert helpers (dev-dependency only)
L7  honba-py (cdylib)               the only crate that depends on pyo3; exposes `honba._honba`
    honba-cli                       binary; no pyo3
```
Market packs are modules of `honba-market` behind cargo features (`india`, `null`), selected by run config and reached only through the
registry; no core crate names a pack directly. If a second real market or an outside contributor appears, the `india` module can be split
into its own crate or repo without changing any core crate.

Interpretation to confirm: "`honba-india` -> `honba-market`" is read as a rename of the L2 crate into the generic market crate that hosts
the India pack as a module, not as two crates (`honba-market` + `honba-market-india`) as in the v2/v3 draft text.

Status (verified by a test and audit pass 2026-10-01 after commits c562d6a .. 9280dd1 in `honba/`): Section 2 and Section 2b driven to completion.
- PASS: layering (no upward deps, no cycles, no core crate names a pack; `scripts/dependency_graph.py` checks production dependencies, dev-dependencies, pyo3 restriction, and prohibits the `india` feature in core crates; verified against `cargo metadata`), `cargo fmt`, `cargo clippy --workspace --all-targets -- -D warnings`, `cargo test --workspace` (unit tests and doctests pass), `RUSTDOCFLAGS="-D warnings" cargo doc --workspace --no-deps` with zero warnings and no output collisions (`honba-cli` bin has `doc = false`).
- Resolved items:
  - CI blockers resolved: `clippy::too_many_arguments` fixed; redundant intra-doc link targets resolved; doc output collision eliminated (`honba-cli` bin `honba` configured with `doc = false`).
  - **E0-S7 DONE:** `honba-market` defines generic market contracts (`MarketCalendar`, `CostSchedule` with named charges, `InstrumentRules`, `SymbolGrammar`, `ExpiryRules`, `MarginModel`, `SettlementRules`, `MarketProfile`, `MarketRegistry`); the `india` feature compiles and passes with or without `--no-default-features`; complete `null` pack and shared contract suite implemented; ADR 005 documented.
  - **E0-S1 DONE (PyO3 separation):** PyO3 completely isolated into `honba-py` cdylib exposing `honba._honba`; duplicate pyo3 shim and pyclasses removed from `honba-cli`; enforced by `scripts/dependency_graph.py` and CI.
  - Upward edges and stubs resolved: `honba-sim` decoupled from `honba-testing` (no upward dev-dependency); `honba-testing` re-exports of `honba-sim` removed and downstream consumers updated; empty 1-line import stubs in `honba-data` removed (`parquet_source` is the concrete reader); `honba-algo` infix dropped workspace-wide into `honba-engine`, `honba-strategy`, `honba-indicators`, etc.; ADRs 001–005 recorded.
  - Environment: system Python 3.14 compatible via `PYO3_USE_ABI3_FORWARD_COMPATIBILITY=1`; CI uses Python 3.12.
- Planned future crates (`honba-backtest`, `honba-ports`, `honba-risk`) deferred per roadmap sequencing: to be created when each has real code to hold rather than empty one-line stubs.

Sequencing:
1. [x] Split `-testing` into `honba-sim` + `honba-testing`; add `honba-py` (E0-S1); extract market contracts and rename `honba-india` (E0-S7).
2. [x] Enforce inward dependency layering and dev-dependency rules in `scripts/dependency_graph.py` and CI.
3. [x] Remove empty stubs in `honba-data`; drop `honba-algo` infix across crates; isolate PyO3 to `honba-py`.
4. Create `honba-ports`, `honba-risk`, `honba-backtest` when each has code to hold (as designated in subsequent phases).

---

## 3. Idea matrix: what we borrow, from whom, and what we skip

| Idea | Source | Verdict | Lands in |
|---|---|---|---|
| Formal adapter Protocol + capability manifest + lazy load + shared contract tests | OpenAlgo (plugin.json + loader; note it has no formal base class, a pitfall) | adapt | E1-S1 |
| Canonical typed order/position/funds/quote/depth models | OpenAlgo mapping layer | adapt | E0-S2, E1-S1 |
| Instrument master with dated snapshots, effective-dated lot/tick/freeze qty | OpenAlgo (weak: no history) | adapt, improve | E1-S3 |
| Strict symbol grammar with round-trip tests (not suffix guessing) | OpenAlgo | adapt | E1-S2 |
| Table-driven product/price-type mapping with property tests | OpenAlgo | adapt | E1-S4 |
| Smart order = target position, per-symbol lock | OpenAlgo | adopt | E3-S5 |
| Lot/freeze-quantity splitter | OpenAlgo | adopt | E3-S6 |
| Sandbox behind the same interface, per-adapter not global flag | OpenAlgo (sandbox), Nautilus | adopt design | E3-S7 |
| Webhook pipeline: ordered stages, hashed token, idempotency, cool-off | OpenAlgo | adopt | E5-S6 |
| MCP tool metadata, toolsets, read-only kill switch, scope map + CI drift test | OpenAlgo | adopt | E5-S1..S3 |
| Trust envelope for untrusted broker text | OpenAlgo | adopt | E5-S2 |
| Approval queue for placements (cancel/modify bypass it) | OpenAlgo, QuantDinger | adapt | E5-S4 |
| Market-data mode normaliser + silent-feed watchdog | OpenAlgo | adapt | E1-S6 |
| Golden-vector tests shared Rust/Python | OpenAlgo | adopt | E0-S2 |
| Audit stream with replicable state | barter-rs | adopt | E2-S4 |
| `Clock` trait live vs historic | barter-rs | adopt | E2-S3 |
| Trading-state kill switch; risk stage with typed refusals | barter-rs, Nautilus | adopt | E2-S1, S2 |
| Command channel + typed `EngineOutput` | barter-rs | adapt | E2-S5 |
| Order request/ack state machine, client order ids | barter-rs, Nautilus | adopt | E2-S6 |
| Terminal/Unrecoverable error traits, reconnecting stream | barter-rs | adapt | E1-S6 |
| Indexed instrument state (u32 index) | barter-rs | adapt later | E7-S2 |
| Reconciliation on startup/reconnect | Nautilus | adopt | E2-S7 |
| Component lifecycle FSM | Nautilus | adapt | E2-S8 |
| Pluggable seeded fill/latency/margin models | Nautilus | adopt | E3-S1..S3 |
| Data catalog with streaming Parquet batches | Nautilus | adapt | E4-S7 |
| Cache persistence, crash-only restart | Nautilus | later | E7-S3 |
| Declarative `hyperparameters()` + seeded sweep + pluggable fitness | Jesse | adopt | E4-S2, S3 |
| Look-ahead protection by construction + property test | Jesse | adopt | E4-S4 |
| Deflated Sharpe, PBO, blind holdout, MC shuffle | QuantDinger, Jesse | adopt | E4-S5, S6 |
| AST/allowlist/sandboxed-subprocess verification of AI strategies | QuantDinger | adopt | E5-S5 |
| Typed structured LLM decisions (veto/size-down only) | QuantDinger | adopt | E5-S7 |

| Net all costs into the effective price first, so "edge" is always after costs (`edge_after_costs` port over `CostModel`) | arbkit (fee enum, fees applied before the overround sum) | adopt | E3-S3 |
| Opportunity/Signal contract: legs, allocations, worst-case profit, profit_bps, structured rejection reason (None + reason, not an error) | arbkit | adopt | E8-S1 |
| Integer money (paise/ppm) and conservative rounding (floor payouts, round stakes up to lot multiples) | arbkit | adapt, audit first | E0-S6 |
| Lot- and freeze-quantity-aware sizing | arbkit (increment-aware allocation) | adapt | E3-S6, E8-S2 |
| Depth survival discount and latency in fills, same function at detection and fill time | arbkit | adopt | E3-S2 |
| Feed staleness circuit breaker on the simulated clock | arbkit | adopt | E2-S10 |
| Durable risk state (atomic snapshots), idempotent client order ids, fingerprinted fill ledger | arbkit | adapt | E2-S11 |
| Multi-leg sizing with closed-form baseline plus search that never does worse | arbkit | adapt, later | E8-S2 |

| Strategy manifest compiled by a `verify` step (API version, source hash, universe, subscriptions, driving timeframe, warm-up, schedules); backtest and live consume the same manifest | QuantDinger (Strategy API V2) | adopt | E0-S8 |
| Parameters declared in one line (`name type default desc range=a:b:step`) that drive both sweep space and UI form | QuantDinger | adapt into `hyperparameters()` | E4-S2 |
| Target-position intents with `reason`, `client_order_id`, `direction_mode`; status set incl. `deferred`/`unknown` | QuantDinger | adapt | E3-S5, E2-S6 |
| Backtest result carries an explicit `not_modelled` assumptions list | QuantDinger | adopt | E4-S3 |
| Cost-stress rerun (2x costs/slippage) and block-bootstrap Monte Carlo (keeps autocorrelation) | QuantDinger | adopt | E4-S6 |
| Backtest cache keyed by hash of strategy, params, dataset id, cost-model version | QuantDinger | adopt | E4-S1 |
| Evaluation never submits orders: strategies emit intents only, all through risk and the order gateway | QuantDinger (AGENTS.md rule) | adopt rule, skip its Kafka/Celery stack | E2-S2, E2-S5 |
| Computed vs broker-reported PnL reconciliation; paper-vs-live drift tracker (inferred from file names, unverified) | QuantDinger | adapt | E2-S7 |
| AI decision filter: typed probabilities and confidence, exits/SL/TP bypass it, every decision journaled | QuantDinger | adopt | E5-S7 |
| Report builder: fact snapshot, then LLM narrative constrained to the snapshot, then a quality gate | QuantDinger (`professional_report`, unverified) | adapt | E5-S9 |
| Per-source circuit breaker, rate limiter and cache for data feeds | QuantDinger (`data_sources`) | adapt | E1-S5, E1-S6 |
| Indicator render contract: pane/overlay, plots, sparse flip signals, zones/levels; indicators never emit execution columns | QuantDinger | adapt | E6-S5, E9-S3 |
| Visual flow builder with a code twin; flow graph and code are two views of one typed strategy IR | OpenAlgo (flow builder, behaviour only), QuantDinger (authoring states) | adapt, clean-room | E9-S1..S4 |
| Ops console: per-run mode, positions/orders/executions/logs/AI decisions, pause/stop/close controls | QuantDinger frontend (description only) | adapt | E9-S6 |
| Differential oracle: naive reference matcher vs optimised simulator on the same event stream; separate stop pool; visible-vs-total buckets | llc-993/matching-core (study only: no licence file, 4 commits, exchange-shaped; read via summarising fetch, tests/processors not read) | adopt ideas, write own code | E3-S1 |

Explicitly skipped: Nautilus message bus and engine replacement (use a recorder tap); OpenAlgo Flask/ZeroMQ stack, its 36-broker
breadth, API key in request body, in-memory rate limiting, global analyzer flag; QuantDinger
Celery/Redis/Postgres/Kafka split (maybe for long sweeps), billing/multi-tenant SaaS, crypto-exchange breadth, grid bots, Vue/Ant Design,
extra UI languages and mobile client (deferred); Jesse `watch_list`, crypto features; using barter or Nautilus as
dependencies (reference designs only). (v4: the OpenAlgo visual flow builder is no longer skipped as an idea; see E9. Its code stays off
limits under the AGPL clean-room rule.)

---

## 4. Delivery workflow (how epics, stories and issues are run)

Hierarchy: Milestone (release theme) > Epic (issue labelled `epic`, tracks stories as a task list) > Story (issue
labelled `story`, has acceptance criteria) > Task (optional child issue or checklist item, one PR each).

Labels: `epic`, `story`, `task`, `contract` (interface artifact), `impl`, `tests`, `docs`; area: `area:engine`,
`area:adapters`, `area:india`, `area:research`, `area:ai`, `area:api`, `area:perf`, `area:ci`; language: `lang:rust`,
`lang:python`; size: `size:S` (<=1 day), `size:M` (2-4 days), `size:L` (needs splitting before start);
flags: `cross-repo`, `breaking`, `needs-adr`, `unverified-source`.

Definition of Ready (story): contract artifact named; acceptance criteria testable; deps linked; cross-repo impact stated;
size <= M (split L first); source idea and licence note recorded if borrowed.

Definition of Done (story):
1. Contract merged first (trait/ABC/schema/stub) with docs.
2. Contract tests and golden vectors merged and green; failing test first for bug fixes.
3. Reference implementation merged; Python binding + `.pyi` if the behaviour is in Rust.
4. Schemas regenerated and diff-checked in CI (no drift).
5. Journal/audit output defined if the feature affects runs.
6. Independent review done; docs updated (`honba-docs` ticket if cross-repo).

PR checklist (add to `.github/pull_request_template.md`): contract-first commit order, tests deterministic, no wall clock/network,
schema drift check, dependency-graph check, no new broker wire types outside adapters, ADR linked if `needs-adr`.

CI gates to add (E0-S1 and E7-S1): make the Python job blocking; add wheel build via maturin; add stub check
(`.pyi` matches bindings); schema export diff check; MCP tool/scope drift test; dependency graph check synced with `Cargo.toml`;
credential-leak log test; shared golden-vector test (Rust and Python read the same JSON).

Issue templates to add: `epic.yml`, `story.yml` (fields: contract artifact, acceptance criteria, deps, cross-repo impact, source
idea + licence), `adr.yml`.

---

## 5. Milestones

| Milestone | Theme | Epics / stories | Exit criteria |
|---|---|---|---|
| M0 Spine | One contract spine from Rust to Python | E0 | `honba._honba` wheel builds in CI; canonical types round-trip via serde + Python; JSON Schemas generated; Python CI blocking; strategy contract parity test passes; market contracts extracted with India as a pack and a null-market pack passing the same suite |
| M1 India instruments and adapter contract | Nothing broker-specific leaks inward | E1-S1..S4 | Adapter Protocol + FakeAdapter + contract suite; instrument model with expiry/strike; symbol grammar round-trips; instrument master with dated snapshots from recorded fixtures |
| M2 Safe engine | Deterministic, controllable, auditable | E2-S1..S6 | Kill switch, risk stage, Clock trait, audit stream with replay test, command/output channel, order ack state machine |
| M3 Trustworthy research | Sweeps and validation that resist self-deception | E4, E6 | Backtest config/result/runner contracts; seeded sweep; look-ahead property test; overfit gates; journal schema v1; O(1) indicators |
| M4 Live-ready | Real brokers, realistic paper | E1-S5..S6, E2-S7, E3 | Reconciliation; session/rate-limit port; connector contract with watchdog; simulator + fill/latency/margin models wired to India costs; sandbox adapter |
| M5 Agent and API surfaces | Safe LLM and automation access | E5 | MCP schemas + scopes + drift CI; trust envelope; approval queue; webhook ingest; verification sandbox; OpenAPI |
| M3b Designer and result views (parallel to M3..M5) | E9 | E0-S8 first, then E9-S1..S4; S6 needs M2; S7 needs E4 | AlgoDesigner edits one strategy as graph or code with a lossless round trip for the supported subset and an explicit code-only mode; verify and run from the UI; result views show assumptions and overfit gates |
| M6 Scale and polish | Performance and ops | E7, E8 (optional) | Indexed state, catalog streaming, cache persistence, lifecycle FSM, multi-leg opportunity contract and cost-gated reference strategies (all optional) |

Order and dependencies: M0 first (everything depends on the spine). M1 and M2 can run in parallel after M0. M3 needs M0 and a
Python runner; it does not need M2. M4 needs M1 and M2. M5 needs M0 (schemas) and M2 (risk gate) for write tools.
E6 (indicators) is a parallel track. E7 items are pulled in only when profiling or operations demand.

Critical path: E0-S1 -> E0-S2 -> E1-S1 -> E2-S6 -> E2-S7 -> E3-S7.

---

## 6. Epics and stories

Each story: size, language, contract-first artifact, acceptance criteria, tasks (optional issues), dependencies, source.

### Epic E0: The contract spine (M0)
Goal: one typed contract chain, Rust -> Python -> schema, with CI enforcement. Fixes inventory gaps 1, 2, 8, 9, 10.

**Restructure follow-ups (from the verification, sequenced before new feature work)** (M total, rust+python, `area:ci`)
1. Fix CI blockers: clippy `too_many_arguments` (builder or scoped allow), redundant doc link targets, doc output-name collision.
2. Remove pyo3, `src/py/` and the duplicate `honba_bridge.py` from `honba-cli` (move `honba run` under `honba-py` or drop it); add graph-script
   rules: pyo3 only in `honba-py`, no core crate enables `india`, and dev-dependency checking (resolve the `honba-sim` <-> `honba-testing` edge).
3. Make the `india` feature real (E0-S7) and add a CI matrix step `cargo check -p honba-market --no-default-features`.
4. Populate `python/honba/_lib/__init__.pyi` and broaden `honba._honba` exports (stub check in CI, E0-S1).
5. Correct the ADRs and CLAUDE.md to describe the actual state (reopen status to "In progress"), add this roadmap or remove the links, fix stale
   `honba-algo*` names in READMEs and docstrings.
6. Fill or delete the `honba-sim` and `honba-data` one-line stubs; create `honba-backtest` when it has code.

**E0-S1 Create the `honba._honba` extension crate and stub pipeline** (M, rust+python, `area:ci`) — DONE
- Contract: crate `honba-py` (cdylib, pyo3) exporting module `honba._honba`; generated `_lib/__init__.pyi`.
- Acceptance: `maturin develop` and wheel build in CI; smoke test imports `honba._honba`; stub generated and diff-checked (`mypy.stubtest`);
  `honba-cli` PyO3 dependency and Python shim removed (PyO3 restricted strictly to `honba-py`).
- Tasks: [x] ADR: single cdylib vs feature-split; [x] crate + pyproject wiring; [x] stub generator and check in CI;
  [x] sync `scripts/dependency_graph.py` ALLOWED with `Cargo.toml` and enforce PyO3-only-in-py; [x] make Python CI blocking.
- Deps: none.

**E0-S2 Canonical domain types with one source of truth** (M, rust+python) — DONE 2026-10-01 (honba commits 7a44813..2628daa, ADR 006; test layout 17 commits after, ADR 007)
- Done: Rust serde is the source of truth; Python pydantic models (`honba.entities.wire`) are verified against it via shared `schema/golden/*.json` (valid, invalid and raw-text cases) and `honba._honba.canonical_json`; values are validated on deserialize; `OrderIntent` gains stop/stop-limit and cannot become an `Order` unless valid; NaN/inf refused on serialize; Rust/Python enum parity test.
- Known gaps (tickets): JSON Schema export + CI drift check (next step of the P2 chain); `BarFillEngine` ignores order type and trigger (stop/limit fill like market), plus an order before any bar fills at 0.0 (3 `#[ignore]` tests); CI `stubtest` does not cover `_lib/__init__.pyi`; `IntentRejection` not exposed to Python; `Trade.costs` is a single total; u64 timestamps exceed JS 2^53 (flag for OpenAPI/MCP); uncompiled orphan files (analytics cointegration/monte_carlo/tearsheet/walk_forward, strategy config/context, cli config).
- Cross-repo check (2026-10-01, read-only grep): no uses of `OrderType.STOP`, `OrderIntent(`, `PositionSide` or `"Long"/"Short"` in `honba-strategies`, `honba-adapters`, `honba-examples`; `honba-docs` has no wire-contract page yet (docs ticket: wire contract, stop/stop-limit intents, `Trade.costs`).
- Contract: serde JSON representation of `Event`, `Message`, `Order`, `OrderIntent` (add stop/stop-limit), `Trade` (with order id, costs),
  `Position`, `InstrumentId`, `Bar`; Python models generated from or verified against the Rust schema; `schema_version` field.
- Acceptance: golden-vector JSON shared by Rust and Python tests; round-trip Rust -> JSON -> Python -> JSON -> Rust is lossless;
  `Event` ids use `OrderId`, not `String`.
- Source: OpenAlgo (shared vectors, canonical models), barter (audit-friendly events).
- Deps: E0-S1.

**E0-S3 Strategy contract parity** (M, python+rust, `cross-repo`) — DONE 2026-10-02 (honba commits 3d04774..0c888ac, ADR 0008; independently reviewed, all 7 findings closed)
- Done: Python `Strategy` is an ABC with `on_quote`/`on_trade` no-ops and a `StrategyContext` (`self.ctx`); Rust hooks take `ctx: &mut dyn StrategyContext`; shared fixture `schema/conformance/strategy_contract.json` (6 scenarios, hand-derived values) run by Python, Rust and `honba._honba.run_strategy` and asserted equal; fill model has validated `flat_cost`/`cost_bps`; compat shim kept through 0.2, removed in 0.3.
- Known gaps (tickets): stub `python/honba/_lib/__init__.pyi` does not map to `honba._honba` so CI stubtest does not cover it (~79 errors would surface); `IntentRejection` not exposed to Python; venue rejects/cancels wait for E2-S6; `BarFillEngine` stop/limit gap (ADR 006); `RsiReversal` not in the fixture; uncompiled placeholder dirs `honba-strategy/src/{order,pnl,position,risk}`; no CI step for the schema drift check.
- Cross-repo (read-only checks 2026-10-02): `honba-strategies` catalog 129/129 unchanged, also with DeprecationWarnings as errors; `honba-adapters`/`honba-examples` unaffected; `honba-docs` needs a ticket: `docs/strategies/{writing_strategies,first-backtest,index}.md`, `tutorials/*`, `india/options.md` must describe `self.ctx`/`StrategyContext` (and that `on_stop` intents are not executed, contradicting the `on_stop: self.close_all()` example; these pages already describe APIs that never existed), and `book/src/architecture/message_flow.md` shows the old Rust `on_bar` signature.
- Contract: Python `Strategy` becomes an ABC/Protocol with on_start/on_bar/on_quote/on_trade/on_fill/on_stop and a `StrategyContext`
  (clock, portfolio, positions, instrument lookup, order submit); Rust trait matches; `OrderIntent` parity.
- Acceptance: a shared conformance fixture runs the same scripted event stream through Python and Rust strategies and compares intents
  and fills; catalog strategies in `honba-strategies` still pass (ticket if they need changes).
- Deps: E0-S2.

**E0-S4 Config schema export** (S, python)
- Contract: pydantic config models (strategy, backtest, live, research) -> JSON Schema files; `honba schema export`.
- Acceptance: `configs/*.toml` validate in CI; drift check; each config carries `schema_version`.

**E0-S6 Money and rounding audit** (S/M, rust+python, `needs-adr`)
- Contract: decide the canonical money representation (integer paise or fixed-point) and a rounding policy (conservative: floor
  receipts, round costs/stakes up, lot multiples). First step is an audit of current `Money`/price types (not yet inspected).
- Acceptance: ADR; property tests that costs never round in the trader's favour; golden vectors shared with Python.
- Source: arbkit. Risk: touches core types, so sequence before E2/E3 depend on them. Deps: E0-S2.

**E0-S7 Market contracts and the market-pack pattern** (M, rust+python, `needs-adr`, `breaking`, `cross-repo`) — DONE
- Contract: crate `honba-market` (L2; `honba-india` renamed, per the crate-layout section) defining `MarketCalendar`
  (sessions, holidays, settlement days), `CostSchedule` (returns a list of named charges, not fixed `stt/gst/...` fields),
  `InstrumentRules` (lot, tick, freeze quantity, price bands), `SymbolGrammar`, `ExpiryRules`, `MarginModel`, `SettlementRules`, and a
  `MarketProfile` bundle; a `MarketRegistry` (compile-time feature registration in Rust; Python entry points for runtime discovery);
  a "null market" pack; config field `market = "nse_bse"` in run configs.
- Rename `honba-india` to `honba-market`; lift the generic traits (`TradingCalendar`, `HolidaySource`, `CostModelSource`,
  `UniverseSource`) to the crate's top level and move the India implementation, data and tests into a feature-gated `india` module
  that implements them (plus a `null` pack for tests). Replace India-shaped `Segment` and `CostBreakdown` fields with generic
  segment and named-charge types (India names remain as constants in the pack). Python bridge and any direct `CostBreakdown.stt` users
  must migrate (measure first: list India types used outside `honba-india`, read-only).
- Acceptance: core crates have no dependency on the pack (dependency-graph check); one contract suite runs against the India pack and
  the null-market pack; results for existing India cost and calendar tests are unchanged.
- Risk: abstracting from one example can produce wrong traits. The charge list and `InstrumentRules` are the likeliest to need a
  revision, so keep the traits small and version them. Runtime dynamic loading of Rust libraries is out of scope (unstable ABI).
- Later option: split the `india` module into its own crate or repo (like `honba-adapters`) once a second market or an outside
  contributor appears.
- Deps: E0-S2. Blocks E1-S2, E1-S3, E2-S2 (rules), E3-S3, E3-S4, E3-S6.

**E0-S8 Strategy IR and manifest** (M, python first then rust, `cross-repo`) — the contract E9 depends on
- Contract: a versioned `StrategyGraph` JSON Schema (nodes, typed ports, edges, params, `schema_version`) and a `StrategyManifest`
  (API version, source hash, universe, subscriptions, driving timeframe, warm-up bars, schedules, direction mode, params with
  `range=a:b:step`). `honba verify <strategy>` compiles either a graph or a Python strategy to the manifest and rejects anything invalid.
- Both authoring forms (graph, code) and both run modes (backtest, live) consume the same manifest. Declarative `initialize` (no data
  access, no orders); orders only as intents (target-position forms preferred, with `reason` and `client_order_id`).
- Acceptance: golden graph and code fixtures compile to identical manifests; manifest drives a backtest and a paper run with the same
  intents on the same data; schema drift check in CI.
- Source: QuantDinger Strategy API V2 (idea only). Deps: E0-S2, E0-S3. Blocks E9-S1..S4, E4-S3.

**E0-S5 Error taxonomy and versioning policy** (S, rust+python)
- Contract: stable error-code enum (domain, adapter, risk, validation), `Unrecoverable`/`Terminal` marker traits, semver policy for
  schemas. Source: barter (error traits), OpenAlgo (missing taxonomy, avoid).
- Acceptance: enum published in schema; every public error maps to a code; docs page.

### Epic E1: Instruments and adapters (M1, M4)
Goal: broker-agnostic adapter contracts, a market-neutral instrument model, and the India market pack's instrument truth
(implemented in the `india` module of `honba-market` against the E0-S7 contracts). Fixes inventory gaps 5, 7.

**E1-S1 Adapter Protocol, capability manifest, registry, contract suite** (M, python, `cross-repo`) — DONE
- Contract: `honba.adapters.base` facade + `MarketDataAdapter`/`ExecutionAdapter` Protocols covering session/auth, place/modify/cancel/cancel-all, order/trade/position/holding books, funds/margin, quotes/depth/history, instrument master fetch, stream subscribe; typed canonical models; a capability descriptor (exchanges, products, price types, stream modes); lazy entry-point discovery in `registry.py`.
- Acceptance: `FakeAdapter` passes the shared contract suite; a second adapter (dhan/zerodha) passes it against recorded fixtures — DEFERRED (no broker code exists yet); broker wire types never appear outside the adapter package (import-lint test).
- Tasks: [x] confirm where the contract lives today in `honba-adapters/shared` (it does not); [x] draft ABC + models; [x] contract suite; [x] migration note for the 8 adapters (ticket, `cross-repo`).
- Source: OpenAlgo (function set, lazy loading; fix its lack of a formal interface).
- Deps: E0-S2.

**E1-S2 Instrument model and symbol grammar** (M, rust+python)
- Contract: extend `Instrument` with expiry, strike, option type, underlying, segment; `InstrumentId = venue + native token`; strict
  `parse`/`format` via the generic `SymbolGrammar` trait, with the equity/futures/options/index implementation in the India pack
  (`honba-market` `india` module, fno submodule); alias grammar for display. `Instrument` itself stays market-neutral (generic optional
  expiry/strike/option-type/underlying fields).
- Acceptance: property-based round-trip tests; ambiguous cases (symbol ending "CE", weekly vs monthly expiry) covered; Python binding + stub.
- Source: OpenAlgo grammar (adapt; its suffix-based classifier is brittle).
- Deps: E0-S2.

**E1-S3 Instrument master port with dated snapshots** (M, python then rust)
- Contract: `InstrumentMaster` port: `snapshot(as_of)`, `resolve(id)`, `search(query)`, effective-dated lot size, tick size and freeze quantity;
  storage in the data catalog with one snapshot per date so backtests resolve old symbols.
- Acceptance: recorded per-broker dump fixtures normalise to the same canonical table; daily refresh job spec; no network in tests.
- Source: OpenAlgo symtoken table (adapt; add history it lacks).
- Deps: E1-S1, E1-S2.

**E1-S4 Product, price-type and time-in-force mapping** (S, python+rust)
- Contract: canonical CNC/NRML/MIS, MARKET/LIMIT/SL/SL-M, TIF enums in `honba-messages`; table-driven per-adapter mapping declared in data.
- Acceptance: mapping round-trip property tests per adapter; unsupported combos rejected with typed errors.
- Source: OpenAlgo mapping layer.

**E1-S5 Session, token and rate-limit port** (M, python, `cross-repo`)
- Contract: `SessionProvider` (expiry-aware, daily broker token expiry), token-bucket `RateLimiter` per adapter and endpoint class, encrypted
  secret store (Argon2 for keys, Fernet-style for broker tokens, rotation procedure).
- Acceptance: fake clock tests for expiry and refresh; limits enforced across worker processes (not in-memory only); credential-leak log test.
- Risk: TOTP auto-login may breach some broker terms: needs per-broker review and an opt-in flag.
- Source: OpenAlgo (Argon2/Fernet, keepalive; avoid its in-memory limiter).

**E1-S6 Market-data connector contract** (M, rust+python)
- Contract: `honba-algo/connector` trait for subscribe/unsubscribe/stream with normalised modes (LTP/quote/depth), reconnect with backoff
  emitting `Reconnecting`/data-gap markers, silent-feed watchdog, `Terminal`/`Unrecoverable` error semantics.
- Acceptance: simulated feed test with drops proves markers and recovery; strategies can observe a gap marker.
- Source: barter (reconnecting stream, terminal traits), OpenAlgo (mode normaliser, watchdog).
- Deps: E0-S5, E1-S1.

### Epic E2: Safe, deterministic engine (M2, M4)
Goal: controllable, auditable, replayable. Fixes inventory gaps 3, 4.

**E2-S1 Trading-state kill switch** (S, rust+python)
- Contract: `TradingState {Active, Reducing, Halted}` and a `Command` message; halted keeps updating state but emits no orders; reducing allows
  only position-reducing orders. Exposed via Python API and CLI.
- Acceptance: engine test suppresses orders under Halted; Python binding + stub; state change appears in the audit stream.
- Source: barter, Nautilus. Deps: E0-S2.

**E2-S2 Risk stage with typed refusals** (M, rust+python)
- Contract: `RiskCheck::check(&State, requests) -> (approved, refused{reason})`, initial rules max notional, order rate, lot size, price band;
  market-specific rules (price bands, lot and freeze limits) come from the active `MarketProfile` (`InstrumentRules`); the risk
  crate never imports a pack.
- Acceptance: refusals are events; property tests; rules configurable via schema-validated config.
- Deps: E2-S1, E1-S2.

**E2-S3 `Clock` trait** (S, rust): live wall clock and historic event-driven clock behind a trait; lint/test forbids wall-clock calls in domain code.

**E2-S4 Audit stream and replay** (M, rust+python)
- Contract: `AuditTick{seq, ts, event, output}` with monotonic sequence and `schema_version`; optional sink trait; journal writer in Python.
- Acceptance: replay test rebuilds identical final state from the log; journal file format documented (feeds E4-S1).
- Source: barter. Deps: E0-S2.

**E2-S5 Command channel and typed `EngineOutput`** (M, rust, `breaking`, `cross-repo`)
- Contract: `Handler::on_event` returns typed output (orders, cancels, state changes); handlers can emit events back to the queue
  (currently impossible). Public-trait change: audit bindings, adapters, docs first.
- Deps: E2-S3, E2-S4.

**E2-S6 Order request/ack state machine** (L -> split, rust+python, `cross-repo`)
- Split: (a) `OrderState` FSM and client order ids in `honba-messages`; (b) `ExecutionEngine` returns account events (ack, reject,
  fill, cancel-ack) instead of `drain_fills`; (c) adapter contract emits the same events; (d) simulator and paper emit identically.
- Acceptance: live-shaped and sim-shaped event streams pass one conformance test; rejects and partial fills handled.
- **Hard requirement: backtest, paper and (later) live gateways share ONE order-state machine.** The `OrderState` FSM and its event types
  (submitted, in-flight, acknowledged, rejected, partially filled, filled, cancel-requested, cancelled, expired) live in `honba-messages`/
  the engine, not in any gateway. The simulator levels (E3-S1), the paper `SandboxAdapter` (E3-S7) and every live adapter emit events
  through the same machine; gateways only translate to and from it. A gateway that needs its own order states is a design bug. One
  shared conformance suite (same scripted scenarios: market, limit, partial fill, reject, cancel race, expiry) must pass for every gateway.
- Source: barter, Nautilus. Deps: E1-S1, E2-S5.

**E2-S7 Reconciliation on startup and reconnect** (M, rust+python)
- Contract: `generate_*_reports` adapter port; engine diffs broker order/fill/position reports against the cache, emits synthetic events, times
  out stale open orders.
- Acceptance: per-broker recorded-fixture scenarios (missed fill, ghost order, position drift).
- Source: Nautilus. Deps: E2-S4, E2-S6, `cache/` implemented (add story E2-S9 below).

**E2-S10 Feed staleness circuit breaker** (S, rust): per-instrument/per-feed quote age; blocks new orders when a needed feed is stale
(`stale_after` config). Driven by the simulated `Clock` so it stays deterministic. Essential for multi-leg work (cash vs futures, NSE vs BSE).
Source: arbkit. Deps: E2-S2, E2-S3.

**E2-S11 Durable risk state and idempotent fill ledger** (M/L, rust+python): risk counters (daily loss, open positions/hedges, per-venue
capital) persisted with atomic write (tmp + rename); in-flight orders keyed by client order id; fills applied idempotently via content
fingerprints so reconnect replays cannot double-count. Feeds E2-S7 reconciliation. Source: arbkit. Deps: E2-S2, E2-S5, E2-S6 (needs the
event feedback loop).

**E2-S8 Component lifecycle FSM** (S/M, optional): uniform start/stop/health for engine, adapters, strategies; illegal transitions rejected.

**E2-S9 Cache/state store contract** (M, rust): implement `honba-algo/cache` (orders, positions, instruments, last quotes) with a query trait
that the strategy context uses; persistence hook left for E7-S3.

### Epic E3: Execution realism (M4)
Goal: credible paper and backtest fills with Indian costs. Fixes inventory gap 3.

**E3-S1 Simulator core: one own-order matching core with selectable fidelity levels** (L -> split into S1a..S1d, rust, `needs-adr`)
- Premise: honba needs a simulator that matches ITS OWN orders against an OBSERVED market (bars, quotes, ticks, depth); it does not need a
  multi-participant exchange matching engine (no other users, no balances, no sharding). Reviewed `llc-993/matching-core` as a reference:
  it is exchange-shaped, has no licence file (cannot be reused) and lacks queue-position, latency, observed-book consumption and India
  rules, so honba writes its own small matcher. Study-only ideas: one `OrderBook`-style trait with a naive reference and an optimised
  implementation checked against each other (differential oracle), a separate stop/trigger pool, visible-vs-total quantity buckets,
  Criterion benchmarks. `arbkit-match` is an event registry, not an order matcher (and was not read).
- Shape: a per-instrument own-order book inside `honba-sim` behind the existing `ExecutionEngine` port (no port change); a `FidelityLevel`
  config selects the level, and every level emits the same order-state events (E2-S6), so strategies never see the difference.
  - **L1 Bar fills (S1a)**: fill at bar close/next open with slippage and costs (today's `BarFillEngine`, hardened); suitable for
    bar-based strategies. Market, limit and stop orders, gap handling, partial fills by bar volume cap, rejects (tick size, lot size,
    price band from the `MarketProfile`).
  - **L2 Quote/tick matching (S1b)**: marketable orders walk the observed top-of-book and displayed depth; resting limit orders fill
    when the price trades through, or at the level via the queue-position model; stop/trigger orders fire from ticks; IOC/FOK/day
    validity; latency and ack delay from E3-S2; depth survival discount from E3-S2. Needed for limit-order quality, scalping and arbitrage.
  - **L3 Full book replay (S1c, optional)**: only if full-depth tick data is available; queue modelling with real depth. Not scheduled
    until a data source exists (broker feeds usually give only a few levels, so this would add false precision).
  - **Differential oracle (S1d)**: a naive reference matcher in tests, compared against the production simulator on identical event
    streams (randomised, seeded).
- Acceptance: one conformance suite runs every level; same event stream + same seed gives byte-identical fills and order events; order
  state transitions validated by the E2-S6 state machine (illegal transitions rejected); results feed the audit stream (E2-S4); fills
  carry named charges from E3-S3.
- Data needed to decide L2 priority: what market data is available (bars only, broker ticks, depth). Open question for the owner.
- Deps: E2-S6(a) for the state machine types, E0-S7 (tick/lot/band rules), E0-S2.
**E3-S2 Seeded fill and latency models** (M, rust+python): pluggable `FillModel` and `LatencyModel` (fill probability, slippage, ack delay, queue-position model for resting limit orders), seeded RNG, config-driven; plugs into the E3-S1 levels.
Add a depth survival discount (resting depth decays while the order is in flight), implemented as one function shared by signal sizing and simulated fills so they cannot disagree. Source: arbkit.
**E3-S3 Wire the market `CostSchedule` into fills** (S/M, rust): costs applied per fill from the active profile's effective-dated
`CostSchedule` (India pack: STT, GST, stamp duty, SEBI and exchange fees, brokerage as named charges); `Trade` carries a list of named
charges. Depends on E0-S7.
Also add an `edge_after_costs(legs)`/`effective_price` port so strategies and the risk stage see costs before acting (e.g. 30bp gross edge minus statutory charges). Prerequisite for any arbitrage work. Source: arbkit.
**E3-S4 Margin model and MIS auto square-off** (M, rust): product-aware margin checks, exchange-timed square-off from the active `MarketProfile` calendar and `MarginModel`.
**E3-S5 Smart order (target position)** (M, rust+python): target-position execution algo with per-instrument serialisation and position-cache invalidation. Source: OpenAlgo (adopt idea).
**E3-S6 Lot and freeze-quantity splitter and lot-aware sizing** (S/M, rust): uses instrument master data (E1-S3); sizing rounds to lot multiples and slices above the freeze quantity. Honba has no lot-size or freeze-quantity code today.
**E3-S7 `SandboxAdapter` (paper on live data)** (M, python+rust): implements the adapter interface with virtual capital and isolated storage, so live vs paper differs by adapter, not a global flag.
- **Reuses the E3-S1 matching core (same code, same fidelity level setting) and the E2-S6 order-state machine**: the sandbox only adds a live
  quote/tick source, virtual funds and isolated storage on top of the same simulator used for backtests. No separate paper fill logic.
  Without a market-impact model, paper results for sizes above displayed depth are optimistic; document this.
- Deps: E1-S1, E2-S6, E3-S1..S4. Source: OpenAlgo sandbox design, Nautilus.

### Epic E4: Trustworthy research pipeline (M3)
Goal: sweeps and validation that resist overfitting, Python-first. Fixes inventory gaps 6, 8. Detailed phase plan (B0-B8) is in the session
scratchpad design doc `workstream_b_design.md`; fold into these stories during refinement.

**E4-S1 Journal schema v1** (S, python+rust): JSON (Parquet optional) run/trial/event records with `schema_version`, seeds, config hash, dataset id, git rev, trial count. Consumes E2-S4 audit output. Add a result cache keyed by hash(strategy source or graph, params, dataset id, cost-model version) so identical runs are not recomputed; AI decisions are journaled with probabilities and confidence.
**E4-S2 Declarative `hyperparameters()` on `Strategy`** (S, python): typed bounded int/float/categorical -> JSON Schema; additive. Also
accept the one-line comment form (`# @param period int 20 desc range=5:100:5`) so graph nodes (E9-S1), code and the sweep read one
declaration; defaults in code must match declared defaults (checked by `verify`). Source: Jesse, QuantDinger.
**E4-S3 Backtest config, result and runner contracts** (M, python first): config schema (E0-S4), `BacktestResult` model, Python event-driven runner over the Rust engine (via `_honba`) and a vectorized pre-filter; CLI `honba backtest`. The result carries an `assumptions` block with a `not_modelled` list (for India: market impact, circuit-limit halts, queue position at L1, and so on) and the timing rule used ("confirm on close, fill at next open"), shown unchanged in the UI (E9-S7). Runs take the E0-S8 manifest.
**E4-S4 Look-ahead guarantees + parity property test** (M): strategies see completed bars only; truncating future data never changes earlier decisions; vectorized vs event-driven agree on a fixture. Source: Jesse.
**E4-S5 Seeded sweep with pluggable fitness** (M, python): pinned seeded sampler (Optuna as an optional `research` extra), fitness in {Sharpe, Calmar, Sortino, Omega}, every trial journaled. Deps: E4-S1..S3.
**E4-S6 Overfitting gates** (M, python then rust): deflated Sharpe, PBO, walk-forward efficiency, one-shot blind holdout (trial count read from journal), MC by block bootstrap of trades/returns (keeps autocorrelation; plain shuffling kept as a baseline; fills `monte_carlo.rs`), a cost-stress rerun at 2x costs and slippage using the effective-dated India `CostSchedule` (pass/fail flag in the result), candle perturbation later (respect tick size and circuit limits). Source: QuantDinger, Jesse.
**E4-S7 Data catalog with streaming batches** (M, rust): Parquet catalog keyed by instrument/type/date, chunked streaming into the engine, dataset ids in the journal. Source: Nautilus.
**E4-S8 Multi-timeframe aggregation in the engine** (M, rust): completed higher-timeframe bars only, bucketed by the NSE session and holiday calendar. Source: Jesse.

### Epic E5: Agent and API surfaces (M5)
Goal: safe machine access; everything typed, scoped, audited. Fixes inventory gap 9.

**E5-S1 MCP tool schemas and metadata** (S/M, python): each tool declared once with a JSON Schema derived from E0 types, toolset (orders/account/market/research/utility), read/destructive annotations, risk class, read-only kill switch, toolset filter. Source: OpenAlgo.
**E5-S2 Trust envelope** (S): all broker/LLM/instrument text returned to a model is wrapped and marked untrusted.
**E5-S3 Scope map and drift test** (S): explicit tool-to-scope map (read:market, read:account, write:orders, research); CI fails on drift; write scope requires fresh second factor; audit log keyed by token id; `retry_safe` semantics for timed-out writes.
**E5-S4 Approval queue for order placement** (M): placements held for approval by default for agents; modify/cancel/status bypass the queue (stale queued actions against triggered orders are unsafe); goes through the E2-S2 risk stage.
**E5-S5 AI strategy verification sandbox** (M, python): AST check, import allowlist, subprocess with resource limits and no network, deterministic smoke test, look-ahead check, red-team tests; same validation as hand-written strategies. Source: QuantDinger. Populates `ai/verification/*`.
**E5-S6 Webhook ingestion** (M, python, optional): stage order kill switch -> IP allowlist -> parse -> live gate; hashed token; size cap; idempotency and cool-off TTL caches; identical responses for unknown/malformed tokens; orders pass the risk stage and journal. Source: OpenAlgo.
**E5-S7 Typed LLM decisions and journal roles** (S): pydantic schemas for agent outputs; LLM may veto or size down only; advisory notes marked non-evidence.
**E5-S9 Report builder with a fact snapshot and quality gate** (M, python, optional): immutable snapshot of run facts (metrics, journal ids,
assumptions) is the only input to the LLM narrative; a quality check rejects any number in the narrative that is not in the snapshot.
Feeds the tearsheet and E9-S5. Source: QuantDinger `professional_report` (unverified, from file names). Deps: E4-S1, E5-S7.

**E5-S8 OpenAPI control plane** (M, python, optional): versioned `/v1`, header auth, stable error codes, generated from E0 schemas. Only after M2 gate for write endpoints.

### Epic E6: Indicators O(1) (parallel, M3)
Findings: the Rust indicator crate is already O(1); all offenders are pure Python under `python/honba/strategies/indicators/`.

| Batch | Scope | Status |
|---|---|---|
| 1 | `_rolling.py` helpers; RollingStd, Bollinger, ZScore, Wma (also speeds hma, coppock, std, bandwidth, %b, hist vol) | Done: commits 6372528, 510e0d4, efca4bd, 7c9a325 after three review rounds |
| 2 | Paired moments: correlation, beta, covariance, lsma, linear_regression | Committed 6a91432; independently reviewed: no correctness bug, worst error 5.2e-12. Open findings: slower than the old O(n) code below about period 20 (about 50-100 for lsma; default lsma period 25 is about 2x slower), one vacuous test, optimistic regression-std docstring (measured 2.9e-8 vs claimed 1.5e-8), no CHANGELOG entry, slow tests need a `slow` marker; `honba-strategies` not checked for users. Behavior changes: NaN windows now report NaN (old correlation returned 1.0, old beta 0.0 in some cases); full unit suite now about 9.5 minutes |
| 3 | Monotonic deque helper: donchian, williams_r, stochastic, kdj, fibonacci, fisher, stoch_rsi, choppiness, chande_kroll, ichimoku; aroon needs an index-aware variant | Not started |
| 4 | Plain sums: vwma, chaikin_money_flow, money_flow_index, vortex, cmo, ultimate_oscillator, chaikin_volatility | Not started |
| Won't fix | ALMA (true O(n) dot product), Hurst (range term); connors_rsi and cci are low priority | Decided |

**E6-S5 Indicator render contract** (S/M, python+schema): each registered indicator declares presentation next to its compute: `pane`
(overlay or separate), per-output plot kind (line, histogram, marker), colour role (theme token, not a hex), sparse signal markers that
fire only when a condition flips, optional zones/levels. Validated in tests: output length matches input, no NaN/inf after warm-up,
look-ahead check. Indicators never emit execution columns; signals become orders only through a strategy. Consumed by the chart (E9-S3),
MCP and alerts. Source: QuantDinger indicator guide. Deps: E0-S4.

Lessons baked into every batch (from batch 1 review rounds): oracle = old O(n) code plus exact-Fraction reference; NaN/inf/overflow (>1e150)
convention; outlier residue and stale-shift tests; rate-limited rebuilds; adversarial rebuild-rate test; `update_bar`, reset, ddof and
period-300 boundary tests; randomized tests for indicators without Jesse golden vectors.

### Epic E7: Scale, ops and docs (M6, pull-based)
**E7-S1 Docs and contract publishing** (S, continuous): `docs/*` filled from schemas; architecture pages; ADR log; `honba-docs` sync ticket.
**E7-S2 Indexed instrument/venue state** (M, rust): `u32` index newtypes internally, strings at the boundary; only if profiling justifies.
**E7-S3 Cache persistence and crash-only restart** (M/L, rust+python): pluggable store, restart -> reload -> reconcile (needs E2-S7).
**E7-S4 Concurrent backtests** (M, rust): shared immutable market data via `Arc`, one engine per run; fills `concurrent/` stub.
**E7-S5 Recorder tap on the event flow** (S): lightweight replacement for a Nautilus-style message bus (journal, MCP live tap).

### Epic E8: Multi-leg and arbitrage primitives (optional, M6; after M2 and E3)
Goal: general contracts for multi-leg opportunities, cost-first, fail-safe. Borrowed from arbkit's design (sports/prediction markets),
re-targeted to Indian instruments. Do not start before E0-S6, E2-S2, E3-S3 and E1-S3 exist.

**E8-S1 Opportunity/Signal contract** (M, rust+python): `Opportunity{legs, allocations, worst_case_profit, profit_bps, edge_after_costs}`
and a structured `Rejection` reason enum (no edge, stale quote, lot granularity, depth, budget, freeze qty, risk refusal). "No edge" is a
normal result, not an error. Serialised to JSON schema and journaled (accepted and rejected) so MCP/LLM agents can read them.
**E8-S2 Multi-leg sizing** (M): lot-multiple allocation with a closed-form baseline plus bounded search that never returns worse than the
baseline; shared with fill simulation. Most Indian strategies are 2 to 4 fixed-ratio legs, so this is mostly lot arithmetic.
**E8-S3 Multi-leg execution and legging-risk handling** (L -> split, rust): leg sequencing, partial-fill and failed-second-leg policy
(hedge, unwind, or cancel), driven by the order state machine (E2-S6). arbkit's handling of this was NOT verified (it looks like
atomic equal-payoff hedging plus a ledger), so design from first principles.
**E8-S4 Reference strategies with honest cost gates** (M, python): calendar spread and cash-futures basis as worked examples through
the validation pipeline (E4), each with a cost-feasibility report. Options parity/box strategies only after options metadata exists.

### Epic E9: Frontend and AlgoDesigner (parallel track, M3 onward)
Goal: the web apps in `honba-frontend` (Screener, Workbench, AlgoDesigner, Simulator, Researcher) consume the same typed contracts as
the CLI and MCP, so nothing is hand-copied. Stack decisions already in `honba-frontend/MODERNIZATION_PLAN.md` (React 19, Zustand,
dockable panels, TanStack Table, React Flow) stand; this epic adds the product contracts. No QuantDinger or OpenAlgo frontend code or
assets are used (source-available and AGPL respectively).

**AlgoDesigner principle: two views of one strategy, never two strategies.** The flow canvas and the code editor are both editors of the
E0-S8 `StrategyGraph` / Python strategy pair, selectable per strategy via a Flow | Code | Split toggle, and both verify to the same
manifest.
- Canonical form: the typed `StrategyGraph` IR (JSON, versioned). The Code view is real, readable Python generated from the graph with a
  fixed builder style (declarative nodes plus `on_bar` logic), not an opaque dump. Example shape: indicator node -> `IndicatorBank`
  entry, condition node -> boolean expression, sizing node -> `order_target_percent`, risk node -> stop/take-profit/time exit.
- Round trip is guaranteed only for the supported subset: Python that parses back into graph nodes (a recogniser built on `ast`, same
  parser the E5-S5 verifier uses) opens in Flow view. Anything outside the subset (loops, custom classes, arbitrary imports) marks the
  strategy **code-only**: Flow view becomes read-only with the unsupported lines highlighted, and it can be re-attached after editing.
  No silent loss: the UI states which direction is lossless for the current strategy.
- Hand-written strategies from `honba-strategies` open in Code view and in Flow view when they fit the subset.
- Editing either side updates the other live; conflicts are impossible because the graph is rebuilt from the code (or code regenerated
  from the graph) on every accepted edit, and generated regions are marked so manual edits in them are detected.

**E9-S1 StrategyGraph editor model and node catalogue** (M, typescript+schema): node types generated from the Python registries, not
hand-listed: data/universe, indicator (all registered indicators with their param specs and `range=` bounds), transform, condition,
logic (and/or/crossover/threshold), position sizing, risk exit, order intent, schedule, benchmark. Typed ports reject invalid connections;
node params get auto-forms from the manifest param declarations. Canvas via `@xyflow/react`. Acceptance: any valid graph fixture
validates in the UI and in `honba verify` with identical errors. Deps: E0-S8, E6-S5. Source: OpenAlgo flow builder idea (clean-room).
**E9-S2 Graph -> Python code generation** (M, python): deterministic, formatter-clean output (ruff), stable ordering and names so diffs are
small; `reason` strings carry the node label; params emitted as one-line declarations. Acceptance: golden graph -> golden code; generated
strategies pass the strategy contract tests (E0-S3) and backtest identically to a hand-written equivalent. Deps: E0-S8.
**E9-S3 Code -> graph recogniser and code-only fallback** (M/L -> split, python): `ast` based, accepts exactly the generated subset plus
documented idioms; reports unsupported spans with line ranges. Acceptance: property test `graph -> code -> graph` is the identity for
the whole node catalogue; unsupported code never gets a lossy graph. Deps: E9-S2.
**E9-S4 Verify and run from the designer** (M, typescript+python): a Verify button calls `honba verify` (manifest, look-ahead check,
sandbox from E5-S5) and shows errors on nodes and on code lines; Run sends the manifest to the backtest runner (E4-S3). Strategy
versions with diff and restore; a publish-readiness checklist before backtest hand-off. Deps: E9-S2, E4-S3, E5-S5.
**E9-S5 AI assistant in the designer** (M, optional): chat with three visibly separate result kinds: discussion, valid candidate
(already verified, shown as a diff), validation error. The assistant edits the graph or the code, never both at once; output is untrusted
and goes through the same verifier (E5-S5, E5-S7). Deps: E9-S4.
**E9-S6 Workbench operations console** (M, typescript): per-run mode badge (paper, signal-only, live), positions, orders, executions,
strategy logs, AI decision records; explicit pause, stop and close-position controls with status feedback; live controls honour the
kill switch and approval queue (E2-S1, E5-S4). Mode is a property of the run, never a global toggle. Deps: E2-S1, E5-S4, E3-S7.
**E9-S7 Simulator/Researcher result views** (M, typescript): equity, drawdown, trade log and chart review from journal data; an
assumptions block (`not_modelled` list); trust badges for deflated Sharpe, PBO and holdout consumed; sweeps and walk-forward run as
async jobs the user can leave and return to. Deps: E4-S1, E4-S3, E4-S6.
**E9-S8 Shared contract client** (S, typescript): TypeScript types and a typed client generated from the E0 JSON Schemas / OpenAPI;
schema drift check in CI. Deps: E0-S4, E5-S8.

Indian-market feasibility (inference, NOT from arbkit; rates are effective-dated, check `CostModel`):
| Use case | Realistic? | Note |
|---|---|---|
| Calendar spreads (futures vs futures) | Yes | Lower costs (STT on sell side only, stamp on buy side); moderate legging risk |
| Cash-futures basis / cash-and-carry | Only at size | STT on the cash leg plus funding and margin eat a thin edge near expiry |
| Put-call parity, box, conversion/reversal | Selective | Options STT is heavy, especially on exercised ITM expiry; 4 legs, freeze quantity, liquidity only near-ATM Nifty/BankNifty |
| NSE vs BSE same stock | Marginal | Edges about 1-3 paise; per-leg statutory charges dominate; needs a latency edge |
| Index vs constituents | Mostly no | 50 cash legs plus a future; sampled baskets become tracking bets |
| ETF vs NAV | Mostly no | Creation/redemption limited to authorised participants |

---

## 7. Cross-repo impact register (flag now, update in the same milestone)

| Change | Affects | Action |
|---|---|---|
| `Strategy` becomes ABC + context (E0-S3) | `honba-strategies` catalog, `honba-examples`, `honba-docs` | ticket per repo; compat shim for one minor version |
| Adapter Protocol (E1-S1) | all 8 `honba-adapters` packages + `shared` | migration guide; contract suite ships as a test dependency |
| `Handler::on_event` returns output (E2-S5), ack events (E2-S6) | bindings, adapters, testing crate | `breaking` label; ADR first |
| Schema and error codes (E0-S4, E0-S5) | `honba-frontend`, `honba-docs` | publish schemas as an artifact |
| MCP tool schemas (E5) | `honba-docs`, `honba-frontend` | docs ticket |
| Market-pack extraction (E0-S7): `honba-india` renamed `honba-market`: generic traits at top level, India as a feature-gated module | `honba-adapters` (lot/tick/freeze sourcing), `honba-strategies` (India helpers), `honba-examples`, `honba-docs`, Python bridge | list India types used outside `honba-india` first; migration note; CLAUDE.md wording (see risks) |
| Strategy IR and manifest (E0-S8), indicator render contract (E6-S5) | `honba-frontend` (AlgoDesigner, charts), `honba-strategies` (strategies must pass `verify`; subset recogniser), `honba-examples`, `honba-docs` | publish `StrategyGraph` and manifest schemas; ticket per repo; frontend consumes generated types (E9-S8) |
| Crate split and renames (section 2b) | `honba-py`/maturin build, `honba-cli`, CI, `honba-docs` architecture pages | ADR, then one mechanical PR per move |

---

## 8. Risks and open decisions (write ADRs)

1. `scripts/dependency_graph.py` is dirty in the working tree: who owns the pending change before we sync its `ALLOWED` map?
2. Single cdylib (`honba-py`) or per-crate bindings? (E0-S1)
3. Is a Python-only path acceptable for research (E4) until `_honba` ships, or does E0-S1 block all Python-facing Rust work?
4. Python `Strategy` -> ABC is a breaking `__init__` change: acceptable with a shim?
5. Which schema toolchain: generate JSON Schema from Rust (schemars) or from pydantic, and verify the other side?
6. Holdout policy (E4-S6): fixed date ranges per instrument, who may reset consumption?
7. Sandbox strength for AI strategies: subprocess with rlimits or containers?
8. Optuna core dependency or `research` extra; pinned version.
9. TOTP auto-login legality per broker; default off.
10. Licence compatibility of LGPL/Apache/AGPL-inspired designs with honba's LICENSE; keep a clean-room log for OpenAlgo-inspired stories
    (spec written from behaviour, reviewer confirms no code copied).
11. Unverified claims (Nautilus fill/reconciliation details, barter trait names, QuantDinger internals, arbkit sim/exec/engine
    internals and legging handling): re-verify in refinement of the relevant story.
12. Money representation (E0-S6): current `Money`/price types were not inspected; decide integer paise vs fixed-point before E3/E8.
13. Market-pack pattern (E0-S7): generic traits vs a single India example may mis-generalise (charge list, `InstrumentRules`). Prove with
    the null-market pack; when do we move the pack to its own repo (trigger: second market or outside contributor)?
14. Owner's CLAUDE.md says Indian-market rules belong in "the `india` module/crate". The pack pattern keeps that intent (India rules stay
    isolated) but changes the crate from a core peer into an implementation of generic contracts; propose a small wording update for the
    owner to approve (not edited).
15. Crate layout ADR (section 2b): merge `honba-market` into `honba-ports`? naming (`honba-engine`) now or never? when to promote kernel
    modules to crates?
16. Is arbitrage a product goal at all? If not, keep E8 unscheduled and use only the cost-first ideas (E3-S3) and staleness breaker (E2-S10).
17. AlgoDesigner round trip (E9): which Python subset is recognisable, and is "code-only" an acceptable fallback for everything else? The
    graph IR is the likeliest thing to be too small or too big at first; keep node types few, generated from the registries, and version
    the schema. Lossless two-way sync for arbitrary Python is out of scope.
18. Should `StrategyGraph` JSON be the stored source of truth, or the Python file with the graph derived on open? Recommendation:
    store the Python file (diffable, what `honba-strategies` already holds) plus a sidecar graph only when the strategy was created in
    Flow view; confirm with the owner.
19. Frontend and QuantDinger/OpenAlgo licences: UI is clean-room from behaviour and screenshots only; keep a log like the OpenAlgo one
    (item 10).

---

## Appendix A: Story template (copy into `.github/ISSUE_TEMPLATE/story.yml`)

```
Title: [E?-S?] <verb> <thing>
Epic: #<epic issue>    Milestone: M?    Size: S|M    Lang: rust|python|both
Contract artifact (write this first): <trait / ABC / schema / stub path>
Acceptance criteria:
  - [ ] ...
Tasks (optional child issues):
  - [ ] contract + docs
  - [ ] contract tests / golden vectors (failing first)
  - [ ] reference implementation
  - [ ] binding + .pyi (if Rust)
  - [ ] schema regenerated, no drift
Depends on: #...
Cross-repo impact: none | honba-adapters | honba-strategies | honba-examples | honba-docs | honba-frontend
Source idea / licence note: <project, verdict, clean-room? y/n>
```

## Appendix B: Optional `gh` bootstrap (NOT RUN; review before use, target repo `honba-labs/honba`)

```
# labels
for l in epic story task contract impl tests docs area:engine area:adapters area:india area:research \
         area:ai area:api area:perf area:ci lang:rust lang:python size:S size:M size:L cross-repo breaking \
         needs-adr unverified-source; do gh label create "$l" --repo honba-labs/honba --force; done

# milestones
for m in "M0 Spine" "M1 India instruments and adapter contract" "M2 Safe engine" \
         "M3 Trustworthy research" "M4 Live-ready" "M5 Agent and API surfaces" "M6 Scale and polish"; do
  gh api repos/honba-labs/honba/milestones -f title="$m"; done

# epics (one per epic; stories are created later from the template and linked in the epic task list)
gh issue create --repo honba-labs/honba --label epic --milestone "M0 Spine" \
   --title "[E0] The contract spine" --body-file epics/E0.md
```
