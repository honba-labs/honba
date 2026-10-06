Archived 2026-10-06; superseded by docs/ROADMAP.md. Kept for history; do not update.

# Honba platform plan: async Rust core, flexible Python SDK, one versioned API

Status: DRAFT, 2026-10-03. Companion to [ROADMAP-borrowed-ideas.md](ROADMAP-borrowed-ideas.md) (DRAFT v4, 2026-10-01).
This document is the **architecture of record**. It supersedes roadmap section 2b (crate arrangement) where the
two disagree, and adds epic **E10 (async runtime)** and **E11 (versioned API surface)** plus the Rust/TypeScript
binding work that roadmap E9-S8 only sketches.

Nothing here has been filed as issues. Nothing here has been implemented.

---

## 1. The ask, and the one correction it needs

The ask, restated:

1. A **multi-threaded async Rust core** that defines everything.
2. A **super-flexible Python SDK** surrounding it.
3. The Rust core serves **both** the Python SDK and `honba-frontend`, the latter directly over **REST and WASM**.
4. A **versioned API/binding** through which every Rust-internal structure and interface is exposed to Python.
5. An evaluation of pulling `honba/python` out into a sibling `honba-python` repo.

One correction, stated up front because it shapes everything below:

> **The event loop stays synchronous and single-threaded. Async and threads live at the edges and across runs,
> never inside a run's event loop.**

This is not conservatism, it is the only design that keeps the four invariants the workspace already depends on:
deterministic replay, backtest/live parity, look-ahead protection, and byte-identical golden vectors. A multi-threaded
event loop with concurrent handler dispatch is a nondeterministic event loop, and a nondeterministic event loop
cannot produce the same fills twice, which means no reproducible backtest, no shared golden vectors between Rust
and Python, no meaningful Monte Carlo, and no trust in any result the platform prints.

So "multi-threaded async Rust core" is true at the level of **the process and the platform**:

| Concern | Where the concurrency lives | Threads |
|---|---|---|
| Broker feeds, HTTP, Parquet reads, journal writes | edge adapters behind ports | tokio async tasks |
| Server request handling, WebSocket fan-out | `honba-api-rest` | tokio worker threads |
| Sweep / Monte Carlo / walk-forward across trials | `honba-sweep`, one engine per trial | `spawn_blocking` pool, one engine per thread |
| One backtest or one live session's event flow | `honba-engine` | exactly one, the calling thread |
| Indicator fan-out inside one run | `honba-engine` join barrier | N, results merged in deterministic instrument order |

The last row is the only intra-run parallelism, and it is permitted only because it is provably order-independent:
per-instrument indicator state is independent, and results are merged back in a fixed `InstrumentId` sort order
before anything downstream reads them.

---

## 2. Where we are today (verified 2026-10-03)

Read from the tree, not from documentation.

**Rust: ~12.8k lines across 12 crates, almost entirely synchronous.**

| Layer | Crates | State |
|---|---|---|
| L0 | `honba-messages` | Real. ids, events, orders, bars, serde wire contract, `SCHEMA_VERSION` |
| L1 | `honba-entities` | Real. instrument, position, portfolio, trade, screener predicates |
| L2 | `honba-market` | Real. generic market traits + `india` and `null` packs, `MarketRegistry` |
| L3 | `honba-engine` | **363 lines total.** sync `Clock`, `EventQueue` (BinaryHeap), `Handler`, `ExecutionEngine`, `Engine::run` |
| L3 | `honba-indicators` | Real. O(1) rolling windows, families incl. `gpu` and `india` subdirs |
| L4 | `honba-sim` | `bar_fill.rs` real; `simulator/`, `concurrent/`, `latency/`, `cost/`, `result/`, `backtest_node/` mostly stubs |
| L4 | `honba-strategy` | Real. `Strategy` trait, `StrategyContext`, `LedgerContext`, `StrategyRunner`, reference strategies |
| L5 | `honba-analytics` | Partial. `equity_stats`, `round_trip`, `trade_stats`, `report`; `monte_carlo`, `tearsheet`, `walk_forward`, `regime`, `cointegration` uncompiled or stub |
| L5 | `honba-data` | Parquet in/out, catalog. No streaming |
| L6 | `honba-testing` | `VecFeed`, fixtures, assertions |
| L7 | `honba-py` | cdylib, pyo3 0.22, `auto-initialize`. Exposes **5 classes + 3 functions** |
| L7 | `honba-cli` | Real CLI over the crates |

Facts that matter for this plan:

- **`tokio` is a workspace dependency but no crate uses it.** There is no async anywhere in Rust.
- **No HTTP, no REST, no WASM, no gRPC.** Zero. `axum`, `hyper`, `actix`, `tonic`, `wasm-bindgen` appear in no
  `Cargo.toml` in the workspace.
- **`honba-engine` cannot receive events from handlers.** `Handler::on_event` returns `Result<()>`, so there is no
  command/ack loop (roadmap E2-S5, not started).
- **The Python binding surface is tiny.** `honba._honba` exposes `SCHEMA_VERSION`, `canonical_json`,
  `wire_enum_values`, `run_strategy`, and `pyclasses::domain::{InstrumentId, QuoteTick, Bar, Fill, OrderIntent}`.
  `RustSmaCrossover` exists. Nothing else from the 12 crates is reachable.
