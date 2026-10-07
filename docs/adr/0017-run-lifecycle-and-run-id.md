# ADR 0017: Run Lifecycle and `run_id`

Date: 2026-10-07. Status: **accepted** (2026-10-07; accepted with review amendments). Roadmap
story: decides D1; unblocks E4-S3, E11-S3 (backtests, sweeps, journals), E4-S5 and the E11-S4
attach point. The run journal uses the event vocabulary of ADR 0019 (`SCHEMA_VERSION` 4).

## Context

Everything a run needs *as a wire type* already exists; nothing exists *as a server object*.

- `RunStatus` is `pending | running | completed | failed` (`crates/honba-api/src/responses.rs:95`,
  `#[non_exhaustive]`, snake_case), aliased as `BacktestStatus` and `SweepStatus`
  (`responses.rs:110`, `:113`). There is no `cancelled`, no `RunState`, and no run state machine
  anywhere in `crates/` or `python/src/honba`. `Failed`'s doc says "see the envelope error", but a
  `200` poll of a failed run has no envelope error to see.
- `BacktestResponse { run_id, status, metrics, assumptions }` (`responses.rs:135`) and
  `SweepResponse { job_id, status, report }` (`responses.rs:150`) carry ids that **nothing mints**:
  `run_id`/`job_id` appear only as DTO fields and test literals
  (`crates/honba-api/src/tests/unknown_fields.rs:83`). No workspace crate depends on `uuid`,
  `ulid`, `rand` or `getrandom` directly; `Cargo.lock` already holds `getrandom` 0.2.17, 0.3.4 and
  0.4.3 as transitive dependencies. `sha2` (`Cargo.toml:53`) produces the compiled-strategy
  content id (`crates/honba-api/src/strategies.rs:134`).
- The endpoint registry `honba_messages::ENDPOINTS` (`crates/honba-messages/src/endpoints.rs:153`)
  has 22 rows. The run rows are `POST /backtests`, `GET /backtests/{id}`,
  `GET /backtests/{id}/journal`, `POST /sweeps`, `GET /sweeps/{id}`, `GET /journals/{id}`. There is
  **no collection route** (`GET /backtests` is not in the registry). All six rows are in
  `honba_api_rest::NOT_IMPLEMENTED_ENDPOINTS` (`crates/honba-api-rest/src/lib.rs:216`) and answer
  501 `not_implemented`; `/capabilities.not_implemented` is derived from that list (`lib.rs:244`)
  and `crates/honba-api-rest/tests/capabilities.rs` probes the router built from
  `AppState::default()` to keep it honest. ADR 0013 (lines 132-134) records those six as 501.
- Request DTOs `BacktestRequest` / `SweepRequest` (`crates/honba-api/src/requests.rs:100`, `:127`)
  are all-optional and `deny_unknown_fields`; `seed` documents "Same seed and data give a
  byte-identical journal" (`requests.rs:119`) and, on sweeps, "Required for a reproducible sweep"
  (`:137`).
- Execution today is synchronous and local: `BacktestSession.run() -> BacktestResult`
  (`python/src/honba/session.py:246`, `:286`) has no run id, status, journal or polling;
  `python/src/honba/cli/backtest.py` is 0 bytes and not registered in `cli/main.py`; the Rust
  `honba-cli backtest` is single-shot over a synthesised series (`crates/honba-cli/src/backtest.rs:70`).
- There is **no journal writer**: only the async `honba_ports::Sink` trait
  (`crates/honba-ports/src/sink.rs:22`, `write`/`flush`, `#[async_trait]`), implemented outside
  tests by nobody; `AuditLog` is in-memory (`crates/honba-engine/src/audit.rs:78`);
  `data/journals` exists only in prose. Journal schema v1 (E4-S1) is not started, so there is no
  dataset id, config hash or git rev anywhere.
- No streaming route exists ("22 routes in `endpoints.rs`, none streaming", `docs/ROADMAP.md:426`).
  The kernel's time source is the data-driven `honba_engine::Clock` (`crates/honba-engine/src/clock.rs:13`);
  `honba-async` adds `LiveClock`/`HistoricClock` and `Command::{Stop, State}` for the async boundary.
- `AppState` (`crates/honba-api-rest/src/state.rs:17`) holds four `Arc<dyn ...>` read ports plus an
  `Arc<Mutex<StrategyCatalog>>` (`state.rs:31`; in-memory, at most 1,000 entries,
  `strategies.rs:28`); `honba serve` builds it once from `--data-dir`
  (`crates/honba-cli/src/serve.rs:31`) and `honba._honba.api_request` caches one per data directory
  in the process-global `STATES` map (`crates/honba-py/src/pyclasses/api.rs:43`), which has no
  shutdown hook.
