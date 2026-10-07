# ADR 0018: The Risk Stage (`honba-risk`)

Date: 2026-10-07. Status: proposed. Roadmap story: decides D4; implements E2-S2, supplies the
enforcement half of E2-S1, and gates E11-S7 and E5-S4.

## Context

Nothing today refuses an order for a risk reason except "the engine is halted".

- There is no `honba-risk` crate. `crates/honba-engine/src/risk/mod.rs` is the single line
  `//! risk`, is not declared in `crates/honba-engine/src/lib.rs:19`, and therefore is not
  compiled; `crates/honba-strategy/src/risk/mod.rs` is the same (`//! risk`, undeclared).
  `docs/ROADMAP.md:423` records the audit correction: "No `honba-risk` crate; `engine/src/risk/mod.rs`
  is 9 bytes; no `RiskCheck` anywhere".
- `TradingState { Active, Reducing, Halted }` lives in `honba-engine`
  (`crates/honba-engine/src/state.rs:9`). `accepts_orders()` returns true for `Active` **and**
  `Reducing` (`state.rs:22`), `can_transition_to` permits every transition by design (`state.rs:37`),
  and `Engine::set_trading_state` is the single transition implementation, audited as
  `AuditKind::StateChanged` (`crates/honba-engine/src/engine.rs:203`). `Engine::submit` refuses an
  order only when the state is `Halted`, auditing the free-text reason `"trading halted"`
  (`engine.rs:260`), or when no execution engine is attached (`"no execution attached"`).
  `Reducing` is therefore not reduce-only anywhere: `reduce_only` appears in no `.rs` or `.py` file
  in the repo.
- The operator path exists: `Command::State(TradingState)` (`crates/honba-async/src/command.rs:34`)
  reaches the same `set_trading_state`. There is no Python or CLI exposure of `TradingState`
  (`docs/ROADMAP.md:439`), which is the other half of E2-S1.
- Market rules already exist and are never called: `MarketProfile::instrument_rules()`
  ("lot sizes, tick sizes, circuit bands", `crates/honba-market/src/profile.rs:29`);
  `InstrumentRules { lot_size, tick_size, min_order_quantity, max_order_quantity }` with
  `validate_quantity` (min, freeze-quantity, lot multiple) and `validate_price` (positive, tick
  aligned) at `crates/honba-market/src/rules.rs:62` and `:91`; `PriceBand { lower, upper }` at
  `rules.rs:10` with `InstrumentRulesProvider::price_band(..)` defaulting to `None` (`rules.rs:120`).
  `validate_quantity`/`validate_price` have **no call sites outside `honba-market`**. The India pack
  supplies `InstrumentRules::new(1.0, 0.05)` for cash equity and does not override `price_band`
  (`crates/honba-market/src/india/profile.rs:17`, `:79`).
- The error taxonomy is already waiting: `ErrorCode::{RiskMaxNotionalExceeded,
  RiskMaxPositionExceeded, RiskMaxDrawdownExceeded}` in `category = "risk"`, documented as not
  retryable (`crates/honba-messages/src/errors.rs:108`, `:147`, `:163`), and surfaced in Python as
  `RiskApiError` (`python/src/honba/client/errors.py:94`). Deserialisation of `ErrorCode` is
  deliberately strict (`errors.rs:74`), so a code an old client has never seen is a decode error.
- `AuditKind` (`crates/honba-engine/src/audit.rs:23`) and `AuditLog` (`audit.rs:77`) records only unstructured
  `OrderRejected { order_id, reason: String }`; `AuditKind` has no serde derive, so it is
  Rust-internal and free to grow.
- Config: `BacktestRunConfig` (`crates/honba-config/src/lib.rs:19`) is `deny_unknown_fields` with
  `#[serde(default)]` on every optional section — an undeclared `[risk]` table is a parse error
  today. `CONFIG_TYPES` in `crates/honba-codegen/src/registry.rs:67` is `["BacktestRunConfig"]`, so
  a new section type reaches the JSON Schema by being registered there (ADR 0014).