- **`honba_bridge.py` (539 lines) is dead.** It is `include_str!`'d into `HONBA_BRIDGE` in `honba-py/src/lib.rs`
  and referenced nowhere. It is a parallel, hand-written Python simulation (`SimObject`, `VectorFeed`, `BarFill`,
  `SmaCrossover`) that duplicates what `honba-engine` + `honba-sim` already do. Delete it.
- **The schema chain is real and already spans three languages.** Rust serde → `honba.entities.wire` pydantic v2 →
  `schema/domain/domain_schema.json` → `json-schema-to-typescript` → `honba-frontend/src/core/types/generated/domain.ts`.
  Python is currently the schema source of truth; `make check-schema` is a blocking CI job.
- **The Python side is ~15.5k lines**, and much of it is not the SDK: `strategies/` 5.0k (of which `indicators/` is
  the bulk), `adapters/` 2.4k, `data/` 1.6k, `screener/` 1.4k, `cli/` 864, `wire/` 806, `domain/` 509,
  `markets/` 492. `core/`, `india/`, `backtest/`, `query/` are 0-byte stubs; `research/` is 16 lines.
- **The frontend is greenfield.** Vite + React 19 + Zustand, 5 app shells, `algodesigner-app.tsx` is a
  `mountPlaceholder` stub. It consumes generated types and a hand-written `data-layer.ts`, with no backend. It is
  the easiest consumer to design for, because nothing depends on it yet.
- **`honba-cli` is the only Rust binary that does real work.** It is the natural first host for `honba-api-rest`.

---

## 3. Target architecture

### 3.1 Layer map

Dependencies point inward. The existing `scripts/dependency_graph.py` rule set is extended, not replaced: it keeps
checking production deps, dev-deps, the pyo3-is-only-in-`honba-py` rule, and the no-`india`-feature-in-core rule,
and gains an **async-isolation rule** and a **wasm-purity rule** (§6.2).

```
L0  honba-messages          ids, events, orders, bars, envelope, error taxonomy   (pure, sync)
L1  honba-entities         instruments, position, account, portfolio, trade      (pure, sync)
L2  honba-ports            async traits: MarketDataFeed, ExecutionGateway,
                           InstrumentMaster, Clock, Sink, SecretStore            (async, no I/O impls)
    honba-market           generic market contracts + india/null packs          (pure, sync)
    honba-risk             pure rule traits + pipeline stage                     (pure, sync)
L3  honba-engine           event loop, queue, handler->output, state cache,
                           audit sink.  STRICTLY SYNC, single-threaded.          (pure, sync)
    honba-indicators       pure compute                                         (pure, sync)
    honba-async            tokio runtime: engine driver, feed multiplexing,
                           task supervision, CPU offload, backpressure          (async)
L4  honba-sim              matching core, fill/latency/margin models, paper      (pure, sync)
    honba-strategy         Strategy trait, StrategyContext, runner, references   (pure, sync)
L5  honba-analytics        metrics, tearsheet, Monte Carlo, walk-forward         (pure, sync)
    honba-data             catalog, streaming Parquet, instrument master        (async edges)
    honba-codegen          THE CONTRACT REGISTRY. one walk of the wire types ->
                           JSON Schema, OpenAPI 3.1, TypeScript, .pyi, MCP     (build-time)
    honba-sweep            seeded sweep, concurrent trials, fitness             (async, sync core)
L6  honba-api              the versioned surface: request/response DTOs, the
                           envelope, endpoint registry, capability manifest,
                           error-code mapping. Transport-free.                  (pure, sync)
    honba-backtest         runner wiring engine + sim + strategy + data          (async)
    honba-testing          fixtures, VecFeed, assertions                        (dev only)
L7  honba-api-rest         axum router, WebSocket, SSE, OpenAPI serving,
                           auth, static assets                                  (async, binary)
    honba-api-wasm         wasm-bindgen surface for honba-frontend
                           (wasm32-unknown-unknown, no tokio, no fs)            (binary)
    honba-py               the only crate with pyo3; exposes honba._honba       (cdylib)
    honba-cli              binary: `honba serve`, `honba backtest`, `honba verify`
```

### 3.2 The sync kernel / async shell seam

`honba-engine` keeps its current synchronous shape, because every property that makes it testable depends on it.

```rust
// honba-engine (L3) — unchanged contract, gains an output channel
pub trait Handler: Send {
    fn on_start(&mut self) -> Result<()>;
    fn on_event(&mut self, ev: &Event, ts_init: UnixNanos) -> Result<EngineOutput>;  // E2-S5
    fn on_stop(&mut self) -> Result<()>;
}

pub enum EngineOutput {                                    // E2-S5
    None,
    Orders(Vec<Order>),
    Cancels(Vec<OrderId>),
    StateChange(TradingState),                             // E2-S1: Active | Reducing | Halted
}

impl Engine {
    /// Synchronous, single-threaded, deterministic. The only entry point the
    /// kernel exposes. Every surface (backtest, paper, live, REST, WASM)
    /// ultimately calls this.
    pub fn run(&mut self, feed: &mut dyn DataFeed) -> Result<()>;
}
```

`honba-async` wraps it. The wrapper owns exactly one `Engine` and never lends it out, so there is no lock and no
concurrency hazard:

```rust
// honba-async (L3)
pub struct EngineHandle { tx: mpsc::Sender<Command>, join: JoinHandle<Result<()>> }

impl EngineHandle {
    /// Spawns the single-writer engine task on the current tokio runtime.
    pub async fn spawn(engine: Engine, feed: BoxedFeed) -> Self;
    pub async fn submit(&self, cmd: Command) -> Result<()>;   // bounded, backpressure
    pub async fn join(self) -> Result<()>;
}
```