- Codegen maps both journal rows to `TradesResponse`
  (`crates/honba-codegen/src/endpoints.rs:29`, `:37`) and emits exactly `200` plus `default` per
  operation (`operation()`, `endpoints.rs:75`), never `202`. MCP already has tools `backtest`
  (`POST /backtests`) and `sweep` (`POST /sweeps`) (`crates/honba-codegen/src/mcp.rs:35`, `:60`;
  bound in `python/src/honba/ai/mcp/tools.py:51`), and nothing to poll or read a journal.
- Layering: `scripts/dependency_graph.py` enforces `ALLOWED_PROD`/`ALLOWED_DEV` per crate and fails
  on an unregistered crate (`scripts/dependency_graph.py:209`). `ALLOWED_PROD["honba-api-rest"]` is
  `honba-api`, `honba-messages`, `honba-entities`, `honba-data`, `honba-market`, `honba-ports`
  (its `Cargo.toml` uses all but `honba-market`); `ALLOWED_DEV["honba-api-rest"]` is
  `{honba-testing}`. `tempfile` is not a dependency of any crate.

## Decision

### 1. A `Run` is a server-side object with five states

`pending -> running -> completed | failed | cancelled`, plus `pending -> cancelled` for a run
cancelled before it started. `completed`, `failed` and `cancelled` are terminal; a terminal run
never changes state again, and an attempted transition out of one is refused (not a no-op that
rewrites timestamps).

- The wire enum stays `RunStatus` and gains exactly one variant, `Cancelled`. `pending` **is** the
  queued state: its doc comment already reads "Accepted, not started" (`responses.rs:96`), so no
  rename is needed.
- `RunStatus::Failed`'s doc changes to "Finished with a failure; see `error` on the response"
  (decision 7).
- Rejected: renaming `pending` to `queued` to match ROADMAP D1's prose. It is a wire break in an
  enum that is already committed to `schema/domain`, OpenAPI, the `.pyi` stubs and the frontend
  TypeScript, for a difference of one word.
- Rejected: a second, internal `RunState` type beside `RunStatus`. Two state vocabularies is the
  same drift problem ADR 0012 solved for versions; the DTO field and the store speak one enum.

### 2. One id space, minted at the edge

- `run_id` (backtests) and `job_id` (sweeps) are the **same generator and the same space**; the
  manifest's `kind` (`backtest` | `sweep`) distinguishes them, and both field names stay as
  committed.
- The value is ULID-shaped: 26 characters of Crockford Base32 (uppercase), a 48-bit Unix
  millisecond timestamp followed by 80 bits. The 80 bits are drawn from OS randomness through
  `getrandom` the first time a millisecond is used; further ids in the same millisecond increment
  the previous 80-bit value by one (the ULID monotonic rule), so the low bits act as a per-process
  counter. `getrandom = "0.3"` becomes a **direct** dependency of `honba-api-rest` (it resolves to
  the 0.3.4 already in `Cargo.lock`, so no new crate enters the lock; MIT/Apache-2.0 per
  `deny.toml`). Rejected: deriving the 80 bits from `sha2` over `(nanos, pid, counter)` — guessable
  inputs, not randomness. Rejected: a `ulid`/`uuid` dependency for one string.
- Ordering: within one process ids are strictly increasing in issue order. If the wall clock steps
  back, the generator keeps the last issued millisecond and keeps incrementing, so that property
  holds; across processes, or across a restart after a clock step, ids sort by creation time only
  approximately. Nothing relies on id order for correctness (retention orders by `finished_at`,
  decision 5). An 80-bit overflow within one millisecond fails the mint with `internal_error`.
- Ids are opaque and not derived from content: two runs of the same seed get different ids, and
  the id never appears in the journal body (decision 3).
- **Validation before any filesystem access.** Every `{id}` path segment is matched against
  `^[0-9A-HJKMNP-TV-Z]{26}$` (a hand-written check, no `regex` dependency) after the router's
  percent-decoding and before the registry, a path join or any `std::fs` call. A non-match is
  `404 not_found` (not 422: an ill-formed id names no resource). This is what keeps `../`,
  `..%2F..%2Fetc`, `%2e%2e` and lowercase spellings out of the journals root.
- Purity split: `honba-api` owns `RunId` (parse/validate, `Display`) and
  `RunIdGenerator::next(&mut self, unix_ms: u64, entropy: [u8; 10]) -> Result<RunId, ErrorDetail>`,
  which reads neither the clock nor the OS. `honba-api-rest` supplies `unix_ms` from the wall
  clock and `entropy` from `getrandom`, keeping the pure layer clock-free for the planned E2-S3
  lint.
- The journal is addressed by this same id (decision 7); on disk the id is the directory name.

### 3. In-memory registry plus an on-disk journal first

