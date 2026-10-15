# ADR 0030: L2 Quote/Tick Matching and Differential Oracle

Date: 2026-10-14. Status: **accepted** (2026-10-14). Roadmap stories: E3-S1b, E3-S1d. Supersedes ADR 0025 decisions 1–3 for scheduling purposes; ADR 0025 decisions 4–5 remain in force.

## Context

ADR 0025 (2026-10-13, accepted) deferred E3-S1b and E3-S1d because no canonical tick/depth tape existed and the default simulator was L1 (bars only). The decision required a follow-up ADR to define "the tick event stream (QuoteTick/TradeTick/Depth delta), time granularity (ns), book construction, and the conformance vector format" before scheduling.

Since then the code inventory shows:

- `QuoteTick`/`TradeTick`/`AggressorSide` already exist with `UnixNanos` timestamps (`crates/honba-messages/src/market_data/tick.rs:44,195`).
- They are wire `Event` variants (`events/event.rs:54,56`) and flow through `MarketEvent::QuoteTick/TradeTick` (`crates/honba-ports/src/connector.rs:32`).
- Deterministic synthetic streams already exist (`honba-testing::VecFeed::quote/trade`, `crates/honba-testing/src/feed.rs:44,60`).
- No multi-level `Depth` event type exists; depth is only a read-port/API response type (`DepthSnapshot`/`DepthLevel` in `honba-ports`/`honba-api`).
- All matching cores are L1: `NextOpenSim` (next-open), `BarFillEngine` (last close), `PaperExecution` (fixed price), `ScriptedExecution` (scripted).

The remaining blocker was the *event-stream contract* for the matcher, not a persistent data tape. This ADR provides that contract so E3-S1b/S1d can be scheduled while preserving ADR 0025 decisions 4–5 (L1 stays canonical reference; no L1 vector changes).

## Decision

### 1. Event stream

**v1 stream = `QuoteTick` (top-of-book) + `TradeTick` (prints). No new wire type.**

- `QuoteTick` fields: `instrument_id`, `bid_price`, `ask_price`, `bid_size`, `ask_size`, `ts_event`, `ts_init` (ns). Represents a top-of-book update.
- `TradeTick` fields: `instrument_id`, `price`, `size`, `aggressor_side`, `trade_id`, `ts_event`, `ts_init` (ns). Represents a market print.
- Optional bounded `DepthSnapshot` **input** (not a streaming delta) may be supplied to the matching core for marketable-order walking. It contains ordered `bids[]`/`asks[]` of `DepthLevel { price, qty }` (existing types from `honba-ports/src/snapshot.rs:31`). This is an opt-in enhancement; the v1 matcher works with QuoteTick only.

**Time granularity:** `UnixNanos` (ns), matching the existing tick types. Event-time only; wall clock is excluded from domain crates (ADR 0016).

### 2. Book construction

Top-of-book `L2Book`: best bid/ask + size per instrument, updated by `QuoteTick` events. **L3 full-book replay remains DROPPED** per ADR 0025 (consequence 5) and `ROADMAP.md` §3.7. No order-book depth deltas are streamed.

### 3. Matching semantics (v1)

| Order type | Fill rule |
|---|---|
| Market | Fill at opposite touch (buy at ask, sell at bid); partial by available top size if `DepthSnapshot` provided |
| Marketable limit | Walk book at current touch; remainder rests (or cancels under IOC) |
| Resting limit | **Fills when a `TradeTick` prints through it** (`buy: trade.price <= limit`; `sell: trade.price >= limit`) |
| Stop / stop-limit | Trigger on `TradeTick` crossing `trigger_price`; then behaves as market/limit |
| IOC | Fill immediately against current book; cancel remainder |
| FOK | Fill all-or-nothing vs current book size; else reject |
| Day | Expire at session close (`MarketProfile`/`ExpiryRules`) |

**Time priority:** FIFO at the top price level. Queue order is the tiebreak (ADR 0019 decision 6). The `depth_survival_discount` stub (`crates/honba-sim/src/latency/mod.rs:90`, currently `1.0`) is wired to quote size for future size-aware partial fills.

**Cancel timing (L2 row per ADR 0019 decision 6):**
- `order_cancel_requested.ts = now` at `cancel()`
- `order_cancelled.ts = now` in v1; later replaced by `LatencyModel` delay at that book position.

### 4. Conformance vectors

