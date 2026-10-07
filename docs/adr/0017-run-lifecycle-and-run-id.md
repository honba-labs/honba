# ADR 0017: Run Lifecycle and `run_id`

Date: 2026-10-07. Status: proposed. Roadmap story: decides D1; unblocks E4-S3, E11-S3 (backtests,
sweeps, journals), E4-S5 and the E11-S4 attach point.

## Context

Everything a run needs *as a wire type* already exists; nothing exists *as a server object*.

- `RunStatus` is `pending | running | completed | failed` (`crates/honba-api/src/responses.rs:91`,
  `#[non_exhaustive]`, snake_case), aliased as `BacktestStatus` and `SweepStatus`
  (`responses.rs:110`, `:113`). There is no `cancelled`, no `RunState`, and no run state machine
  anywhere in `crates/` or `python/src/honba`.
- `BacktestResponse { run_id, status, metrics, assumptions }` (`responses.rs:134`) and
  `SweepResponse { job_id, status, report }` (`responses.rs:149`) carry ids that **nothing mints**:
  `run_id`/`job_id` appear only as DTO fields and test literals
  (`crates/honba-api/src/tests/unknown_fields.rs:83`). No `uuid`, `ulid`, `rand` or `getrandom`
  appears anywhere in `Cargo.lock`; `sha2` is the workspace's hashing dependency
  (`Cargo.toml:53`) and already produces the compiled-strategy content id
  (`crates/honba-api/src/strategies.rs:134`).
- The endpoint registry `honba_messages::ENDPOINTS` (`crates/honba-messages/src/endpoints.rs:153`)
  has 22 rows. The run rows are `POST /backtests`, `GET /backtests/{id}`,
  `GET /backtests/{id}/journal`, `POST /sweeps`, `GET /sweeps/{id}`, `GET /journals/{id}`. There is
  **no collection route** (`GET /backtests` is not in the registry). All six rows are in
  `honba_api_rest::NOT_IMPLEMENTED_ENDPOINTS` (`crates/honba-api-rest/src/lib.rs:216`) and answer
  501 `not_implemented`; `/capabilities.not_implemented` is derived from that list (`lib.rs:244`)
  and `crates/honba-api-rest/tests/capabilities.rs` probes the router built from `AppState::default()` to keep it honest.
- Request DTOs `BacktestRequest` / `SweepRequest` (`crates/honba-api/src/requests.rs:98`, `:124`)
  are all-optional and `deny_unknown_fields`; `seed` documents "Same seed and data give a
  byte-identical journal" (`requests.rs:119`).
- Execution today is synchronous and local: `BacktestSession.run() -> BacktestResult`
  (`python/src/honba/session.py:246`, `:286`) has no run id, status, journal or polling;
  `python/src/honba/cli/backtest.py` is 0 bytes and not registered in `cli/main.py`; the Rust
  `honba-cli backtest` is single-shot over a synthesised series (`crates/honba-cli/src/backtest.rs:70`).
- There is **no journal writer**: only the `honba_ports::Sink` trait (`crates/honba-ports/src/sink.rs:22`),
  implemented outside tests by nobody; `AuditLog` is in-memory (`crates/honba-engine/src/audit.rs:77`);
  `data/journals` exists only in prose. Journal schema v1 (E4-S1) is not started.
- No streaming route exists (`22 routes, none streaming` — `docs/ROADMAP.md:425`); `honba-async`
  offers `EngineHandle`, `Command::{Stop, State}` and `LiveClock`/`HistoricClock`, no SSE.
- `AppState` (`crates/honba-api-rest/src/state.rs:16`) holds four `Arc<dyn ...>` read ports plus an
  `Arc<Mutex<StrategyCatalog>>`; `honba serve` builds it once from `--data-dir`
  (`crates/honba-cli/src/serve.rs:31`) and `honba._honba.api_request` caches one per data directory
  (`crates/honba-py/src/pyclasses/api.rs:42`).
- Codegen maps both journal rows to `TradesResponse`
  (`crates/honba-codegen/src/endpoints.rs:29`, `:37`) and emits exactly `200` plus `default` per
  operation (`crates/honba-codegen/src/endpoints.rs`, `operation()`), never `202`.
- Layering: `scripts/dependency_graph.py` enforces `ALLOWED_PROD` per crate and fails on an
  unregistered crate (`scripts/dependency_graph.py:208`); `honba-api-rest` (L7) currently depends on
  `honba-api`, `honba-messages`, `honba-entities`, `honba-data`, `honba-ports` only.

## Decision

### 1. A `Run` is a server-side object with five states

`pending -> running -> completed | failed | cancelled`, plus `pending -> cancelled` for a run
cancelled before it started. `completed`, `failed` and `cancelled` are terminal; a terminal run
never changes state again.