Rules the async layer must hold, enforced by tests and by a clippy lint group:

1. **One engine, one thread.** `EngineHandle` is `!Sync` and owns its task. No `Arc<Mutex<Engine>>` anywhere.
2. **All I/O is async; all CPU work in the kernel is sync and inline.** Parquet reads, journal writes, HTTP, broker
   sockets use `tokio::fs` / `spawn_blocking`. The event loop itself never awaits.
3. **Ordering is preserved by the kernel, not by the transport.** Feeds may deliver out of order or in bursts; the
   queue reorders on `ts_event` with a monotonic sequence tiebreak, exactly as today.
4. **Backpressure is explicit.** `mpsc` with a bounded capacity; a slow consumer stalls its feed, not the runtime.
5. **Cancellation is a command, not an abort.** `Command::Stop` drains the queue and runs `on_stop` so the audit
   stream is always complete. `JoinHandle::abort` is reserved for panics.
6. **`Clock` is the only time source.** `LiveClock` wraps `tokio::time::Instant`; `HistoricClock` advances with
   `ts_event`. Domain code never sees either (roadmap E2-S3).

### 3.3 Where the parallelism actually goes

`honba-sweep` is the first consumer of real threads, and it is deliberately boring:

```rust
// One engine per trial, shared immutable data, results returned in trial order.
pub struct SweepPlan { strategy_manifest: Manifest, trials: Vec<TrialParams>, fitness: Fitness }

pub async fn run(pool: &ThreadPool, plan: &SweepPlan, data: Arc<Dataset>)
    -> Result<SweepReport>
{
    // spawn_blocking per trial; each trial builds its own Engine + Sim + Strategy.
    // Dataset is Arc'd and read-only. Results land in a Vec indexed by trial id,
    // so the report is byte-identical regardless of completion order.
}
```

`honba-strategies` already has a `VectorFeed`; what is missing is a `Dataset` type that is `Send + Sync + 'static`
and cheap to clone. That means the columnar read path in `honba-data` must stop handing out owned `Vec<Position>`
per instrument and start handing out `Arc<ColumnarSlice>`. This is a prerequisite story, not an optimisation.

**Definition of done for the async work:** same seed + same data ⇒ byte-identical journal, at `worker_threads = 1`,
`4`, and `16`. That test is the whole argument, and it is blocking.

---

## 4. The versioned API and binding surface

### 4.1 One registry, four renderings

`honba-codegen` (L5) is a **build-time crate that walks the Rust wire types once** and emits every artifact. Today
the chain is Rust serde → hand-verified pydantic → JSON Schema → TypeScript, and the Rust→Python link is maintained
by *convention plus tests* (golden vectors, `wire_enum_values()` parity, `canonical_json` round trip). That works,
but it means adding a field is a four-repo manual ritual and the Python side is a re-implementation that can drift.

The registry inverts it: **Rust declares, everything is generated.**

```
                    honba-messages / honba-entities / honba-strategy / honba-api
                                        │
                              honba-codegen  (build-time)
                     ┌──────────┬───────┴───────┬──────────┬─────────────┐
                     ▼          ▼               ▼          ▼             ▼
               JSON Schema   OpenAPI 3.1   TypeScript    .pyi        MCP tool
               (golden,      (REST)      (frontend)   (Python)    schemas
                conformance)                          stubs
```

Rules:

- **One registry, one version.** `honba-codegen` owns `pub const SCHEMA_VERSION: u32` and
  `pub const API_VERSION: &str = "1.0.0"`. `_honba.SCHEMA_VERSION`, the `schema_version` envelope field,
  the OpenAPI `info.version`, and the frontend's generated header all read from these two constants.
- **Generation is checked in CI by drift, not by review.** Regenerate and `git diff --exit-code`. Four drift
  checks, one per artifact, all blocking. `make check-schema` becomes `make check-codegen`.
- **Python types are generated, then hand-extended.** `honba/wire/generated/` is generated and never edited.
  Anything a user touches lives in `honba/wire/` beside it. A test asserts no generated file was modified.
- **Every Rust interface is exposed**, per the ask, via three mechanisms:
  - *Data-carrying types* → generated classes in every surface (PyO3 `#[pyclass]`, OpenAPI schema, TS type).
  - *Traits (ports)* → a **capability manifest** generated from the trait's method set, plus a Python `Protocol`
    and a TypeScript interface with the same name, so the shape cannot diverge.
  - *Enums and error codes* → generated constants with the `ALL`-constant pattern already used by
    `honba_messages::enum_with_all!`.
- **Capabilities, not just types.** `GET /api/v1/capabilities` (and `honba.capabilities()`) returns the manifest:
  which crates are compiled in, which market packs, which endpoints, which toolsets, which adapters are
  registered. The frontend and MCP clients read it at startup instead of probing.

### 4.2 Versioning policy

Three independent version axes, each with one owner:

| Axis | Carried by | Bumped when | Owner |
|---|---|---|---|
| `schema_version` (u32, integer) | the `Message` envelope, every response body | any wire-shape change, additive or breaking | `honba-codegen`, via a migration note in `CHANGELOG.md` |
| `api_version` (semver) | `/api/v1` URL prefix, `info.version` in OpenAPI | endpoint added/removed/reshaped | `honba-api` |
| crate version (semver) | cargo, PyPI `honba` | releases | release process |