New file: `schema/conformance/l2_sim.json`.

Shape mirrors `next_open_sim.json` (`schema/conformance/next_open_sim.json`): `{ fixture_version, type: "L2Sim", description, scenarios: [{ name, chunk, config, steps[], expect: { drains[], final{} } }] }`.

Step ops added: `quote` (QuoteTick), `trade` (TradeTick), alongside existing `bar`, `submit`, `cancel`, `drain`, `advance_ts`.

`expect.drains[]` entries contain the same `[order_id, symbol, side, qty, price, ts, costs_minor]` rows. `expect.final` matches the journal schema.

**Requirement:** `next_open_sim.json` and `order_state.json` are **not modified** (ADR 0025 decision 4). L2 is a separate, opt-in `ExecutionEngine`.

### 5. Differential oracle (E3-S1d)

A **naive reference matcher** (simple, exhaustive, O(n)) and the optimised `L2Sim` run on the same seeded event stream. Property test `oracle_agrees_seeded_streams` asserts identical fills/sequences. Fixture: a recorded seed set (same generator as conformance vectors). This matches the "matching-core study" item in `ROADMAP.md` §3.7.

### 6. Scheduling

E3-S1b split into three chunks (each S–M):
- **E3-S1b-1:** L2 matching core (`honba-sim/src/l2/`), `ExecutionEngine` + `Handler`, seeded `FillModel`/`LatencyModel`, unit tests.
- **E3-S1b-2:** Python parity (`make_simulator(fill="l2")`, native binding via `honba._honba`, fallback reference).
- **E3-S1b-3:** `l2_sim.json` + `gen_l2_vectors.py`; conformance replay (Rust + Python).

E3-S1d (differential oracle) ships after E3-S1b-3.

All work is opt-in; the default simulator and research pipeline remain L1 (ADR 0025 decisions 1–2).

## Consequences

- Removes the scheduling blocker from ADR 0025 without introducing a tick/depth tape.
- L1 behavior stays the canonical reference; L2 vectors are separate.
- Determinism preserved: seeded models, recorded fixtures, no wall clock.
- `DepthSnapshot` input is optional; no new streaming depth wire type.
- Cross-repo impact: new docs page; `on_quote`/`on_trade` hooks already exist in strategy API.

## Open items for code review

- Trade-through vs quote-touch for resting limits: default is trade-through on `TradeTick`; config flag `fill_on_quote_touch` may be added later.
- `depth_survival_discount` wired to quote size for v2 size-aware partials.
- IOC/FOK day-expiry interaction with session rules.

## Decision log

- 2026-10-14: proposed.
- 2026-10-14: accepted; E3-S1b/S1d scheduling authorized. E3-S1b-1 implemented `L2Sim` (`honba-sim/src/l2/`); `depth_survival_discount` left unwired (matching is instantaneous at event time, no in-flight interval to discount), still an open item for v2.
- 2026-10-14: E3-S1b-2/3 implemented: `honba._honba.L2Simulator` (`crates/honba-py/src/pyclasses/l2.rs`), Python `L2Execution` (`honba.backtest.l2_sim`, native + pure-Python reference) behind `make_simulator(fill="l2")`; `l2_sim.json` replays on the Rust core, the pyclass and both Python backends, plus a seeded native-vs-Python parity test. Slippage, latency and margin models of the Rust core are not exposed to Python.
- 2026-10-14: E3-S1d implemented: `crates/honba-sim/tests/l2_oracle.rs` holds an independent O(n^2) reference matcher (append-only order tape, status flags, no shared book/priority code) and a seeded stream generator; `oracle_agrees_seeded_streams` compares the full event queue (fills, rejections, cancels, expiries with timestamps), cash, fees, traded notional, positions and working orders after every step over 600 seeds. Ambiguities were resolved per this ADR and `l2_sim.json` (listed in the test header). No `L2Sim` divergence was found.
- 2026-10-15: wire `BacktestRunConfig.execution.fill_model` gains `l2` (`honba_config::FillModel::L2`; schema, OpenAPI, MCP, `.pyi` and frontend TypeScript regenerated). The REST `BacktestExecutor` reads bars only (no tick reader port yet), so a run with `fill_model=l2` fails with `ErrorCode::Unsupported` and context `{"reason": "l2_requires_ticks", "fill_model": "l2"}`; it never falls back to bar fills. Wiring `L2Sim` into the executor waits on a tick source for the run.
