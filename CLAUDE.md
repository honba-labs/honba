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
cargo test -p honba-algo-testing <test_name>   # single test in one crate
cargo test --workspace --doc
cargo fmt --all
cargo clippy --workspace --all-targets -- -D warnings
RUSTDOCFLAGS="-D warnings" cargo doc --workspace --no-deps
python3 scripts/dependency_graph.py            # crate-hierarchy check (also a CI job)
```

`make build|test|lint|fmt` wrap the above (note `make lint` omits `--workspace`; CI uses it).

Python (`python/`, built with maturin; extension module `honba._honba`; Python >=3.10):

```
cd python && pip install -e ".[dev]"     # or: make python  (maturin develop)
cd python && pytest -q tests/            # asyncio_mode=auto
cd python && pytest tests/unit/test_x.py::test_name
ruff check .                              # line-length 100
```

Optional extras: `ai` (openai, anthropic, litellm, mcp), `rl` (torch, gymnasium). CLI entry point: `honba` → `honba.cli.main:app` (typer; subcommands in `python/honba/cli/`: backtest, data, optimize, research, strategy, ai).

CI: Rust job is blocking (fmt, check, clippy `-D warnings`, test, doctest, doc); the Python job is `continue-on-error`.

## Architecture

**Crate layering.** `honba-messages` (events, identifiers, market-data/order messages) is the base; `honba-entities` (instruments, orders, positions, portfolio, trades) builds on it; `honba-algo` is the engine core (engine, clock, cache, queue, risk, execution, data/connector); then feature crates: `-indicators`, `-strategies`, `-testing` (backtest node, simulator, cost/latency models, paper trading, concurrent sweeps), `-analytics` (metrics, Monte Carlo, walk-forward, regime, tearsheet), `-import`/`-export` (NSE/BSE/AMFI/broker/CSV/Parquet in; CSV/JSON/Excel/Parquet/Markdown out), and `honba-india` (calendar, STT/GST/stamp-duty costs, universes, F&O, options, MFs, ETFs).

**Enforced hierarchy.** `scripts/dependency_graph.py` holds an `ALLOWED` map of permitted `honba-*` dependencies per crate and fails CI on violations. When adding an inter-crate dependency, update that map deliberately. Note: the current `Cargo.toml`s already exceed it in places (e.g. `honba-algo-strategies` depends on indicators/testing/analytics, and `honba-algo-testing` on entities/messages) — reconcile the script or the manifests rather than ignoring the check.

**Python mirrors Rust.** `python/honba/` has parallel packages (`core`, `entities`, `india`, `backtest`, `strategies`) plus Python-only layers: `research` (vectorized pre-filter, data loader, notebooks), `ai` (autoresearch, LLM, journal data, MCP gateway, RL, verification), `adapters` (base + registry for broker adapters, implemented in the separate honba-adapters repo). Rust bindings are exposed via `honba._honba` (stubs in `python/honba/_lib/__init__.pyi`).

**Config & data.** `configs/{backtest,live,ai}/*.toml` are run configs (e.g. `nifty50_momentum.toml`, `dhan_paper.toml`, `research_loop.toml`); `data/{cache,catalog,journals}` is runtime data (journals feed the LLM research loop). Docs sources live in `docs/` (and are published via the honba-docs repo).

## Notes

- `deny.toml` (cargo-deny) allows MIT/Apache-2.0/BSD-3-Clause/ISC/LGPL-3.0; other copyleft warns.
- `chrono` is deliberately pinned to `>=0.4.20, <0.4.40` in workspace dependencies; arrow/parquet are on 51.
- `python/tests/` subdirs (`unit`, `integration`, `strategies`, `fixtures`) are currently mostly empty scaffolding.