Per run, under a journals root defaulting to `data/journals` (cwd-relative, matching
`DataSourceConfig::Local { root: "data/catalog" }`; `honba serve --journals-dir` and a keyword
argument on the in-process transport override it):

```
data/journals/<run_id>/manifest.json   # run metadata + status, rewritten atomically
data/journals/<run_id>/events.ndjson   # append-only JSON Lines of the wire Message envelope
```

- `manifest.json` holds `manifest_version` (integer, starts at `1`; its own on-disk axis, not
  `schema_version`), `run_id`, `kind`, `status`, `seed`, the **resolved** request (decision 6),
  the strategy content id and resolved `StrategyIr`, the journal's `schema_version`,
  `created_at` / `started_at` / `finished_at` (wall clock, metadata only), and, once terminal,
  `metrics`, `assumptions` (backtests), `report` (sweeps) and, when `failed`, `error`. It is
  written to a sibling temp file and renamed, so a reader never sees a half-written manifest. A
  reader rejects an unknown `manifest_version` (decision 5).
- `events.ndjson` is one wire `Message` per line (`{"schema_version":N,"event":{...},"ts_init":{...}}`,
  the shape `schema/golden` pins), in kernel processing order, with `N` read from
  `honba_messages::SCHEMA_VERSION` (never a literal; 4 once ADR 0019 story (a) lands). Records use
  the ADR 0019 vocabulary only: market data (`bar`, ...) and `order`, `order_accepted`,
  `order_rejected`, `order_partially_filled`, `order_filled`, `order_cancel_requested`,
  `order_cancelled`, `order_expired`. There is no run-status record (status lives in the
  manifest).
- **The journal body carries no run id, no wall-clock time and no local path.** Byte-identity of
  `events.ndjson` for a given seed (decision 6) would be broken by any of them.
- The in-memory registry is a `HashMap<RunId, RunRecord>` inside `AppState` behind an
  `Arc<Mutex<...>>`, like `StrategyCatalog` (`state.rs:31`), rebuilt from the manifests at start-up.
  It is a cache of the manifests, never the only copy: every transition writes the manifest first,
  then updates the registry, under the registry lock.

### 4. Admission, queue, workers

- `POST /backtests` (or `/sweeps`): resolve and validate (decision 6), **then** admit, **then** mint
  the id, write `manifest.json` with `status = pending`, push onto the queue and answer **200** with
  `BacktestResponse` / `SweepResponse`. A request refused at any step before the manifest write
  leaves nothing on disk and consumes no id. Rejected: `202 Accepted` — the OpenAPI generator emits
  only `200` and `default` for every operation, so `202` would need a generator change for no
  client benefit.
- Workers: a fixed pool of `max_concurrent` plain `std::thread`s (default: available parallelism;
  `honba serve --max-concurrent-runs`) draining one FIFO queue. They are outside the tokio runtime;
  the engine kernel stays synchronous and tokio-free (`SYNC_KERNEL_CRATES`,
  `scripts/dependency_graph.py:157`).
- The queue is bounded: at most `max_queued` pending runs (default 64; `--max-queued-runs`). A
  submission that would exceed it answers **429** `rate_limited` (already in `ErrorCode`, retryable)
  with `context = {"reason": "run_queue_full", "max_queued": n}`. Rejected: 503 with a new
  `ErrorCode` variant — a full queue is load the caller can retry, which `rate_limited` already
  means, and it avoids another strict-decoder break.
- Handlers run on the tokio runtime, so every filesystem call in a handler (manifest write,
  journal read, directory scan) goes through `tokio::task::spawn_blocking`. Workers do their file
  I/O directly on their own thread.
- Rejected: running the backtest synchronously inside the request. Long runs would block the
  server, there would be nothing to poll, stream or cancel, and it would contradict the
  fire-and-poll job shape ADR 0009 already assumes ("Fire-and-poll for long work", line 114).

### 5. Shutdown, restart and retention

- **Graceful shutdown** (`honba serve` on SIGINT/SIGTERM): stop admitting (`POST` answers 429
  `run_queue_full` with `reason` `"shutting_down"`), write `cancelled` with `finished_at` for every
  `pending` run, wait up to a grace period (default 10 s, `--shutdown-grace-secs`) for `running`
  runs to finish on their own, then write `cancelled` with `finished_at` for every run still
  `running` and exit. A worker that finishes after its run was cancelled cannot overwrite it:
  terminal is final (decision 1) and the transition is checked under the registry lock. The
  cancelled run's `events.ndjson` keeps the records already flushed.