Policy:

- **Wire additive changes do not bump `schema_version`.** A new optional field is additive. Readers must reject an
  unknown `schema_version` and must ignore unknown fields, so a v1 reader can consume v1-with-extras safely. This is
  already how `Message` works; it just needs to be stated and tested.
- **`/api/v1` is frozen the day M5 lands.** Additive-only within a major: new endpoints, new optional fields,
  new enum variants are fine; removing or retyping anything is `/api/v2`. Enum-variant addition is a breaking change
  for exhaustive decoders, so OpenAPI consumers get `additionalProperties`-tolerant types, not closed unions.
- **`u64` nanosecond timestamps exceed `Number.MAX_SAFE_INTEGER`.** This is already flagged as a known gap in
  ADR 006 and roadmap E0-S2. **It must be solved before the API ships.** Decision: `ts_init`/`ts_event` cross the
  HTTP and WASM boundary as **ISO-8601 strings with nanosecond precision plus a separate integer `unix_nanos`
  string**, never as a JSON number. Fix this in the wire envelope now, while the only consumers are Rust and Python.
- **Every response is enveloped**, success and failure alike:

```json
{ "api_version": "1.0.0", "schema_version": 4, "data": { }, "error": null }
{ "api_version": "1.0.0", "schema_version": 4, "data": null,
  "error": { "code": "risk.max_notional_exceeded", "category": "risk",
             "message": "notional 250000 exceeds limit 100000",
             "retryable": false, "context": { "limit": 100000 } } }
```

- **Stable error codes**, from roadmap E0-S5: an enum in `honba-messages`, published in the schema, one code per
  failure mode, `retryable` set for anything a caller may safely repeat. No error crosses a surface without a code.

### 4.3 REST surface (`honba-api-rest`)

axum on tokio. Read-only endpoints first; write endpoints gated on the risk stage and approval queue (roadmap E5).

```
GET    /api/v1/capabilities
GET    /api/v1/health
GET    /api/v1/schema              → the JSON Schema bundle, served (E9-S8 reads it)

GET    /api/v1/instruments          → snapshot-as-of, effective-dated          (E1-S3)
GET    /api/v1/instruments/{id}
GET    /api/v1/quotes?symbols=&venue=
GET    /api/v1/bars/{id}?tf=&from=&to=
GET    /api/v1/depth/{id}

POST   /api/v1/strategies            → verify + compile to manifest            (E0-S8)
GET    /api/v1/strategies
POST   /api/v1/backtests             → start a run, returns run_id             (E4-S3)
GET    /api/v1/backtests/{id}        → status, metrics, assumptions
GET    /api/v1/backtests/{id}/journal  → streaming
POST   /api/v1/sweeps                → seeded sweep, returns job_id            (E4-S5)
GET    /api/v1/sweeps/{id}

POST   /api/v1/orders                → gated: risk stage + approval queue      (E5-S4)
GET    /api/v1/orders
DELETE /api/v1/orders/{id}
POST   /api/v1/positions/close

GET    /api/v1/screener/scan         → predicate eval over the catalog
GET    /api/v1/journals/{id}
WS     /api/v1/stream                → events, fills, run progress, AI decisions
```

- **Auth**: bearer token, hashed at rest, per-token scope (`read:market`, `read:account`, `write:orders`,
  `research`), audited by token id. `write:orders` requires a second factor and the approval queue (roadmap E5-S3).
- **Streaming**: WebSocket for live/event traffic, SSE for journal and run-progress (simpler, resumable, no
  client-side reconnect logic needed for one-way streams).
- **OpenAPI is served, not written.** Generated by `honba-codegen` from the same registry; the frontend's typed
  client is generated from it (roadmap E9-S8). No hand-written OpenAPI, ever.

### 4.4 WASM surface (`honba-api-wasm`)

The frontend gets **both**, for different jobs. This split is the design.

| Concern | Transport | Why |
|---|---|---|
| Indicators, chart math, screener predicate evaluation, backtest replay over already-downloaded data | **WASM** | Pure compute, sub-millisecond, offline, no server round-trip. This is what makes 5,000-row virtualized tables and live candle updates viable. |
| Runs, journals, sweeps, instruments, catalog, orders, positions, auth, streaming | **REST + WS** | Server state, long-lived jobs, secrets, the only copy of the truth |
| Anything needing filesystem or network from Rust | neither | wasm32 has no filesystem; brokers and Parquet stay server-side |

WASM constraints, enforced by the dependency-graph script:

- `honba-api-wasm` may depend only on **L0–L4 pure crates**: `honba-messages`, `honba-entities`, `honba-market`
  (generic traits only), `honba-indicators`, `honba-engine`, `honba-strategy`, `honba-sim`, `honba-codegen`.
- It may **not** depend on `honba-async`, `honba-data`, `honba-api-rest`, `honba-py`, or anything pulling tokio.
  tokio does not build for `wasm32-unknown-unknown` without a target-specific shim, and we refuse to maintain one.
- Size budget: the WASM artifact stays under ~3 MB gzipped. Enforced in CI. Indicators are the bulk; that is the
  point.
- The frontend loads WASM lazily per app surface (Screener and Workbench need it, AlgoDesigner does not yet).

### 4.5 Python binding (`honba-py`) — what "expose everything" means in practice

Today `honba._honba` exposes 8 symbols out of ~12.8k lines of Rust. The ask is to close that gap, and the
mechanism is the same registry:

