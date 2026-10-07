# ADR 0019: The Order-State FSM and Client Order Ids

Date: 2026-10-07. Status: **accepted** (2026-10-07; accepted with review amendments). Roadmap
story: decides D5, resolves D2; breaks E2-S6 into (a)-(d) and completes E2-S6's wired behaviour.

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
  (`crates/honba-sim/src/paper.rs:122`). No production code sets `Submitted` or builds
  `Event::Order`: the variant is only matched (`event.rs:100`), never produced.
- The wire events are `Event::{Order, OrderAccepted, OrderRejected, OrderFilled, OrderCancelled}`
  (`crates/honba-messages/src/events/event.rs:46`, tag `"order"` for submission), at
  `SCHEMA_VERSION = 3` (`event.rs:30`; a reader accepts exactly its own version, `event.rs:155`).
  There is no `order_partially_filled`, no `order_cancel_requested` and no `order_expired`;
  rejections and cancellations never reach the event queue — they appear only as
  `AuditKind::{OrderRejected, OrderCancelled}` (`crates/honba-engine/src/audit.rs:40`, `:47`; the
  latter records that a cancel was *routed*, not that the venue cancelled) while fills are turned
  back into `Event::OrderFilled` by `Engine::acknowledge_fills`
  (`crates/honba-engine/src/engine.rs:234`). Every fill, partial or not, is an `order_filled`.
- The engine port and the Python port each present **two** drains:
  `ExecutionEngine::{drain_fills, drain_rejections}` (`crates/honba-engine/src/execution.rs:97`,
  `:106`, `drain_rejections` a default `Ok(Vec::new())`), the public `Engine::drain_fills`
  (`engine.rs:220`, buffered in `Engine.pending_fills`), and `ExecutionPort::drain_fills` /
  `RejectingExecutionPort::{cancel, drain_rejections}` (`python/src/honba/strategies/execution.py:77`,
  `:86`), with shims `drain_port_rejections` / `cancel_order` (`execution.py:115`, `:121`). The
  Python `cancel(order_id)` takes no `now`. A fill and a subsequent rejection are therefore two
  independent queues with no defined relative order — the exact class of sequencing bug an FSM must
  forbid. `drain_fills|drain_rejections|drain_port_rejections` appears in **57 files** under
  `honba/` (`grep -rlE`, excluding `target/`; 54 in the review's count, which excluded the
  handoff note and ADRs): 33 Rust files across `honba-engine`, `honba-sim`, `honba-strategy`,
  `honba-sweep` (`TrialSink`), `honba-testing` and `honba-py` (`NextOpenSimulator`,
  `PricedBarFill`); 17 Python files (`execution.py`, `runner.py`, `testing.py`,
  `backtest/simulated.py`, their tests and one script); plus `CHANGELOG.md`, `docs/ROADMAP.md`,
  ADRs 0008/0016/0019, an archive doc and `.claude/HANDOFF.md`. Sibling
  repos: `honba-docs/docs/architecture/order_reject_cancel.md` names the drains;
  `honba-strategies` and `honba-examples` have no direct hit but run through the runner, so all
  three are flagged for a confirm-first follow-up (workspace rule). The async mirror
  `honba_ports::ExecutionGateway` (`crates/honba-ports/src/execution.rs:20`) has no drains at all
  (`submit_order`, `cancel_order`, `modify_order`, `next_fill`).
- `OrderRejection` lives in `honba-engine`
  (`crates/honba-engine/src/execution.rs:17`, `CANCELLED = "cancelled"`, `is_cancelled() = reason
  == "cancelled"`), first proposed by ADR 0008 decisions 11/13 and two addenda: the two cannot
  disagree on cancellation and `cancel(order_id, now: UnixNanos)` is stamped with the engine time
  at which the cancel was processed, not the order's `ts_event` (`execution.rs:94`, ADR 0008 decision
  13 addendum). Python mirrors this as a frozen dataclass with
  `cancelled == (reason == "cancelled")` (`execution.py:49`, `:66`).
- `OrderId` is already defined as "A client-assigned order identifier"
  (`crates/honba-messages/src/identifiers.rs:84`); strategies mint `"{name}-{n}"` (ADR 0008
  decision 5). There is no broker venue id: `TradeId` is "an exchange-assigned trade identifier"
  (`identifiers.rs:120`), but `Trade` (`crates/honba-entities/src/trade.rs:41`) carries no
  `TradeId` field and there is no `VenueOrderId`. Python adapters carry
  `place_order(..., client_order_id)` as the idempotency key with the comment "E2-S11"
  (`python/src/honba/adapters/base.py:191`), and adapters return an
  `OrderReport { order_id, status, filled_quantity, average_price, ... }` whose prose says it is
  "the snapshot counterpart of the order-state machine (E2-S6)"
  (`python/src/honba/adapters/models.py:283`). `docs/ROADMAP.md:432` states the correction:
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
- ADR 0018 (risk stage) refuses orders inside `Engine::submit` (`engine.rs:259`) and its decision 6
  routes refusals to the strategy as an `OrderRejection`; its reduce-only rule reads a position
  map the engine keeps from `acknowledge_fills`. Both touch the surface this ADR replaces, so
  decision 5 below states the joint contract.

## Decision

### 1. `OrderState` is the FSM, over `OrderStatus`, with events as the verbs

*No new state is added to the wire status enum.* `CancelRequested` is an **event**, not a state.
`OrderStatus` stays the eight values every consumer already knows; `ALL.len() == 8` keeps its
assertion. `Order.status` is the wire field on `Order` and on `OrderReport`.

**L0 types (all in `honba-messages`, which may depend on nothing — `scripts/dependency_graph.py:31`).**
The machine speaks a quantity-only event vocabulary so that it needs no `Trade`, `Money` or
instrument type:

