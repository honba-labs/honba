# ADR 0021: Run Event Streaming (SSE)

Date: 2026-10-10. Status: **proposed**. Roadmap story: decides D6; implements E11-S4
("SSE for run progress and journals (resume-from-sequence), WS for events later; schema
for streamed events", `docs/ROADMAP.md:326`). Attaches at ADR 0017 decision 10, which is
explicitly non-normative and says E11-S4's own ADR decides it.

## Context

A run is a server object today but its status is only ever read by polling.

- The run lifecycle is built and committed: `RunStatus` (`crates/honba-api/src/responses.rs:97`),
  the run state machine (`crates/honba-api/src/runs.rs`), `RunStore` with `manifest.json` +
  `events.ndjson` per run (`crates/honba-api-rest/src/run_store.rs:92`), a worker pool on plain
  `std::thread`s (`crates/honba-api-rest/src/service.rs:124`), `POST /backtests` / `/sweeps`,
  `GET /backtests/{id}` / `/journals/{id}`, and backend routes verified by
  `crates/honba-api-rest/tests/rest_backtest.rs`.
- The run journal (`NdjsonJournal`, `crates/honba-api-rest/src/journal.rs`) is append-only JSON
  Lines of the wire `Message` envelope, one message per line, flushed every 256 records and on
  every terminal transition (`crates/honba-api-rest/src/executor.rs:437`). The journal body
  carries no run id, no wall clock and no path (ADR 0017 decision 3), and status never becomes a
  journal record — it lives in `manifest.json` (ADR 0017 decision 7).
- Reading is all-or-nothing: `RunStore::read_journal` (`run_store.rs:273`) reads the whole file,
  splits complete lines, decodes each into a `Message`. There is no range or incremental read and
  no streaming of any kind in the tree: `rg "sse|text/event-stream|Server-Sent"` finds nothing in
  `crates/` or `python/src/`.
- The only progress signal is the manifest `status` (written on each transition). There is no
  percentage, bar counter or broadcast channel from the worker (`service.rs:384-413` transitions
  `pending→running→completed|failed` on its own thread). A client today loops
  `GET /backtests/{id}` until `is_terminal` (`python/src/honba/client/client.py:246`,
  `Client.wait`).
- The HTTP stack is axum 0.7 (`crates/honba-api-rest/Cargo.toml:26`), which ships
  `axum::response::sse::{Event, Sse}` plus `KeepAlive` with no feature change; axum also gives us
  `futures::stream` primitives through its tokio dependency set. The in-process transport is
  `honba._honba.api_request` → `honba_api_rest::dispatch` (`crates/honba-py/src/pyclasses/api.rs:151`,
  `crates/honba-api-rest/src/dispatch.rs:86`), an async function that **buffers the entire
  response** into `(u16, String)` — a streaming response cannot be observed incrementally through it.
- Codegen owns the wire contract (ADR 0014): a route must appear in `honba_messages::ENDPOINTS`
  (`crates/honba-messages/src/endpoints.rs:153`), get a `response_type_for` / `request_type_for`
  row in `crates/honba-codegen/src/endpoints.rs:14`, and its response type must be registered so
  `domain_schema.json`, OpenAPI, TS and `.pyi` all see it. `honba-codegen` already depends on
  `honba-api` (it registers `BacktestResponse`, `BacktestMetrics`, `SweepResponse`, …
  `crates/honba-codegen/src/registry.rs:194`), so a frame type in `honba-api` is reachable.
- `RunStatus`, `ErrorDetail`, `Message` already exist and are usable by a frame type in
  `honba-api` (`responses.rs`, `honba-messages::ErrorDetail`, `honba-messages::Message`).
  Layering is enforced by `scripts/dependency_graph.py`; `honba-api` keeps its edges, no new crate.

## Decision

### 1. Protocol: SSE first, one route, one frame type

Streaming is **Server-Sent Events** (SSE) over `GET`, per D6's "SSE first". WebSocket is deferred
(the run event set is small, SSE has automatic resume via `Last-Event-ID`, and browser `EventSource`
needs no client library). One new route:

```
GET /backtests/{id}/events?resume_from_seq=N
```

- A backtest **journal stream**: replay the journal records with line index `>= N` (0-based), then
  follow the run, appending records as the journal grows and a `status` frame whenever the manifest
  status changes. The stream ends when the run is terminal and all its records have been delivered.
- `GET /journals/{id}/events` is **not added**: `/backtests/{id}/events` is the one stream
  surface, mirroring how `GET /backtests/{id}/journal` and `GET /journals/{id}` share one reader.
  Sweep journals stay 422 (`sweep_journal_per_trial`, ADR 0017 decision 7) because sweeps write no
  run-level journal.
- The response is `text/event-stream`. Each frame is one SSE `data:` JSON line with `id:` set to
  the frame's `seq`; the connection is kept alive with `KeepAlive` heartbeats (SSE comment lines,
  delivered every 15 s) so proxies do not time out a long `running` run with no records.