```python
# generated — never edited
from honba._honba import (              # pyo3 classes
    Bar, QuoteTick, TradeTick, Order, OrderIntent, Trade, Position,
    Portfolio, Instrument, InstrumentId,
)
from honba._honba.enums import (        # from enum_with_all! ALL constants
    OrderSide, OrderType, OrderStatus, TimeInForce, PositionSide, Currency,
)
from honba._honba.ports import (        # Protocol per Rust trait, same names
    MarketDataFeed, ExecutionGateway, InstrumentMaster, Clock, Sink,
)
from honba._honba.engine import Engine, EngineHandle, EventQueue   # sync kernel + async handle
from honba._honba.sim import BarFillEngine, MatchingCore, FillModel, LatencyModel
from honba._honba.market import MarketRegistry, MarketProfile, IndiaPack, NullPack
from honba._honba.api import ApiClient, BacktestRequest, SweepRequest, ErrorCode
from honba._honba.codegen import SCHEMA_VERSION, API_VERSION, CORE_VERSION
```

Two binding styles, both required:

1. **Zero-copy typed objects** for the hot path (`Bar`, `Order`, `Trade`) — `#[pyclass]` wrappers over the Rust
   struct with `to_rust()`/`from_rust()`. This is what exists today and it should stay.
2. **JSON boundary** for everything structural — the existing `canonical_json` / `wire_enum_values` / `run_strategy`
   pattern, extended to every request/response DTO in `honba-api`. This is what scales to hundreds of types without
   hand-writing 500 `#[pyclass]`es, and it is the same bytes REST and WASM see, so the golden vectors apply unchanged.

The async surface uses `pyo3-async`'s `into_future` on methods that can block, and a dedicated
`honba.event_loop` thread per Python interpreter. Do **not** enable `pyo3`'s `auto-initialize` in the shipped
configuration once a real tokio runtime exists: one runtime per interpreter, created once, torn down on interpreter
finalisation. `honba.async_run(coro)` is the public entry.

---

## 5. The Python SDK

"Super-flexible" is interpreted as five concrete properties, not as a slogan.

1. **Two transports, one object model.** Every client is constructed against a transport.

   ```python
   from honba import Client, Strategy

   client = Client()                       # in-process, PyO3, zero copy, fastest
   client = Client("http://localhost:8080")  # same API, over REST/WS

   class SmaCross(Strategy):
       def on_bar(self, bar): ...

   result = await client.backtest(SmaCross(), universe="nifty50",
                                  start="2024-01-01", end="2025-01-01").run()
   ```

   The REST client is not a fallback or a debugging tool. It is how the SDK talks to a hosted deployment, how CI
   runs against a server, and how a strategy that runs locally in a notebook runs unchanged inside the FastAPI-style
   service. Same `Strategy`, same `BacktestResult`, same `assumptions` block.

2. **Sync sugar over async.** `honba` is async-first (`asyncio_mode = "auto"` already set), and `honba.sync`
   provides a thread-offload wrapper so notebooks and `honba-strategies` (which are sync today) keep working.

3. **Strategy contract is already settled** (ADR 008): `on_start`, `on_bar`, `on_quote`, `on_trade`, `on_fill`,
   `on_stop`, all acting through `self.ctx`. The SDK adds nothing to the hook set. A strategy must never see the
   transport, the run mode, or a broker type.

4. **Everything machine-readable.** Typed config → JSON Schema → validated `configs/*.toml`. Results as
   `BacktestResult` pydantic models with `schema_version`, `metrics`, `assumptions.not_modelled`, and
   `validation` (deflated Sharpe, PBO, holdout consumed). `.to_json()`, `.to_parquet()`, `.to_journal()`. No
   free-form prints as the primary output.

5. **Composability over frameworks.** No plugin system, no metaclass magic, no global state. `Client` holds a
   transport and a registry; `Strategy` is a base class; `IndicatorBank` is a dict. An agent (MCP) and a notebook
   call the same functions.

Python package layout after this work:

```
honba/
  __init__.py            public API surface, re-exports only
  client.py              Client, transports (inproc / http)
  sync.py                sync wrapper
  wire/
    generated/           GENERATED from Rust — never edited
    models.py            hand-extended pydantic on top of generated
    loads.py             duplicate-key-rejecting loaders (existing)
  domain/                strategy-facing domain types (existing, unchanged)
  strategies/            base, context, runner, reference, indicators
  backtest/              runner, result, config models
  research/              vectorized pre-filter, loader, notebooks
  ai/                    llm, autoresearch, journal data, mcp, rl, verification
  adapters/              base, registry, contract suite (existing, ADR 010)
  screener/              catalog, evaluator, presets
  cli/                   typer app
```

---

## 6. What goes where

### 6.1 Repository layout (top level of `honba-labs/`)

Unchanged. Seven sibling repos, each its own git repo. No new repos are created by this plan.

| Repo | Contents | Change under this plan |
|---|---|---|
| `honba/` | Rust workspace `crates/` + `python/` + `schema/` + `configs/` + `docs/adr/` | Grows: new L2–L7 crates. Keeps `python/` (§8) |
| `honba-adapters/` | 9 broker packages + `shared` | Unchanged. Adapters stay Python-side per ADR 010; the Rust side gains the `honba-ports` traits they will eventually implement |
| `honba-strategies/` | 129-strategy catalog + `registry.json` | Unchanged until E0-S8 manifests land, then every strategy gains `hyperparameters()` and passes `honba verify` |
| `honba-examples/` | numbered learning-path scripts | Unchanged; becomes the acceptance test that the async SDK is usable end-to-end |
| `honba-frontend/` | Vite + React 19, 5 apps | Grows: WASM loader, generated typed client, capability-driven UI |
| `honba-docs/` | MkDocs + mdBook | Grows: generated API reference from OpenAPI, architecture pages |
| `meta/` | org README, bootstrap scripts | Unchanged |