```rust
// honba-messages (L0)
pub enum OrderEvent {
    Submitted { quantity: f64 },          // order quantity; the only event that sets it
    Accepted,
    Rejected,
    Fill { last_qty: f64, complete: bool },   // `complete` is the producer's claim
    CancelRequested,
    Cancelled,
    Expired,
}
pub enum OrderEventKind { Submitted, Accepted, Rejected, Fill, CancelRequested, Cancelled, Expired }

pub struct OrderState {
    pub status: OrderStatus,
    pub quantity: f64,          // 0.0 until Submitted
    pub filled_qty: f64,
    pub cancel_requested: bool,
}
impl OrderState {
    pub fn new() -> Self { /* Initialized, 0.0, 0.0, false */ }
    /// Ok(true) = transitioned, Ok(false) = duplicate no-op, Err = illegal (state unchanged).
    pub fn apply(&mut self, ev: &OrderEvent) -> Result<bool, IllegalTransition>;
    pub fn can_transition(from: (OrderStatus, bool), via: OrderEventKind) -> bool { ... }
}

pub enum IllegalTransition {
    Transition { status: OrderStatus, cancel_requested: bool, event: OrderEventKind },
    Overfill   { quantity: f64, filled_qty: f64, last_qty: f64 },
    FillMismatch { claimed_complete: bool, derived_complete: bool },
    InvalidQuantity { value: f64 },   // non-finite or <= 0 quantity / last_qty
}
```

- `ExecutionEvent` (L1, decision 4) converts with `ExecutionEvent::order_event(&self) ->
  OrderEvent`; the FSM never sees a `Trade`.
- **Partial vs filled** is derived, with the ADR 0016 tolerance convention: after a fill,
  `filled_qty + 1e-9 >= quantity` is `Filled`, otherwise `PartiallyFilled`. The producer's
  `complete` flag must agree with that derivation, else `IllegalTransition::FillMismatch`.
  A fill with `filled_qty + last_qty > quantity + 1e-9` is `IllegalTransition::Overfill`.
- `cancel_requested` records "I asked for a cancel and am waiting for the answer". It is cleared
  on every terminal transition.
- `IllegalTransition` is a typed error with a fixed `Display` prose used in unit-test assertions.
  `apply` leaves the state untouched on `Err`.

**Transition table.** Rows are `(status, cancel_requested)`; `+cr` means `cancel_requested =
true`. `x` = `IllegalTransition::Transition`; `noop` = duplicate, `Ok(false)`, state unchanged.
`Fill` goes to `PartiallyFilled` or `Filled` per the derivation above (written `PF/F`), subject
to the overfill and mismatch checks; `cr` is kept on non-terminal targets and cleared on terminal
ones.

| From \ event | Submitted | Accepted | Rejected | Fill | CancelRequested | Cancelled | Expired |
|---|---|---|---|---|---|---|---|
| Initialized | Submitted | x | Rejected (pre-gate only) | x | x | x | x |
| Submitted | x | Accepted | Rejected | PF/F | Submitted+cr | Cancelled (unsolicited) | Expired |
| Submitted+cr | x | Accepted+cr | Rejected | PF+cr/F | noop | Cancelled | Expired |
| Accepted | x | noop | Rejected (venue-initiated) | PF/F | Accepted+cr | Cancelled (unsolicited) | Expired |
| Accepted+cr | x | noop | Rejected (venue-initiated) | PF+cr/F | noop | Cancelled | Expired |
| PartiallyFilled | x | noop (late ack) | Rejected (venue-initiated, remainder) | PF/F | PF+cr | Cancelled (unsolicited, remainder) | Expired (remainder) |
| PartiallyFilled+cr | x | noop (late ack) | Rejected (remainder) | PF+cr/F | noop | Cancelled (remainder) | Expired (remainder) |
| Filled | x | x | x | x (repeat fill = Overfill) | x | x | x |
| Cancelled | x | x | x | x | x | **noop** | x |
| Rejected | x | x | **noop** | x | x | x | x |
| Expired | x | x | x | x | x | x | **noop** |

- **Terminal finality.** `Filled`, `Cancelled`, `Rejected`, `Expired` are final: every event is
  `IllegalTransition` except an exact duplicate of the event that made the order terminal, which
  is a no-op. `Filled` has no duplicate no-op: without a trade id a repeated completing fill is
  indistinguishable from an overfill (decision 6, E2-S11).
- **`Initialized -> Rejected`** is produced only by the submitter's pre-gate (risk, trading halted,
  no execution attached; decision 5). A venue rejection comes after `Submitted`.
- **`Accepted`/`PartiallyFilled -> Rejected`** is venue-initiated only (an exchange or broker RMS
  rejecting a resting order, or a sim rejecting a partial's remainder, as in
  `partial_fill_rejects_the_remainder`). The client never produces it.
- **`Submitted` is legal for fills, cancels and expiry** because L1 gateways never acknowledge
  (decision 8 profiles); `Submitted -> Accepted` is the live path.
- **Second `Accepted`** is a no-op (brokers re-send acks); a *late* ack after a partial fill is a
  no-op that may still carry the first-seen venue id (decision 3).
- **`CancelRequested`** is illegal from `Initialized` (nothing was sent) and from any terminal
  state; the submitter never emits it there (decision 6), so seeing it is a bug, not a race.
- **Unsolicited cancels.** `Cancelled` without a prior `CancelRequested` is legal from every working
  state: exchange-initiated cancellation and **the unfilled remainder of an IOC/FOK order, which is
  `Cancelled`, not `Expired`** (that is what Indian venues report, Appendix A). `Expired` is reserved
  for time-in-force expiry (`Day`/`GTD` at session end).

`Order` gains an additive record field so a reader sees a pending cancel without following the
stream:

```rust
// honba_messages::Order
#[serde(default, skip_serializing_if = "is_false")]
pub cancel_requested: bool,
```

