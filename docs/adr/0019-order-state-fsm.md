# ADR 0019: The Order-State FSM and Client Order Ids

Date: 2026-10-07. Status: proposed. Roadmap story: decides D5, resolves D2; breaks E2-S6 into
(a)-(d) and completes E2-S6's wired behaviour.

## Context

Today an order has a status field but no state machine, two drain queues but no single
lifecycle vocabulary, and no place where the client order id and the broker order id meet.

- `OrderStatus { Initialized, Submitted, Accepted, PartiallyFilled, Filled, Cancelled, Rejected,
  Expired }` plus `TimeInForce` and `OrderType` live in `honba-messages`
  (`crates/honba-messages/src/orders/order.rs:44`, `enum_with_all!`, eight values, `ALL.len() == 8`
  pinned by `crates/honba-messages/src/tests/order.rs:50`). `Order::new` creates
  `Initialized` (`orders/order.rs:174`, `:195`). No transition function exists, `OrderStatus` has
  no `can_transition_to`, and the per-engine status store is the ad-hoc
  `OrderLedger: HashMap<String, OrderStatus>` private to `honba-sim::PaperExecution`
  (`crates/honba-sim/src/paper.rs:122`).
- The wire events are `Event::{Order, OrderAccepted, OrderRejected, OrderFilled, OrderCancelled}`
  (`crates/honba-messages/src/events/event.rs:43`, tag `"order"` for submission). There is no
  `order_partially_filled`, no `order_cancel_requested` and no `order_expired`; rejections and
  cancellations never reach the event queue — they appear only as `AuditKind::{OrderRejected,
  OrderCancelled}` (`crates/honba-engine/src/audit.rs:40`, `:47`) while fills are turned back into
  `Event::OrderFilled` by `Engine::acknowledge_fills` (`crates/honba-engine/src/engine.rs:233`).
- The engine port and the Python port each present **two** drains:
  `ExecutionEngine::{drain_fills, drain_rejections}` (`crates/honba-engine/src/execution.rs:97`,
  `:106`, `drain_rejections` a default `Ok(Vec::new())`) and `ExecutionPort::drain_fills` /
  `RejectingExecutionPort::{cancel, drain_rejections}` (`python/src/honba/strategies/execution.py:76`,
  `:85`), with shims `drain_port_rejections` / `cancel_order` (`execution.py:115`, `:121`).
  A fill and a subsequent rejection are therefore two independent queues with no defined relative
  order — the exact class of sequency bug an FSM must forbid. `drain_*` appears in ~30 files across
  `honba-engine`, `honba-sim`, `honba-strategy`, `honba-sweep` (`TrialSink`), `honba-py`
  (`NextOpenSimulator.drain_fills`/`.drain_rejections`, `PricedBarFill`) and `python/src/honba`
  (`execution.py`, `runner.py`, `testing.py`, `backtest/simulated.py`); the async mirror
  `honba_ports::ExecutionGateway` (`crates/honba-ports/src/execution.rs:20`) has no drains at all
  (`submit_order`, `cancel_order`, `modify_order`, `next_fill`).
- `OrderRejection` lives in `honba-engine`
  (`crates/honba-engine/src/execution.rs:16`, `CANCELLED = "cancelled"`, `is_cancelled() = reason
  == "cancelled"`), first proposed by ADR 0008 decisions 11/13 and two addenda: the two cannot
  disagree on cancellation and `cancel(order_id, now: UnixNanos)` is stamped with the engine time
  at which the cancel was processed, not the order's `ts_event` (`execution.rs:94`, ADR 0008 decision
  13 addendum). Python mirrors this as a frozen dataclass with
  `cancelled == (reason == "cancelled")` (`execution.py:44`, `:66`).
- `OrderId` is already defined as "A client-assigned order identifier"
  (`crates/honba-messages/src/identifiers.rs:87`); strategies mint `"{name}-{n}"` (ADR 0008
  decision 5). There is no broker venue id: `TradeId` is "an exchange-assigned trade identifier"
  (`identifiers.rs:122`), but no `VenueOrderId`. Python adapters carry `place_order(..., client_order_id)`
  as the idempotency key with the comment "E2-S11" (`python/src/honba/adapters/base.py:191`), and
  adapters return an `OrderReport { order_id, status: OrderStatus, ... }` whose snapshot prose
  says it is "the snapshot counterpart of the order-state machine (E2-S6)"
  (`python/src/honba/adapters/models.py:283`). `docs/ROADMAP.md:431` states the correction:
  "Only `OrderStatus` enum and `drain_rejections`/`drain_fills`; no FSM, no `client_order_id`, no
  ack events".