- The wire enum stays `RunStatus` and gains exactly one variant, `Cancelled`. `pending` **is** the
  queued state: its existing doc comment already reads "Accepted, not started"
  (`responses.rs:95`), so no rename is needed.
- Rejected: renaming `pending` to `queued` to match ROADMAP D1's prose. It is a wire break in an
  enum that is already committed to `schema/domain`, OpenAPI, the `.pyi` stubs and the frontend
  TypeScript, for a difference of one word.
- Rejected: a second, internal `RunState` type beside `RunStatus`. Two state vocabularies is the
  same drift problem ADR 0012 solved for versions; the DTO field and the store speak one enum.

### 2. One id space, minted at the edge

- `run_id` (backtests) and `job_id` (sweeps) are the **same generator and the same space**: a run is
  a run, `kind` distinguishes `backtest` from `sweep`, and both field names stay as committed.
- The value is ULID-shaped: 26 characters of Crockford Base32 — a 48-bit millisecond timestamp
  prefix (so ids sort by creation time at millisecond granularity) followed by 80 bits derived from
  `sha2` over `(unix_nanos, pid, per-process counter)`. No new dependency: `sha2` is already in the
  workspace, and a `ulid`/`uuid` dependency would have to be added to `Cargo.lock` for one string.
  A future switch to a real ULID is invisible to clients.
- Ids are opaque and not derived from content: two runs of the same seed must get different ids,
  and the id must not appear in the journal body (decision 3).
- Minting reads the wall clock, so it happens in `honba-api-rest` (the accept handler), not in
  `honba-api`. That keeps the pure layer clock-free for the E2-S3 lint (`no_system_time_in_domain_crates`).
- The journal is addressed by this same id: `GET /backtests/{id}/journal` and `GET /journals/{id}`
  return the journal of run `{id}`; on disk the id is the directory name.

### 3. In-memory registry plus an on-disk journal first

Per run, under a journals root defaulting to `data/journals` (cwd-relative, matching
`DataSourceConfig::Local { root: "data/catalog" }`; `honba serve --journals-dir` and a keyword
argument on the in-process transport override it):

```
data/journals/<run_id>/manifest.json   # run metadata + status, rewritten atomically
data/journals/<run_id>/events.ndjson   # append-only JSON Lines of the wire Message envelope
```

- `manifest.json` holds `run_id`, `kind`, the resolved request, `status`, `seed`, `strategy` content
  id, `created_at` / `started_at` / `finished_at` (wall clock, metadata only) and, once terminal,
  `metrics` and `assumptions`. It is written to a temp file and renamed, so a reader never sees a
  half-written manifest.
- `events.ndjson` is one wire `Message` per line (`{"schema_version":3,"event":{...},"ts_init":{...}}`,
  the shape `schema/golden` already pins), appended in run order, flushed on every terminal
  transition.
- **The journal body carries no run id and no wall-clock time.** Byte-identity of `events.ndjson`
  for a given seed (decision 6) would be broken by either. Run-scoped metadata lives in the
  manifest.
- The in-memory registry is a `HashMap` inside `AppState` behind an `Arc<Mutex<...>>`, exactly like
  `StrategyCatalog` (`state.rs:31`), seeded from the manifests at start-up. It is a cache of the
  manifests, never the only copy.

### 4. Lifecycle, workers and cancellation

- `POST /backtests` (or `/sweeps`) validates, mints the id, writes `manifest.json` with
  `status = pending`, enqueues the job and answers **200** with the `BacktestResponse` /
  `SweepResponse`. Rejected: `202 Accepted` — the OpenAPI generator emits only `200` for every
  operation, so `202` would need a generator change and would contradict the committed spec for no
  client benefit.
- Rejected: running the backtest synchronously inside the request. Long runs would block the
  server, there would be nothing to poll, stream or cancel, and it would contradict the
  fire-and-poll job shape ADR 0009 §3 already assumes.
- A worker thread (one per run, bounded by a configurable `max_concurrent` defaulting to available
  parallelism) moves `pending -> running`, executes the run, then writes `finished_at` and the
  terminal status to the manifest before updating the registry. The engine kernel stays synchronous
  and tokio-free (`SYNC_KERNEL_CRATES`, `scripts/dependency_graph.py:157`); the worker is a plain
  thread, not a task driving the kernel.
- Cancellation in v1 has no HTTP route (there is no `DELETE /backtests/{id}` in the registry).
  `cancelled` is reached by a graceful shutdown: `honba serve` stops accepting work, asks each
  worker to stop, and writes `cancelled` for every run still non-terminal. A process that dies
  without that path leaves a non-terminal manifest, which start-up closes as `failed` (decision 5).

### 5. Restart behaviour

