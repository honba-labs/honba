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
2. ~~M4: Zerodha native Rust connector~~ DROPPED: crate `honba-broker-zerodha` removed (f3fc461, last in history d6b752e); Zerodha is Python-only in `honba-adapters` (24fe7a7), Rust behaviour ported with shared golden vectors.
3. WASM remainder: screener predicate evaluation, backtest replay in wasm, OHLC indicators (ATR).
4. REST remainder: strategies list/compile, backtests, sweeps, orders routes.

## Trailing stop + declarative strategies (updated 2026-10-13)
Done (Python, committed): `OrderType.TRAILING_STOP` + `OrderIntent.trail_amount|trail_percent` (6e01d81), opt-in per adapter via `AdapterCapabilities.order_types` (default excludes it; contract checks refusal); `DeclarativeStrategy`/`Entry` (d1eb190), `TargetWeightStrategy` (7eb0e08), composable `strategies/portfolio/` (Universe/Selector/WeightingScheme/RebalanceSchedule/`PortfolioStrategy`, config factory `from_params`; f0c17e1, d6b752e, 5a26497). Catalog `honba-strategies/portfolio/rebalancing/equal_weight`; examples 06/07; docs pages `strategies/strategy_styles.md`, `portfolio_construction.md`.
IN PROGRESS: Rust/wire parity for trailing stop, chunk A (agent ac08e661aa5617655, resumed after a weekly rate limit that lost its first run; nothing was committed before): add `OrderType::TrailingStop` + trail fields in honba-messages/honba-strategy with the same validation table, regenerate codegen outputs (schema, OpenAPI, TS, .pyi, MCP), honba-py parsing, python `wire.OrderIntent/Order` carry trail fields, delete `PYTHON_ONLY_VARIANTS` in `python/tests/integration/test_wire_roundtrip.py`; sim/engine must REJECT it explicitly for now. Check `git log --grep trailing` to see how far it got.
NEXT, chunk B: simulator/paper/backtest actually trail the stop (SELL protects long: high-water mark; BUY protects short: low-water mark; trigger fills as market), then let adapters opt in (check each broker's native trailing/super-order support before listing the type). No broker lists TRAILING_STOP today.
Known gaps: DeclarativeStrategy OCO is done (oco=True; partial leg fills do not resize the sibling, no order-modify yet; honba-docs strategy_styles.md/position_management.md still describe the old limitation and need the text from the OCO commit); honba-frontend TS enums need the new order type after codegen; Alpha 30 point-in-time history is inception-dated only.
Env rules: cargo with CARGO_TARGET_DIR=honba/target, never /tmp; pytest `--basetemp=$HOME/.cache/pytest-honba` then delete; one sonnet subagent at a time (never opus); commit by explicit path, no trailers; nothing pushed.

## 2026-10-14
E3-S7 done to 436aac6/9b37cf5. NEXT: LiveQuoteStore (honba-feed) -> POST /bars ingest + serve wiring -> yfinance driver. Dhan feed unwired.