### 6.2 Dependency rules (extends `scripts/dependency_graph.py`)

New checks, all blocking CI:

1. **Async isolation.** `honba-async` is the only L3 crate that may depend on tokio. Crates at L0–L2 and L4 may not.
   `honba-engine` in particular must not gain a tokio dependency; a grep for `tokio` in its `Cargo.toml` fails CI.
2. **Sync kernel purity.** `honba-engine`, `honba-messages`, `honba-entities`, `honba-indicators`, `honba-sim`,
   `honba-strategy`, `honba-market` (generic traits) may not depend on any crate whose name ends in `-async`,
   `-rest`, or that is not in the pure list. Enforced by an explicit allowlist, not by a naming convention alone.
3. **WASM purity.** `honba-api-wasm` may depend only on the pure list in §4.4. Any tokio or filesystem dependency
   fails CI.
4. **pyo3 containment.** Unchanged: `honba-py` only.
5. **Codegen position.** `honba-codegen` sits at L5 and is a `build-dependencies` consumer, not a runtime one.
   Runtime crates must not depend on it; only build scripts and the CLI may.
6. **Existing four checks stay**: production deps, dev-deps (no upward edges), pyo3-only-in-`honba-py`,
   no `india` feature in core crates.

### 6.3 Surface-to-crate map

| Capability | In-process (PyO3) | REST | WASM |
|---|---|---|---|
| Domain types, enums, errors | generated classes + `canonical_json` | JSON Schema + OpenAPI | TS types via generated client |
| Engine / backtest | `Engine`, `EngineHandle`, `honba.backtest` | `POST /backtests`, journal stream | replay over local data only |
| Sweep / Monte Carlo | `honba.sweep` (spawn_blocking) | `POST /sweeps` | no |
| Simulator / fills | `honba-sim` classes | via backtest results | replay only |
| Market packs | `MarketRegistry`, entry points | `?market=nse_bse` | generic traits + india, no registry |
| Indicators | `honba-indicators` classes | `GET /quotes` consumers | **primary consumer** |
| Screener | `honba.screener` | `GET /screener/scan` | **primary consumer** |
| Adapters / brokers | Python per ADR 010, behind `honba-ports` | server-side only | never |
| Catalog / Parquet | `honba-data` | `GET /bars`, `GET /instruments` | never (no filesystem) |
| CLI | `honba` binary | — | — |

---

## 7. Delivery plan

Sequenced after the roadmap's M0–M6, with two new epics. Roadmap IDs are kept where they already exist.

### Phase 0 — Contract spine completion (roadmap M0, in progress)

| Story | Why it blocks this plan |
|---|---|
| E0-S4 config schema export | Config is the input to every surface |
| E0-S5 error taxonomy and versioning policy | The envelope and stable error codes in §4.2 need this |
| E0-S6 money and rounding audit | Decides the money type in the generated schema |
| E0-S8 strategy IR and manifest | The unit the frontend and MCP send |
| Replace the Python→JSON Schema link with `honba-codegen` | §4.1 |

**Deliverable:** `honba-codegen` emits JSON Schema, OpenAPI, TypeScript, `.pyi` and MCP tool schemas from the Rust
registry. Four drift checks green. Python's `wire/generated/` exists and is import-protected.

### Phase 1 — Async core (new epic E10)

| Story | Size | Note |
|---|---|---|
| E10-S1 `honba-ports`: async trait ports (L2) | M | `MarketDataFeed`, `ExecutionGateway`, `InstrumentMaster`, `Clock`, `Sink`, `SecretStore`. Object-safe via `async_trait`. No implementations |
| E10-S2 `honba-async`: runtime, `EngineHandle`, supervisor | M | tokio, bounded channels, structured concurrency, panic capture |
| E10-S3 E2-S5: `Handler::on_event` returns `EngineOutput` | M | `breaking`. This is the prerequisite for everything live |
| E10-S4 Shared `Arc<Dataset>` + streaming Parquet | M | Prerequisite for sweeps and for WASM replay |
| E10-S5 `honba-sweep`: seeded concurrent trials | M | `spawn_blocking` per trial, `Arc<Dataset>`, results in trial order |
| E10-S6 Determinism-under-threads test | S | **Blocking.** 1/4/16 worker threads ⇒ byte-identical journal |
| E10-S7 PyO3 async bridge (`pyo3-async`, one runtime per interpreter) | M | Replaces `auto-initialize` |
| E10-S8 Rust-native connectors (NSE/bhavcopy, broker REST) as `honba-ports` impls | L | Prerequisite for live; also unblocks `honba-cli` running Python strategies |

### Phase 2 — Versioned API surface (new epic E11)