- **Invariant** (in `Order::validate`, therefore in `OrderRepr`'s `TryFrom`): `cancel_requested`
  may be `true` only when `status` is `Submitted`, `Accepted` or `PartiallyFilled` (a working
  order; `Submitted` included because L1 never acks); any other status with `true` is an
  `InvariantError`. Accessors: `cancel_requested()` and `with_cancel_requested(bool)` beside
  `status()`/`with_status()` (`order.rs:252`, `:298`). Golden `schema/golden/order.json` gains
  `limit_buy_accepted_cancel_requested` and an invalid case `filled_with_cancel_requested`.
- For `Order` as a record this field is additive (ADR 0012: records ignore unknown fields). It lands
  in the same `SCHEMA_VERSION` 4 bump as decision 2, so no separate migration.
- **Who flips `Initialized -> Submitted` and builds `Event::Order`:** the submitter — `Engine::submit`
  on the kernel path, `StrategyRunner` on the port path — after `execution.submit(..)` returns
  `Ok`. It applies `OrderEvent::Submitted { quantity }`, builds
  `Event::Order(order.with_status(Submitted))` and enqueues `ExecutionEvent::Submitted` ahead of
  anything the gateway can drain. Gateways never emit `Submitted`.

Rejected: adding `CancelRequested` as a ninth `OrderStatus` value so that `status` itself says
"cancel pending". That would change the wire enum for every consumer (including the sibling
`honba-adapters` package, every frontend consumer via the generated TypeScript, and the Python
`OrderStatus` schema) and duplicate the idea of "accepted with a pending cancel", whose willing
consumer today already has a status — `Accepted` / `PartiallyFilled`.

### 2. Eight FSM events, realised as wire variants, and a `SCHEMA_VERSION` bump

The conceptual event set — **submitted, accepted, rejected, partially-filled, filled,
cancel-requested, cancelled, expired** (ROADMAP §4's D5 list) — maps to:

| Concept | Wire `Event` variant (tag) | Notes |
|---|---|---|
| submitted | `Order(Order)` (`"order"`) | already exists; the `Order` inside carries `status = Submitted` |
| accepted | `OrderAccepted` (`"order_accepted"`) | exists; gains `venue_order_id` (decision 3) |
| rejected | `OrderRejected` (`"order_rejected"`) | exists |
| partially-filled | `OrderPartiallyFilled` (`"order_partially_filled"`) | new |
| filled | `OrderFilled` (`"order_filled"`) | **meaning narrows** to "the completing fill" |
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
  earlier fill emits `order_partially_filled`. Both carry `last_qty`/`last_px`; the partial also
  carries `cum_qty`. `OrderFilled` keeps its wire shape (no `cum_qty`; by definition
  `cum_qty == quantity`).
- `cancel_requested` has no fill or price — it is the FSM's record that a cancel was asked for.
- `expired` is defined now; producers are the `Scripted` sim (for tests, decision 7) and E3-S1b.
- **Wire decision: `SCHEMA_VERSION` 3 -> 4.** The new variants alone would be additive (ADR 0012
  rule 1, with rule 3's caveat that enum-variant addition breaks exhaustive decoders). But
  `order_filled` *changes meaning*: a v3 reader that sums `order_filled.last_qty` would silently
  miss every partial. ADR 0012 ("a field whose *meaning* changes is a reshape that bumps it",
  the record/input section under rule 1) therefore applies, and the version is bumped so a v3
  reader rejects a v4 stream (`event.rs:155`) instead of misreading it. CHANGELOG
  (`[Unreleased]`, per ADR 0012 rule 5): "`schema_version` 4: `order_filled` now means the
  completing fill; earlier fills are the new `order_partially_filled` (additive);
  `order_cancel_requested`, `order_expired`, `OrderAccepted.venue_order_id` and
  `Order.cancel_requested` are additive." Every golden vector carrying `"schema_version": 3`
  (`schema/golden/*.json`) moves to 4 in the same commit.
- `Event` is `#[non_exhaustive]` and serialized `tag = "type"`; `make codegen` regenerates the
  domain schema, OpenAPI, `.pyi`, MCP and TypeScript. The Python wire mirrors
  (`python/src/honba/wire/wire.py`, `python/src/honba/wire/generated`) are strict unions and move in
  the same commit (ADR 0014).

Rejected: folding the whole lifecycle into an optional `cum_qty` on `OrderFilled` and deriving the
states — that would have kept the variant count flat, but it would have made an `order_rejected`
after a partial fill indistinguishable in the stream from a fill with a cum field of zero, and it
would not give cancel-requested any place at all. Rejected: keeping `order_filled` = "any fill"
and adding `order_completed` — no version bump, but it doubles the events per completing fill and
leaves the old name meaning less than a reader assumes.

### 3. Client order ids, and the missing broker order id

- `OrderId` **is** the client order id: minted by the strategy/runner, it is the FSM key, the
  REST/Journals key (`GET /orders/{id}`, `DELETE /orders/{id}`, `GET /journals/{id}`), and the value
  adapters must forward as the broker's idempotency key
  (`ExecutionAdapter.place_order(..., client_order_id) -> OrderReport`; `fingerprint_dedup` in
  E2-S11 deduplicates on this). No rename, no second client id.
- What *is* missing is the **venue order id**: the exchange/broker id that E2-S7 reconciliation
  needs to join venue reports to client orders. So `honba-messages` gains
  `pub struct VenueOrderId(String);` beside `OrderId`.
- **It rides on every post-submit venue event** in the port vocabulary: `ExecutionEvent::{Accepted,
  Rejected, Fill, Cancelled, Expired}` carry `venue_order_id: Option<VenueOrderId>` (`None` for
  sims and for pre-gate refusals), because a live ack can be lost while a later fill or cancel
  still names the venue order. The engine records the first non-`None` value per order; a later
  different value is logged as `IllegalTransition`-class drift (decision 6), not applied.
- On the **wire** it appears once, on the ack:

```rust
OrderAccepted { order_id: OrderId, venue_order_id: Option<VenueOrderId>, ts_event: UnixNanos },
```

  with `#[serde(default, skip_serializing_if = "Option::is_none")]`. `Order` does **not** gain
  `venue_order_id` — a client order has no broker id until it is accepted.
- Python mirror: `OrderReport` (`python/src/honba/adapters/models.py:283`) gains
  `venue_order_id: str | None = None`; its `__post_init__` invariants are unchanged.

Rejected: introducing a separate `ClientOrderId` type beside `OrderId` — two client ids and three
total ids is the drift this ADR would have to manage.

### 4. One ordered event queue replaces two drains

This is the breaking core of E2-S6(b). Two independent queues cannot define the order between a fill
and a later rejection without inventing a third queue to keep them in step. One queue can.

**Port contract.** A single pull queue, drained in order. Because `Trade` lives in
`honba-entities` (L1) and `honba-messages` (L0) may not name it, the port event lives in
`honba-entities`; it carries what the runner needs to release and book without a side map:

```rust
// honba-entities (L1); seen by honba-ports (L2), honba-engine (L3), honba-strategy (L4), honba-py (L7)
pub enum ExecutionEvent {
    Submitted { order_id: OrderId, instrument_id: InstrumentId, side: OrderSide,
                quantity: f64, ts: UnixNanos },                        // submitter-synthesised
    Accepted  { order_id: OrderId, instrument_id: InstrumentId, side: OrderSide,
                quantity: f64, venue_order_id: Option<VenueOrderId>, ts: UnixNanos },
    Rejected  { order_id: OrderId, instrument_id: InstrumentId, side: OrderSide,
                quantity: f64, reason: String, venue_order_id: Option<VenueOrderId>, ts: UnixNanos },
    Fill      { trade: Trade, cum_qty: f64, complete: bool, venue_order_id: Option<VenueOrderId> },
    CancelRequested { order_id: OrderId, ts: UnixNanos },              // submitter-synthesised
    Cancelled { order_id: OrderId, instrument_id: InstrumentId, side: OrderSide,
                quantity: f64, venue_order_id: Option<VenueOrderId>, ts: UnixNanos },
    Expired   { order_id: OrderId, instrument_id: InstrumentId, side: OrderSide,
                quantity: f64, venue_order_id: Option<VenueOrderId>, ts: UnixNanos },
}
// `quantity` on Accepted is the open quantity; on Rejected/Cancelled/Expired it is the
// unfilled remainder that is released (as `OrderRejection.intent.quantity` is today).
impl ExecutionEvent { pub fn order_id(&self) -> &OrderId; pub fn order_event(&self) -> OrderEvent; }

pub trait ExecutionEngine: Send {
    fn submit(&mut self, order: Order) -> Result<()>;
    fn cancel(&mut self, order_id: &str, now: UnixNanos) -> Result<()>;
    fn drain_events(&mut self) -> Result<Vec<ExecutionEvent>>;
}
```

`order_event()` maps `Fill` to `OrderEvent::Fill { last_qty: trade.quantity(), complete }` and
`Submitted` to `OrderEvent::Submitted { quantity }`. `Rejected.reason` keeps today's free string
(venue text, or an `ErrorCode` wire spelling for pre-gate refusals); `OrderRejection::CANCELLED`
and `is_cancelled` disappear because `Cancelled` is its own variant.

**Removed surface:** `ExecutionEngine::{drain_fills, drain_rejections}`, `OrderRejection` (engine and
Python), `Engine::drain_fills` and its `pending_fills`/`pull_fills` buffer (`engine.rs:220`-`:232`),
replaced by `Engine::drain_events()` with the same "already observed first, then since" semantics.
Every in-repo engine (BarFill, Paper, NextOpen, Scripted in `honba-sim`; PricedBarFill,
`NextOpenSimulator` in `honba-py`; `TrialSink` in `honba-sweep`; testing fixtures) and every call
site counted in Context migrates in one series. Removing trait methods is a hard Rust break; every
`impl ExecutionEngine` stops compiling, which is the migration guide. Pre-1.0, noted once in the
CHANGELOG per ADR 0012 rule 5.

**Python is the reference.** `honba.strategies.execution` defines the reference types first and
Rust matches them through the conformance vectors:

```python
# python/src/honba/strategies/execution.py
@dataclass(frozen=True, slots=True)
class Submitted:       order_id: str; intent: OrderIntent; ts: int
class Accepted:        order_id: str; intent: OrderIntent; ts: int; venue_order_id: str | None = None
class Rejected:        order_id: str; intent: OrderIntent; reason: str; ts: int; venue_order_id: str | None = None
class Fill:            trade: Trade; cum_qty: float; complete: bool; venue_order_id: str | None = None
class CancelRequested: order_id: str; ts: int
class Cancelled:       order_id: str; intent: OrderIntent; ts: int; venue_order_id: str | None = None
class Expired:         order_id: str; intent: OrderIntent; ts: int; venue_order_id: str | None = None
ExecutionEvent = Submitted | Accepted | Rejected | Fill | CancelRequested | Cancelled | Expired
```

(all frozen, slotted; `intent` carries instrument, side and the quantity defined above, as
`OrderRejection.intent` does today). `honba.entities.order_state.OrderState` / `OrderEvent` /
`IllegalTransition` are the pure-Python reference FSM. The port protocol becomes
`submit(order_id, intent, ts)`, `cancel(order_id, now)`, `drain_events() -> list[ExecutionEvent]`
(same name as Rust).

**Shims (ADR 0008 decision 7 policy: present in 0.1.x and 0.2.x, removed in 0.3.0; the package is
at 0.1.0, `python/pyproject.toml:12`).**

- *Legacy port, new runner.* A port that implements only `drain_fills`/`drain_rejections` is wrapped
  by `LegacyPortEvents`: each `drain_events()` returns `[Fill..]` for `drain_fills()` (with `cum_qty`
  and `complete` derived from the wrapper's own `OrderState` per order, so a legacy port's partials
  become correct `order_partially_filled`) followed by `[Rejected | Cancelled ..]` for
  `drain_rejections()` (by `reason == "cancelled"`). That fills-then-rejections order is the
  documented *legacy* order. Class creation of such a port emits one `DeprecationWarning`.
- *New port, legacy caller* (Python `drain_fills`/`drain_rejections` callers and the `honba._honba`
  bindings' `NextOpenSimulator.drain_fills/.drain_rejections`): a buffered shim. Each call to either
  legacy method first calls `drain_events()` once and splits the result into a fill buffer (`Fill`
  -> `Trade`) and a rejection buffer (`Rejected`/`Cancelled`/`Expired` -> `OrderRejection`, an
  `Expired` mapped to `reason = "expired"`); `Submitted`/`Accepted`/`CancelRequested` are dropped
  from the legacy view. It then returns and clears only its own buffer. Neither event is lost;
  only the cross-queue order is (Known limits).
- *Legacy `cancel(order_id)`.* The runner inspects `inspect.signature(port.cancel)` once at bind
  time: two positional parameters -> `cancel(order_id, now)`; one -> `cancel(order_id)` plus a
  `DeprecationWarning`. `cancel_order(port, order_id)` / `drain_port_rejections(port)` stay as
  deprecated helpers over the same logic.

Rejected: keeping two drains and adding a sequence number across them — a consumer that forgets to
merge has the same bug. Rejected: a push `Sink<Message>`/`Handler` instead of a pull — `Engine`
would have to depend on `honba_ports::Sink` (L2), and the Python port path has no `Engine`.

### 5. Wire events vs port events, the engine's order store, and ADR 0018

Wire events and port events are two faces of one channel. The engine translates once, in
`Engine::acknowledge_events` (the present `acknowledge_fills`, `engine.rs:234`): each
`ExecutionEvent` is applied to the order's `OrderState`, then produces exactly one `Event` pushed on
the engine queue for `Handler::on_event`, the journal and the REST read API. A fixture can assert
that `Fill { trade { qty=1, px=10 }, complete=false }` and
`order_partially_filled { last_qty=1.0, last_px=10.0, cum_qty=1.0 }` carry the same `ts_event`.

- **Order store.** `Engine` keeps `orders: HashMap<OrderId, TrackedOrder>` where `TrackedOrder =
  { state: OrderState, instrument_id, side, venue_order_id: Option<VenueOrderId> }`, inserted by
  `submit`, never removed during a run (terminal entries answer "what happened to `O-7`").
- **Position map.** `acknowledge_events` carries ADR 0018's minimal position map: it is updated
  from `Fill` events (the only events that move a position), seeded as 0018 decision 7 says.
- **Working exposure.** `Engine::working_exposure(&InstrumentId, OrderSide) -> f64` is the signed
  sum (`+` buy, `-` sell) of `quantity - filled_qty` over that instrument's working orders on that
  side. ADR 0018's reduce-only rule must use `position + working_exposure(instrument, side)` as
  `RiskRequest.position`, so two working reducing orders cannot together cross zero (long 100,
  working sell 60, new sell 50: effective 40, refused). The 0018 text is amended separately.
- **Pre-gate refusals (supersedes the `OrderRejection` wording of ADR 0018 decision 6).** Every
  refusal inside `Engine::submit` before `execution.submit` — risk refusal, trading halted, no
  execution attached — applies `Initialized -> Rejected` and enqueues
  `ExecutionEvent::Rejected { reason, venue_order_id: None, .. }` on the **same** queue the
  gateway's events go through, so the strategy sees an `order_rejected` and the runner releases
  the order exactly as for a venue reject. `reason` is the `ErrorCode` wire spelling
  (`risk_trading_halted`, `risk_*`, and for "no execution attached" a code chosen with ADR 0018's
  five new codes — 0018 owns it). The free strings `"trading halted"` / `"no execution attached"`
  (`engine.rs:264`, `:271`) go away. The audit keeps 0018's two records (`RiskRefused` then
  `OrderRejected { reason }`). The Python runner (port path, no `Engine`) does the same with its
  own stage.
- **Audit vocabulary.** `AuditKind::OrderCancelled` (which records a *routed* cancel) is renamed
  `AuditKind::CancelRequested`; a venue cancellation is recorded by the new
  `AuditKind::OrderLifecycle { order_id, event: OrderEventKind, ts_event }`, written for every
  translated non-fill event (fills keep `FillProduced`); `AuditKind::IllegalTransition { order_id,
  error }` records every rejected transition. `AuditKind` has no serde derive, so this is
  Rust-internal.

The async `honba_ports::ExecutionGateway` (D10) should adopt the same `ExecutionEvent` when its bridge
is decided; until then it stays as it is — this ADR is not D10.

### 6. Cancel times, idempotence and ordering (resolves D2)

Starting from ADR 0008 decision 13's two addenda — a cancel is stamped with `now` at the time the
cancel is *processed*, and `ExecutionEngine::cancel` has been `cancel(order_id, now)` since commit
`1c50fc7`:

| Fidelity | What `cancel` does | `order_cancel_requested.ts` | `order_cancelled.ts` |
|---|---|---|---|
| **L1** (BarFill, paper, `NextOpenSimulator`, chunk 1 vectors) | processed at `now` | `now` at `cancel()` | **same `now`** — there is no venue delay |
| **L2** (tick-matched sim, E3-S1b, `FillModel` + depth) | ack before fill at `now` | `now` | `now` in v1; once `LatencyModel` (E3-S2) exists, the delay the model gives at that book position |
| **Live** | `cancel` at `now` over REST/WS; `order_cancelled` at the **venue ack** | engine clock at the request (`now`, `ctx.now()`) | **the venue's exchange timestamp**, normalised to `UnixNanos` at the adapter boundary (`parsing.py`/`shared`) |

- Backtests never read the wall clock: `ts_event` comes from the simulated `ts_init`
  (`crates/honba-async/src/clocks.rs:91`, `HistoricClock`). The live adapter converts venue
  timestamps at the boundary, so the FSM never sees wall-clock time.
- **Ordering.** Queue order, not timestamp, is the tiebreak: two events at the same `ts` are applied
  in the order the gateway enqueued them, and the submitter's synthesised `Submitted` /
  `CancelRequested` are enqueued at the moment of the call. Timestamps are data, never sort keys.
- **Cancel is emitted once.** `cancel` stays idempotent (ADR 0010 decision 4): on an unknown,
  `Initialized` or terminal order it is a no-op and emits nothing; on a working order it emits
  `CancelRequested` once; later cancels while `cancel_requested` is set are no-ops (no event, no
  gateway call). A fill that beats a cancel wins: the gateway emits the fill and no `Cancelled`.
- **Duplicates and late events.** A duplicate terminal event is a no-op (table, decision 1). Any
  other event the table marks `x` is an `IllegalTransition`: the engine/runner records it in the
  audit, does not change the state and does not emit a wire event, and **never panics**. In live
  that is log-and-reconcile (flag for E2-S7). Exception: an illegal `Fill` still books its `Trade`
  into positions and cash (money moved at the venue) before being flagged, because dropping it
  would make the ledger lie. In sims and tests an `IllegalTransition` is a bug: the conformance
  harness fails on any `IllegalTransition` audit row.
- **Duplicate fills** cannot be detected here: `Trade` has no `TradeId` field (Context). Fill
  dedup by `(venue_order_id, trade_id)` is deferred to E2-S11; until then a re-delivered fill is
  either an `Overfill` (caught) or a silent double partial (not caught, Known limits).

### 7. Migration plan — four stories, in order, with their tests

| # | Scope | Gate | Key files | Unit tests (`src/tests/`, `tests/unit/`) | Integration tests (`<crate>/tests/`, `tests/integration/`) |
|---|---|---|---|---|---|
| **(a)** | `OrderEvent`, `OrderState`, `IllegalTransition`, `VenueOrderId`, the three new `Event` variants, `OrderAccepted.venue_order_id`, `Order.cancel_requested` (+ invariant) in `honba-messages`; `ExecutionEvent` + `order_event()` in `honba-entities`; Python reference `OrderState`/`ExecutionEvent`; `SCHEMA_VERSION` 4 | this ADR | `crates/honba-messages/src/{identifiers.rs, events/event.rs, orders/order.rs, orders/state.rs}`, `crates/honba-entities/src/execution_event.rs`, `python/src/honba/entities/order_state.py`, codegen + `make codegen` | `transition_table_exhaustive` (every `(status, cr) x kind` cell against the table, Rust and Python from one table literal), `terminal_is_final`, `duplicate_terminal_is_noop`, `overfill_rejected`, `fill_mismatch_rejected`, `partial_fill_sequence`, `expired_transitions` (from every working state; illegal from `Initialized`/terminal), `cancel_requested_cleared_on_terminal`, `order_cancel_requested_invariant`; `ALL.len()==8` unchanged | golden `event.json` cases `order_partially_filled`, `order_cancel_requested`, `order_expired`, `order_accepted_with_venue_id`, `order_accepted_without_venue_id`; golden `order.json` cases of decision 1; `order_state_replay` (replays each golden `expect_events` sequence through `OrderState` in both languages and compares `expect_state`) |
| **(b)** | `ExecutionEngine::drain_events`, `Engine::{acknowledge_events, drain_events, working_exposure}`, pre-gate `Rejected`, order store; every engine in `honba-sim`/`honba-py`, `TrialSink`, `StrategyRunner` (Rust and Python), shims | (a) | `crates/honba-engine/src/{execution.rs, engine.rs, audit.rs}`, `crates/honba-sim/src/{bar_fill.rs, paper.rs, next_open.rs, scripted.rs}`, `crates/honba-py/src/pyclasses/{next_open.rs, run.rs}`, `crates/honba-strategy/src/runner.rs`, `python/src/honba/strategies/{execution.py, runner.py}` | `acknowledge_translates_one_to_one`, `pre_gate_refusal_is_rejected_event`, `working_exposure_sums_open_remainders`, `cancel_emitted_once`, `illegal_transition_audited_not_panicked`, shim splitting (`buffered_shim_loses_no_event`), `legacy_cancel_signature_adapted` | `StrategyRunner` + `Scripted` scenarios through the real flow: submit -> partial -> fill, venue reject after partial, **cancel race** (fill and cancel at the same `ts`, both enqueue orders), scripted expiry (`Scripted` gains an `Expire` action); the eight `order_rejections.json` vectors stay green; `execution_contract.rs` invariant `filled + released == ordered` holds |
| **(c)** | Adapter contract — event form beside the query form, snapshot-diff reducer (Appendix A) — `python/src/honba/adapters/{base,models,contract,testing}`; sibling `honba-adapters` (ask first, `docs/ROADMAP.md:362`) | (b) | `python/src/honba/adapters/*`, `honba-adapters/shared/*` | `snapshot_reducer_*` (monotone fills, terminal last, decreasing fill flagged) | `order_state.json` on `FakeAdapter` under the `ack` profile |
| **(d)** | Every gateway runs `order_state.json` through the same harness | (b) | `crates/honba-sim/tests/order_state_conformance.rs`, `crates/honba-py/tests/*`, `python/tests/integration/test_order_state_conformance.py` | — | each gateway, under its declared profile, produces the scenario's normalised `expect_events` and `expect_state` (decision 8) |

Rejected threading: building the engines `(b)` before the vocabulary `(a)` — then no type can name
`VenueOrderId` or `ExecutionEvent`; building the adapter surface `(c)` before the engine contract
`(b)` — then adapters implement folklore until the tag spellings and the cancel-time rule are pinned.

### 8. Conformance scenario set — one set, every gateway, per-gateway profiles

`schema/conformance/order_state.json` (new), run by both languages and by every gateway:

```json
{
  "fixture_version": 1,
  "type": "OrderState",
  "description": "...",
  "profiles": {
    "l1":  { "requires_ack": false, "emits_expiry": false },
    "ack": { "requires_ack": true,  "emits_expiry": true }
  },
  "scenarios": [
    {
      "name": "partial_fill_sequence",
      "profile": ["l1", "ack"],
      "steps": [
        { "op": "submit", "order_id": "S-1", "side": "buy", "symbol": "X", "quantity": 3.0,
          "order_type": "limit", "price": 10.0, "tif": "day", "ts": 1 },
        { "op": "venue", "event": "fill", "order_id": "S-1", "last_qty": 1.0, "last_px": 10.0, "ts": 2 },
        { "op": "cancel", "order_id": "S-1", "ts": 3 },
        { "op": "venue", "event": "cancelled", "order_id": "S-1", "ts": 3 }
      ],
      "expect_events": [
        { "type": "order", "order_id": "S-1", "status": "submitted" },
        { "type": "order_partially_filled", "order_id": "S-1", "last_qty": 1.0, "cum_qty": 1.0 },
        { "type": "order_cancel_requested", "order_id": "S-1" },
        { "type": "order_cancelled", "order_id": "S-1" }
      ],
      "expect_state": { "S-1": { "status": "cancelled", "filled_qty": 1.0, "cancel_requested": false } }
    }
  ]
}
```

- `op` is one of `submit`, `cancel`, `bar` (drives price-driven sims: `{ts, open, high, low,
  close}`), `venue` (a scripted venue event for `Scripted` and `FakeAdapter`: `accepted`,
  `rejected`, `fill`, `cancelled`, `expired`, with their fields). A price-driven gateway runs only
  scenarios it can realise from `bar` steps; the others are `not_applicable` for it, by name.
- **Normalisation, not byte identity.** Gateways are compared on a normalised stream: per event
  only `type`, `order_id` and the quantity fields (`last_qty`, `cum_qty`, the released quantity);
  `ts_event`, `last_px`, `venue_order_id` and ids beyond `order_id` are compared only where the
  scenario lists them. Under a profile with `requires_ack: false`, `order_accepted` is removed from
  both sides before comparison; under `requires_ack: true` an `order_accepted` must precede the
  first fill. `emits_expiry: false` makes `tif_expiry` `not_applicable`.
- Scenarios: `market_fill`, `limit_fill`, `partial_fill_sequence`, `reject_at_submit` (venue
  reject; risk refusals are ADR 0018's set), `reject_remainder_after_partial`, `cancel_race`
  (fill and cancel at the same `ts`: exactly one terminal event, the one queue order makes
  first), `cancel_twice_emits_once`, `ioc_remainder_cancelled`, `tif_expiry` (`Scripted` and the
  `ack` profile now; L1 price-driven sims when E3-S1b lands), `duplicate_terminal_is_noop`. The
  eight `order_rejections.json` scenarios stay as they are and stay green.

### 9. What breaks in `honba-adapters` and in the sibling `honba` packages

| Surface | Today | After this ADR |
|---|---|---|
| `honba-engine::ExecutionEngine` (Rust trait) | `drain_fills`/`drain_rejections`/`cancel(id, now)` | `drain_events()`/`cancel(id, now)` |
| `honba-engine::Engine` | `drain_fills`, `pending_fills`, `acknowledge_fills`, free-string pre-gate audit | `drain_events`, `acknowledge_events`, order store, `working_exposure`, pre-gate `Rejected` events |
| every engine in `honba-sim` and `honba-py`, `honba-sweep::TrialSink` | `impl ExecutionEngine` with two drains | one drain |
| `honba._honba` bindings + `.pyi` | `drain_fills`/`drain_rejections` | `drain_events`; the old pair is the buffered shim until 0.3.0 |
| `python/src/honba/strategies/execution.py` | `ExecutionPort.drain_fills`; `RejectingExecutionPort.drain_rejections`; `cancel(order_id)`; `OrderRejection`; shims | `drain_events() -> list[ExecutionEvent]`, `cancel(order_id, now)`; old pair and one-arg `cancel` adapted by shims until 0.3.0 |
| `python/src/honba/strategies/runner.py` | drains fills, then rejections (`runner.py:151`) | drains `drain_events()`, keeps `OrderState` per order, releases from one stream |
| `honba.backtest.simulated.NextOpenExecution` | two drains, `cancel(id)` | one drain, `cancel(id, now)` |
| `python/src/honba/adapters/testing.py` / `FakeAdapter` | `status = FILLED` set directly (`testing.py:302`) | derives `status` from `OrderState` |
| `honba-wire` Python mirror + generated `.pyi` | union `EventOrder\|Accepted\|Rejected\|Filled\|Cancelled` (`wire.py:402`) | adds the three variants, `venue_order_id`, `cancel_requested`; `SCHEMA_VERSION` 4 |
| `schema/golden/*.json` | `schema_version: 3` | 4, plus the cases in decision 7(a) |
| **Sibling `honba-adapters`** (ask before touching) | query form (ADR 0010) | event form beside it, `OrderReport.venue_order_id`, `client_order_id == OrderId` |
| **Sibling `honba-docs`, `honba-strategies`, `honba-examples`** (ask before touching) | `order_reject_cancel.md` documents the two drains; strategies/examples run through the runner | doc rewrite; strategies/examples re-run against v4 journals; flagged, not edited here |

## Consequences

- One transition table, two drain bugs removed, every path ordered. The runner, the engine and the
  adapter translate once, so a cancel race can be pinned with one vector every gateway replays.
- `StrategyRunner` and `Engine` can answer "what is the state of order `O-7`?" from `OrderState`.
- ADR 0018 gets one refusal channel (an `order_rejected` event on the same queue) and a
  reduce-only rule that accounts for working orders.
- Wire: `SCHEMA_VERSION` 4, three new `Event` variants, two additive fields; `make codegen` plus
  `make schema-ts` once; CHANGELOG migration note. Golden vectors, `schema/domain`/`openapi`/
  `mcp_tools.json` and `python/src/honba/wire` mirrors move in the same commit (ADR 0014).
- `GET /orders` plus the journal expose `Order.cancel_requested`.

## Known limits

- TIF expiry is produced only by `Scripted` (tests) and the `ack` profile until E3-S1b; price-driven
  L1 sims mark `tif_expiry` `not_applicable`.
- Amend/replace is not in the vocabulary: `modify_order` exists on the adapter contract (ADR 0010
  decision 4) but the FSM has no modify events (names reserved in Appendix A). Change an order by
  `cancel` + `submit`.
- `Order` carries no `venue_order_id`; the venue id is on `order_accepted`, `ExecutionEvent` and
  `OrderReport`.
- The legacy shims cannot preserve the one-queue order by definition.
- Duplicate fill detection waits for a trade id on `Trade` (E2-S11); reconciliation of
  `IllegalTransition` rows is E2-S7.
- v3 journals are rejected by a v4 reader (exact-version check, `event.rs:155`). No upcaster: a v3
  `order_filled` cannot be split into partial/complete without the order quantity. Research
  journals are regenerated; a v3->v4 reader is a separate change if one is ever needed.
- L1 engines never emit `order_accepted`; a live gateway cannot be certified by replaying only the
  `l1` profile.
- The `/orders` write routes remain 501 until ADR 0018's risk stage and the E5-S4 approval queue
  cover them.

## Appendix A: broker status mapping and the snapshot reducer (design intent)

**Not verified against live APIs.** Status strings below are from the brokers' public docs as
recalled at writing time; story (c) verifies them with recorded fixtures before any adapter relies
on them.

| Dhan `orderStatus` | Zerodha `status` | Events (in order) |
|---|---|---|
| `TRANSIT` | `PUT ORDER REQ RECEIVED`, `VALIDATION PENDING`, `OPEN PENDING`, `AMO REQ RECEIVED` | none (still `Submitted`) |
| `PENDING` | `OPEN` (`filled_quantity == 0`) | `Accepted` |
| `PART_TRADED` | `OPEN` (`filled_quantity > 0`) | `Accepted` if not yet seen, then `Fill { complete: false }` for the delta |
| `TRADED` | `COMPLETE` | `Fill { complete: true }` for the delta |
| `REJECTED` | `REJECTED` | delta fill if any, then `Rejected` (reason = broker text) |
| `CANCELLED` | `CANCELLED` (incl. IOC remainder) | delta fill if any, then `Cancelled` |
| `EXPIRED` | — | delta fill if any, then `Expired` |
| `TRIGGERED` | `TRIGGER PENDING` | `Accepted`; reserved `trigger_pending`/`triggered` |
| — | `MODIFY VALIDATION PENDING`, `MODIFY PENDING`, `MODIFIED` | reserved `modify_requested`/`modified`/`modify_rejected` |
| — | `CANCEL PENDING` | none (`CancelRequested` was emitted by the client) |

**Snapshot-diff reducer** (`OrderReport` polling, query form): given the previous report for an
order (or none) and the new one, emit in this order: `Accepted` if the order moved from a pre-ack
status to a working one or a venue id is first seen; one `Fill` for `delta = new.filled_quantity -
prev.filled_quantity` when `delta > 1e-9`, priced at
`(new.average_price * new.filled_quantity - prev.average_price * prev.filled_quantity) / delta`,
`complete` iff the new status is `Filled`; the terminal event last. A decreasing
`filled_quantity` or a status moving backwards emits nothing and is flagged for reconciliation
(E2-S7). A poll that collapses several venue fills into one `Fill` is a known loss of
granularity, not an error.

**Reserved vocabulary** (names only, no transitions defined): `modify_requested`, `modified`,
`modify_rejected`, `cancel_rejected`, `trigger_pending`, `triggered`. Adding any of them is a
future ADR and an additive `Event` variant.

## Amendments

Accepted 2026-10-07 with these changes from the proposed text, resolving the critical review:

1. Full transition table with illegal cells, duplicate no-ops and terminal finality; IOC remainder
   is `Cancelled`, `Expired` is TIF only; `CancelRequested` legal in `Submitted` (L1 has no ack).
2. L0 `OrderEvent`/`OrderEventKind`/`IllegalTransition` in `honba-messages`; `OrderState.quantity`;
   partial/filled by the 1e-9 rule; `FillMismatch`, `Overfill`; `order_event()` conversion.
3. `ExecutionEvent` variants carry instrument, side and remainder; `venue_order_id` on every
   post-submit venue event.
4. ADR 0018 joint contract: pre-gate refusals as `Rejected` events with `ErrorCode` reasons, position
   map in `acknowledge_events`, order store and `working_exposure`, audit renames, `Engine::drain_fills`
   removal.
5. `SCHEMA_VERSION` 3 -> 4 for the `order_filled` meaning change, with CHANGELOG text.
6. Buffered shim spec, `cancel` signature adaptation, 0.3.0 removal, Python reference
   `ExecutionEvent`; corrected file count and sibling-repo flags.
7. Queue-order tiebreak, cancel-once, duplicate/late event handling, fill dedup deferred to E2-S11.
8. `Order.cancel_requested` invariant, accessors, goldens; submitter flips `Submitted` and builds
   `Event::Order`.
9. Per-phase unit and integration test plan; `order_state.json` format; profiles over normalised
   streams instead of byte identity.
10. Appendix A (Dhan/Zerodha mapping, snapshot reducer, reserved vocabulary), marked design intent.
11. Citation fixes (`engine.rs:234`, `docs/ROADMAP.md:432`, `identifiers.rs:84`/`:120`,
    `event.rs:46`, `execution.rs:17`, `execution.py:49`/`:77`/`:86`).