- Write routes are already marked: `Access::{ReadOnly, Write}` and `WRITE_PATHS = {POST /orders,
  DELETE /orders/{id}, POST /positions/close}` (`crates/honba-messages/src/endpoints.rs:52`,
  `:193`), documented as "the ones an approval queue and the risk stage must gate". All four
  trading rows answer 501, no auth/approval/scope code exists, and the middleware stack is
  compression + CORS + trace. `POST /backtests` and `POST /sweeps` are deliberately *not* in
  `WRITE_PATHS` (`endpoints.rs:189`), and the generated MCP tools mark `backtest`/`sweep` with
  `readOnlyHint: true` (`schema/mcp/mcp_tools.json`).
- Layering is enforced by an adjacency allow-list (`scripts/dependency_graph.py:30`), an
  unregistered crate is a CI failure (`dependency_graph.py:208`), and `SYNC_KERNEL_CRATES`
  (`:157`) / `CORE_CRATES` (`:143`) are the tokio-free and market-neutral sets.

## Decision

### 1. One new pure crate: `honba-risk`, at L2

`honba-risk` holds the rules, the typed refusals, the `RiskCheck` interface and the `RiskLimits`
config type. It is pure: no I/O, no clock, no tokio, no broker vocabulary, no `india` feature.

Dependencies: `honba-messages`, `honba-entities`, `honba-market`. L2 (with `honba-market`,
`honba-ports`) is the recommendation in D4 and it is the layer that fits: the rules read
`MarketProfile`/`InstrumentRules`/`PriceBand` (L2) and money/positions (L1), and they must be
callable from `honba-engine` (L3), `honba-strategy` (L4) and `honba-api-rest` (L7) alike, so the
crate sits below all of them.

Rejected: putting the rules in `crates/honba-engine/src/risk/` (the existing stub) — the REST write
path and the Python runner path do not run through `Engine`, so they could not reuse the check
without depending on the kernel. Rejected: a crate at L3 beside `honba-engine` — same reuse problem
one layer down, and it would put config types (L5) above the rules they configure.

### 2. `TradingState` moves to `honba-messages`, re-exported from `honba-engine`

The risk stage must name `TradingState` from L2, and L2 may not depend on L3. `TradingState` is
pure data (a three-variant enum plus two total functions), it is the ubiquitous term for what a
strategy, an operator command and a rule all talk about, and it already crosses a channel as
`Command::State`. So it moves to `honba-messages` (L0) and `honba-engine` re-exports it
(`pub use honba_messages::TradingState;`), which keeps `honba_engine::TradingState`,
`honba_async::Command::State` and `EngineOutput::StateChange` source-compatible with no call-site
change.

Rejected: giving `honba-risk` its own `RiskMode { Normal, ReduceOnly, Halted }` and mapping — two
vocabularies for one concept, and a mapping that can drift. Rejected: keeping `TradingState` in
`honba-engine` and passing a plain `bool`/`&str` — same drift, without type safety.

### 3. Interface: `RiskCheck -> approved` plus typed refusals

```rust
// honba-risk
pub struct RiskRequest {              // everything a rule needs, all values in
    pub order_id: OrderId,
    pub instrument_id: InstrumentId,
    pub side: OrderSide,
    pub quantity: f64,
    pub price: Option<f64>,           // limit / stop price; None for market
    pub trigger_price: Option<f64>,
    pub position: f64,                // current signed position in the instrument
    pub trading_state: TradingState,
    pub ts: UnixNanos,                // event time, never the wall clock
}

pub enum RiskDecision { Approved, Refused(RiskRefusal) }

pub trait RiskCheck: Send {
    /// Evaluate every rule in fixed order; the first refusal wins.
    fn check(&mut self, req: &RiskRequest) -> RiskDecision;
}
```

- `&mut self` because the order-rate rule carries a window; the stage is owned by the single-threaded
  kernel (or by one runner), so a plain `&mut` is correct and needs no interior mutability.
- `RiskRefusal` is a typed enum, not a string: `TradingHalted`, `ReduceOnly`,
  `LotSize { quantity, lot, max }`, `PriceBand { price, lower, upper }`,
  `MaxNotional { notional, limit }`, `OrderRate { count, limit }`. Each carries the numbers a
  message or an audit row needs, so no consumer parses prose.