- At start-up the journals root is scanned once and the registry is rebuilt from the manifests.
  A manifest whose status is `cancelled` or `completed` or `failed` is kept as-is. A manifest still
  `pending` or `running` means the process died mid-run: it is closed as `cancelled` if the journal
  directory contains the graceful-shutdown marker written in decision 4, otherwise as `failed` with
  `ErrorDetail { code: internal_error, context.reason: "interrupted" }` and
  `finished_at` = the start-up time.
- An id with no manifest (evicted, or never issued) is `404 not_found`, never a 501: the route is
  built, the resource is gone.
- Runs are per process. Two `honba serve` instances pointed at one journals root would race on the
  same directories; there is no lock file in v1 (Known limits).

### 6. Determinism

- `seed` is **required** on `POST /backtests` and `POST /sweeps`: missing or zero is `422
  validation_invalid_request` with `context.field = "seed"`, decided by a pure resolver
  (`BacktestRequest::resolve`, `SweepRequest::resolve`) in `honba-api`, mirroring
  `BarsQuery::resolve` (ADR 0013). This matches `SweepRequest.seed`'s doc ("Required for a
  reproducible sweep"), `BacktestRunConfig.seed: u64` validated non-zero
  (`crates/honba-config/src/lib.rs:34`, `:145`, `RunConfigError::ZeroSeed`) and the fact that
  `BacktestRequest` currently accepts `{}`. Rejected: a server-generated seed returned in the
  response — useful later, but it hides the seed from the caller of a research API.
- Same seed + same dataset + same manifest ⇒ byte-identical `events.ndjson`. The id, manifest
  timestamps and any wall clock are outside the journal body (decision 3), so this holds even
  though ids and `created_at` differ.
- Runs execute on `HistoricClock` in backtest; nothing in the run path reads the wall clock, so the
  guarantee holds at 1, 4 and 16 worker threads as the definition of done already requires
  (`docs/ROADMAP.md:403`) and as `honba-sweep` already tests for sweeps.
- `config_hash`, `dataset id` and `git rev` are **not** recorded yet (they do not exist in the
  codebase); they are E4-S1's journal schema and slot into `manifest.json` without touching the
  state machine or the id.

### 7. REST behaviour, and what stays 501

| Case | Status | Body |
|---|---|---|
| `POST /backtests`, valid | 200 | `BacktestResponse {run_id, status: pending}` (no `metrics`) |
| `POST /backtests`, missing/zero `seed`, missing `strategy`/`universe`/`start`/`end` | 422 | `validation_invalid_request`, `context.field` |
| `GET /backtests/{id}`, known | 200 | `BacktestResponse`; `metrics` only when `completed`, `assumptions` only when terminal |
| `GET /backtests/{id}`, unknown/evicted | 404 | `not_found` |
| `GET /backtests/{id}/journal`, `GET /journals/{id}` | 200 | `TradesResponse` (the fills of that run) |
| `POST /sweeps`, `GET /sweeps/{id}` | 200 / 404 | `SweepResponse`, same rules |
| anything not built | 501 | `not_implemented` |

- Journal routes keep the **committed** `TradesResponse` payload in v1: the fills, in run order,
  read back from `events.ndjson`. Rejected: introducing `JournalResponse` now — it retypes a
  response the OpenAPI already publishes (`docs/adr/0012` rule 2 reads a retype as `/api/v2`
  material) and it is exactly what D3 (journal schema v1, E4-S1) has its own ADR for. The full
  event stream is on disk and arrives over the wire with E11-S4 (decision 8).
- `assumptions` stays `Option<serde_json::Value>`; E4-S3 fills it with
  `{"not_modelled": [...], "timing": ...}`. Narrowing `Value` to a typed struct is a later,
  separately-noted reshape.
- After R2, `NOT_IMPLEMENTED_ENDPOINTS` shrinks to the four trading rows — `POST /orders`,
  `GET /orders`, `DELETE /orders/{id}`, `POST /positions/close` — which stay 501 until E11-S7
  (they need E2-S2 and E5-S4 first). `GET /schema` stays a stub (ADR 0013). Removing a row from
  the list is what flips `/capabilities.not_implemented`; the probe test then requires the default
  router to answer non-501 on those routes.

### 8. Where the code lives (and the one layering change)

- **Pure, in `honba-api` (L6):** `RunId` minting helper (hash only), the transition function
  `RunStatus::next(...)`, `RunRecord`, the request resolvers from decision 6. No new crate edges
  for `honba-api`.
- **I/O, in `honba-api-rest` (L7):** the registry in `AppState`, the manifest/journal files, the
  worker threads, the six handlers.
- **The executor behind a trait `RunExecutor` defined in `honba-api`** (`fn execute(&self, job:
  RunJob, journal: &mut dyn JournalWriter) -> Result<RunOutcome>`), naming only types `honba-api`
  may already see (`StrategyIr` is L4, `honba-api` already depends on `honba-strategy`). The
  concrete implementation lives in `honba-api-rest` so that **both** composition roots —
  `honba serve` and `honba._honba.api_request` — get runs without wiring anything, and
  `AppState::default()` keeps answering honestly to the 501 probe test.
