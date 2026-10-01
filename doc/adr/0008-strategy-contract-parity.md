# ADR 008: Strategy Contract Parity (Hooks, `StrategyContext`, Conformance Suite) (E0-S3)

## Status
Accepted

## Context
Story E0-S3 in [ROADMAP-borrowed-ideas.md](../../ROADMAP-borrowed-ideas.md) asks for one strategy contract in both
languages: the same hooks, a `StrategyContext` (clock, portfolio, positions, instrument lookup, order submit) and a
shared conformance fixture that runs the same scripted event stream through a Python and a Rust strategy. Before
this ADR:

- Python `honba.strategies.base.Strategy` was a plain class with `on_start/on_bar/on_fill/on_stop`. It kept its own
  book (`_positions`, `_pending`, `_intents`) and exposed `position/busy/buy/sell/submit` plus the runner entry points
  `drain_intents/handle_fill/handle_rejected`. There were no quote or trade-tick hooks, no clock and no instrument
  lookup, and no Python domain types for quotes or trade ticks.
- Rust `honba_strategy::Strategy` had `on_start/on_bar/on_quote/on_trade/on_fill/on_intent_rejected/on_stop` with a
  `ts_init` argument on the market-data hooks, and returned intents through `drain_intents`. A strategy had no way
  to read its position, the time or instrument metadata except by tracking them itself.
- `crates/honba-strategy/src/context.rs` and `config.rs` were one-line placeholders that no module declared.
- `OrderIntent` parity (stop and stop-limit, validation, golden vectors) was already settled by E0-S2 (ADR 006).

## Decisions
1. **One hook set.** Both languages have `on_start`, `on_bar`, `on_quote`, `on_trade`, `on_fill` and `on_stop`. All
   hooks default to no-ops; a strategy overrides only what it uses. Rust additionally keeps `on_intent_rejected`
   (Rust `OrderIntent` fields are public, so an invalid intent can be built; a Python `OrderIntent` validates on
   construction and cannot be invalid). `on_trade` receives a market `TradeTick`; the strategy's own executions
   arrive in `on_fill` as a `Trade`.
2. **`StrategyContext` is the strategy's only view of the world (a port).** It is defined in the strategy context
   (`honba-strategy`, L4; `honba.strategies.context`) and is pure: no I/O, no wall clock, no broker types.

   | Capability | Python (`StrategyContext` ABC) | Rust (`StrategyContext` trait) |
   |---|---|---|
   | clock | `now() -> int` | `now() -> UnixNanos` |
   | position | `position(instrument_id) -> float` (net signed) | `position(&InstrumentId) -> f64` |
   | positions | `positions() -> dict[InstrumentId, float]` | `positions() -> Vec<(InstrumentId, f64)>` |
   | portfolio cash | `cash() -> float` | `cash() -> f64` |
   | open orders | `busy(instrument_id) -> bool` | `busy(&InstrumentId) -> bool` |
   | instrument lookup | `instrument(instrument_id) -> Instrument \| None` | `instrument(&InstrumentId) -> Option<&Instrument>` |
   | order submit | `submit(intent) -> None` | `submit(OrderIntent)` |

   - `now()` is the `ts_init` of the event being processed (0 before the first event): the time Honba saw the event,
     which is what orders are stamped with. It is the simulated time in a backtest and the event time in live; a
     strategy never reads the wall clock and cannot tell which mode it runs in.
   - `positions()` lists non-flat positions ordered by instrument id (symbol, then venue).
   - "Portfolio" in this story is the read-only pair `cash()` + `positions()`. Cash starts at the context's initial
     cash (default 0) and moves with fills: a buy debits `quantity * price + costs`, a sell credits
     `quantity * price - costs`. Equity, margin and multi-account views wait for the margin model (E3) and
     `honba_entities::Portfolio` integration.
   - `busy(id)` is true while an intent submitted for `id` is not fully filled or rejected (fills arrive after the
     intent; gate new orders on it).
3. **Access differs by language, the concept does not.** Rust passes the context into every hook
   (`fn on_bar(&mut self, ctx: &mut dyn StrategyContext, bar: &Bar)`) because a strategy cannot hold a borrow of
   runner state. Python binds it to the instance: hooks keep their signatures (`on_bar(self, bar)`) and read
   `self.ctx`. Rust drops the `ts_init` hook argument (use `ctx.now()`) and `Strategy::drain_intents` (intents go
   through `ctx.submit`).
4. **`LedgerContext` is the reference implementation**, used by the runners in backtest, paper and live alike: a
   deterministic in-memory ledger fed by the runner (clock, fills, rejections) that queues submitted intents.
   Runner-facing methods (`set_now`, `apply_fill`, `release`, `drain_intents`, `add_instrument`) are not part of
   the strategy-facing port.