- `RiskRefusal::rule() -> &'static str` (stable rule name) and `RiskRefusal::error_code() ->
  ErrorCode` live in `honba-risk`; `honba-risk` already depends on `honba-messages`, which owns
  `ErrorCode`.

### 4. Rules, in a fixed evaluation order

| # | Rule | Input | Configurable |
|---|---|---|---|
| 1 | `TradingHalted` | `trading_state == Halted` | no |
| 2 | `ReduceOnly` | `trading_state == Reducing`, `position`, `side`, `quantity` | no |
| 3 | `LotSize` | `InstrumentRules::validate_quantity` (min, freeze max, lot multiple) | no (from profile) |
| 4 | `PriceBand` | `price_band(instrument_id)`, checked against `price`/`trigger_price` | no (from profile) |
| 5 | `MaxNotional` | `Money::mul_qty(quantity, price)` vs `max_notional` | yes |
| 6 | `OrderRate` | token bucket over `ts` (event time) | yes |

- The first refusal wins, in that order, and this order is pinned by tests: it puts the rules that
  need no arithmetic first, so a halted or reduce-only engine reports *why* it is closed rather
  than a notional error, and it makes the rate counter — the only rule with memory — advance only
  for orders that passed every shape rule.
- **Reduce-only** (the E2-S1 enforcement): with `trading_state == Reducing`, an order is approved
  only if the resulting position `p + s*q` (`s = +1` buy, `-1` sell) stays within
  `[min(p, 0), max(p, 0)]` — it may not move away from zero and may not cross it. A flat position
  refuses every order. `Active` and `Halted` do not use this rule.
- **Lot size and price band** are market rules, not request validation: tick alignment and positive
  prices stay in the request resolvers (`honba-api`) and the intent validators, while quantity
  shape and the band come from the profile. The band rule checks only orders that carry a price;
  a market order has no price to band-check at submit (Known limits).
- `ErrorCode::RiskMaxPositionExceeded` and `RiskMaxDrawdownExceeded` stay **reserved**: position and
  drawdown are account-level rules that belong with the margin model (E3-S4) and the analytics
  gates (E4-S6), not in this stage.

### 5. Wire codes: three existing, five new

| Refusal | `ErrorCode` | Status |
|---|---|---|
| `MaxNotional` | `risk_max_notional_exceeded` | exists (`errors.rs:108`) |
| `OrderRate` | `risk_order_rate_exceeded` | new |
| `LotSize` | `risk_lot_size_exceeded` | new |
| `PriceBand` | `risk_price_band_exceeded` | new |
| `ReduceOnly` | `risk_reduce_only_violation` | new |
| `TradingHalted` | `risk_trading_halted` | new |

All six map to `ErrorCategory::Risk`, are not retryable, and render as HTTP **422** on the write
routes through the existing `failure(StatusCode::UNPROCESSABLE_ENTITY, ..)` helper
(`crates/honba-api-rest/src/market.rs:96`). Rejected: `403 Forbidden` — that code means
"authenticated but not permitted", i.e. scopes and authz (E5-S3/E11-S8), not an order that breaks a
trading limit; conflating them would make the scope map lie.

Five new variants are additive within `/api/v1` (ADR 0012 rules 1-3): no `schema_version` bump,
`make codegen` in the same commit, and a CHANGELOG note that strict decoders reject an unknown code
until regenerated (Known limits).

### 6. Refusals are audited events

- `AuditKind` gains `RiskRefused { order_id, rule: &'static str }` (`crates/honba-engine/src/audit.rs`).
  The engine records it **and then** the existing `OrderRejected { order_id, reason }` with
  `reason` set to the stable refusal string (the `ErrorCode` wire spelling). Two records for one
  refusal: `RiskRefused` is what E2-S4 replays and what `strategy_engine_risk_fills_refusal_in_audit`
  asserts; `OrderRejected` keeps every existing audit consumer working unchanged. `AuditKind` has
  no serde derive, so this is invisible on the wire.