- **Start-up** scans the journals root once (in `AppState::with_journals_dir`, never in
  `AppState::default()`), skipping any entry whose name fails the id check. A terminal manifest is
  kept as-is. A **non-terminal manifest at start-up always means a crash** (graceful shutdown
  leaves none): it is closed as `failed` with
  `error = ErrorDetail { code: internal_error, context: {"reason": "interrupted"} }` and
  `finished_at` = the start-up time, and the manifest is rewritten. There is no marker file.
  Non-terminal runs are never re-executed. A manifest with an unknown `manifest_version` is left
  untouched on disk, logged, and not loaded (its id answers 404).
- **In-process transport**: `honba._honba.api_request`'s `STATES` (`api.rs:43`) has no shutdown
  hook, so runs started through `InprocTransport` are lost at interpreter exit (their worker
  threads die with the process) and are recovered as `failed`/`interrupted` the next time a
  process loads the same journals root. Adding an `atexit` drain is a possible later improvement,
  not part of v1.
- **Retention** runs at start-up only, after recovery. A terminal run is **evicted iff** it is
  *both* outside the `keep_runs` most recent terminal runs (default 1,000; ordered by
  `finished_at`, then `run_id`) *and* older than `keep_days` (default 30, by `finished_at`).
  Equivalently, a run survives if it is among the newest `keep_runs` **or** younger than
  `keep_days`. Eviction deletes the run's directory; an evicted id answers 404. Non-terminal runs
  are never evicted. Disk use is therefore bounded only across restarts.
- Runs are per process. Two `honba serve` instances pointed at one journals root would race on the
  same directories; there is no lock file in v1 (Known limits).

### 6. Resolution and determinism

- `BacktestRequest::resolve` and `SweepRequest::resolve` are pure functions in `honba-api`,
  mirroring `BarsQuery::resolve` (ADR 0013). For backtests, `seed` (non-zero), `strategy`,
  `universe`, `start` and `end` are **required**; `bar_spec` defaults to `"1d"` and
  `initial_capital` to the `AccountConfig` default. Each missing or invalid field is `422
  validation_invalid_request` with `context.field`. For sweeps, `seed` (non-zero), `strategy`,
  `params` and `trials` are required. The wire schema is unchanged (the fields stay `Option` in
  `requests.rs`); the strictness lives in the resolver and in each field's doc comment. This
  matches `BacktestRunConfig.seed` validated non-zero (`crates/honba-config/src/lib.rs:34`,
  `:146`, `RunConfigError::ZeroSeed`). Rejected: a server-generated seed returned in the response —
  it hides the seed from the caller of a research API.
- **Strategy resolution** happens once, at submit: `strategy` is a content id in this process's
  `StrategyCatalog` or the name of a Rust-registered strategy factory
  (`honba_sweep::StrategyFactory` shape, `build(seed) -> Box<dyn Strategy>`). The resolved
  `StrategyIr` and content id are copied into the manifest, so a run never consults the catalog
  again. The catalog is session-scoped (in-memory, bounded): after a restart, a content id from a
  previous process's `POST /strategies` is `422` (`context.field = "strategy"`) until the caller
  posts the manifest again. Recovered runs need no strategy (decision 5).
- A `StrategyIr` with no registered Rust implementation is `422 validation_invalid_request`,
  `context.field = "strategy"`, until an IR interpreter exists.
- **What the manifest pins:** resolved `StrategyIr` (and content id), `universe`, `start`, `end`,
  `bar_spec`, `initial_capital`, the data root, and `seed`. The data root is stored on disk for
  local audit only; it and the journals path are **never** serialised into a response, an
  `ErrorDetail.message` or `context` (errors name the run id and field, not paths).
- Claim: same seed + same pinned inputs + same bytes under the data root ⇒ byte-identical
  `events.ndjson`, at 1, 4 and 16 worker threads (`docs/ROADMAP.md:404`). Ids, manifest timestamps
  and paths are outside the journal body (decision 3); the kernel runs on the data-driven
  `honba_engine::Clock`; workers share no mutable state. **This is only verifiable on fixed test
  fixtures until E4-S1 introduces a dataset id**: the manifest cannot tell whether the bytes under
  the data root changed between two runs. `config_hash`, `dataset_id` and `git_rev` are E4-S1's
  journal schema and slot into `manifest.json` (a `manifest_version` bump) without touching the
  state machine or the id.
- **Parity with Python `BacktestSession`** is defined only on the Rust-registered strategy subset;
  every other strategy is 422 server-side. E4-S3's `rest_backtest_roundtrip_matches_session`
  acceptance must say so (follow-up, below).

### 7. REST behaviour, failure detail, and what stays 501