- The frame type is the *only* wire addition:

```rust
// honba-api
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize, JsonSchema)]
#[serde(tag = "kind", rename_all = "snake_case")]
pub enum StreamFrame {
    Status { status: RunStatus, seq: u64, error: Option<ErrorDetail> },
    Record  { seq: u64, message: Message },
}
```

`Record.seq` is the record's 0-based line index in `events.ndjson`. `Status.seq` is the number of
journal records delivered so far (equivalently, the next line index to read). **Every frame's
`seq` is a valid resume token**: resuming at `N` replays records with line index `>= N`, so a
client that stops after a `Record.seq` or a `Status.seq` of `K` resumes with `resume_from_seq=K`
and neither skips nor repeats a record. A `Status` frame carries `error` only when the run failed
and the manifest records an error (ADR 0017 decision 7), present on the terminal `failed` frame.

Rejected: a status record inside the journal — it would break the byte-identity guarantee of
`events.ndjson` (ADR 0017 decision 3) and duplicate the manifest. Rejected: an envelope type with
a `kind: "status"` string — the serde-tagged enum already produces that on the wire.

### 2. Frame emission order

1. On connect, read the manifest once and emit a `Status` frame with the current status and
   `seq = resume_from_seq` (the reconnect acknowledgment).
2. Replay journal records with index `>= resume_from_seq`, one `Record` frame each.
3. Then follow in ticks (default 25 ms): read new complete records since the last emitted index
   (`Record` frames), then read the manifest; if the status differs from the last emitted status,
   emit a `Status` frame with `seq` = number of records delivered so far.
4. When the manifest status is terminal and every remaining record has been delivered, the stream
   ends. For a run already terminal at connect, the initial `Status` frame is terminal and the
   stream ends after the replay; no second terminal frame is emitted. Frames are therefore
   always ordered status-ack → records → transitional statuses → final terminal status (or just
   terminal-ack → records when joining late).
5. A trailing partial journal line is **not** emitted and the next tick re-reads it from the same
   byte offset; like `read_journal`, a reader sees only complete records, so the stream is always a
   strict prefix of the final journal plus status frames.
6. Connection drop cancels the SSE task (axum drops it). There is no server-side push state, no
   cancellation of the run itself, and no client "unsubscribe" — same as every other read route.

### 3. Resume selection and errors

- `resume_from_seq` (default `0`) is a query parameter read via a small DTO
  `BacktestEventsQuery { resume_from_seq: Option<u64> }` in `honba-api`, registered with codegen so
  it appears as a documented query parameter. Negative, non-numeric or overflowing values are a
  `422 validation_invalid_request` (`context.field = "resume_from_seq"`).
- An `E...` value beyond the current journal length simply replays nothing and streams freely
  (a client that resumes after terminal reads the final status frame and the stream closes).
- Errors **before** the stream starts use the normal error envelope and statuses: ill-formed,
  unknown, evicted or sweep ids → `404 not_found`; `runs` not configured → `503`; bad
  `resume_from_seq` → `422` (as above). Once the stream is open, errors never appear as SSE text:
  a failed run is a `Status{status: failed, error}` frame and the stream closes.
- Journal records that fail to decode (a corrupt line the writer could not have produced) are
  skipped and the stream continues; the manifest status remains authoritative. This mirrors the
  "strict-reader" stance of ADR 0012 rule 3 with a bounded outcome: skip, never wedged.

### 4. Polling loop, not a new channel ("progress is the journal")

ADR 0017 decision 10 says progress *is* the journal. The worker thread already appends and flushes
and already transitions the manifest; adding a broadcast channel to the worker is the one change
that risks coupling the executor, so v1 deliberately does **not** add one:

- A dedicated SSE task (spawned by axum for HTTP, or by `api_request_stream` for in-proc) reads two
  small files per tick: the journal forward from its byte offset, and `manifest.json` (a few
  hundred bytes). It never reads more bytes than the run has produced since the last tick except
  on reconnect, where it skips `N` lines once.
- Tick 25 ms. The `resume_from_seq` acceptance name is read from the same file contract the worker
  writes, so there is no second channel to keep in step.