- Downstream of the audit, a refused order behaves exactly like a refused order does today: the
  strategy sees an `OrderRejection` (`crates/honba-engine/src/execution.rs:16`) whose `reason` is
  the refusal string, released through the existing runner path (ADR 0008 decisions 11/13), and a
  REST caller sees the 422 envelope with `context.limit` / `context.rule` filled from the refusal's
  numbers.
- Refusals are never dropped, never retried silently and never turned into a missing order: the
  order is refused *before* it reaches an `ExecutionEngine`.

### 7. Where the stage sits

- **Backtest and live, Rust kernel:** inside `Engine::submit`
  (`crates/honba-engine/src/engine.rs:260`), before `execution.submit`. The engine owns the stage
  (`Engine::with_risk(..)`, with `RiskLimits::default()` when none is supplied), so every path that
  reaches an `ExecutionEngine` through the kernel passes exactly one gate. The engine also supplies
  `position` from a minimal position map it maintains: seeded by the caller at start-up and updated
  from the fills it already observes in `acknowledge_fills` (`engine.rs:233`). The full instrument
  and position store is still E2-S9's cache; this map is deliberately small.
- **Python / port path:** the Python `StrategyRunner` applies the same stage before
  `ExecutionPort.submit`, with `position` from `LedgerContext` (the object that already owns
  positions and cash) and `trading_state` from a new, exposed `TradingState`. This needs a
  `honba-py` binding of `RiskCheck`/`RiskLimits` plus a `.pyi` entry — the Python half of E2-S1
  (`python_halt_blocks_orders_in_run`). The Rust `StrategyRunner` gets the same call for the
  port-driven path.
- **Exactly one stage instance per run.** In a kernel configuration the engine's stage is the gate
  and the runner holds none; in a port configuration (Python runner driving a port directly, no
  `Engine`) the runner's stage is the gate. This is a construction invariant, stated in both
  crates' docs, because two instances would double-count the rate rule.
- **REST write routes (E11-S7):** `POST /orders`, `DELETE /orders/{id}` and `POST /positions/close`
  are the only `WRITE_PATHS` and stay the single definition of "this endpoint can move money".
  The order route runs: approval queue (E5-S4, when configured) -> risk stage -> execution gateway
  (simulator only until E3-S7). A refusal is the 422 of decision 5, is audited as in decision 6,
  and never reaches the gateway — `post_order_flows_to_sim_only` and
  `approve_flows_through_risk_to_sim` are two assertions about that one line. Cancel and close
  bypass the approval queue (they cannot increase exposure, per E5-S4) and are not shape-checked;
  they stay behind the `write:orders` scope and are audited.
- **Research routes are never gated.** `POST /backtests` and `POST /sweeps` are `Access::ReadOnly`
  and cannot move money, so no risk rule applies to them; the E5-S3 scope map keys off `Access`,
  not off the HTTP method.

### 8. Config schema

```toml
# BacktestRunConfig gains one optional section (#[serde(default)], so every
# existing config keeps parsing under deny_unknown_fields):
[risk]
max_notional = 500000.0     # rupees, per order; absent = unlimited
order_rate_max = 30         # orders per window; absent = unlimited
order_rate_window_secs = 1  # window in event time
```

- The section type is `honba_risk::RiskLimits`, registered in `CONFIG_TYPES`
  (`crates/honba-codegen/src/registry.rs:67`) so ADR 0014 renders it into the domain schema,
  and referenced from `BacktestRunConfig` as `risk: RiskLimits` with `#[serde(default)]`
  (ADR 0012: inputs gain fields only with defaults).
- Money fields are authored as `f64` major units, matching `AccountConfig.starting_cash`
  (`crates/honba-config/src/lib.rs:120`), and converted **once** with
  `Money::mul_qty(quantity, price, currency)` for the comparison, so the rule itself is exact
  (ADR 0011). Rejected: a `Money` value object in TOML (`{ amount = 50000000, currency = "INR" }`)
  — exact, but it invites authors to write minor units in a major-unit file.
- Defaults are unlimited for `max_notional` and `order_rate`; rules 1-4 are not configurable
  because they come from the profile and the trading state. Rejected: mandatory non-null limits —
  they would break every existing config and test, and a guessed default would silently refuse
  legitimate research runs. A future live run config **must** set `max_notional` (there is no live
  config type yet: `configs/live/*.toml` are one-line placeholders), which is recorded as a known
  limit rather than a rule this ADR can enforce today.

