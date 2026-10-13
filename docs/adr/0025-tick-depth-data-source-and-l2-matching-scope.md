# ADR 0025: Tick/Depth Data Source and L2 Matching Scope

Date: 2026-10-13. Status: **accepted**. Roadmap stories: affects E3-S1b, E3-S1d.

## Context

The roadmap calls out E3-S1b (L2 quote/tick matching: marketable orders walk top of book, resting limits, stops from ticks, IOC/FOK/day) with a dependency on "tick data decision". E3-S1d is the differential oracle (naive reference matcher vs optimized simulator on the same stream). D14 asks: "Tick/depth data source for L2 (bars only today; `/depth` returns none)".

As of now:
- The codebase uses OHLCV bars as the primary market data feed (Bar events). Adapter `/depth` endpoints exist in the contract surface but the current adapters return depth from available quotes; there is no persistent tick/depth tape in the data lake or in the reference fixtures for L2 replay.
- The simulator remains L1/next-open semantics (ADR 0016) — limit/stop are rejected as `unsupported_order_type` by the next-open path (decision 6 of ADR 0016). Extending to L2 would require an event model for top-of-book updates (QuoteTick/Depth updates), order book state, time priority, and conformance vectors.
- ADR 0019 (OrderState FSM) and E2-S6 cover the event/state machine surface, but do not define a L2 matching core.

## Decision

1. **No tick/depth persistent tape.** For the foreseeable future, Honba does not maintain a canonical tick/depth parquet/csv tape in the repo or require tick data for the default research flow. Bars remain the canonical data type for reproducible backtests.
2. **Stay L1 for the default simulator.** E3-S1b (L2 quote/tick matching) is **deferred**, not dropped. It remains on the roadmap but will only be scheduled when a concrete, license-compatible tick/depth source is identified and an event stream format is agreed.
3. **Differential oracle (E3-S1d) deferred.** E3-S1d depends on a L2 matcher and the same event stream; it is deferred alongside E3-S1b.
4. **L1 behavior is the canonical reference.** The next-open simulator (ADR 0016) and BarFill semantics are the authoritative reference implementations. Any future L2 matching must be tested against the same journal schema and must not change the existing L1 conformance vectors.
5. **Depth is advisory only.** Adapter `depth()` remains available where brokers provide it, but it is not consumed by the default simulator. The matching-core ideas from the borrowed study may inform a future design, but no code is to be introduced until (1) and (2) are satisfied.

## Consequences

- Removes the blocker for "E3-S1*" progression: E3-S1a is done; E3-S1b/S1d are explicitly deferred by this ADR.
- Clarifies scope: "bars only" remains the supported path; no new tick data parsing, storage, or replay code is required now.
- Preserves determinism and simplicity of the research pipeline (seeds, recorded fixtures, journal v1).
- When scheduling E3-S1b later, a follow-up ADR must define the tick event stream (QuoteTick/TradeTick/Depth delta), time granularity (ns), book construction, and the conformance vector format.