| Case | Status | Body |
|---|---|---|
| `POST /backtests`, valid | 200 | `BacktestResponse {run_id, status: pending}` |
| `POST /backtests`, resolver failure (decision 6) | 422 | `validation_invalid_request`, `context.field` |
| `POST /backtests` / `/sweeps`, queue full or shutting down | 429 | `rate_limited`, `context.reason` |
| `GET /backtests/{id}`, known backtest | 200 | `BacktestResponse`; `metrics` only when `completed`, `assumptions` only when terminal, `error` only when `failed` |
| `GET /backtests/{id}`, ill-formed id, unknown, evicted, or a **sweep** id | 404 | `not_found` |
| `GET /backtests/{id}/journal`, known backtest (any status) | 200 | `TradesResponse` from the complete records on disk |
| `GET /backtests/{id}/journal`, sweep id / unknown / ill-formed | 404 | `not_found` |
| `GET /journals/{id}`, known backtest | 200 | same as `/backtests/{id}/journal` |
| `GET /journals/{id}`, known sweep | 422 | `validation_invalid_request`, `context.reason = "sweep_journal_per_trial"` (E4-S5) |
| journal written at another `schema_version` | 422 | `unsupported`, `context = {"found": n, "expected": m}` |
| `POST /sweeps`, `GET /sweeps/{id}` | 200 / 404 | `SweepResponse`, same rules; a backtest id on `/sweeps/{id}` is 404 |
| anything not built | 501 | `not_implemented` |

- **Failure on the wire.** `BacktestResponse` and `SweepResponse` gain
  `error: Option<ErrorDetail>` (`#[serde(default, skip_serializing_if = "Option::is_none")]`),
  present **only** when `status = failed` and copied from the manifest. Executor errors map to
  their `ErrorCode`; journal write failures and panics in a worker are `internal_error` with
  `context.reason` (`"journal_write"`, `"panic"`); recovery uses `"interrupted"` (decision 5).
- **Partial journals.** For `pending` or `running` runs the journal routes answer 200 with the
  trades in the complete (newline-terminated) records currently on disk; a trailing partial line
  is ignored, so the result is always a prefix of the final answer and may be empty. Completeness
  is read from `GET /backtests/{id}`'s `status`, not from the journal. Rejected: 409 for
  non-terminal runs — it gives a polling client nothing, and the prefix property already makes the
  partial answer safe.
- **Fill to `Trade` mapping** (journal routes keep the committed `TradesResponse`). Each
  `order_partially_filled` and `order_filled` record yields one `Trade`, in journal order:
  `order_id` from the event; `instrument_id` and `side` from the run's earlier `order` record with
  the same `order_id` (absent: `internal_error`, `context.reason = "journal_orphan_fill"`);
  `quantity = last_qty`; `price = last_px`; `ts_event` from the event; `ts_init` from the envelope;
  `costs` = zero in the run's account currency, because no fill event carries costs before E4-S1.
  Costs are included in `metrics.net_pnl`; per-fill costs on the journal route are an E4-S1
  follow-up. Rejected: introducing `JournalResponse` now — it retypes a published response (ADR
  0012 rule 2 makes a retype `/api/v2` material) and is D3/E4-S1's decision.
- `assumptions` stays `Option<serde_json::Value>`; E4-S3 fills it with
  `{"not_modelled": [...], "timing": ...}`. Narrowing it to a typed struct is a later reshape.
- After this ADR's implementation, `NOT_IMPLEMENTED_ENDPOINTS` shrinks to the four trading rows —
  `POST /orders`, `GET /orders`, `DELETE /orders/{id}`, `POST /positions/close` — which stay 501
  until E11-S7. `GET /schema` stays a stub (ADR 0013). Removing a row flips
  `/capabilities.not_implemented`; the probe test then requires the default router to answer
  non-501 on those routes. The probe sends invalid bodies and ill-formed ids, so it writes nothing.
- **Cancellation** has no route in v1 (no `DELETE /backtests/{id}` in the registry), and
  cooperative stop of a running run is **undefined**: the synchronous kernel has no stop check
  between events, and `RunJob` carries no cancel token. `cancelled` is reachable only through
  graceful shutdown (decision 5). Defining cooperative stop (a token checked by the executor
  between events, plus the route) is deferred to the ADR that adds the route.

### 8. Where the code lives, the ports, and the layering change

- **Pure, in `honba-api` (L6):** `RunId`, `RunIdGenerator` (decision 2), `RunStatus::next(event)`
  (the transition table, terminal-is-final), `RunRecord`, the resolvers (decision 6), and the two
  ports below. `honba-api` gains **no** crate edge.
- **I/O, in `honba-api-rest` (L7):** the registry in `AppState`, the queue and worker pool, the
  manifest store, `NdjsonJournal`, the concrete executor, the six handlers, and the `getrandom`
  call.
- The ports, defined in `honba-api`:

```rust
/// Synchronous, append-only, per-run journal. Same contract as `honba_ports::Sink`
/// (append-only, explicit flush, a failure is kept), but sync: the worker is a plain thread.
pub trait JournalWriter: Send {
    fn append(&mut self, msg: &Message) -> Result<(), ErrorDetail>;
    fn flush(&mut self) -> Result<(), ErrorDetail>;
}

pub trait RunExecutor: Send + Sync {
    fn execute(&self, job: RunJob, journal: &mut dyn JournalWriter) -> Result<RunOutcome, ErrorDetail>;
}

pub struct RunJob {
    pub run_id: RunId,
    pub kind: RunKind,                 // Backtest | Sweep
    pub strategy_id: String,           // content id or registered name
    pub strategy: StrategyIr,
    pub request: ResolvedRequest,      // ResolvedBacktest { universe, start, end, bar_spec,
                                       //   initial_capital } | ResolvedSweep { params, trials }
    pub seed: u64,
}

pub enum RunOutcome {
    Backtest { metrics: BacktestMetrics, assumptions: serde_json::Value },
    Sweep { report: SweepReportResponse },
}
```

- **Relationship to `honba_ports::Sink`.** `Sink` is async (`#[async_trait]`, `PortResult`);
  `honba-api` may not depend on `honba-ports` and the worker is not a task, so `JournalWriter` is a
  separate synchronous port with the same three rules. `NdjsonJournal` (honba-api-rest, a
  `BufWriter<File>` over `events.ndjson`) implements it; flush is called on every terminal
  transition. The bridge, when a non-file journal is needed, is an adapter in `honba-api-rest`
  (which may see both crates) implementing `JournalWriter` over any `S: Sink` by
  `tokio::runtime::Handle::block_on` from the worker thread (legal because workers are not runtime
  threads). It is not built in v1.
- The data root, `ExecutionConfig` and `AccountConfig` reach the concrete executor at
  construction, not through `RunJob`; the executor builds and validates a
  `honba_config::BacktestRunConfig`-equivalent so seed, cost and account defaults have one owner.
- The concrete executor lives in `honba-api-rest` so that **both** composition roots —
  `honba serve` and `honba._honba.api_request` — get runs without wiring anything, and
  `AppState::default()` keeps answering honestly to the probe test. Rejected: injecting it from
  `honba-cli` and `honba-py` (two identical wirings, `AppState::default()` stays 501). Rejected: a
  new `honba-runs` crate (a registry row and edge set for no isolation gain, at L7 anyway).
- **Dependency-graph change** (`scripts/dependency_graph.py`, in the same commit as the
  `Cargo.toml` edits; `python3 scripts/dependency_graph.py` must pass):

```python
ALLOWED_PROD["honba-api-rest"] |= {
    "honba-engine",     # L3: kernel, honba_engine::Clock
    "honba-strategy",   # L4: Strategy, StrategyIr
    "honba-sim",        # L4: fill/execution simulation
    "honba-analytics",  # L5: BacktestMetrics
    "honba-config",     # L5: ExecutionConfig, AccountConfig, BacktestRunConfig validation
    "honba-sweep",      # L5: only in the commit that wires POST /sweeps
}
# ALLOWED_DEV["honba-api-rest"] stays {"honba-testing"}.
# honba-async is NOT added: the kernel uses honba_engine::Clock, workers are std threads.
# honba-market is already allowed; add it to Cargo.toml only if the executor needs it.
```

  All edges point inward (L7 -> L3/L4/L5). `honba-api` is unchanged
  (`{messages, entities, strategy, indicators}`); `getrandom` and `tempfile`-style crates are not
  `honba-*` and are not governed by the script, but `getrandom` must not appear in any
  `WASM_CRATES` entry.

### 9. Python and MCP surface

- `honba.client.Client` gains `submit_backtest(...) -> BacktestResponse`, `backtest(run_id)`,
  `backtest_journal(run_id) -> list[Trade]`, `submit_sweep(...)`, `sweep(job_id)` and a
  convenience `wait(run_id, *, timeout, poll_interval)` that polls to a terminal status; request
  builders live in `honba/client/requests.py` with the same `_id_path_segment` validation. All work
  unchanged over `HttpTransport` and `InprocTransport` (the latter accepts a `journals_dir`
  keyword). A failed run surfaces `error` as the typed `ErrorDetail` model, not an exception, since
  the poll itself succeeded.
- MCP: the existing `backtest` and `sweep` tools keep their names (submit only); `honba-codegen`
  (`mcp.rs`) adds `get_backtest`, `get_backtest_journal` and `get_sweep`, bound in
  `python/src/honba/ai/mcp/tools.py`. An agent can then submit, poll and read without the CLI.
- CLI: `python/src/honba/cli/backtest.py` stays empty in this ADR; E4-S3 owns the `honba backtest`
  entry and must build it on `honba.client`, not on a second execution path.
