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
   `max(order ts, previous fill ts + 1)`, costs per decision 10), which is what `honba_sim::BarFillEngine` and the Python mirror
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
   - Breaking, no warning possible: `ctx` is a read-only property, so a legacy subclass that assigns
     `self.ctx = ...` now raises `AttributeError`. Migration: use a different attribute name (the strategy's own
     `ctx` was never part of the contract); read the new `self.ctx` for the context.
   - `honba.strategies.testing.replay` sets `ctx.now()` to the bar's `ts` before each bar, because domain `Bar`s
     carry no `ts_init`; `StrategyRunner` sets it to the message's `ts_init`. A strategy reading `ctx.now()` under
     `replay` therefore sees event time, not init time.
   - Behaviour note without a warning: a method named `on_quote` or `on_trade` on an existing subclass is now a hook
     and is called by the runner with a `QuoteTick` / `TradeTick`. No catalog strategy defines either.
8. **Placeholders.** `context.rs` is wired in as the context module. `config.rs` is deleted: strategy configuration
   is owned by the pydantic models (`honba.strategies.config`, schema export in E0-S4) and the CLI's run config.
9. **Python binding.** `honba._honba.run_strategy(strategy, params, events, instruments="[]", initial_cash=0.0)`
   runs a Rust reference strategy over JSON wire messages with `BarFillEngine` and returns JSON (intents, fills,
   observations, positions, cash). It is the cross-language half of the conformance suite and a machine-readable
   entry point for research and agents. Like the Python mirror it refuses (`ValueError`) an order submitted before
   any bar instead of filling it at 0.0.

10. **Fill costs.** The `bar_close` model has an optional, backward-compatible cost parameter, `flat` and `bps`, both
    defaulting to 0 (no costs, every earlier scenario and caller unchanged). The cost of a fill is

    `costs = flat + (quantity * price) * bps / 10_000`

    evaluated left to right in IEEE-754 `f64`, with no rounding (`quantity * price` first, then `* bps`, then
    `/ 10_000`, then `flat +`). `price` is the fill price (the last bar close). `costs` is an amount in the settlement
    currency: never negative and never signed by side. It is stored in `Trade.costs`; the ledger applies the sign
    (decision 2): a buy debits `quantity * price + costs`, a sell credits `quantity * price - costs`. Validation:
    `flat` is finite within `[0, 1e9]` and `bps` finite within `[0, 10_000]` (100% of the notional), otherwise a typed
    error (Rust `FillCostsError::{InvalidFlat, InvalidBps}`, Python `FillCostsError(ValueError)`, and a `ValueError`
    from `run_strategy`); nothing panics across the FFI.
    - Rust: `honba_sim::FillCosts::new(flat, bps)` and `BarFillEngine::with_costs`; `run_strategy` takes
      `flat_cost` and `cost_bps` keyword arguments (stub `python/honba/_lib/__init__.pyi`), and the Rust API gains
      `run_strategy_costed_json` beside the unchanged `run_strategy_json`.
    - Python: `BarCloseFills(flat_cost=0.0, cost_bps=0.0)`.
    - Fixture: a scenario may carry `"fill_costs": {"flat": ..., "bps": ...}`; absent means no costs. The scenario
      `contract_probe_fill_costs` (flat 0.5, 625 bps; prices 64, 64, 32) pins the semantics with values derived by
      hand: costs 4.5, 4.5, 2.5; cash -68.5, -9.0, -43.5. 625 bps is chosen so every product is exact in binary
      floating point.

11. **Port reject/cancel path (Python).** The port contract lives in `honba.strategies.execution`
    (`ExecutionPort` is re-exported from `honba.strategies.runner`). Beyond the required `submit` / `drain_fills`, a
    port may implement `drain_rejections() -> list[OrderRejection]` and `cancel(order_id)`. An `OrderRejection`
    (`order_id`, `intent` carrying the quantity that will never fill, `reason`, `ts`, `cancelled`) is drained by the
    runner after the fills of every event (step 5 of the runner semantics) and released in the context (or passed to
    a legacy `handle_rejected` override), so a port never needs a handle on `ctx.release`. `StrategyRunner.cancel`
    asks the port to cancel and books what it reports at once. Both methods are optional: ports without them work
    unchanged through the shims `drain_port_rejections` / `cancel_order`, and `BaseExecutionPort` supplies inert
    defaults. Logged as the wire types `order_rejected` / `order_cancelled`. Every core port runs the shared contract
    test `python/tests/integration/test_execution_port_contract.py` (fills belong to submitted orders, and once the
    working orders are cancelled `filled + released == ordered` for every order).

12. **Warm-up gate (both languages).** `StrategyManifest.warmup_bars` is enforced by the runner: Rust
    `StrategyRunner::with_warmup_bars(n)`, Python `StrategyRunner(..., warmup_bars=n)` (default: the strategy's
    `warmup_bars` class attribute, 0; `StrategyConfig.warmup_bars` carries it in `config.toml`). A *driving bar* is a
    bar event whose `ts_init` differs from the previous bar event's. While at most `n` driving bars have been seen
    (and before the first), every event still reaches the strategy, so indicators converge, but each valid intent is
    released in the context and recorded as a `SuppressedIntent` (`suppressed()` / `RunResult.suppressed`) instead of
    becoming an order; it consumes no order id. Invalid intents are rejected as before. The Python manifest mirror is
    `honba.strategies.manifest.StrategyManifest` (same JSON shape and validation codes). Shared vectors:
    `schema/conformance/warmup_gate.json` and `schema/conformance/strategy_manifest.json`, run by
    `crates/honba-strategy/tests/warmup.rs` and `python/tests/integration/test_warmup_conformance.py`.