- Determinism: the *frames* are not a dimension of run determinism (the journal and manifest are
  unchanged by streaming); the conformance vectors pin the frame *schema*, not the arrival timing.
  Tests use the gated/real executors from `tests/support/mod.rs`.

Rejected: a broadcast/watch channel from `RunStore`/worker in v1. It adds a cross-thread contract
for no observable difference in the frame stream (records are only visible after flush anyway —
every 256 records), and the whole point of decision 1-3 is that the client rebuilds the same view
from the journal + manifest files.

### 5. In-process transport: a streaming sibling of `dispatch`

`honba_api_rest::dispatch` stays as-is (buffered, used by every JSON route). A new
`honba_api_rest::dispatch_stream` runs the same axum router against the SSE route and drives the
frame stream to a callback (`FnMut(StreamFrame) -> Result<(), String>`), returning the callback
error or `Ok(())` when the stream ends. It shares the router build with `dispatch` (one
`api_router_with_config`), so route registration cannot drift.

- `honba-py` exposes `honba._honba.api_request_stream(data_dir, method, path, query_json=None,
  journals_dir=None, ..., callback)` which runs `dispatch_stream` on a **dedicated thread with its
  own private current-thread tokio runtime** (never `runtime_start`'s shared runtime, so a
  long-lived stream cannot wedge concurrent in-process API calls) and calls `callback(frame_json)`
  per frame under the GIL, then returns `None`. Any exception raised by the callback aborts the
  stream and is re-raised to the caller as the function result.
- `python/src/honba/_honba.pyi` gains the stub; `mypy.stubtest` covers it in CI.
- `InprocTransport` gains a `stream(...)` method yielding the frame JSON lines; `HttpTransport`
  gains the httpx `stream=True` equivalent. The existing `request` API is unchanged.

### 6. Python client surface

- `honba.client.transport.Transport` gains `stream(method, path, *, query=None, body=None) ->
  Iterator[str]`, yielding one frame JSON per iteration for both transports (HTTP: httpx line
  iteration over `text/event-stream`, taking `data: ` payloads; in-proc: the frames from
  `api_request_stream`). This is a potential breaking change for third-party `Transport`
  implementations — the Protocol grows one method with a default-free declaration; the two in-repo
  transports implement it.
- `honba.client.models` gains a typed `StreamFrame` model (same tagged union; `Record` carries the
  decoded `Message`) and `honba.client.Client` gains `stream_backtest(run_id, *,
  resume_from_seq=0) -> Iterator[StreamFrame]` (a generator that yields frames and stops when the
  stream closes). `Client.wait` (polling) stays for callers that prefer it.
- Sweep streams are refused client-side with the same 422 the server sends (`sweep_journal_per_trial`).

### 7. Codegen and wire contract

- `honba_messages::ENDPOINTS` gains `("GET", "/backtests/{id}/events")`.
- Codegen: `response_type_for` maps the route to `StreamFrame` (so `domain_schema.json`, TS and
  `.pyi` carry it), `request_type_for` maps it to `BacktestEventsQuery` (so OpenAPI documents
  `resume_from_seq`), and the OpenAPI generator marks stream routes with `x-honba-stream: true`
  and a `text/event-stream` 200 content block referencing `StreamFrame` instead of
  `application/json`. The route is `Access::ReadOnly` (`readOnlyHint: true`); no MCP tool is added
  (the MCP tool table is hand-kept and run submission already covers the agent path).
- `make codegen` regenerates all five artifacts in the implementation commit; drift is blocked by
  `cargo test -p honba-codegen` and `make check-codegen-ci`.

### 8. Test plan (workspace rule R3)

Golden vectors: `schema/conformance/stream_frames.json` pins the frame shapes
(`{fixture_version:1, type:"StreamFrame", frames:[...]}`), run by Rust
(`crates/honba-api/tests/stream_frame_conformance.rs`) and by Python through the client model.

Unit:

- `resume_from_seq` (`crates/honba-api-rest/src/tests/streams.rs`): a pure helper that splits a
  journal byte-string at line `N` returns exactly records `>= N`; `N` past the end returns `[]`;
  a trailing partial line is kept out of the result and the byte offset is preserved. The reader
  shown in decision 4 calls this helper.
- `stream_frame_schema` / `event_schema` (`crates/honba-api/src/tests/`): `StreamFrame` serde —
  `Status` and `Record` round-trip with `kind` tag, snake-cased variants; `Status.seq` semantics
  documented; `error` absent unless failed.