### 9. The dependency-graph change

`scripts/dependency_graph.py` needs exactly this:

```python
# L2: honba-market, honba-ports, honba-risk
ALLOWED_PROD = {
    ...
    "honba-risk":     {"honba-messages", "honba-entities", "honba-market"},   # new crate
    "honba-engine":   {..., "honba-risk"},      # Engine::submit gate
    "honba-strategy": {..., "honba-risk"},      # runner gate (port path)
    "honba-config":   {..., "honba-risk"},      # RiskLimits on BacktestRunConfig
    "honba-codegen":  {..., "honba-risk"},      # CONFIG_TYPES registration
    "honba-api-rest": {..., "honba-risk"},      # write-route gate, ErrorCode rendering
    "honba-py":       {..., "honba-risk"},      # RiskCheck binding
    "honba-testing":  {..., "honba-risk"},      # fixtures and property tests
}
```

plus `honba-risk` added to `CORE_CRATES` (it must never enable the `india` feature: bands and lots
arrive through whichever `MarketProfile` is registered at run time) and to `SYNC_KERNEL_CRATES`
(it must stay tokio-free). Every edge points inward; an unregistered crate fails the script
(`dependency_graph.py:208`), so this edit and `crates/honba-risk/Cargo.toml` must land together.

## Consequences

- New crate, new public types, no breaking wire change; the five new `ErrorCode` variants are
  additive (decision 5) and need `make codegen` plus a CHANGELOG migration note.
- `TradingState` changes home with re-exports, so Rust call sites are unchanged; Python and CLI
  gain their first exposure of it, which is what completes E2-S1.
- `InstrumentRules::validate_quantity` gets its first non-test caller, and `price_band` its first —
  both stop being dead code by construction.
- A risk refusal becomes a first-class, replayable fact: `RiskRefused` in the audit, a typed
  `RiskRefusal` in memory, a `risk_*` code on the wire, the same vocabulary in Rust and Python.
- E11-S7 and E5-S4 become "wire the gate in", not "invent the gate": the `WRITE_PATHS` registry
  already names the routes, the approval queue already has its acceptance tests, and the refusal
  codes are already in the error taxonomy and the Python client's `RiskApiError`.
- Tests to write first (TDD): unit `max_notional_refused`, `price_band_from_profile`,
  `reduce_only_refuses_position_increasing_order`, `order_rate_refused_in_event_time`,
  `lot_size_refused`, plus property tests for rule order and idempotence (a decision carries no
  state except the rate window); integration `strategy_engine_risk_fills_refusal_in_audit`,
  `python_halt_blocks_orders_in_run`, `order_refused_by_risk`, `post_order_flows_to_sim_only`.

## Known limits

- `price_band` defaults to `None` and the India pack does not override it, so rule 4 refuses
  nothing in production until a pack supplies bands; `price_band_from_profile` must use a test
  profile (or the India pack gains real circuit bands as a separate change).
- Market orders carry no price, so the band rule cannot check them at submit; a band on a market
  order needs a reference price and belongs with the L2 fill model (E3-S1b), not here.
- `position` is as good as its seed: the kernel's map starts from what the caller seeds, and live
  starting positions arrive with E2-S7 reconciliation. Until then, reduce-only in live mode is
  correct only for positions opened in the same session.
- No account-level rules: `risk_max_position_exceeded` and `risk_max_drawdown_exceeded` exist as
  codes with no rule; margin (E3-S4) and the analytics gates (E4-S6) are their owners.
- The rate rule runs on event time, which is right for determinism and wrong as a transport limiter;
  protecting the HTTP surface from request floods stays with E11-S8 auth/rate limiting.
- Risk state (the rate window, and any future durable limit) is in-memory and per run; E2-S11's
  durable, idempotent state is a later story.
- New `ErrorCode` variants break strict decoders until they regenerate (ADR 0012 rule 3), and there
  is no live run config type to hang the "live must set `max_notional`" requirement on yet.