| Story | Size | Note |
|---|---|---|
| E11-S1 `honba-api`: envelope, DTOs, endpoint registry, capability manifest | M | Transport-free |
| E11-S2 Timestamp representation fix (nanoseconds as strings) | S | **Must precede any external consumer.** Closes the ADR 006 gap |
| E11-S3 `honba-api-rest`: axum, read-only endpoints | M | `capabilities`, `health`, `schema`, `instruments`, `quotes`, `bars`, `depth` |
| E11-S4 Streaming: WS for events, SSE for journals and run progress | M | Reconnect, resume-from-sequence |
| E11-S5 `honba-api-wasm`: wasm-bindgen compute surface | M | Indicators, screener eval, replay. 3 MB budget enforced |
| E11-S6 Frontend typed client generated from OpenAPI | S | Roadmap E9-S8, pulled forward |
| E11-S7 Write endpoints behind risk stage + approval queue | M | Roadmap E5-S4. Not before E2-S2 |
| E11-S8 Multi-tenancy, auth, secrets, audit | L | Deferred, designed for |

### Phase 3 — Python SDK (roadmap E4/E5, reordered)

`Client` with two transports, `honba.sync`, generated `wire/generated/`, async bridge, then the research pipeline
(E4-S1 journal, E4-S3 backtest contracts, E4-S5 sweeps, E4-S6 overfitting gates) and the agent surfaces
(E5-S1 MCP schemas, E5-S3 scopes, E5-S5 verification sandbox). The SDK is not finished until a strategy in
`honba-examples` runs unchanged against both transports.

### Phase 4 — Frontend (roadmap E9)

Capability-driven shell, WASM compute path, generated client, then the apps in `Design.md` order: Screener,
Workbench, Simulator, Researcher, AlgoDesigner (last, since it needs E0-S8 and E9-S1..S4).

---

## 8. Should `honba/python` move to a sibling `honba-python` repo?

**Verdict: no, not yet. Keep it in `honba/`. Revisit when the trigger conditions in §8.4 are met.**

### 8.1 The case for

- **Release cadence.** Rust crates and the Python wheel ship different things. A strategy-only fix should not
  rebuild twelve crates and re-run the Rust CI matrix.
- **Dependency hygiene.** `python/` carries pandas, pyarrow, yfinance, typer, and optional torch/anthropic/openai.
  None of that belongs in the Rust crate graph, and `maturin develop` currently installs it in every Python CI job
  that also needs the cdylib.
- **Contributor focus.** Python strategy authors and Rust systems engineers want different repositories. The
  precedent is set: `honba-adapters/` and `honba-strategies/` are already separate.
- **Boundary clarity.** A separate repo makes "the Python SDK" a thing with a public API, rather than a
  subdirectory that happens to import `honba._honba`.

### 8.2 The case against, which is stronger today

- **The build is coupled by construction.** `python/pyproject.toml` declares
  `manifest-path = "../crates/honba-py/Cargo.toml"` with `module-name = "honba._honba"`. Moving `python/` out means
  maturin can no longer find the cdylib; it would need the Rust workspace published to crates.io first, a git
  dependency, or a subdirectory vendoring hack. All three make the Python build depend on a Rust release, which is
  the exact coupling the split was supposed to remove.
- **The contract spine crosses the boundary in both directions.** `scripts/export_schema.py` generates
  `schema/domain/domain_schema.json` from Python pydantic models and TypeScript from that JSON, and
  `make check-schema` is a blocking CI job. Split the repo and that check becomes a cross-repo coordination problem:
  which repo owns the schema, how does a consumer pin it, what happens when they disagree. §4.1 fixes the root cause
  by making **Rust** the generator, which removes Python from the codegen path entirely. Do that first; then the
  argument weakens substantially.
- **The conformance suite is cross-language.** `schema/conformance/strategy_contract.json` is asserted equal by
  Python, Rust, and `honba._honba.run_strategy` in one CI run (ADR 008). Splitting turns one atomic gate into two
  that can disagree.
- **The golden vectors are cross-language.** Same reasoning: `schema/golden/*.json` is read by
  `crates/*/tests/golden.rs` and `python/tests/unit/test_wire_golden.py`. An atomic gate becomes a coordination
  protocol.
- **Scale does not justify it.** `python/` is ~15.5k lines, and much of that is not the SDK proper: `strategies/`
  5.0k (mostly `indicators/`), `adapters/` 2.4k, `data/` 1.6k, `screener/` 1.4k. `core/`, `india/`, `backtest/`,
  `query/` are 0-byte stubs. A 15.5k-line package with two real consumers is not a distribution-scale artifact yet.

### 8.3 What to do instead, now

1. **Make Rust the codegen source** (§4.1). Python stops generating schemas and starts consuming them. This removes
   the strongest technical argument for splitting.
2. **Split the CI jobs properly.** The Rust job should not install Python deps beyond `maturin`. The Python job
   should not run `cargo test`. Today the Python job already only needs the cdylib; tighten the matrix so neither
   job pays for the other's dependencies.
3. **Move what is genuinely not the SDK.** `honba/strategies/indicators/` (the bulk of the 5.0k lines) is a
   self-contained pure-Python library with no `_honba` dependency; it belongs in `honba-strategies` or its own
   `honba-indicators` repo. That is a better decomposition than "SDK vs not SDK" and it shrinks `python/` honestly.