- `backtest_events_query` (`crates/honba-api/src/tests/`): `resume_from_seq` parse/deny-behaviour
  of the DTO.

Integration (`crates/honba-api-rest/tests/`):

- `sse_backtest_progress` (in-process axum `oneshot` peer of `rest_backtest.rs`): submit a fixture
  run, stream `/backtests/{id}/events`, assert the frames are exactly the journal records (seq 0..)
  in order plus a first `status` frame and a terminal `status` frame, and that `status.seq` equals
  the record count. 
- `sse_resume_mid_stream` (with the gated executor): one reader to completion; a second reader
  with `resume_from_seq = K` sees exactly the `>= K` records and the terminal status, and the two
  streams never overlap or skip a record across the boundary.
- `sse_segmented_http` (TCP peer of `serve.rs`) over `127.0.0.1:0`: assert
  `content-type: text/event-stream`, `id:` present, records stream incrementally (the connection
  yields a prefix before the run completes) and closes after the terminal status frame.
- `sse_errors_before_stream` (tests/support crate): invalid id 404, sweep id 404, bad
  `resume_from_seq` 422, no `runs` configured 503.

Python (`python/tests/`):

- unit: `transport.py` stream parsing (httpx line filter; fake in-proc frames), `StreamFrame`
  model decode/encode, `stream_backtest` generator lifecycle.
- integration `python/tests/integration/test_stream_parity.py`: `stream_backtest` over
  `InprocTransport` and `HttpTransport` on synthetic data yield equal frame streams that match the
  Rust journal, extending `test_client_parity.py`'s run scenario.

## Consequences

- The wire contract gains one route, two types (`StreamFrame`, `BacktestEventsQuery`), additive
  within `/api/v1` (ADR 0012 rule 2); a strict decoder must regenerate to know `StreamFrame` (rule
  3) — CHANGELOG note in the same commit, `make codegen` in the same commit. `API_VERSION`
  unchanged (no endpoint removed or retyped).
- CLI `GET`-style monitoring for humans is unchanged; a future `honba events`/frontend consumer
  uses `stream_backtest`.
- No worker/executor change, no new channel, no new crate: the story lands entirely at the
  edges (one handler, one reader helper, one pyo3 function, the client).
- **Behaviour notes.** Multiple simultaneous readers of one run are independent (each has its own
  byte offset and tick). A reader that connects after retention eviction gets 404. In-process
  streams run on a private runtime per stream thread, so they do not consume the shared runtime's
  threads and do not touch `event_loop`'s lifecycle.
- **Cross-repo consumers (flag, do not edit in this effort):** `honba-frontend` (generated
  `domain.ts` grows `StreamFrame`; an SSE consumer lands with E11-S6/E9-S7);
  `honba-docs` (REST/run-lifecycle pages + stream page); `honba-examples` (a
  `backtesting/07_run_via_client.py` augment can use `stream_backtest`); `honba-strategies`
  (no change; catalogs already rerun through the journal path).

## Out of scope

| Item | Disposition |
|---|---|
| WebSocket event stream | deferred (D6: SSE first) |
| `GET /journals/{id}/events` | not added; `/backtests/{id}/events` is the one surface |
| Sweep progress ('trial K of N' frames) | deferred to E4-S5 rest / E11-S3; sweeps have per-trial journals |
| Cooperative run cancellation over the stream | deferred to the ADR that adds `DELETE /backtests/{id}` (ADR 0017 decision 7) |
| Auth/scope on the stream | E11-S8 (ids already unguessable-ish per ADR 0017 limits) |
| Backpressure / replay for a disconnected tracker | out of scope; resume via `resume_from_seq` is request-scoped |
| Pushing markets/orders as they occur (fine-grained) | records are visible at flush boundaries only (every 256) in v1 |

## Known limits

- Records appear to a reader only after a flush (every 256 records or a terminal transition), so a
  quiet long run may show no `Record` frames for a while; `KeepAlive` keeps the socket alive. A
  finer flush cadence is a worker change, deliberately not made here (decision 4).
- In-process `api_request_stream` uses one OS thread per open stream; HTTP uses one tokio task per
  stream. No cap in v1.
- `manifest.json` is re-read every tick; a 25 ms tick over a long idle run is a cheap small-file
  read, but a finer-grained or fancier strategy belongs to a future profiled change.
- The terminal `Status` frame's `error` reflects only what the manifest stores (ADR 0017 decision
  7); a run cancelled gracefully still ends as a `cancelled` terminal frame.