5. **Runner semantics (identical in both languages).** For each message `(event, ts_init)`:
   1. the context clock is set to `ts_init`;
   2. a bar, quote or trade event is dispatched to its hook (other events reach no hook);
   3. every intent submitted since the last drain is processed in submission order, including those submitted in
      `on_start` (processed with the first event) and in `on_fill` during the previous event. An invalid intent is
      released and reported (`on_intent_rejected`, Rust); a valid one becomes an order with id
      `"{strategy name}-{n}"` (`n` from 0) stamped `ts_init` and is submitted to the execution port;
   4. fills drained from the execution port are applied to the context, then passed to `on_fill`, in order.

   `on_stop` runs after the last event. Intents submitted in `on_stop` are never executed: the run is over, so a
   strategy must square off on an event (for example the last bar of the session), not in `on_stop`.
6. **Shared conformance fixture.** `schema/conformance/strategy_contract.json` (same conventions as
   `schema/golden`, `schema_version` 1) holds scenarios: a strategy name and params, instruments, initial cash and a
   scripted stream of wire `Message`s, plus the expected intents (with the `ts_init` they were submitted at), fills
   (wire `Trade`), per-hook observations of the context, final positions and cash. The execution model is fixed by
   the fixture (`"fill_model": "bar_close"`: every order fills in full at the close of the most recent bar, fill time
   `max(order ts, previous fill ts + 1)`, no costs), which is what `honba_sim::BarFillEngine` and the Python mirror
   `honba.strategies.testing.BarCloseFills` do. The strategies under test exist in both languages:
   `contract_probe` (exercises every hook, every context capability and all four order types), `buy_and_hold` and
   `sma_crossover`. The suite runs in three places: Rust (`honba-strategy/tests/conformance.rs`), Python
   (`python/tests/integration/test_strategy_conformance.py`), and across the boundary: the Python test also runs
   the Rust strategies through `honba._honba.run_strategy` and requires the two results to be equal.
7. **Python compatibility shim (one minor version: present in 0.1.x and 0.2.x, removed in 0.3).**
   - `Strategy` is now an `abc.ABC`. A subclass that defines only `on_bar` (and any of the old hooks) and that omits
     or calls `super().__init__()` works unchanged: a default `LedgerContext` is bound in `__new__`, so a strategy is
     usable stand-alone (tests calling `on_bar` directly, `replay`).
   - `position`, `busy`, `buy`, `sell` and `submit` stay as conveniences that delegate to `self.ctx`; they are not
     deprecated.
   - `drain_intents`, `handle_fill` and `handle_rejected` remain as the legacy runner entry points (used by
     `honba.strategies.testing.replay`) and delegate to the context. The new `StrategyRunner` talks to the context
     directly. A subclass that *overrides* one of them is still honoured by the runner in this window, but its class
     creation emits a `DeprecationWarning`, because that override will stop being called in 0.3. This is the only
     warning: nothing else changes behaviour.
   - Behaviour note without a warning: a method named `on_quote` or `on_trade` on an existing subclass is now a hook
     and is called by the runner with a `QuoteTick` / `TradeTick`. No catalog strategy defines either.
8. **Placeholders.** `context.rs` is wired in as the context module. `config.rs` is deleted: strategy configuration
   is owned by the pydantic models (`honba.strategies.config`, schema export in E0-S4) and the CLI's run config.
9. **Python binding.** `honba._honba.run_strategy(strategy, params, events, instruments="[]", initial_cash=0.0)`
   runs a Rust reference strategy over JSON wire messages with `BarFillEngine` and returns JSON (intents, fills,
   observations, positions, cash). It is the cross-language half of the conformance suite and a machine-readable
   entry point for research and agents.

## Consequences
- Breaking (Rust): every `Strategy` hook takes `ctx: &mut dyn StrategyContext`; market-data hooks lose `ts_init`;
  `drain_intents` is removed (submit with `ctx.submit`). `StrategyAdapter` owns a `LedgerContext`. In-repo
  implementors (`BuyAndHold`, `SmaCrossover`, `RsiReversal`, tests) are migrated. No sibling repo implements the
  Rust trait.
- Python: additive (`on_quote`, `on_trade`, `ctx`, `bind`, `StrategyContext`, `LedgerContext`, `StrategyRunner`,
  domain `QuoteTick` / `TradeTick` / `Instrument`). Catalog strategies in `honba-strategies` run unchanged.
- Docs and examples that describe the old hook set should mention `on_quote`, `on_trade` and `self.ctx`
  (`honba-docs`, ticketed).
- A new conformance scenario must pass in both languages before a Rust or Python runner change lands.

## Known gaps
- `BarFillEngine` fills at the last bar close of *any* instrument and at 0.0 before the first bar (ADR 006). The
  fixture avoids both; the Python mirror raises instead of filling at 0.0.
- `IntentRejection` is not exposed to Python; Python intents cannot be invalid.
- Venue-side rejections and cancellations (an order state machine) are E2-S6; until then `release` is only driven by
  invariant rejections in Rust and by `handle_rejected` in Python.