- **Strictness change:** `BacktestRequest` today accepts `{}`; after this ADR a submit needs
  `seed`, `strategy`, `universe`, `start` and `end`. No working client breaks (the routes are 501
  today), but ROADMAP D1 and R2 do not state it and are reconciled in the follow-up below.

### 10. How E11-S4 SSE attaches later (non-normative)

This section records design intent; it is not binding and E11-S4's own ADR decides it.

- Every record in `events.ndjson` has an implicit 0-based sequence (its line number), usable as
  the resume token (`?resume_from_seq=N`, the E11-S4 acceptance name).
- A stream route (`GET /backtests/{id}/events`, a new `ENDPOINTS` row, regenerated per ADR 0014)
  would replay records `>= N`, then follow the run, with status frames derived from the manifest
  carrying `seq` = records already written, so status never becomes a journal record.
- Progress *is* the journal: fills, rejections and the terminal transition are ordinary records,
  so there is no second channel to keep in step.

## Test plan

Unit and integration levels are both required (workspace rule R3). Unit tests touch no
filesystem; integration tests use scratch directories under `env!("CARGO_TARGET_TMPDIR")`
(cargo's `target/tmp`), never the repo's `data/` and never `/tmp`. No `tempfile` dev-dependency
is added.

**Unit, `crates/honba-api/src/tests/`** (new files `runs.rs`, `run_id.rs`, `run_requests.rs`):

- `run_status_transition_table` — every `(status, event)` cell, legal and illegal, from one table
  literal; `terminal_is_final` (no transition out of `completed`/`failed`/`cancelled`);
  `pending_to_cancelled_legal`.
- `run_id_format` (26 chars, alphabet, round-trip parse/display), `run_id_rejects` (`../x`,
  `..%2F..%2Fetc`, `%2e%2e`, lowercase, 25/27 chars, `I`/`L`/`O`/`U`),
  `run_id_monotonic_within_ms`, `run_id_monotonic_on_clock_regression`,
  `run_id_overflow_is_error`, `run_ids_differ_for_same_seed`.
- `backtest_request_resolve` — `{}`, missing each required field, `seed: 0`, defaults for
  `bar_spec`/`initial_capital`, unknown strategy; `sweep_request_resolve` likewise.
- `retention_evicts_only_when_both_old_and_beyond_count` — the and/or rule on a synthetic list,
  including ties on `finished_at`.

**Integration, `crates/honba-api-rest/tests/`** (new `runs.rs`, `runs_restart.rs`,
`runs_determinism.rs`):

- `submit_poll_journal` — submit with a fixture strategy and data, poll to `completed`, read
  `metrics`, `assumptions` and the journal; `TradesResponse` matches the fill-to-`Trade` mapping.
- `failed_run_carries_error` — an executor failure yields `status: failed` and `error`.
- `bad_id_is_404` — ill-formed ids including `..%2F..%2Fetc` and `%2e%2e%2f` on all journal and
  status routes, asserting no path outside the scratch root was touched; `kind_mismatch_is_404`.
- `queue_full_is_429` with `max_concurrent = 1`, `max_queued = 1`.
- `partial_journal_is_prefix` — a reader during `running` gets a prefix of the final trades.
- `restart_recovers_interrupted` — a scratch journals root with `pending` and `running`
  manifests reloads as `failed` / `interrupted`; terminal manifests unchanged; unknown
  `manifest_version` skipped.
- `graceful_shutdown_cancels` — non-terminal runs become `cancelled` with `finished_at`.
- `determinism_under_workers` — the same seed at `max_concurrent` 1, 4 and 16 gives
  byte-identical `events.ndjson` across runs and across worker counts.
- `responses_leak_no_paths` — no response or error body contains the scratch root.
- `tests/capabilities.rs` (existing) — the six rows leave `NOT_IMPLEMENTED_ENDPOINTS`, and the probe
  asserts non-501 for each.

**Shared contract, both transports:** `python/tests/integration/test_client_parity.py` gains a
run scenario (submit, `wait`, status, journal) run against `InprocTransport` and `HttpTransport`,
asserting equal responses and byte-identical `events.ndjson`; Python unit tests cover the new
request builders and models. Codegen drift (`cargo test -p honba-codegen`) covers the regenerated
artifacts.

## Consequences

- Wire changes: `RunStatus` gains `cancelled`; `BacktestResponse` and `SweepResponse` gain optional
  `error`. Both are permitted within `/api/v1` by ADR 0012 rule 2 (new enum variant, new optional
  field) and need no `schema_version` bump of their own (rule 1). The new variant **is breaking for
  exhaustive decoders** (rule 3): the Python `Literal` mirror, pydantic models and generated TS
  reject `cancelled` until regenerated. Per rule 5 the `CHANGELOG.md` migration note lands in the
  same commit as the code, and `make codegen` regenerates all five artifacts in that commit. No
  endpoint is added, so `API_VERSION` (`1.0.0`) is unchanged.
- `/capabilities.not_implemented` shrinks to the trading routes.
- `honba-api-rest` becomes a worker host and acquires filesystem writes, new for that crate; its
  layering entry grows the edges in decision 8.
- E4-S3, E4-S5, E11-S3 (rest) and `honba-examples/backtesting/07_run_via_client.py` get a concrete
  contract: submit, poll, read metrics, read the journal, keyed by one id.

**Consumers to flag (other repos; confirm before editing, per the workspace rules):** the Python
wire mirrors and `.pyi` stubs (this repo, regenerated); `honba-frontend` generated TS
(`src/core/types/generated/domain.ts`) and any exhaustive switch on `RunStatus`;
`honba-examples/backtesting/07_run_via_client.py`; `honba-docs` (REST and run lifecycle pages).

**Required follow-ups (not edited by this ADR):**

- `docs/ROADMAP.md` D1 row (~line 335) and the ADR list (~line 355) already say "decided"; they
  must be reconciled with this text: `pending` (not `queued`), the seed/required-field strictness,
  graceful-shutdown-only cancellation, and the R2 row. E4-S3's acceptance
  (`rest_backtest_roundtrip_matches_session`, ~line 279) must state "on the Rust-registered
  strategy subset".
- ADR 0013 needs a "superseded in part by ADR 0017" addendum on its list of 501 routes
  (lines 132-134).

## Known limits

- No collection route (`GET /backtests`); adding it is an `ENDPOINTS` row plus codegen.
- No client cancellation and no defined cooperative stop (decision 7).
- Journal over REST is fills-only (`TradesResponse`) with zero per-fill `costs` until E4-S1; sweep
  journals are per trial (E4-S5).
- No auth or ACL on runs (E11-S8). Ids are hard to guess across milliseconds but sequential within
  one; neither is authorization. Any local client can read any run.
- Single process, no journal-directory lock; retention runs at start-up only.
- In-process runs do not survive interpreter exit (decision 5).
- Determinism is checked on fixtures only until E4-S1's dataset id (decision 6).
- The executor runs registered Rust strategies only; there is no IR interpreter, so other
  strategies are an honest 422.
- `BacktestMetrics` is a fixed five fields (`responses.rs:120`); DSR/PBO/holdout gates (E4-S6)
  arrive as additive fields.

## Amendments

Accepted 2026-10-07 with these changes from the proposed text, resolving the critical review:

1. Removed the false "no `getrandom` in `Cargo.lock`" claim; ids use 80 bits of `getrandom` (direct
   dependency, 0.3) with a monotonic in-millisecond increment; ordering claim weakened for clock
   steps; ids validated against `^[0-9A-HJKMNP-TV-Z]{26}$` before any filesystem access, 404 on
   mismatch, with traversal tests.
2. Shutdown writes `cancelled` for every non-terminal run; a non-terminal manifest at start-up is
   always a crash (`failed`/`interrupted`); the undefined marker file is removed; in-process
   transport loss at exit stated.
3. `error: Option<ErrorDetail>` on `BacktestResponse`/`SweepResponse`; `RunStatus::Failed` doc fixed.
4. Versioning wording: rule 2 permits, rule 3 breaks exhaustive decoders, rule 5 CHANGELOG note;
   cross-repo consumers flagged.
5. `JournalWriter` (sync) vs `honba_ports::Sink` (async) and the bridge; `RunExecutor`, `RunJob`,
   `RunOutcome` defined; exact `dependency_graph.py` edits (incl. `honba-config`, no
   `honba-async`); layering table corrected (`honba-market` already allowed).
6. Test plan section (unit, integration, cross-transport contract; scratch dirs under `target/`).
7. Determinism scoped to fixtures until E4-S1; pinned inputs listed; strategy resolution and
   session-scoped catalog; Python parity on the Rust-registered subset; `manifest_version`; no
   paths on the wire.
8. Bounded queue (429 `run_queue_full`), worker pool, `spawn_blocking`, partial-journal prefix
   reads, id-kind mismatch 404, per-route kinds, fill-to-`Trade` mapping, retention as an explicit
   and/or rule, cancellation semantics deferred, SSE section non-normative.
9. Python and MCP surface; seed/required-field strictness noted against ROADMAP D1/R2.
10. ROADMAP D1 and ADR 0013 reconciliation listed as required follow-ups; journal aligned with ADR
    0019's vocabulary and `SCHEMA_VERSION` 4; citation fixes (`responses.rs:95`/`:135`/`:150`,
    `requests.rs:100`/`:127`, `state.rs:17`, `api.rs:43`, `audit.rs:78`, `ROADMAP.md:404`/`:426`,
    `dependency_graph.py:209`, `lib.rs:146`).
