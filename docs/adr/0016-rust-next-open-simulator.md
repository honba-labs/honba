# ADR 0016: Rust Next-Open Simulator (`honba_sim::NextOpenSim`)

Date: 2026-10-06. Status: accepted. Roadmap story: E3-S1a.

## Context

The event-driven backtester fills orders with `honba.backtest.simulated.NextOpenExecution`, a Python `ExecutionPort`.
Rust has only `BarFillEngine` (fill at the last close, ADR 008 decision 6), `PaperExecution` and `ScriptedExecution`.
Core and performance logic belongs in Rust (parameter sweeps, WASM replay, one runtime for backtest and sandbox), so the
next-open rules are ported to Rust behind `honba_engine::ExecutionEngine`, the Python class keeps its API and later
delegates to the Rust type, and a shared vector file proves the two agree. The Python class is the reference: when the
two differ, Python wins until the vectors say otherwise.

## Catalog of the Python reference (`NextOpenExecution`)

**Sessions.** A session is one point on the driving clock with an increasing integer key (the bars' `ts`). It opens by
`open_session(ts, bars)` (raises `ValueError` if `ts <= current session ts`), by a `SessionOpen` event (same, and the
port then ignores bar order: a later bar only opens an instrument the event left out, at the same ts), or by plain
`Bar` events through `on_event`: the first bar with a ts greater than the current opens a session, a later bar with
the same ts opens its own instrument, an earlier ts raises `ValueError` (non-monotonic), a second bar for an
instrument already opened in the session raises `ValueError` (duplicate). Opening a session increments the session
index (starting at -1), clears the "opened" set, drops receivables already due, then fills.

**Open price.** A bar opens its instrument only if `open` is finite and `> 0` and the instrument is not yet opened in
this session; otherwise it is ignored (the instrument did not print). The first usable bar per instrument wins.

**Order types.** Market only. `submit` of any other type (limit, stop-market, stop-limit) is not queued: it is
reported at once as `OrderRejection(reason="unsupported_order_type", ts=<submit ts>)` for the whole quantity. `submit`
raises `ValueError` for an order without a side and for an order id that is already working. (Python `OrderIntent`
validates itself, so limit and stop intents reach `submit` valid.)

**Timing.** An order records the session index at submit (-1 before the first session). It is eligible at a session
whose index is greater, for an instrument that opens in that call. An order submitted before any session fills at the
first session's open; an order submitted during session *k* never fills at a later bar of session *k*. An order whose
instrument does not print simply stays working (held). Fills are priced at the bar `open`, stamped with the bar `ts`,
`Trade.costs` is the fill cost.

**Order of fills.** Per `_fill_at` call the eligible list is fixed first (submission order); all eligible sells fill,
then all eligible buys.

**Sell.** Long only (default): `qty = clamp(want, 0, held)`; if `0 < qty < want` and `want - qty <= 1e-9` the shortfall
is float residue and `want` becomes `qty` (sells all, no rejection). Otherwise the shortfall is rejected as
`no_position` with the remainder quantity, stamped with the session ts; `qty <= 0` fills nothing. With `long_only=False`
the sell fills in full and the position goes negative. Proceeds `mul_qty(qty, open) - cost` are credited to cash at
once and become available `settlement_days` sessions later (receivable due at `session + settlement_days`).

**Buy.** Cost of the whole order is `mul_qty(want, open) + cost(want)`. If it is at most *available cash* the order
fills in full. Else, if sale proceeds are pending and `session - first_try < settlement_days`, the order waits
(`first_try` is the first session it was considered). Else it is cut to the largest whole number of lots (default lot 1,
per-instrument `set_lot_size`, quantities with `1e-9` tolerance) whose notional plus cost fits available cash (bisection;
costs assumed non-decreasing in quantity); the remainder is rejected as `insufficient_funds` (stamped with the session
ts); nothing fills when zero lots fit. Cash is debited `notional + cost` at once. Available cash is booked cash less
receivables not yet due.

**Money.** Integer minor units (ADR 0011). Notional is `Money.mul_qty(qty, open)`: `round_half_away(qty * open * 10^exp)`.
Prices and quantities stay `f64`. Positions use `1e-9` tolerance to drop a flat position. A cost function must return a
non-negative `Money`; a negative value raises and leaves the order working (notional and cost are computed before the
order is dequeued, so an exception never loses an order).

**Costs hook.** `costs(side, qty, price) -> Money`, default zero. `make_simulator` resolves named packs
(`none`, `india.equity[.delivery]`, `india.equity.intraday`) from `honba.markets.india.costs`, or adapts a post-hoc
`CostModel` (`fill_costs_from_model`).

**Settlement.** `settlement_days >= 0`, default 0; `make_simulator` takes it from `settlement_days_for(exchange, as_of)`
(T+2 before 2023-01-27, T+1 after for NSE/BSE) and requires it explicitly for intraday timeframes. A session is one bar,
so the cycle counts bars. It can only be changed before the first session.

**Cancel.** `cancel(order_id)` removes a *working* order and reports a whole-remainder `OrderRejection(reason="cancelled",
cancelled=True)` stamped with the **time of the cancel**, which is the current session ts (0 before the first session),
per ADR 008 decision 13 addendum. Unknown, filled, rejected or already cancelled ids are a no-op. Because orders only
leave `working` when fully dealt with, a partly cut order has no cancellable remainder (the rest was rejected at the
fill).

**Rejection reasons.** `unsupported_order_type` (submit time, ts = submit ts), `no_position`, `insufficient_funds`
(fill time, ts = session ts), `cancelled` (cancel time). Remainders only; a fully filled order has none. Per order,
`filled + rejected + cancelled == ordered`.

**Draining.** `drain_fills` and `drain_rejections` return and clear; an engine drained twice repeats nothing.

**Bounds and errors.** `ValueError` on: negative cash, negative settlement days, non-positive or non-finite lot size,
non-monotonic or duplicate bars, non-increasing session, a missing side, a duplicate working id, negative cost. Money
overflow also raises.

## Decision

1. **Type and location.** `honba_sim::NextOpenSim` in a new module `honba-sim/src/next_open.rs` (L4). Dependencies stay
   `honba-messages`, `honba-entities`, `honba-engine`, as `scripts/dependency_graph.py` allows. It implements
   `ExecutionEngine` (`submit`, `cancel(id, now)`, `drain_fills`, `drain_rejections`) and `Handler` (feeds bars), and has
   inherent `open_session(ts, &[Bar])` and `on_bar(&Bar)`. Chunk 2's costs need a cost port: it is an injected closure
   (`Box<dyn Fn(OrderSide, f64, f64) -> Result<Money> + Send>`, like Python's `FillCostFn`), so `honba-sim` does not
   have to depend on `honba-market`; adapting `honba_market::CostSchedule` happens above it (or the dependency is added
   to `ALLOWED_PROD` deliberately, as an inward edge L4 -> L2, in that chunk).
2. **Error mapping.** Python `ValueError` becomes `Err(AlgoError::Component(..))`; the state is unchanged after the
   error, as in Python. Nothing panics.
3. **Time.** Session key and fill timestamps are the bar's `ts_event` (`ts_init` equal); Python `ts` is that integer.
   The cancel stamp is the `now` argument of `ExecutionEngine::cancel` (the runner passes the latest event time, which
   is the session ts in a runner-driven run, so the two agree). A submit-time rejection is stamped with the order's
   `ts_event`.
4. **Determinism and money.** No wall clock, no I/O, no randomness, no hash-order iteration in outputs (positions and
   working orders are kept in vectors or ordered maps). Cash, fees and notional are `i64` minor units via
   `Money::mul_qty` (half away from zero, ADR 0011); every arithmetic step is checked and overflow is an error that
   leaves the order working. Prices and quantities are `f64` with the same expressions and `1e-9` tolerances as Python
   so results are bit-identical, not merely close.
5. **Vectors.** `schema/conformance/next_open_sim.json` is generated from the Python reference by
   `scripts/gen_next_open_vectors.py` (deterministic) and replayed by Rust
   (`crates/honba-sim/tests/next_open_conformance.rs`) and Python
   (`python/tests/integration/test_next_open_sim_conformance.py`). Each scenario carries `chunk` (1, 2, 3); a runner
   consumes only the chunks its implementation covers. (The ROADMAP names the file `next_open.json`; this file is
   `next_open_sim.json` and the ROADMAP row should be read accordingly.)
6. **Corrections to the roadmap row.** The Python reference supports market orders only: limit and stop orders are
   rejected `unsupported_order_type`. Parity for limit and stop therefore means *rejecting* them identically (chunk 1);
   actually filling them is new behavior that needs a Python change first (or a separate, explicitly new Rust-only
   feature behind the L1 known-gap tests), not part of the port.

## Scope per chunk

- **Chunk 1 (this ADR's first implementation).** `NextOpenSim` with market orders, fill at the next bar open
  (sessions from bars and `open_session`, unusable opens, multi-instrument, held while not printing, sells before
  buys), cancel with the cancel-time rule, `unsupported_order_type` / `no_position` / `insufficient_funds` rejections,
  long-only and lot-sized funding cuts at `settlement_days = 0` and zero costs, integer cash, positions, monotonic and
  duplicate bar errors, duplicate id error.
- **Chunk 2.** Settlement (`settlement_days > 0`: receivables, available cash, waiting buys with `first_try`,
  `set_settlement_days` guard), the costs hook (`FillCostFn` equivalent; adapter from the Rust India cost model in
  `honba-market`, whose charges are `f64` and need per-leg rounding to minor units to match
  `nse_equity_delivery_fill_cost`; negative-cost error), the `SessionOpen` event path (`_from_open` leniency), cost
  aware funding bisection, `long_only` interplay, plus every vector tagged `chunk: 2`. Limit/stop: see decision 6.
- **Chunk 3.** PyO3 binding in `honba-py` (+ stub), Python `NextOpenExecution` delegating to the Rust type with the API
  unchanged (`make_simulator`, `group_sessions`, properties `cash`, `fees`, `traded_notional`, `positions`,
  `unsettled`, `available_cash`, `working_orders`), `honba._honba` replaying all vectors and asserting equality with the
  Python reference, and a parity sweep over the whole catalog (including fuzzed scenario comparison).

## Consequences

- One new public type in `honba-sim`; no change to `ExecutionEngine`.
- Until chunk 3 the Python class is untouched and the Rust type is unused outside tests.
- Vector changes are made by changing the Python reference and regenerating; Rust must follow.

## Chunk 2 addendum (settlement, costs, session open)

Implemented in `honba_sim::NextOpenSim`; vectors tagged `chunk: 2` (23 scenarios) replay identically in Rust and
Python.

- **Settlement.** `with_settlement_days` / `set_settlement_days(i64)`, `settlement_days()`, `unsettled()`,
  `available_cash()`, `receivables()` (pending `(due session index, amount)`). A sell books `notional - cost` at once and
  a receivable due at `session + settlement_days`; due receivables are dropped when a session opens. A buy compares
  notional plus cost with available cash; on a shortfall it waits while proceeds are pending and
  `session - first_try < settlement_days` (`first_try` is set when the order is first considered at an open, so an
  instrument that does not print does not start the clock), else it is cut. `set_settlement_days` returns
  `Err(AlgoError::Component)` with the state unchanged for a negative value or after the first session (Python raises
  `ValueError` / `RuntimeError`; the vectors flag both as `error`).
- **Costs hook.** `pub type FillCostFn = Box<dyn Fn(OrderSide, f64, f64) -> Result<Money> + Send>`, installed with
  `with_costs` (default zero). The cost is part of funding (the lot bisection uses notional plus cost), is computed
  before the order is dequeued (a failure leaves it working, state unchanged), `fees()` accumulates it and `Trade::costs`
  carries it. A negative cost is an error; a zero cost is currency-neutral; a non-zero cost in another currency is an
  error (Rust raises it before dequeuing; Python raises it later, after dequeuing a sell, an edge no vector covers).
- **Adapter location (decision).** The adapter from `honba_market::CostSchedule` to `FillCostFn`
  (`nse_equity_delivery_cost_fn`, rounding each charge leg to minor units like Python
  `nse_equity_delivery_fill_cost`) is NOT in this chunk. `scripts/dependency_graph.py` allows no crate that may depend on
  both `honba-sim` and `honba-market` except the L7 crates `honba-py` and `honba-cli`: `honba-testing` and `honba-sweep`
  do not allow `honba-market`, and `honba-market` is not an allowed production or dev dependency of `honba-sim`.
  Layering is not weakened. The adapter will live in `honba-py` (chunk 3 binding crate), where `make_simulator` needs it
  anyway, with a test comparing it to the Python function over a grid of sides, quantities and prices. The cost vectors
  use a parametric test model (`config.costs`: `flat_buy/flat_sell/bps_buy/bps_sell`, defined in
  `scripts/gen_next_open_vectors.py`) that both runners implement.
- **Session open.** Rust has no `SessionOpen` event variant (adding one to the L0 `Event` would touch wire, codegen and
  every match), so the equivalent is the inherent `NextOpenSim::on_session_open(ts, &[Bar])`: it opens the session and then
  switches to the lenient `_from_open` bar rules permanently (a bar only opens an instrument the event left out, at the
  same ts; everything else is ignored). If opening fails the mode does not change. Vector op: `session_open`.
- **Vector additions** (chunk 1 bytes unchanged): ops `session_open`, `set_settlement_days`, `probe`; `config.costs`;
  chunk 2 scenarios also record `unsettled`, `available_cash` and `probes` in `expect.final`.

### Remaining for chunk 3

1. `honba-py` PyO3 class for `NextOpenSim` plus `.pyi` stub and stubtest; expose `on_session_open`, `open_session`,
   `on_bar`, `set_lot_size`, settlement and cost configuration, accessors (`cash`, `fees`, `traded_notional`, `positions`,
   `unsettled`, `available_cash`, `working_orders`).
2. The `CostSchedule` -> `FillCostFn` adapter in `honba-py` (India delivery and intraday), rounding each leg, with a
   parity test against `nse_equity_delivery_fill_cost` / `nse_equity_intraday_fill_cost`.
3. Python `NextOpenExecution` delegates to the Rust type with its API unchanged (`make_simulator`, `group_sessions`,
   `SessionOpen`, `FillCostFn` callables bridged through the binding, error types `ValueError` / `RuntimeError`).
4. `honba._honba` replays every vector (all chunks) and equals the Python reference, plus a fuzzed scenario comparison
   over the whole catalog.
5. Limit and stop fills remain out of scope (Python rejects them; decision 6).