- The conformance gap is explicit. ADR 0008 shipped `schema/conformance/order_rejections.json`
  (eight scenarios including `late_cancel_is_stamped_with_the_cancel_time`), runner semantics
  (`python/src/honba/strategies/runner.py:151`, `crates/honba-strategy/src/runner.rs:279`,
  `honba-sim/tests/execution_contract.rs`), but noted as a known gap that
  "Venue-side rejections and cancellations as an order *state machine* (partial-fill lifecycle,
  amend, venue acknowledgements) are E2-S6".
- Handlers see events through the one method `Handler::on_event(&Event, ts_init)`
  (`crates/honba-engine/src/handler.rs:19`), so a wire vocabulary change is visible everywhere a
  handler or a journal reader exists.

## Decision

### 1. `OrderState` is the FSM, over `OrderStatus`, with events as the verbs

*No new state is added to the wire status enum.* `CancelRequested` is an **event**, not a state.
`OrderStatus` stays the eight values every consumer already knows; `ALL.len() == 8` keeps its
assertion. `Order.status` is the wire field on `Order` and on `OrderReport`.

What is new is a value object in `honba-messages` that **is** the machine:

```rust
// honba-messages
pub struct OrderState {
    pub status: OrderStatus,
    pub filled_qty: f64,
    pub cancel_requested: bool,
}
impl OrderState {
    pub fn new() -> Self { /* Initialized, zero, false */ }
    pub fn apply(&mut self, ev: &OrderEvent) -> Result<(), IllegalTransition>;
    pub fn can_transition(from: (OrderStatus, bool), via: OrderEventKind) -> bool { ... }
}
```

- `filled_qty` lets the machine know how much of the order's quantity (from the `order` event)
  is done, so `partially-filled` vs `filled` is derivable without asking any other store.
- `cancel_requested` is needed because that much of the FSM is "I asked for a cancel and am
  waiting for the answer"; it is internal to `OrderState`.
- `IllegalTransition` is a typed error carrying the ordered triple `(status, cancel_requested,
  event_kind)`, with a fixed prose that is used in unit-test assertions.

To make `cancel_requested` observable without following the event stream, `Order` gains an additive
record field:

```rust
// honba_messages::Order
#[serde(default, skip_serializing_if = "is_false")]
pub cancel_requested: bool,
```

For an `Event` record this is an additive field (ADR 0012: records ignore unknown fields), so old
readers drop it and a missing field reads `false`; the journal needs no migration and `make codegen`
plus `make schema-ts` is the only artifact work.

Rejected: adding `CancelRequested` as a ninth `OrderStatus` value so that `status` itself says
"cancel pending". That would change the wire enum for every consumer (including the sibling
`honba-adapters` package, every frontend consumer via the generated TypeScript, and the Python
`OrderStatus` schema) and duplicate the idea of "accepted with a pending cancel", whose willing
consumer today already has a status — `Accepted` / `PartiallyFilled`.

### 2. Eight FSM events, realised as three wire variants and one port vocabulary

