# ADR 0015: One Explicit Async Runtime Per Interpreter, No pyo3 `auto-initialize`

Date: 2026-10-06. Status: Accepted (implementation pending). Roadmap story: E10-S7 (plan.md §3.2, §9 row 3).

## Context

`honba-py` is the `honba._honba` extension module. It is loaded into a host Python interpreter, so it never owns the
process: Python starts first and the extension is imported into it. plan.md §3.2 puts all I/O in an async shell on
tokio around a synchronous engine kernel, which means the extension has to run a tokio runtime on behalf of Python.

Two hazards follow. pyo3's `auto-initialize` feature starts an interpreter from Rust, which is for embedding Python in a
Rust binary and is wrong for an `extension-module` cdylib. And if each call site builds its own tokio runtime, an
interpreter ends up with several runtimes, with no defined owner for startup, shutdown or the asyncio bridge.

## Decision

- `honba-py` does not enable pyo3 `auto-initialize`. It stays an `extension-module`; the interpreter belongs to the host.
- There is exactly one async runtime per interpreter. It is created and torn down explicitly by `honba.event_loop`
  (E10-S7), a Python-side owner that creates it before the first async call, hands it to `pyo3-async-runtimes`, and
  shuts it down deterministically at exit, so the audit stream is complete (plan.md §3.2 rule 5).
- No other module creates a runtime that outlives a call, drives timers or sockets, or runs engine work.

## Current state (verified 2026-10-06)

Implemented:

- `crates/honba-py/Cargo.toml` depends on `pyo3 = { version = "0.22", features = ["extension-module"] }` and
  `pyo3-async-runtimes` with `tokio-runtime`. `auto-initialize` is **not** enabled. Nothing yet enforces that (no test
  or CI check fails if someone adds the feature).
- `crates/honba-py/src/lib.rs` exposes `initialize_runtime()`, which builds a multi-thread tokio runtime, leaks it
  (`Box::leak`) and registers it with `pyo3_async_runtimes::tokio::init_with_runtime`. It has no teardown, and it
  `unwrap`s the runtime build. `get_runtime_handle()` returns the constant string `"tokio-multi-thread"` whether or not
  a runtime was initialized. Both are declared in `python/src/honba/_honba.pyi`.
- `crates/honba-py/src/pyclasses/api.rs` holds a second, private runtime: a `OnceLock` current-thread runtime that only
  runs `block_on` for the in-process REST router (`request`). It owns no sockets or timers.

Not built:

- `honba.event_loop` does not exist: there is no such module under `python/src/honba`, and nothing in Python calls
  `initialize_runtime` or `get_runtime_handle`.
- No explicit teardown of the multi-thread runtime, and no guard that `initialize_runtime` is called at most once or
  before the first async use.
- No test asserting the absence of `auto-initialize` or the single-runtime rule.

## Consequences

- Until `honba.event_loop` lands, the code does not meet this decision: an interpreter can hold the leaked runtime from
  `initialize_runtime` and the private `api.rs` runtime at once. The `api.rs` runtime is tolerable because it is
  current-thread, drives only the in-process router and does no I/O. Whether it folds into the explicit runtime or stays
  as a documented exception is for E10-S7 to settle.
- E10-S7 must deliver, test-first: the `honba.event_loop` owner (create, idempotent get, shutdown), a Rust-side
  replacement for the leaked runtime that can be torn down, a test that a second creation is refused or returns the same
  runtime, and a check that `Cargo.toml` does not enable `auto-initialize`.
- This ADR is revisited if `honba-py` is ever split into per-crate bindings (plan.md §9 row 7 says it will not be).
