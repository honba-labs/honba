# Next Session Task: E4-S3 — Finish Runner (CLI + REST run_id + assumptions.not_modelled)

## Roadmap context
- **Milestone**: R2 "Runs — A backtest is a first-class server object"
- **Story**: E4-S3 (M) — depends on ADR 0017 (accepted), E3-S1a
- **Acceptance**: `result_has_not_modelled`, `cli_exit_codes` / `rest_backtest_roundtrip_matches_session` (parity on Rust-registered strategy subset; others 422)

## Current state at HEAD
- ADR 0017 implemented: run lifecycle, `RunStore`, worker pool, `POST /backtests`, `POST /sweeps`, `GET /backtests/{id}`, `GET /backtests/{id}/journal`, `GET /journals/{id}`, `GET /sweeps/{id}`, `GET /sweeps/{id}/journal` (422)
- `BacktestResponse` has `assumptions: Option<Value>` but **no `not_modelled` field is populated**
- `crates/honba-cli/src/backtest.rs` exists but is the old single-shot backtest over synthetic data
- No `honba backtest` CLI subcommand wired to the new run service
- Python `BacktestResult` has no `assumptions` field

## What needs to be built

### 1. `honba backtest` CLI (`crates/honba-cli/`)
- New subcommand `backtest` (or reuse existing name) that:
  - Reads a TOML config (`BacktestRunConfig` with `[risk]` section)
  - Submits via `honba._honba.api_request` (InprocTransport) to `POST /backtests`
  - Prints `run_id` immediately, then optionally `--wait` / `--stream` to follow progress
  - On completion, prints `BacktestResponse` with `metrics`, `assumptions.not_modelled`, `error`
  - Exit codes: 0 success, 1 validation error, 2 run failed, 3 transport error

### 2. `assumptions.not_modelled` in run response
- In `BacktestExecutor::backtest` (or a new `BacktestExecutor::run`): collect assumptions from the strategy/engine/simulator
- Add to `RunManifest` at completion: `assumptions: { not_modelled: [...], timing: {...} }`
- Populate `BacktestResponse.assumptions` with this typed object (not raw `Value`)
- Update `BacktestResponse` schema if needed (still additive within `/api/v1`)

### 3. Python `BacktestResult` surface
- Add `assumptions: Option<Assumptions>` where `Assumptions` has `not_modelled: Vec<String>`, `timing: TimingAssumptions`
- `Client.backtest()` / `Client.wait()` / `Client.run_backtest()` return it

### 4. Integration test
- `rest_backtest_roundtrip_matches_session`: submit same strategy via REST and Python `BacktestSession`, assert identical journal + `run_id` traceability + `assumptions.not_modelled` present

## File touch-points (high confidence)
| Area | Files |
|------|-------|
| CLI | `crates/honba-cli/src/backtest.rs` (rewrite), `crates/honba-cli/src/main.rs` (subcommand) |
| Executor | `crates/honba-api-rest/src/executor.rs` (collect assumptions, write to manifest) |
| Manifest | `crates/honba-api/src/runs.rs` (`RunManifest::assumptions` typed field) |
| Response | `crates/honba-api/src/responses.rs` (`BacktestResponse.assumptions` typed) |
| Python result | `python/src/honba/client/models.py` (`BacktestResult.assumptions`) |
| Python CLI | `python/src/honba/cli/backtest.py` (wire `honba backtest` to client) |
| Tests | `crates/honba-api-rest/tests/rest_backtest.rs` (add parity test) |

## TDD order (per R1)
1. **Failing test**: add `rest_backtest_roundtrip_matches_session` in `rest_backtest.rs` asserting `assumptions.not_modelled` present and matches Python session
2. **Minimal impl**: wire assumptions collection in executor → manifest → response
3. **CLI**: implement `honba backtest` with `--wait` / `--stream`
4. **Python**: `Assumptions` model + `BacktestResult` field
5. **Full suite**: `cargo test --workspace`, `pytest python/tests/ -x`, `make check-codegen-ci`

## Dependencies / risks
- E3-S1a chunk 2/3 (next-open Rust port) is **in progress**; if not complete, CLI will delegate to Python `NextOpenExecution` temporarily (ADR 0016)
- `BacktestRunConfig` validation already enforces `[risk]` section (E2-S2 r3)
- No schema version bump needed (additive fields per ADR 0012 rule 2)
- `make codegen` must pass after response type changes

## Estimated size
- M (2-4 days) — largest chunk is the CLI + assumptions plumbing