The conceptual event set — **submitted, accepted, rejected, partially-filled, filled,
cancel-requested, cancelled, expired** (ROADMAP §4's D5 list) — maps to:

| Concept | Wire `Event` variant (tag) | Notes |
|---|---|---|
| submitted | `Order(Order)` (`"order"`) | already exists; the `Order` inside carries `status = Submitted` |
| accepted | `OrderAccepted` (`"order_accepted"`) | exists |
| rejected | `OrderRejected` (`"order_rejected"`) | exists |
| partially-filled | `OrderPartiallyFilled` (`"order_partially_filled"`) | new |
| filled | `OrderFilled` (`"order_filled"`) | narrows to "the completing fill" |
| cancel-requested | `OrderCancelRequested` (`"order_cancel_requested"`) | new |
| cancelled | `OrderCancelled` (`"order_cancelled"`) | exists |
| expired | `OrderExpired` (`"order_expired"`) | new |

```rust
// honba_messages::Event, additions:
OrderPartiallyFilled { order_id: OrderId, last_qty: f64, last_px: f64, cum_qty: f64, ts_event: UnixNanos },
OrderCancelRequested { order_id: OrderId, ts_event: UnixNanos },
OrderExpired         { order_id: OrderId, ts_event: UnixNanos },
```

- `OrderPartiallyFilled` means "a fill happened and left a remainder"; `OrderFilled` means "the
  fill that completed the order" — a fill that finishes the order emits only `order_filled`; every
  earlier fill emits `order_partially_filled`. Both events carry `last_qty`/`last_px` like
  `OrderFilled` does today; the partial also carries `cum_qty`, so a journal reader can reconstruct
  how much of the order is done without having to sum the stream. `OrderFilled` keeps its current
  wire shape (no `cum_qty`) — by definition it is the completing fill, so `cum_qty == quantity`.
- `cancel_requested` has no fill or price — it is the FSM's record that a cancel was asked for.
- `expired` is E3-S1b's `TimeInForce::Day` expiry; defined now, emitted then (Known limits).
- `Event` is `#[non_exhaustive]` and is serialized `tag = "type"`; the three new variants are
  additive on the wire, so `make codegen` regenerates the domain schema, OpenAPI, `.pyi`, MCP and
  TypeScript. The Python wire mirrors (`python/src/honba/wire/wire.py`, the generated `TypedDict`
  literals and `python/src/honba/wire/generated`) are strict unions: an old reader hitting the new
  tags is a decode error, exactly as ADR 0012 rule 3 says, and gets a CHANGELOG migration note.
  Golden vectors (`schema/golden/event.json`) gain accepted/part-fill/expire cases.

Rejected: folding the whole lifecycle into an optional `cum_qty` on `OrderFilled` and deriving the
states — that would have kept the variant count flat, but it would have made a `order_rejected`
after a partial fill indistinguishable in the stream from a fill with a cum field of zero, and it
would not give cancel-requested any place at all.

### 3. Client order ids, and the missing broker order id

- `OrderId` **is** the client order id: minted by the strategy/runner, it is the FSM key, the
  REST/Journals key (`GET /orders/{id}`, `DELETE /orders/{id}`, `GET /journals/{id}`), and the value
  adapters must forward as the broker's idempotency key
  (`ExecutionAdapter.place_order(..., client_order_id) -> OrderReport`,
  `crates/honba-ports`-equivalent in Python; `fingerprint_dedup` in E2-S11 deduplicates on this).
  No rename, no second client id.
- What *is* missing is the **venue order id**: the exchange/broker id that E2-S7 reconciliation
  needs to join venue reports to client orders. So:

```rust
// honba-messages, a new opaque wrapper beside OrderId:
pub struct VenueOrderId(String);
```

and on the only wire place where a venue id can be learned:

```rust
// honba_messages::Event::OrderAccepted, additive optional field:
OrderAccepted { order_id: OrderId, venue_order_id: Option<VenueOrderId>, ts_event: UnixNanos },
```

`Order` itself does **not** gain `venue_order_id` — a client order has no broker id until it is
accepted; `GET /orders` in live mode therefore shows the broker id (when known) through the same
field on `OrderReport` below. Both optional fields carry `#[serde(default,
skip_serializing_if = "Option::is_none")]`, so a missing field on the wire reads `None`.

- Python mirror: `OrderReport` (frozen dataclass, `python/src/honba/adapters/models.py:283`)
  gains `venue_order_id: str | None = None`; its `__post_init__` keeps `reject_reason` validated
  only for `REJECTED` and otherwise `None`, and the `OrderExecuted`/`Trade`-style books keep their
  invariants.

Rejected: introducing a separate `ClientOrderId` type beside `OrderId` — two client ids and three
total ids is the drift this ADR would have to manage; the one-sentence doc on `OrderId` already
says what it is and the adapter idempotency contract already wires it.

### 4. One ordered event queue replaces two drains

This is the breaking core of E2-S6(b). Two independent queues cannot define the order between a fill
and a later rejection without inventing a third queue to keep them in step, which no consumer today
does. One queue can.

**Port contract.** A single pull queue, drained in order, over an enriched set of events that carry
a full `Trade` where a fill happened. Because `Trade` (with `costs: Money`, `ts_init`, full
`quantity`/`price`) lives in `honba-entities` (L1) and `honba-messages` (L0) may not name anything
in `honba-entities`, the type cannot live in `honba-messages`:

```rust
// honba-entities (L1), so honba-ports (L2), honba-engine (L3), honba-strategy (L4),
// honba-py (L7) and a future ExecutionGateway all see it:
pub enum ExecutionEvent {
    Submitted       { order_id: OrderId, instrument_id: InstrumentId, side: OrderSide,
                      quantity: f64, ts: UnixNanos },
    Accepted        { order_id: OrderId, venue_order_id: Option<VenueOrderId>, ts: UnixNanos },
    Rejected        { order_id: OrderId, reason: String, ts: UnixNanos },
    Fill            { trade: Trade, cum_qty: f64, complete: bool },   // complete=false -> partially
    CancelRequested { order_id: OrderId, ts: UnixNanos },
    Cancelled       { order_id: OrderId, quantity: f64, ts: UnixNanos },
    Expired         { order_id: OrderId, ts: UnixNanos },
    // `quantity` on Rejected/Cancelled is the unfilled remainder, as today on OrderRejection
}

pub trait ExecutionEngine: Send {
    fn submit(&mut self, order: Order) -> Result<()>;
    fn cancel(&mut self, order_id: &str, now: UnixNanos) -> Result<()>;
    fn drain_events(&mut self) -> Result<Vec<ExecutionEvent>>;
}
```

`drain_fills` and `drain_rejections` are removed. Every in-repo engine (BarFill, Paper,
NextOpen, Scripted in `honba-sim`; PricedBarFill, `NextOpenSimulator` in `honba-py`; `TrialSink` in
`honba-sweep`; testing fixtures) migrates in one series, and so does every call site (the ~30
listed in Context). `OrderRejection`'s `rejected`/`cancelled` ctors and `is_cancelled` migrate into
the `Rejected`/`Cancelled` variants (`quantity` is the remainder; `is_cancelled()` becomes
`matches!(ExecutionEvent::Cancelled { .. }, ...)` or a dedicated `Reason::cancelled` string —
decided with the patch).

**Rust trait break.** Removing two methods with no default that still returns the right answer is a
hard break: every `impl ExecutionEngine` stops compiling, which is exactly the migration guide in
this case. It is a pre-1.0 breaking change, noted once in the CHANGELOG per ADR 0012 rule 5.

**Python / bindings break.** `ExecutionPort.drain_fills` / `RejectingExecutionPort.drain_rejections`
/ `BaseExecutionPort` / shims `drain_port_rejections` / `cancel_order` in
`python/src/honba/strategies/execution.py` and every Python port
(`honba.backtest.simulated.NextOpenExecution`, `honba.strategies.testing` helpers) break the same
way. The new Python protocol is `events(self) -> list[ExecutionEvent]`, mirroring the Rust one.

- For one minor version the old pair stays as a **deprecated shim** over `events()` (mirroring ADR 0008
  decision 7's policy: present in 0.1.x and 0.2.x, removed in 0.3), emitting a `DeprecationWarning`
  on the port class when a subclass *implements* only the legacy pair. Inside the shim a
  `ExecutionPort` that only implements the old pair is run as `events() = [Fill(trade, ..)]` (ordered
  as the runner does today — `drain_fills` then `drain_rejections`). The shim's ordering is the
  documented *legacy* order; only `events()` carries the defined order across fills and rejections.
- `honba._honba` bindings (`crates/honba-py/src/pyclasses/next_open.rs` and `run.rs`) gain
  `drain_events` and their `.pyi` stubs are regenerated; `drain_fills`/`drain_rejections` remain as
  shim methods that derive from `drain_events`.

Rejected: keeping two drains and adding a "sequence number" across them — a consumer that forgets to
merge the number has the same bug it has today, only now it errors on a number mismatch. Rejected: a
push `Sink<Message>`/`Handler` instead of a pull — `Engine` would have to depend on
`honba_ports::Sink` (L2) for a gain in the kernel and a loss on the Python port path, which has no
`Engine` and would need a sink contract too.

### 5. The other half of one channel: wire events vs port events (and `honba-ports`)

Wire events and port events are two faces of one channel, not two channels. The engine translates
once, in `Engine::acknowledge_events` (the present `acknowledge_fills`, `engine.rs:233`, which
already pushes `Event::OrderFilled` for handlers): each `ExecutionEvent` produces exactly one
`Event` (or, for `Fill { complete: true }`, the one `Event` that completes the order), pushed onto
the engine queue for `Handler::on_event`, for the journal (`Sink`) and for the wall of the REST
read API. That way a conformance fixture can assert that `Fill { trade { qty=1, px=10 } }` and
`order_partially_filled { last_qty=1.0, last_px=10.0, cum_qty=1.0 }` carry the same `ts_event` and
cannot drift.

The async `honba_ports::ExecutionGateway` (D10) should adopt the same `honba_entities::ExecutionEvent`
when its bridge is decided: whether a live gateway batches events with `drain_events` or pushes them
from an adapter task, the variants are the same, and an SSI L3 book or a latency model (E3-S2) is
the only thing that changes timing. Until then `ExecutionGateway` stays as it is (four methods, no
drains) — this ADR is not D10.

### 6. Cancel times per fidelity (resolves D2)

Starting from ADR 0008 decision 13's two addenda — a cancel is stamped with `now` at the time the
cancel is *processed*, not with the order's `ts_event`, and `ExecutionEngine::cancel` has been
`cancel(order_id, now)` since commit `1c50fc7`:

| Fidelity | What `cancel` does | `order_cancel_requested.ts` | `order_cancelled.ts` |
|---|---|---|---|
| **L1** (BarFill, paper, `NextOpenSimulator`, chunk 1 vectors) | processed at `now` | `now` at `cancel()` | **same `now`** — there is no venue delay |
| **L2** (tick-matched sim, E3-S1b, `FillModel` + depth) | ack before fill at `now` | `now` | `now` in v1; once `LatencyModel` (E3-S2) exists, the delay the model says it would have taken at that position in the simulated book |
| **Live** | `place_order` at `now` over REST/WS, `cancel` at `now`, `order_cancelled` at the **venue ack** time | client/engine clock at the request (`now`, `ctx.now()` in a strategy, `ts_event` on the wire) | **the venue's exchange timestamp** for the cancellation, normalised to `UnixNanos` at the adapter boundary (`parsing.py`/`shared` anti-corruption layer) |

- Backtests never read the wall clock: `ts_event` in the event and `TradingState` in the request
  both come from the simulated `ts_init` (`crates/honba-async/src/clocks.rs:91` (`HistoricClock`; imports at `:11`)).
  The live adapter boundary converts venue timestamps into `UnixNanos` there, so the FSM never
  sees wall-clock time.
- `cancel` remains idempotent (ADR 0010 decision 4): cancelling an unknown or terminal order is a
  no-op, `cancel_requested` is re-emitted only when the order is still working, and a fill that
  beats a cancel wins — no `cancelled` event, the fill event stands.

### 7. Migration plan — four stories, in order

| # | Scope | Gate | Key files | Acceptance |
|---|---|---|---|---|
| **(a)** | `OrderState` FSM, `ExecutionEvent`, the three new `Event` variants, `VenueOrderId`, `Order.cancel_requested` — `honba-messages` + `honba-entities` | this ADR | `crates/honba-messages/src/{identifiers.rs, events/event.rs, orders/order.rs}`, `crates/honba-entities/src/{order_event.rs}` plus codegen (`crates/honba-codegen/src/{registry.rs, endpoints.rs, mcp.rs}`) and `make codegen` | `illegal_transition_rejected`, `partial_fill_sequence` unit; `ALL.len()==8` unchanged; golden `event.json` gains the three accepted/rejected/cancelled partial/expire cases |
| **(b)** | `ExecutionEngine` migrates to `drain_events`, the engine translates, the bindings follow — `honba-engine`, every engine in `honba-sim` and `honba-py`, `honba-sweep::TrialSink`, `StrategyRunner` (`crates/honba-strategy` — `runner.rs:279`, books from `ExecutionEvent::Fill` → `LedgerContext`) | (a) done | `crates/honba-engine/src/{execution.rs, engine.rs, handler.rs}`, `crates/honba-sim/src/{bar_fill.rs, paper.rs, next_open.rs, scripted.rs}`, `crates/honba-py/src/{pyclasses/next_open.rs, pyclasses/run.rs}`, `crates/honba-strategy/src/runner.rs`, `python/src/honba/strategies/{execution.py, runner.py}` | the existing `order_rejections.json` 8 vectors must stay green and the `execution_contract.rs` invariant `filled + released == ordered` must hold with the new ordering |
| **(c)** | Adapter contract — `ExecutionAdapter`'s push form beside its query form — `python/src/honba/adapters/{base,models,contract,testing}` and the sibling `honba-adapters` package (ask first, per `docs/ROADMAP.md:362`) | (b) (so the adapter can implement `events()` against a real `OrderState`) | `python/src/honba/adapters/*`, `honba-adapters/shared/*`, adapter contract suite | the shared scenario set of decision 8 runs on `FakeAdapter` |
| **(d)** | Sim and paper identical — L1 sims run the same scenario set through the same code path | (b) | `crates/honba-sim/tests/*`, `crates/honba-py/tests/*`, `python/tests/integration/test_order_state_conformance.py` | every gateway (BarFill, Paper, NextOpen, Scripted; Python ports; bindings) produces byte-identical event streams for each scenario |

Rejected threading: building the new engines `(b)` before the vocabulary `(a)` — then no type can name
`VenueOrderId` or `ExecutionEvent`; and building the adapter surface `(c)` before the engine
contract `(b)` — then adapters implement folklore until the tag spellings and the cancel-time rule
are pinned. ADR first, then vocabulary, then engines, then adapters, then parity (`honba-async`'s
`ExecutionGateway` when D10 is decided).

### 8. Conformance scenario set — one set, every gateway

`schema/conformance/order_state.json` (new), run by both languages and by every gateway. The set
exercises market, limit, partial, reject, a cancel race and a TIF expiry (ROADMAP E2-S6's acceptance
phrase), plus the edge cases that already have path names in `order_rejections.json`:

| Scenario | Why it must be run by every gateway |
|---|---|
| `market_fill` | `submitted -> (accepted?) -> filled` (L1 has no ack; live must show `accepted` before a fill — the FSM allows `submitted -> filled` and the *adapter* contract asserts the ack) |
| `limit_fill` | limit accepted, resting, then `partially`/`filled` across three driving events; TIF handling is via the expiry row, not here |
| `partial_fill_sequence` | the `partial_fill_sequence` unit pin plus a chain of three `order_partially_filled` whose `cum_qty` is monotone and whose final `order_filled.last_qty` finishes the order |
| `reject_at_submit` | `submitted -> rejected` (`unsupported_order_type`, quantity shape, or risk — but risk is ADR 0018, so this set does not rely on it) |
| `cancel_race` | fill and cancel at the same `ts_event`: exactly one terminal event (`order_filled` or `order_cancelled`); verifies the "fill that beats a cancel wins" invariant and the cancel time table |
| `tif_expiry` | a `Day` order that becomes `expired` at session end (defined now, see Known limits) |

plus the four legacy orders from `order_rejections.json` that exercise `held_order_stays_busy`,
`cancel_finished_or_unknown_is_a_no_op` and `release_is_per_instrument` — they stay green.

### 9. What breaks in `honba-adapters` and in the sibling `honba` packages

| Surface | Today | After this ADR |
|---|---|---|
| `honba-engine::ExecutionEngine` (Rust trait) | `drain_fills`/`drain_rejections`/`cancel(id, now)` | `drain_events()`/`cancel(id, now)` |
| every engine in `honba-sim` and `honba-py` and `honba-sweep::TrialSink` | `impl ExecutionEngine` with two drains | the four engines above plus the honba-py `NextOpenSimulator`/`PricedBarFill` follow |
| `honba._honba` bindings + `.pyi` | names `drain_fills`/`drain_rejections` | those hit `drain_events`; the old pair remains as a deprecated shim for one minor (decision 4) |
| `python/src/honba/strategies/execution.py` | `ExecutionPort.drain_fills`; `RejectingExecutionPort.drain_rejections`; `cancel(order_id)` without `now`; shims `drain_port_rejections`/`cancel_order` | `ExecutionPort.events() -> list[ExecutionEvent]`, `cancel(order_id, now)` (the runner supplies `now = ctx.now()`, so Python callers need not change); old pair become deprecated shims |
| `python/src/honba/strategies/runner.py` | drains `fills`, then `rejections` per event (`runner.py:151`) | drains `events()`, derives fills + releases from one stream, updates `OrderState`; legacy drain path kept behind the shims |
| `honba.backtest.simulated.NextOpenExecution` | two drains, `cancel(id)` without `now` | one drain, `cancel(id, now)` (runner supplies `now`) |
| `python/src/honba/adapters/testing.py` / `FakeAdapter` | no status machine; `status = FILLED` at (`testing.py:302`) | derives `status` from `OrderState`, which forbids rewinding a terminal status |
| `honba-wire` Python mirror + generated `.pyi` | union `EventOrder\|Accepted\|Rejected\|Filled\|Cancelled` (`python/src/honba/wire/wire.py:402`) + `EventOrderFilled` literal | add `EventOrderPartiallyFilled`/`EventOrderCancelRequested`/`EventOrderExpired`, and `OrderAccepted.venue_order_id`, `Order.cancel_requested`; literals `_on.pyi` regen |
| `schema/golden/event.json`, OpenAPI | `order_filled_zero_qty`, duplicate-type, etc. | gain the three new variants plus `order_accepted_with_venue_id`, `order_partial_then_fill` |
| **Sibling `honba-adapters`** (sibling repo, **ask before touching**): each broker adapter will | expose `place/cancel/order_status` query form (ADR 0010) | expose a streaming form beside it: `events(since) -> list[OrderEvent]` or the adapter's websocket maps into the same `OrderState`, plus `OrderReport.venue_order_id` and the `client_order_id == OrderId` dedup line |

## Consequences

- One transition table, two drain bugs removed, every path ordered. The runner, the engine and the
  adapter translate once, in one place, so an order that the paper path `rejected` can be refused by
  strategy tests and a cancel race can be pinned with one vector that every gateway must replay.
- `StrategyRunner` can now answer "what is the state of order `O-7`?" from `OrderState` rather than
  from two booleans ("is `busy`?"), which is what stops the Python shim `self.ctx.busy(id)` from
  being re-implemented when the port gains a real acknowledgement.
- Wire growth: `Event` gains three variants (each an additive `tag` value), so `make codegen` plus
  `make schema-ts` (the sibling-frontend check) once, and a CHANGELOG note because a consumer
  pattern-matching on `type` with a strict decoder is now non-exhaustive. Python and Rust golden
  vectors, `schema/domain`/`openapi`/`mcp_tools.json` and `python/src/honba/wire` mirrors move in
  the same commit (ADR 0014).
- `GET /orders` plus the journal now expose `Order` with `cancel_requested`, so a UI or an agent
  can show a pending cancel without subscribing to the event stream.

## Known limits

- TIF expiry (`order_expired`) is defined by this ADR but no engine emits it: `TimeInForce::Day`
  is a field on every order, nothing checks it. E3-S1b (L2) plus the market pack's expiry/session
  helpers are the producers; until then `tif_expiry` is `not_applicable` for every gateway.
- Amend/replace (change quantity or price of a working order) is not in the vocabulary. The
  adapter contract already has `modify_order` (ADR 0010 decision 4) but the FSM has no
  `modify_requested`/`modified`/`modify_rejected`, so the only way to change an order today is
  `cancel` + `submit`.
- `Order` carries no `venue_order_id`: the REST and journal order arrays are client orders; the
  venue id is on `order_accepted` / `OrderReport` as in decision 3.
- The legacy Python drain shims cannot preserve the one-queue order by definition; they exist so
  that a port that only implemented the old pair keeps failing for the same reason it does today,
  not to give cross-queue ordering to legacy ports.
- Reconciliation (E2-S7, `missed_fill_detected`) and durable idempotency (E2-S11,
  `fingerprint_dedup`, E5's `client_order_id`) build on this vocabulary but are their own stories.
- L1 engines never emit `order_accepted`; the FSM allows `submitted -> filled` and the adapter
  contract **requires** `accepted` before a fill, so a live gateway cannot be certified by replaying
  only L1 vectors.
- The reporting layer (`TradesResponse`, `PositionsResponse`) stays typed as today; the order books
  are still `Vec<Order>`, not paginated, and the `/orders` write routes remain 501 until ADR 0018's
  risk stage and the E5-S4 approval queue cover them.

