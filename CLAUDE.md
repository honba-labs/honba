# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

Honba is an AI-native trading/research platform for Indian markets (NSE/BSE equities, indices, mutual funds, ETFs, options). This repo is the core: a Rust workspace (`crates/`) plus a Python control plane (`python/`). It is one of several sibling repos under `honba-labs` (adapters, strategies, examples, frontend, docs) that are separate git repos — keep changes within this one unless told otherwise.

Design intent (from README): the same deterministic event flow runs in backtest and live; three-tier backtesting (vectorized pre-filter → event-driven simulator → concurrent parameter sweep); an AI research loop on top.

## Commands

Rust (toolchain pinned to `stable` with rustfmt + clippy; MSRV 1.75, `max_width = 100`):

```
cargo build --workspace
cargo test --workspace
cargo test -p honba-testing <test_name>   # single test in one crate
cargo test --workspace --doc
cargo fmt --all
cargo clippy --workspace --all-targets -- -D warnings
RUSTDOCFLAGS="-D warnings" cargo doc --workspace --no-deps
python3 scripts/dependency_graph.py            # crate-hierarchy check (also a CI job)
```

`make build|test|lint|fmt` wrap the above (all with `--workspace`).

Codegen (Rust `honba-codegen` is the single owner; never edit the outputs by hand, each carries a
DO NOT EDIT marker):

| Artifact | Committed at | Regenerate |
|---|---|---|
| JSON Schema bundle | `schema/domain/domain_schema.json` | `make schema` (also writes frontend TS) |
| OpenAPI 3.1 | `schema/openapi/openapi.json` | `make openapi` |
| Python wire stubs | `python/src/honba/wire/generated/__init__.pyi` | `make pyi` |
| MCP tool schemas | `schema/mcp/mcp_tools.json` | `make mcp` |
| TypeScript | `../honba-frontend/src/core/types/generated/domain.ts` | `make schema` (`FRONTEND_TS_DIR=...`) |

- `make codegen` regenerates all of them. The Python `honba schema export` and
  `scripts/export_schema.py` are wrappers: the former writes `honba._honba.codegen_render(kind)`
  (same bytes, no cargo or npx needed), the latter runs the Rust binary.
- Drift: `cargo test -p honba-codegen` compares freshly generated artifacts with the committed
  ones (`tests/committed_artifacts.rs`). `make check-codegen-ci` (schema, openapi, pyi, mcp) runs
  in CI and fails on any modified **or untracked** file in the generated dirs.
  `make check-codegen` adds `check-schema-ts`, which is LOCAL ONLY: it writes into the sibling
  `../honba-frontend` checkout.
- Renderer goldens live in `crates/honba-codegen/tests/golden/`; `HONBA_BLESS=1 cargo test -p
  honba-codegen --test renderer_golden` rewrites them after an intended change.

Python (`python/`, built with maturin; extension module `honba._honba`; Python >=3.10):

```
cd python && pip install -e ".[dev]"     # or: make python  (maturin develop)
cd python && pytest -q tests/            # asyncio_mode=auto
cd python && pytest tests/unit/test_x.py::test_name
ruff check .                              # line-length 100
```

`import honba` does not need the extension: native access is lazy (`honba._native.native()` / `native_attr(name)`). The first native-backed use raises `ImportError` (extension missing) or `RuntimeError` naming the symbol (extension stale; rebuild). Still needing it at first use: `honba.SCHEMA_VERSION` / `honba.wire.API_VERSION` / `STRATEGY_API_VERSION`, `Currency` minor-unit accessors and `Money` conversions, `WireEnvelope` build/validate, `strategies.verify.verify_manifest`, and `honba schema export`.

Optional extras: `ai` (openai, anthropic, litellm, mcp), `rl` (torch, gymnasium). CLI entry point: `honba` → `honba.cli.main:app` (typer; subcommands in `python/honba/cli/`: backtest, data, optimize, research, strategy, ai).

CI (`.github/workflows/ci.yml`), all blocking: Rust job (fmt, check, check `honba-market` without default features, clippy `--workspace --all-targets -D warnings`, test, doctest, codegen drift via `make check-codegen-ci`, doc with `-D warnings`; cargo cache via `Swatinem/rust-cache`), dependency-graph check, and Python job (deps, maturin develop, stubtest, pytest, ruff lint: `ruff check python` and `ruff format --check python`). The frontend TypeScript drift check is not in CI.

## Rust test layout (ADR 007)

- **Unit tests** go in each crate's `src/tests/`: declare `#[cfg(test)] mod tests;` once in `lib.rs` (`main.rs` for
  `honba-cli`), with `src/tests/mod.rs` and one file per area (`src/tests/<area>.rs`). No inline
  `#[cfg(test)] mod tests { ... }` blocks in implementation files. Doctests stay on the items they document.
- **Integration tests** go in `<crate>/tests/` and use only the public API (the CLI's run the built binary).
- Tests needing internals get `pub(crate)` access, never a wider public API.
- Shared test data: small helpers such as `any_instrument()` in each crate's `src/tests/mod.rs`; integration tests in
  crates allowed to dev-depend on `honba-testing` use `honba_testing::fixtures`. Keep literals where the value matters.
- Known gaps are `#[ignore = "known gap: ..."]` tests with the reason, not deleted or weakened assertions.

## Architecture

**Crate layering.** `honba-messages` (L0: events, identifiers, market-data/order messages) is the base; `honba-entities` (L1: instruments, orders, positions, portfolio, trades) builds on it; `honba-market` (L2: generic market contracts, India pack, null test pack); `honba-engine` (L3: event loop, clock, queue, engine, execution traits) and `honba-indicators` (L3: pure compute); `honba-sim` (L4: simulator, paper execution, fill models) and `honba-strategy` (L4: Strategy trait, runner, reference models); `honba-analytics` (L5: metrics, tearsheet, Monte Carlo) and `honba-data` (L5: catalog, Parquet import/export, loaders); `honba-testing` (L6: test fixtures, VecFeed, assertions); `honba-py` (L7: PyO3 extension cdylib exposing `honba._honba`) and `honba-cli` (L7: native binary).

**Enforced hierarchy.** `scripts/dependency_graph.py` enforces the inward layer dependency rules across all crates and fails CI on violations.

**Python mirrors Rust.** `python/honba/` has parallel packages (`core`, `entities`, `india`, `backtest`, `strategies`) plus Python-only layers: `research` (vectorized pre-filter, data loader, notebooks), `ai` (autoresearch, LLM, journal data, MCP gateway, RL, verification), `adapters` (base + registry for broker adapters, implemented in the separate honba-adapters repo). Rust bindings are exposed via `honba._honba` (stubs in `python/honba/_lib/__init__.pyi`).

**Config & data.** `configs/{backtest,live,ai}/*.toml` are run configs (e.g. `nifty50_momentum.toml`, `dhan_paper.toml`, `research_loop.toml`); `data/{cache,catalog,journals}` is runtime data (journals feed the LLM research loop). Docs sources live in `docs/` (and are published via the honba-docs repo).

## Notes

- `deny.toml` (cargo-deny) allows MIT/Apache-2.0/BSD-3-Clause/ISC/LGPL-3.0; other copyleft warns.
- `chrono` is deliberately pinned to `>=0.4.20, <0.4.40` in workspace dependencies; arrow/parquet are on 51.
- `python/tests/` subdirs (`unit`, `integration`, `strategies`, `fixtures`) are currently mostly empty scaffolding.
