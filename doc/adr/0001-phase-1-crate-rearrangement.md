# ADR 001: Phase 1 Crate Re-arrangement and Layering

## Status
Accepted / Completed

## Context
Per [ROADMAP-borrowed-ideas.md](../../ROADMAP-borrowed-ideas.md), the Honba workspace requires structural decoupling:
1. `honba-algo-testing` mixes test-only fixtures (`VecFeed`, assertion helpers) with core simulation and execution models (`BarFillEngine`, `PaperExecution`, latency, cost, and backtest node).
2. PyO3 is bound directly inside `honba-cli`, preventing `python/pyproject.toml` from importing `honba._honba` as a clean extension cdylib crate.
3. `honba-india` contains both generic market contracts (`TradingCalendar`, `CostModelSource`, `UniverseSource`) and India-specific market logic (NSE calendar, STT calculations, NIFTY 50 universe).

## Decisions
1. **Split `honba-algo-testing` into `honba-sim` and `honba-testing`:**
   - Create `honba-sim` (L4) containing execution/simulation primitives: `BarFillEngine`, `PaperExecution`, fill/latency/cost models.
   - Retain `honba-testing` (L6 / dev-helper) containing test feed (`VecFeed`), test recording (`Recorder`), and test assertion helpers (`assert_close`).
2. **Create `honba-py` (L7):**
   - Create a cdylib/rlib crate `honba-py` that depends on PyO3 and exports module `honba._honba`.
   - Wire domain bindings into `honba-py`.
3. **Generalize `honba-india` into `honba-market` (L2):**
   - Provide generic market interfaces (`TradingCalendar`, `HolidaySource`, `CostModelSource`, `UniverseSource`, `MarketProfile`) at the root of `honba-market`.
   - Move India implementations into an `india` module behind a cargo feature `india` (enabled by default for backward compatibility).

## Consequences
- Clean separation between test utilities and reusable simulation/execution components.
- Strict inward dependency layering enforced in CI.
- Standalone compilation of Python extensions without coupling to the CLI binary.