13. **Port reject/cancel path (Rust), parity with decision 11.** `honba_engine::OrderRejection` is a value object
    in the crate that owns `ExecutionEngine` (`honba-engine`, L3, so no layering change): `order_id`,
    `instrument_id`, `side`, `quantity` (the unfilled remainder, the amount to release), `reason`, `ts` and
    `cancelled`, with constructors `OrderRejection::rejected(..)` and `OrderRejection::cancelled(..)` (reason
    `"cancelled"`). It carries instrument, side and quantity rather than an `OrderIntent` because `OrderIntent`
    lives in `honba-strategy` (L4), above the port; the runner releases the remainder with
    `LedgerContext::release_remainder`. `ExecutionEngine` gains `drain_rejections() -> Result<Vec<OrderRejection>>`
    with a default that returns nothing, so every existing engine compiles and behaves as before. `cancel(order_id)`
    keeps its signature: an engine holding the order reports the unfilled remainder as a cancelled rejection;
    cancelling an unknown or finished order is a no-op, and the cancellation is stamped with the order's original
    `ts_event` (not the time of the call), matching the shared vectors and the Python venue. Because the
    remainder excludes anything already filled, `StrategyRunner::cancel` need not drain fills first: pending only
    decreases by `filled + released`, which sums to the ordered quantity in either booking order. `StrategyRunner` drains rejections after the fills of every
    event (runner step 5, as in Python), releases and records them (`order_rejections()`), and gains `cancel(id)`
    which books what the engine reports at once. Reason strings are the engine's and shared by both languages:
    `insufficient_funds`, `no_position`, `cancelled`. `honba_sim::ScriptedExecution` is the reference engine
    (fill, reject, partial fill, hold until cancelled). Shared vectors: `schema/conformance/order_rejections.json`,
    run by `crates/honba-strategy/tests/order_rejections.rs` and
    `python/tests/integration/test_order_rejections_conformance.py`; every in-crate engine also runs the contract
    test `crates/honba-sim/tests/execution_contract.rs` (`filled + released == ordered` once working orders are
    cancelled). Not exposed through `honba._honba`: Python already has `OrderRejection` and
    `RunResult.order_rejections`, and the `run_strategy` reference engine never rejects.

    **Addendum to decision 13 (cancel timestamp).** The statement above that a cancellation is stamped with the
    order's original `ts_event` is superseded: a cancellation is stamped with the time of the cancel, the engine time
    at which it is processed. This is what the Python venue (`NextOpenExecution.cancel`, the session time) always did;
    the Rust side differed and the shared vectors missed it because both sides used scripted ports. Breaking change:
    `ExecutionEngine::cancel(&mut self, order_id, now: UnixNanos)` gains the time. `Engine` passes its clock,
    `StrategyRunner::cancel` passes the `ts_init` of the latest event it saw (0 before any event), and
    `ScriptedExecution` stamps the cancelled rejection with `now`. Rejections raised by the venue at submit or fill
    keep the order's `ts_event`. The vector `late_cancel_is_stamped_with_the_cancel_time` in
    `schema/conformance/order_rejections.json` (submit at ts 2, cancel at ts 5, rejection ts 5) runs on both sides,
    and the Python integration test also drives the real `NextOpenExecution` through it. Higher-fidelity venues that
    only learn of the cancel at the venue acknowledgement (L2, live) are left to the order-state ADR (ROADMAP D2).

    **Addendum to decision 13 (one source of truth).** `cancelled` is not stored: Rust exposes
    `OrderRejection::is_cancelled()` (`reason == "cancelled"`), and Python's `OrderRejection` rejects a `cancelled` flag
    that contradicts its reason. A port also refuses an order without a side and a duplicate working order id at submit.

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
  fixture avoids both; the Python mirror and `run_strategy` raise instead of filling at 0.0.
- `IntentRejection` is a Python `StrategyRunner` result (`rejections`) and a `run_strategy` output, but a Python
  `OrderIntent` validates on construction, so it can be invalid only if built around that validation
  (`object.__setattr__`, unpickling, a duck-typed intent); `LedgerContext` keeps such intents out of its pending
  state, as in Rust.
- The Rust stub `python/honba/_lib/__init__.pyi` is not mapped to the module `honba._honba`, so type checkers
  and the CI stubtest do not cover it (including `run_strategy`). Moving it would surface about 79 existing
  stubtest errors in older pyclasses; fixing them is a separate ticket. The stub was deliberately not moved here.
- Venue-side rejections and cancellations as an order *state machine* (partial-fill lifecycle, amend, venue
  acknowledgements) are E2-S6. Both languages now carry the reject/cancel queue itself (decision 13).
