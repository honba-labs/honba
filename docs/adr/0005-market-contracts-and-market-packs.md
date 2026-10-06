# ADR 005: Market Contracts and Market-Pack Architecture (E0-S7)

## Status
Accepted

## Context
Per [ROADMAP-borrowed-ideas.md](../../ROADMAP-borrowed-ideas.md) Section 2b & E0-S7:
1. `honba-market` (L2, formerly `honba-india`) was only moved under `src/india/` without true generic market decoupling.
2. The `india` cargo feature does not compile when disabled (`cargo check -p honba-market --no-default-features` fails).
3. Core contracts such as `CostBreakdown`, `Segment`, and calendar types remained India-shaped with fixed fields (`stt`, `gst`, `stamp_duty`, `sebi_fee`).
4. Core market traits (`MarketCalendar`, `CostSchedule`, `InstrumentRules`, `SymbolGrammar`, `ExpiryRules`, `MarginModel`, `SettlementRules`, `MarketProfile`, `MarketRegistry`) were missing.

## Decisions
1. **Generic Core Market Abstractions:**
   - Define domain-neutral traits and value objects at `honba-market` top level:
     - `MarketCalendar`: session windows, holiday checks, trading day and settlement day arithmetic.
     - `CostSchedule`: itemized transaction cost calculation returning named charges (`Vec<Charge>`) and total fees, supporting dynamic tax and regulatory structures across different exchanges.
     - `InstrumentRules`: price increment (tick size), lot size, minimum order size, freeze quantity, and upper/lower price bands (circuit limits).
     - `SymbolGrammar`: canonical symbol parsing, format verification, and venue token mapping.
     - `ExpiryRules`: contract settlement dates, expiry determination, and last trading day calculation.
     - `MarginModel`: initial margin, maintenance margin, and exposure calculation for orders and positions.
     - `SettlementRules`: settlement cycle duration (e.g. T+1, T+0) and cash/physical delivery semantics.
     - `MarketProfile`: comprehensive trait bundling calendar, cost schedule, instrument rules, expiry, margin, and settlement models for a venue or market ecosystem.
     - `MarketRegistry`: thread-safe compile-time/runtime registry to resolve market profiles and calendars by market code (e.g. `"nse_bse"`, `"null"`).
2. **Feature Gate Isolation:**
   - Put India-specific implementations (`NSE`, `NIFTY 50`, `STT`, Indian statutory charges) strictly inside `#[cfg(feature = "india")] pub mod india;`.
   - Provide a fully standalone `null` market pack (`NullCalendar`, `NullCostSchedule`, `NullInstrumentRules`, `NullMarketProfile`) compiled unconditionally so `cargo check -p honba-market --no-default-features` compiles cleanly and passes all contract tests.
3. **Contract Test Suite:**
   - Provide a shared, generic contract verification suite that exercises any `MarketProfile` / `MarketCalendar` / `CostSchedule` implementation across both `india` and `null` packs.
4. **Date-aware settlement (amendment):**
   - A settlement cycle is a function of the trade date, not a constant. `SettlementRules` gains `settlement_days_as_of(kind, date)` (default: the fixed cycle) and `settlement_date` uses the cycle in force on the trade date. `SettlementSchedule` carries dated phases.
   - India (NSE and BSE, all equities): T+2 until 2023-01-26, T+1 from 2023-01-27. `settlement_days(kind)` without a date reports the latest phase (T+1). BSE follows NSE.
   - Python asks through `honba._honba.nse_equity_settlement_days(as_of)` / `honba.markets.india.settlement_days_for(exchange, as_of=...)`. An explicit `settlement_days` (`Honba.backtest`, `make_simulator`, `StrategyConfig`) always wins. A backtest resolves the default as of its first session date and keeps it for the run (a run spanning 2023-01-27 does not switch mid-run).
   - The next-open simulator counts one session per bar, which equals trading days only for daily-or-longer bars; intraday timeframes must pass `settlement_days` explicitly (an error otherwise).

## Consequences
- Core engine, simulation, and risk layers can consume generic market models without any compile-time dependency on Indian market specifics.
- New markets (e.g., US equities, crypto, prediction markets) can be added cleanly as feature-gated packs or external crates implementing `MarketProfile`.
- Downstream crates and CI matrix can test and build `honba-market` with `--no-default-features`.