- Dependency-graph change (the only one):
  `ALLOWED_PROD["honba-api-rest"]` gains `honba-engine`, `honba-strategy`, `honba-sim`,
  `honba-analytics`, and `honba-sweep` (if sweeps are wired in the same commit;
  `honba-cli` as committed does not depend on `honba-sweep`, see
  `crates/honba-cli/Cargo.toml`). All are inward (L7 -> L3/L4/L5) and the direction rule
  is unchanged.
- Rejected: injecting the executor from the composition roots (`honba-cli` and `honba-py`), which
  would leave `AppState::default()` answering 501, force the capabilities probe to build a
  non-default state, require two identical wirings, and still need `honba-analytics` added to
  `honba-py` for metrics. Rejected: a new `honba-runs` crate — a crate holding one feature adds a
  registry row and an edge set for no isolation gain, and it would sit at L7 beside
  `honba-api-rest` anyway.
- Strategy execution inside the executor uses the registered strategy factory
  (`honba_sweep::StrategyFactory`'s shape: `build(seed) -> Box<dyn Strategy>`); a `StrategyIr`
  that has no registered Rust implementation is a `422 validation_invalid_request` naming
  `context.field = "strategy"` until an IR interpreter exists (Known limits).

### 9. How E11-S4 SSE attaches later

Nothing in decisions 1-7 changes when streaming lands.

- Every record in `events.ndjson` has an implicit sequence: its 0-based line number. That number
  is the resume token (`?resume_from_seq=N`, the E11-S4 acceptance name).
- The stream route (`GET /backtests/{id}/events`, a **new** `ENDPOINTS` row, so `make codegen`
  regenerates OpenAPI/domain schema/`.pyi`/MCP/TS per ADR 0014) replays records `>= N`, then
  follows the run live through the same store. Status frames are derived at stream time from the
  manifest and carry `seq` = the number of records already written, so a client resuming at `N`
  gets records `N..` and then the current status without the manifest having to be a journal
  record (which would break byte-identity).
- Progress *is* the journal: fills, rejections and the terminal transition are ordinary records, so
  there is no second channel to keep in step, and the run id/states are already the addressing
  scheme. The streamed event schema is registered in `honba-codegen` (ADR 0014) and appears in
  `/capabilities` like every other route.

## Consequences

- Wire change: `RunStatus` gains `cancelled`. Additive within `/api/v1` (ADR 0012 rules 1-3), so
  no `schema_version` bump, but `make codegen` must regenerate all five artifacts in the same
  commit and the CHANGELOG carries the note that strict decoders (the Python `Literal` mirror,
  pydantic models, generated TS) reject an unknown spelling until regenerated.
- `NOT_IMPLEMENTED_ENDPOINTS` loses six rows; `/capabilities.not_implemented` shrinks to the
  trading routes, which is now an honest statement of "these can move money and are not gated yet".
- `honba-api-rest` becomes a worker host, not only a read API: its layering entry grows five
  inward edges (decision 8) and it acquires filesystem writes, which is new for that crate and
  must be covered by tests that use `tempfile`-style directories, never the repo's `data/`.
- E4-S3, E4-S5, E11-S3 (rest) and `honba-examples/backtesting/07_run_via_client.py` now have a
  concrete contract: submit, poll, read metrics, read the journal, all keyed by one id.
- Retention and restart rules make a run durable enough for the research loop (E4-S1 can key its
  journal schema on `run_id`) without adopting a database.

## Known limits

- No collection route: `GET /backtests` (list a run's history) is not in `ENDPOINTS`; adding it is
  an `ENDPOINTS` row plus codegen, deliberately deferred.
- No `DELETE /backtests/{id}`: `cancelled` is reachable only via graceful shutdown in v1, so a
  client cannot cancel a run it started.
- Journal over REST is fills-only (`TradesResponse`) until D3/E4-S1 lands `JournalResponse`; the
  full stream exists on disk and, later, through SSE.
- No auth or ACL on runs (E11-S8): ids are unguessable, which is not authorization; any local
  client can read any run.
- Single process, no journal-directory lock: two servers on one journals root will corrupt
  manifests. Retention eviction (default: keep the 1,000 most recent terminal runs and anything
  under 30 days) runs at start-up only, so disk use is bounded only across restarts.
- The executor runs registered Rust strategies only; there is no IR interpreter, so most manifests
  cannot be executed server-side yet. `POST /backtests` honestly reports that as 422 rather than
  starting a run it cannot finish.
- `BacktestMetrics` is a fixed five fields (`responses.rs:119`); DSR/PBO/holdout gates (E4-S6)
  arrive as additive fields on the same record.
