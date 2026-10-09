# Handoff (updated 2026-10-09)

Workspace: /home/pmallapp/Devel/Finance/honba-labs (not a git repo; sibling repos). Work is local commits only, NOTHING PUSHED.
Rules in force (user): TDD (failing test first), unit + integration tests, commit by explicit path only, NO Co-Authored-By/any trailer, one subagent at a time, small chunks, core/perf logic in Rust and rest in Python.
Approved repos: honba, honba-strategies, honba-frontend, honba-docs, honba-adapters, honba-examples.

## Current State & Recent Accomplishments
1. **Generic Broker Adapters & M1-M5:**
   - Architecture & generic broker API documented in `honba/docs/broker-apis.md`.
   - Shared infra (`rate_limit.py`, `auth.py`, `websocket.py`), Dhan adapter, Zerodha adapter implemented and contract certified in `honba-adapters`.
   - Pre-flight capability validation strictly enforced (GTC/FOK/GTD, fractional quantities rejected upfront; alphanumeric tags <= 20 chars).
2. **Fill Simulation (`honba.strategies.testing.replay`):**
   - Per-instrument fill prices tracked (using last close / open per instrument).
   - Orders sorted sell-before-buy per rebalance bar, ensuring sell proceeds are credited before buys are funded.
3. **Point-in-Time Universe Resolution:**
   - Inception-dated `default_alpha30_history()` registered in `honba.markets.india.universes`, allowing `resolve_universe("alpha30", as_of=...)` to resolve point-in-time constituents without error.
4. **Strategy Catalog Modernization (`honba-strategies`):**
   - All 5 `universe/alpha/*` strategies (`factor`, `low_vol`, `mean_reversion`, `momentum`, `quality`) converted to `Selector`-based portfolio strategies.
   - Core portfolio scoring extended with `mean_reversion`.
   - Full 90-day backtests executed and populated into real `backtest_result.json` files for all alpha strategies.
5. **Documentation Overhaul (`honba-docs`):**
   - Removed all references to nonexistent APIs (`BacktestNode`, `Alpha30::nifty100()`, `alpha30_nifty100`, `honba-india`).
   - Standardized on `Honba.backtest(...)`, `honba.markets.india`, and `honba-market`.
   - Integrated `avoiding_backtest_biases.md` into `book/src/SUMMARY.md` and verified `mkdocs build` and `mdbook build`.

## Pending / Open Items
1. Rust port of next-open simulator behind `ExecutionPort`.
2. M4: Zerodha native Rust connector (`kiteconnect-rs` integration in `honba-ports`).
3. WASM remainder: screener predicate evaluation, backtest replay in wasm, OHLC indicators (ATR).
4. REST remainder: strategies list/compile, backtests, sweeps, orders routes.