4. **Clean out the dead.** `honba_bridge.py` (539 lines, `include_str!`'d, referenced nowhere) and the 0-byte stub
   packages (`core/`, `backtest/`, `india/`, `query/`) should be deleted or filled, not carried into a new repo.
5. **Publish one wheel from one repo** and keep `honba-adapters`, `honba-strategies`, `honba-examples` consuming it
   from PyPI or a path dependency as they do now.

### 8.4 Trigger conditions — split when **all** of these are true

1. `honba-codegen` is the sole schema source and `python/` no longer generates anything (§4.1 done).
2. `honba-py` is published to crates.io, so `honba-python` depends on a released crate rather than a path, and the
   wheel can build without a sibling checkout.
3. A second, independently-versioned Python consumer exists (a hosted deployment, an external plugin author, or a
   stable plugin API), so the wheel needs its own release train.
4. `python/` exceeds ~40k lines or three or more people need to change it per week without touching Rust.
5. A written migration plan exists for the cross-repo conformance suite and golden vectors — e.g. schemas published
   as a versioned artifact both repos pin, and a compatibility job that runs both sides against the same pinned
   vectors.

Condition 2 is the one that really gates it. Until `honba-py` is on crates.io, the split makes the Python build
*more* fragile, not less. **Sequencing: Phase 0 → Phase 1 → Phase 2 → then re-evaluate §8.**

---

## 9. Risks and open decisions

Carried forward from roadmap §8, plus what this plan adds. Each needs an ADR.

| # | Question | Recommendation | Status |
|---|---|---|---|
| 1 | `async fn` in traits, or `#[async_trait]`? | `async_trait` for the L2 ports (needs `dyn` for the registry). Native `async fn` in trait is fine for internal, generic, non-`dyn` call sites. Split by use, not by taste | ✅ Done (honba-ports uses async_trait; no ADR required) |
| 2 | Does `Engine` stay sync, or gain an async sibling? | Sync kernel, async wrapper (§3.2). An async `Engine` breaks `&mut dyn Handler` ergonomics and the golden-vector tests for no gain | ✅ Done (honba-engine/src/engine.rs: Engine.run is sync) |
| 3 | Is `auto-initialize` in `honba-py` acceptable? | No. One explicit runtime per interpreter, created and torn down by `honba.event_loop` (E10-S7) | ✅ Done (ADR 0015; honba.event_loop + honba::runtime, no auto-initialize, enforced by test) |
| 4 | Nanosecond timestamps over JSON | Strings plus a separate integer field. Must land before REST ships (E11-S2) | ✅ Done (ADR 0012 rule 4: {iso: string, unix_nanos: string}; tests in honba-messages) |
| 5 | Does WASM get the engine? | No. Engine + `EngineHandle` are excluded; WASM gets sync compute only. Replay is a separate, simpler entry point | ✅ Done (honba-api-wasm Cargo.toml depends only on honba-indicators) |
| 6 | JSON Schema from Rust (`schemars`) or from Python pydantic? | Rust. ADR 006 decision 1 chose pydantic because the roadmap put schemas downstream of Python; this plan reverses that, because §4.1 needs one generator. **This requires an ADR that explicitly supersedes ADR 006 §1** | ✅ Done (ADR 0014) |
| 7 | Should `honba-py` become per-crate bindings instead of one cdylib? | No. One cdylib, with `honba-codegen` deciding what surfaces. Per-crate bindings multiply wheel count without reducing work | ✅ Done (honba-py Cargo.toml: one cdylib "honba") |
| 8 | Sweep parallelism: threads or processes? | Threads via `spawn_blocking` + `Arc<Dataset>`. Processes only if a profile shows GIL or allocator contention | ✅ Done (honba-sweep/src/run.rs: tokio::task::spawn_blocking + Arc<Dataset>) |
| 9 | Long-lived live sessions in a request/response server | A `Run` is a server-side object with a `run_id`, not an HTTP connection. Restart, pause and stop are commands (roadmap E2-S1, E9-S6) | ⬜ Open (BacktestResponse has run_id + status; routes POST/GET /backtests answer 501, not built yet) |
| 10 | Is the `honba-python` split blocked on crates.io publishing? | Yes. Re-evaluate after Phase 2 | ⬜ Open (explicitly re-evaluate after Phase 2; no ADR overriding this) |

### Status as of 2026-10-06

Audit complete: 7 rows ✅ Done, 1 row 🟡 Partial, 2 rows ⬜ Open. **Not all decisions are documented.** Missing ADRs:
- **E2-S1, E9-S6** (row 9): long-lived server-side runs with run_id and lifecycle commands (types exist, routes not built; needs ADR once built)

Recorded since the audit:
- **Row 6**: ADR 0014 (JSON Schema from Rust via schemars) supersedes ADR 006 §1.
- **Row 3**: ADR 0015 accepted (no auto-initialize, one explicit runtime owned by `honba.event_loop`); `honba.event_loop` (E10-S7) is not built yet, so the row stays Partial.

## 10. Definition of done for this architecture

1. A strategy written against the Python SDK runs **unchanged** in-process, over REST, and in `honba-examples`.
2. The frontend computes indicators, screener results and backtest replay in WASM with no Python in the loop, and
   reads/writes all server state over REST/WS.
3. `honba-codegen` emits JSON Schema, OpenAPI, TypeScript, `.pyi` and MCP tool schemas; four drift checks are
   blocking CI.
4. Every error crossing any surface carries a stable code from the published enum.
5. Same seed, same data, `worker_threads` of 1, 4 and 16 ⇒ byte-identical journal.
6. `cargo test --workspace`, `pytest`, `stubtest`, `cargo fmt`, `clippy -D warnings`, `ruff` all clean.
7. `scripts/dependency_graph.py` enforces the six rules in §6.2, including the two new ones (async isolation,
   wasm purity).