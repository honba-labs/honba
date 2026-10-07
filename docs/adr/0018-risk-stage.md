# ADR 0018: The Risk Stage (`honba-risk`)

Date: 2026-10-07. Status: **accepted** (2026-10-07; accepted with review amendments). Roadmap
story: decides D4; implements E2-S2, supplies the enforcement half of E2-S1, and gates E11-S7 and
E5-S4. Implementation is gated on ADR 0019 story (b) (order store, `working_exposure`, pre-gate
`ExecutionEvent::Rejected`).

## Context

Nothing today refuses an order for a risk reason except "the engine is halted".

- There is no `honba-risk` crate. `crates/honba-engine/src/risk/mod.rs` is the single line
  `//! risk` and is not among the modules declared in `crates/honba-engine/src/lib.rs:19`-`:27`,
  so it is not compiled; `crates/honba-strategy/src/risk/mod.rs` is the same (`//! risk`,
  undeclared). `docs/ROADMAP.md:424` records the audit correction: "No `honba-risk` crate;
  `engine/src/risk/mod.rs` is 9 bytes; no `RiskCheck` anywhere".
- `TradingState { Active, Reducing, Halted }` lives in `honba-engine`
  (`crates/honba-engine/src/state.rs:10`), with no serde derive. `accepts_orders()` returns true for
  `Active` **and** `Reducing` (`state.rs:22`), `can_transition_to` permits every transition by
  design (`state.rs:37`), and `Engine::set_trading_state` is the single transition implementation,
  audited as `AuditKind::StateChanged` (`crates/honba-engine/src/engine.rs:203`).
  `Engine::submit` (`engine.rs:259`) refuses an order only when the state is `Halted`, auditing the
  free-text reason `"trading halted"` (`engine.rs:264`), or when no execution engine is attached
  (`"no execution attached"`, `:271`). `Reducing` is therefore not reduce-only anywhere:
  `reduce_only` appears in no `.rs` or `.py` file in the repo.
- **`Engine::submit` is not the only submitter.** The Rust `StrategyRunner` is a `Handler` that
  owns its own `ExecutionEngine` and calls `execution.submit` itself
  (`crates/honba-strategy/src/runner.rs:251`); every in-repo strategy run does this — the CLI
  backtest (`crates/honba-cli/src/backtest.rs:113`), the sweep trial
  (`crates/honba-sweep/src/trial.rs:100`, runner added to an `Engine`), `honba-py`'s
  `run_strategy*` (`crates/honba-py/src/pyclasses/run.rs:137`) and the Python `StrategyRunner`.
  `Engine::submit` sees only `EngineOutput::Orders` from handlers that return orders. So today an
  engine set to `Halted` does **not** stop orders from a hosted `StrategyRunner`.
- The operator path exists: `Command::State(TradingState)` (`crates/honba-async/src/command.rs:34`)
  reaches the same `set_trading_state`. There is no Python or CLI exposure of `TradingState`
  (`docs/ROADMAP.md:440`), which is the other half of E2-S1.
- Market rules already exist and are never called: `MarketProfile::instrument_rules()`
  ("lot sizes, tick sizes, circuit bands", `crates/honba-market/src/profile.rs:29`);
  `InstrumentRules { lot_size, tick_size, min_order_quantity, max_order_quantity }` with
  `validate_quantity` (min, freeze-quantity, lot multiple) and `validate_price` (positive, tick
  aligned at a 1e-4 tolerance) at `crates/honba-market/src/rules.rs:62` and `:91`, both returning
  `MarketError::RuleViolation(String)`; `PriceBand { lower, upper }` at `rules.rs:11`.
  `InstrumentRulesProvider::rules_for(&Instrument)` (`rules.rs:115`) needs a full `Instrument`,
  not an id; `price_band(&InstrumentId)` defaults to `None` (`rules.rs:120`).
  `validate_quantity`/`validate_price` have **no call sites outside `honba-market`**. The India pack
  supplies `InstrumentRules::new(1.0, 0.05)` for cash equity and does not override `price_band`
  (`crates/honba-market/src/india/profile.rs:18`, `:82`).
- **Nothing checks price sign or tick today.** `IntentError` refuses only non-finite prices
  (`crates/honba-strategy/src/intent.rs:65`), `honba-api` has no tick check, and
  `Instrument::is_on_tick` (`crates/honba-entities/src/instrument.rs:714`, 1e-6 tolerance) has no
  production caller: its only caller is `settle_notional` (`:739`), itself called only from tests
  and doctests.
- The error taxonomy is already waiting: `ErrorCode::{RiskMaxNotionalExceeded,
  RiskMaxPositionExceeded, RiskMaxDrawdownExceeded}` in `category = "risk"`, documented as not
  retryable (`crates/honba-messages/src/errors.rs:108`, `:147`, `:163`), and surfaced in Python as
  `RiskApiError` (`python/src/honba/client/errors.py:94`, docstring "A `risk_max_*` refusal") via
  `CATEGORY_OF_CODE` / `ERROR_CLASS_OF_CODE` (`errors.py:131`, `:152`). Deserialisation of
  `ErrorCode` is deliberately strict (`errors.rs:79`), so a code an old client has never seen is a
  decode error.
- `AuditKind` (`crates/honba-engine/src/audit.rs:24`) and `AuditLog` (`audit.rs:78`) record only
  unstructured `OrderRejected { order_id, reason: String }`; `AuditKind` derives
  `Debug, Clone, PartialEq` and no serde, so it is Rust-internal and free to grow.
- Config: `BacktestRunConfig` (`crates/honba-config/src/lib.rs:21`) is `deny_unknown_fields` with
  `#[serde(default)]` on every optional section — an undeclared `[risk]` table is a parse error
  today. `AccountConfig.currency` (`lib.rs:122`) is the ISO code of `starting_cash`.
  `CONFIG_TYPES` in `crates/honba-codegen/src/registry.rs:67` is `["BacktestRunConfig"]`, each
  name backed by an `add_type::<honba_config::..>` (`registry.rs:149`) (ADR 0014).
- Write routes are already marked: `Access::{ReadOnly, Write}` and `WRITE_PATHS = {POST /orders,
  DELETE /orders/{id}, POST /positions/close}` (`crates/honba-messages/src/endpoints.rs:54`,
  `:193`), documented as "the ones an approval queue and the risk stage must gate" (`:49`). All
  four trading rows answer 501, no auth/approval/scope code exists, and the REST `AppState` holds
  only read ports (`InstrumentMaster`, bars, quotes, depth; `crates/honba-api-rest/src/state.rs:17`)
  — no engine, no position store. `POST /backtests` and `POST /sweeps` are not in `WRITE_PATHS`,
  and the generated MCP tools mark `backtest`/`sweep` with `readOnlyHint: true`.
- Layering is enforced by an adjacency allow-list (`scripts/dependency_graph.py:30`, dev edges at
  `:119`); an unregistered crate is a CI failure (`:209`) and a crate missing from `ALLOWED_DEV`
  raises `KeyError` (`:225`). `CORE_CRATES` (`:143`) is checked only against a direct
  `honba-market` dependency enabling `india` (`:243`); `SYNC_KERNEL_CRATES` (`:157`) is the
  tokio-free set.

## Decision

### 1. One new pure crate: `honba-risk`, at L2, with a `RulesSource` port

`honba-risk` holds the rules, the typed refusals, `RiskRequest`/`RiskDecision`, the `RiskCheck`
interface, its **sole** implementation `RiskStage`, the `RulesSource` port and the `RiskLimits`
config type. It is pure: no I/O, no clock, no tokio, no broker vocabulary, no `india` feature.

Dependencies: `honba-messages`, `honba-entities`, `honba-market`. L2 (with `honba-market`,
`honba-ports`) is the recommendation in D4 and the layer that fits: the rules read
`InstrumentRules`/`PriceBand` (L2) and money/positions (L1), and they must be callable from
`honba-engine` (L3), `honba-strategy` (L4), `honba-api-rest` and `honba-py` (L7) alike.

```rust
// honba-risk
pub trait RulesSource: Send + Sync {
    /// `None` = the instrument is unknown to this run; the stage refuses (never approves) it.
    fn rules(&self, id: &InstrumentId) -> Option<(InstrumentRules, Option<PriceBand>)>;
}

/// Adapter over honba-market's provider. It lives here, not in honba-market, because
/// honba-market cannot name honba-risk (that edge would be a cycle).
pub struct ProfileRulesSource { /* Arc<dyn MarketProfile>, BTreeMap<InstrumentId, Instrument> */ }
impl ProfileRulesSource {
    pub fn new(profile: Arc<dyn MarketProfile>, instruments: impl IntoIterator<Item = Instrument>) -> Self;
}
// rules(id) = instruments.get(id).map(|i| (profile.instrument_rules().rules_for(i),
//                                          profile.instrument_rules().price_band(id)))
```

Who injects the source, per path:

| Path | Builds `RiskStage` with | Instruments from |
|---|---|---|
| Kernel (`Engine::with_risk`) | caller / run assembler | the run's instrument set |
| Rust `StrategyRunner::with_risk` (CLI backtest, `honba-py` `run_strategy*`) | the assembler | `LedgerContext` instruments (`crates/honba-strategy/src/context.rs:79`) |
| Python `StrategyRunner(risk=...)` | `_honba.RiskStage` (decision 10) | `LedgerContext.instrument(..)` (`python/src/honba/strategies/context.py:115`) |
| REST write route | the handler, per request | `InstrumentMaster::get_instrument` (async) awaited first, then a one-instrument `ProfileRulesSource` |

A run assembler that builds the stage from config resolves every instrument of the declared
universe **at build time** and fails the build (`RiskConfigError::UnknownInstrument`) if one is
unresolved, so the runtime refusal `risk_instrument_unknown` occurs only for an instrument outside
the declared universe. When E2-S9's instrument store lands, it implements `RulesSource` (via
`ProfileRulesSource` over its instruments) and replaces the per-path instrument maps; nothing in
the stage changes.

Rejected: putting the rules in `crates/honba-engine/src/risk/` (the existing stub) — the REST and
Python runner paths do not run through `Engine`. Rejected: a crate at L3 — same reuse problem one
layer down, and config types (L5) would sit above the rules they configure. Rejected: approving an
order whose instrument has no rules — a silent hole exactly where the stage exists to look.

### 2. `TradingState` moves to `honba-messages`, re-exported from `honba-engine`

The risk stage must name `TradingState` from L2, and L2 may not depend on L3. It moves to
`honba-messages` (L0) and `honba-engine` re-exports it (`pub use honba_messages::TradingState;`),
keeping `honba_engine::TradingState`, `honba_async::Command::State` and
`EngineOutput::StateChange` source-compatible.

- It **gains** `Serialize, Deserialize, JsonSchema` with `#[serde(rename_all = "snake_case")]`:
  wire spelling `"active"`, `"reducing"`, `"halted"` (used by the golden vectors and the Python
  binding). It is not added to a codegen registry here: no REST route carries it yet.
- `accepts_orders()` is **kept** for source compatibility, its doc reworded to "may accept orders
  (`Reducing`: reduce-only, enforced by `honba-risk`)"; `Engine::submit` stops calling it — the
  stage's `TradingHalted` rule replaces that check.
- Its unit tests move from `crates/honba-engine/src/tests/state.rs` to
  `crates/honba-messages/src/tests/trading_state.rs` unchanged, plus a serde round-trip test.

Rejected: a separate `RiskMode` — two vocabularies for one concept. Rejected: passing a
`bool`/`&str` — same drift, no type safety.

### 3. Interface: `RiskCheck -> approved` plus typed refusals

```rust
// honba-risk
pub struct RiskRequest {               // everything a rule needs, all values in
    pub order_id: OrderId,
    pub instrument_id: InstrumentId,
    pub side: OrderSide,
    pub quantity: f64,
    pub price: Option<f64>,            // limit price; None for market / stop-market
    pub trigger_price: Option<f64>,
    pub reference_price: Option<f64>,  // last observed price, supplied by the submitter
    /// Signed position plus the signed working remainder on the order's side
    /// (ADR 0019 `working_exposure(instrument, side)`).
    pub position: f64,
    pub trading_state: TradingState,
    pub ts: UnixNanos,                 // event time, never the wall clock
}

pub enum RiskDecision { Approved, Refused(RiskRefusal) }

pub trait RiskCheck: Send {
    /// Evaluate every rule in fixed order; the first refusal wins.
    fn check(&mut self, req: &RiskRequest) -> RiskDecision;
}

pub struct RiskStage { /* limits, currency, Arc<dyn RulesSource>, rate window */ }   // not Clone
impl RiskStage {
    pub fn new(limits: RiskLimits, currency: Currency, rules: Arc<dyn RulesSource>)
        -> Result<Self, RiskConfigError>;   // validates limits (decision 8)
}
impl RiskCheck for RiskStage { .. }
```

- `currency` is explicit: the assembler parses `AccountConfig.currency`; `max_notional` is in that
  currency's major units.
- `&mut self` because the order-rate rule carries a window; the stage is owned by one submitter
  (decision 7), so a plain `&mut` is correct. `RiskStage` is deliberately not `Clone`.
- Who supplies `reference_price`: `Engine` keeps `last_px: HashMap<InstrumentId, f64>` from the
  bar closes and trade prices it dispatches; the runners use the last bar/quote their context saw;
  REST uses `QuoteReader` (last, else mid).
- `RiskRefusal` is a typed enum (derives `Debug, Clone, PartialEq`), each variant carrying the
  numbers a message or audit row needs:

```rust
pub enum PriceField { Price, TriggerPrice }
pub enum RiskRefusal {
    TradingHalted,
    ReduceOnly { position: f64, side: OrderSide, quantity: f64 },
    InstrumentUnknown { instrument_id: InstrumentId },
    QuantityBelowMin { quantity: f64, min: f64 },
    QuantityOverFreeze { quantity: f64, max: f64 },
    LotMultiple { quantity: f64, lot: f64 },
    TickSize { field: PriceField, price: f64, tick: f64 },          // non-positive or off tick
    PriceBand { field: PriceField, price: f64, lower: f64, upper: f64 },
    MaxNotional { notional: Money, limit: Money },
    MaxNotionalUnpriceable { limit: Money },
    OrderRate { count: u32, max_orders: u32, window_ms: u64 },
}
```

- `RiskRefusal::rule() -> &'static str` (stable rule name), `error_code() -> ErrorCode` and
  `context() -> serde_json::Value` (the numbers, plus `"rule"` and, where relevant, `"reason"`)
  live in `honba-risk`.

### 4. Rules, in a fixed evaluation order

| # | Rule | Input | Refusal |
|---|---|---|---|
| 1 | `TradingHalted` | `trading_state == Halted` | `TradingHalted` |
| 2 | `ReduceOnly` | `trading_state == Reducing`, `position`, `side`, `quantity` | `ReduceOnly` |
| 3 | `InstrumentUnknown` | `rules.rules(instrument_id) == None` | `InstrumentUnknown` |
| 4 | `QuantityBelowMin` | `validate_quantity` (decision 4a) | `QuantityBelowMin` |
| 5 | `QuantityOverFreeze` | `validate_quantity` | `QuantityOverFreeze` |
| 6 | `LotMultiple` | `validate_quantity` | `LotMultiple` |
| 7 | `TickSize` | `price` and `trigger_price`, each if present: `> 0` and on `tick_size` | `TickSize` |
| 8 | `PriceBand` | band, if any, contains `price` and `trigger_price`, each if present | `PriceBand` |
| 9 | `MaxNotional` | `Money::mul_qty(quantity, px, currency)` vs `max_notional`, if configured | `MaxNotional` / `MaxNotionalUnpriceable` |
| 10 | `OrderRate` | orders-per-window over `ts`, if configured | `OrderRate` |

- The first refusal wins, in that order, pinned by tests: state rules need no rules source, so a
  halted or reduce-only engine reports *why* it is closed; shape rules precede arithmetic; and the
  rate window — the only rule with memory — advances only for orders that passed every other rule.
- **Reduce-only** (the E2-S1 enforcement): with `trading_state == Reducing`, with
  `p = RiskRequest.position` (position plus same-side working remainder, ADR 0019 decision 5) and
  `s = +1` buy / `-1` sell, an order is approved only if `p + s*q` stays within
  `[min(p, 0), max(p, 0)]`. Flat (`p == 0`) refuses every order. Two in-flight reducing orders
  cannot together cross zero (long 100, working sell 60, new sell 50: `p = 40`, refused).
- **Tick size** uses `InstrumentRules::validate_price` (decision 4a), whose tolerance aligns to the
  entities' `LOT_TICK_TOLERANCE` (1e-6 ticks) so `is_on_tick` and the stage agree. A non-positive
  price is `context.reason = "non_positive"`, an off-tick one `"off_tick"`.
- **Notional price**: `price`, else `trigger_price`, else `reference_price`. If none is present and
  `max_notional` is configured the order is refused as `MaxNotionalUnpriceable`
  (`risk_max_notional_exceeded`, `context.reason = "unpriceable"`). With `max_notional` unset the
  rule is skipped.
- **Order rate**: approved orders are counted in the half-open event-time window
  `(ts - window_ns, ts]`, `window_ns = window_ms * 1_000_000`; an order whose `ts` is exactly
  `window_ns` after an earlier one does not see it. If the count is already `>= max_orders` the
  order is refused. **Refused orders never count**, from any rule. A `ts` earlier than the last
  recorded one (not produced by the kernel) is treated as the last recorded `ts`, so the window is
  deterministic and never shrinks.
- The band rule checks only prices the order carries; a market order is not band-checked
  (out of scope, below).
- `ErrorCode::RiskMaxPositionExceeded` and `RiskMaxDrawdownExceeded` stay **reserved** (out of scope).

#### 4a. `honba-market` returns typed violations (cross-crate change)

`InstrumentRules::validate_quantity` becomes `-> Result<(), QuantityViolation>` with
`QuantityViolation { BelowMin { quantity, min }, OverFreeze { quantity, max }, NotLotMultiple {
quantity, lot } }`, and `validate_price` becomes `-> Result<(), PriceViolation>` with
`PriceViolation { NonPositive { price }, OffTick { price, tick } }` (tolerance as above). Both
implement `Display` with today's prose and convert `Into<MarketError>`. Tests:
`crates/honba-market/src/tests/rules.rs:25`-`:35` change from `contains("...")` string matches to
variant matches (deliberate, R1: the result is now typed; the cases and values are unchanged),
plus `validate_price_tolerance_matches_is_on_tick`; `tests/market_contract.rs:52`-`:53` (`is_ok()`)
are unchanged.

### 5. Wire codes: one existing, ten new

| Refusal | `ErrorCode` (wire = `Rejected.reason`) | Category | Status |
|---|---|---|---|
| `MaxNotional`, `MaxNotionalUnpriceable` | `risk_max_notional_exceeded` | risk | exists (`errors.rs:108`) |
| `OrderRate` | `risk_order_rate_exceeded` | risk | new |
| `QuantityBelowMin` | `risk_quantity_below_min` | risk | new |
| `QuantityOverFreeze` | `risk_quantity_over_freeze` | risk | new |
| `LotMultiple` | `risk_lot_multiple_violation` | risk | new |
| `TickSize` | `risk_tick_size_violation` | risk | new |
| `PriceBand` | `risk_price_band_exceeded` | risk | new |
| `ReduceOnly` | `risk_reduce_only_violation` | risk | new |
| `TradingHalted` | `risk_trading_halted` | risk | new (replaces `"trading halted"`) |
| `InstrumentUnknown` | `risk_instrument_unknown` | risk | new |
| no execution attached (not a risk rule) | `order_execution_unavailable` | order | new (replaces `"no execution attached"`) |

All are not retryable. The `ErrorCode` wire spelling is the `reason` of the pre-gate
`ExecutionEvent::Rejected` and of `AuditKind::OrderRejected` (ADR 0019 decision 5). On the REST
write routes they render as HTTP **422** through `failure(StatusCode::UNPROCESSABLE_ENTITY, ..)`
(`crates/honba-api-rest/src/market.rs:96`) with `context` from `RiskRefusal::context()`.
Rejected: `403 Forbidden` — that means authn/authz scopes (E5-S3/E11-S8), not a broken limit.

Ten new variants are additive within `/api/v1` (ADR 0012 rules 1-3): no `schema_version` bump of
its own (ADR 0019's bump to 4 may carry them), `make codegen` in the same commit, and a CHANGELOG
note that strict decoders reject an unknown code until regenerated.

### 6. Refusals are audited events

- `AuditKind` gains `RiskRefused { order_id: String, refusal: RiskRefusal }` (the typed value, not
  a string). The submitter records it **and then** `OrderRejected { order_id, reason }` with
  `reason = refusal.error_code().as_str()`, applies `Initialized -> Rejected` and enqueues
  `ExecutionEvent::Rejected { reason, venue_order_id: None, .. }` on the one event queue
  (ADR 0019 decision 5, which supersedes this ADR's earlier `OrderRejection`/`drain_rejections`
  wording). The strategy sees `order_rejected`; the runner releases the order as for a venue reject.
- "No execution attached" is checked **first** in `Engine::submit`, before the stage (a wiring
  fault, not a risk decision): it records only `OrderRejected { reason:
  "order_execution_unavailable" }` plus the `Rejected` event, and never touches the rate window.
- Refusals are never dropped, never retried silently and never reach an `ExecutionEngine`.

### 7. Where the stage sits: one stage per submitter, at most one per run

The stage sits in front of the code that calls `ExecutionEngine::submit` — the **submitter**
(ADR 0019 decision 1). Each order has exactly one submitter, so each order passes exactly one gate.

- **`Engine::submit`** (`engine.rs:259`), for `EngineOutput::Orders`:

  ```rust
  impl Engine {
      pub fn with_risk(mut self, stage: RiskStage) -> Self;
      pub fn with_positions(mut self, seed: impl IntoIterator<Item = (InstrumentId, f64)>) -> Self;
  }
  ```

  `position` comes from the minimal position map that `acknowledge_events` (today
  `acknowledge_fills`, `engine.rs:234`) updates from `Fill` events (ADR 0019 decision 5), seeded by
  `with_positions`, plus `working_exposure(instrument, side)`. Without `with_risk` the engine
  applies the state rules only (1-2, through the same pure `honba_risk::check_state(&RiskRequest)`
  that `RiskStage` calls first), so `Halted` and reduce-only hold on every engine, and existing
  engine tests need no rules source.
- **Rust `StrategyRunner`** (`runner.rs:251`): `StrategyRunner::with_risk(stage)`; `position` from
  its `LedgerContext` plus its own order store's working exposure (ADR 0019 (b)). It learns the
  engine's state through a new `Handler::on_trading_state(&mut self, state: TradingState)` default
  no-op that `Engine::set_trading_state` calls on every handler; without `with_risk` it too applies
  the state rules. This closes the Context gap: `Halted` now stops a hosted runner.
- **Python `StrategyRunner`**: `StrategyRunner(..., risk: RiskStage | None = None)`,
  `set_trading_state(state)` / `trading_state`; `position` from `LedgerContext.position(..)` plus
  its `OrderState` store; refusals as `Rejected` events per ADR 0019 (`python_halt_blocks_orders_in_run`).
- **Construction guard.** A run may hold at most one `RiskStage`, because two would split or
  double-count the rate window. `Handler` gains `fn holds_risk_stage(&self) -> bool { false }`
  (`StrategyRunner` returns whether it has one); `Engine::start` returns
  a new `AlgoError::DuplicateRiskStage` (additive; `AlgoError` is `#[non_exhaustive]`,
  `crates/honba-engine/src/error.rs:10`) when the engine has a stage and any
  handler reports one, or when two handlers do. Test: `double_stage_rejected_at_build`. Assemblers
  put the stage on the runner when a runner is the submitter, on the engine otherwise.
- **REST write routes (E11-S7):** `POST /orders` runs approval queue (E5-S4, when configured) ->
  risk stage -> execution gateway (simulator only until E3-S7). With no `Engine`, the handler builds
  the `RiskRequest` from: rules via `InstrumentMaster` + the registered profile (decision 1);
  `position` and working exposure from the simulator gateway's ledger and order store that E11-S7
  wires into `AppState` (write routes stay 501 until it exists); `trading_state` from the app's
  operator-set `TradingState`; `reference_price` from `QuoteReader`; `ts` from the composition
  root's `honba_ports` clock (the sim clock until E3-S7). The REST app owns one `RiskStage` behind
  a `Mutex`. A refusal is the 422 of decision 5, audited as in decision 6, and never reaches the
  gateway (`post_order_flows_to_sim_only`, `approve_flows_through_risk_to_sim`).
- **Cancel and close.** `DELETE /orders/{id}` is never risk-checked and is allowed in every state,
  including `Halted` (it cannot add exposure; kernel `cancel` already routes when halted).
  `POST /positions/close` is allowed when `Halted`: it submits an order evaluated with
  `trading_state = Reducing` substituted for `Halted`, so it must pass reduce-only (it cannot
  cross zero) and the shape rules. Both bypass the approval queue (E5-S4), stay behind the
  `write:orders` scope and are audited.
- **Research routes are never gated.** `POST /backtests` / `POST /sweeps` are `Access::ReadOnly`.

### 8. Config schema

```toml
# BacktestRunConfig gains one optional section; absent = RiskLimits::default() (no limits).
[risk]
max_notional = 500000.0   # per order, major units of [account].currency; absent = unlimited

[risk.order_rate]         # absent = unlimited
max_orders = 30           # >= 1
window_ms = 1000          # >= 1, event time
```

```rust
// honba-risk
#[derive(Clone, Debug, Default, PartialEq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct RiskLimits {
    #[serde(default)] pub max_notional: Option<f64>,      // None = no notional rule; Some: finite, > 0
    #[serde(default)] pub order_rate: Option<OrderRateLimit>, // None = no rate rule
}
#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct OrderRateLimit { pub max_orders: u32, pub window_ms: u64 }
```

- `BacktestRunConfig` gains `#[serde(default)] pub risk: RiskLimits` (ADR 0012: inputs gain
  fields only with defaults); `BacktestRunConfig::validate` and `RiskStage::new` reject a
  non-finite or non-positive `max_notional` and zero `max_orders`/`window_ms`.
- `honba-config` re-exports `RiskLimits` and `OrderRateLimit`; `CONFIG_TYPES` becomes
  `["BacktestRunConfig", "RiskLimits"]` with `add_type::<honba_config::RiskLimits>`, so
  `honba-codegen` needs no `honba-risk` edge.
- Money is authored as `f64` major units (like `starting_cash`) and converted once with
  `Money::mul_qty(quantity, px, currency)` and `Money::from_major_f64(max_notional, currency)`, so the
  comparison is exact (ADR 0011). Rejected: a `Money` table in TOML (invites minor units in a
  major-unit file). Rejected: a `currency` key in `[risk]` — two currencies for one account.
- **Live runs.** There is still no live config type (`configs/live/*.toml` are placeholders), so the
  guard is at runtime: an assembler building a non-simulated run (any execution other than an
  in-repo simulator) calls `RiskLimits::require_live()`, which returns
  `RiskConfigError::LiveRunWithoutLimit` unless **both** `max_notional` and `order_rate` are set,
  and refuses to start. Test: `live_run_without_limits_refused`.

### 9. The dependency-graph change

`scripts/dependency_graph.py` (verified against the current script):

```python
# L2: honba-market, honba-ports, honba-risk        <- layer comment block, line 23
ALLOWED_PROD = {
    ...
    "honba-risk":     {"honba-messages", "honba-entities", "honba-market"},   # new crate
    "honba-engine":   {..., "honba-risk"},      # Engine::submit gate, check_state
    "honba-strategy": {..., "honba-risk"},      # StrategyRunner gate
    "honba-config":   {..., "honba-risk"},      # RiskLimits on BacktestRunConfig (re-exported)
    "honba-api-rest": {..., "honba-risk"},      # write-route gate
    "honba-py":       {..., "honba-risk"},      # bindings (decision 10)
    "honba-cli":      {..., "honba-risk"},      # CLI backtest assembler
}
ALLOWED_DEV = {
    ...
    "honba-risk": set(),                         # required: the script indexes ALLOWED_DEV[name]
}
CORE_CRATES |= {"honba-risk"}        # honba-risk depends on honba-market directly and must
                                     # never enable its `india` feature (rule 4 checks that edge)
SYNC_KERNEL_CRATES |= {"honba-risk"} # tokio-free
```

No `honba-codegen` or `honba-testing` edge (decision 8 re-export; property tests use a hand-rolled
seeded generator, no new dev dependency). `crates/honba-risk/Cargo.toml` enables no `honba-market`
feature beyond its default and must land in the same commit as this edit.

### 10. Python surface

- `honba-py` binds `_honba.TradingState` (`ACTIVE`/`REDUCING`/`HALTED`, `str()` = wire spelling),
  `_honba.RiskLimits(max_notional: float | None = None, order_rate: tuple[int, int] | None = None)`
  with `from_dict(d)` (same serde parser, so `deny_unknown_fields` applies), and
  `_honba.RiskStage(limits, currency: str, market: str, instruments: list[dict])` with
  `check(request: dict) -> RiskDecision` (`.approved`, `.code`, `.rule`, `.context`). The profile is
  chosen by name from the profiles `honba-py` already builds; instruments use the JSON shape of
  `parse_instrument` (`crates/honba-py/src/pyclasses/run.rs:59`). `run_strategy*` gain an optional
  `risk` argument (limits plus seed positions).
- Stubs: entries in `python/src/honba/_honba.pyi`; CI's `mypy.stubtest honba._honba` covers them.
- `python/src/honba/client/errors.py`: `CATEGORY_OF_CODE` gains the nine new `risk_*` codes as
  `"risk"` and `order_execution_unavailable` as `"order"`; `ERROR_CLASS_OF_CODE` maps the `risk_*`
  codes to `RiskApiError` and `order_execution_unavailable` to `OrderRejectedApiError`;
  `RiskApiError`'s docstring becomes "A `risk_*` refusal (HTTP 422)". The existing check against
  the generated stub catches a missed code.
- Config path: Python reads `[risk]` from the run's TOML table and passes it to
  `RiskLimits.from_dict`; there is no second Python parser. Catalog `StrategyConfig`
  (`config.toml`) gains no `[risk]` in this ADR.
- Tests: unit `python/tests/unit/test_risk_limits.py` (parse, unknown key refused, invalid values,
  `require_live`), `test_client_errors.py` (every new code -> class and category),
  `test_trading_state.py`; integration `test_python_halt_blocks_orders_in_run.py`,
  `test_reduce_only_two_orders_in_flight.py`, `test_risk_conformance.py` (decision 11).

### 11. Shared contract and test plan

**Golden vectors**: `schema/conformance/risk_decisions.json`, run by the Rust stage
(`crates/honba-risk/tests/conformance.rs`) and by Python through `_honba.RiskStage` and through
the Python `StrategyRunner` with a scripted port (`python/tests/integration/test_risk_conformance.py`):

```json
{
  "fixture_version": 1,
  "type": "RiskDecision",
  "currency": "INR",
  "instruments": { "X.NSE": { "lot_size": 25.0, "tick_size": 0.05, "min_order_quantity": 25.0,
                              "max_order_quantity": 100.0, "band": { "lower": 90.0, "upper": 110.0 } } },
  "cases": [
    { "name": "order_rate_refused_in_event_time",
      "limits": { "order_rate": { "max_orders": 2, "window_ms": 1000 } },
      "requests": [ { "order_id": "A", "instrument_id": "X.NSE", "side": "buy", "quantity": 25.0,
                      "price": 100.0, "position": 0.0, "trading_state": "active", "ts": 1 }, "..." ],
      "expect": [ { "decision": "approved" }, "...",
                  { "decision": "refused", "code": "risk_order_rate_exceeded",
                    "context": { "rule": "order_rate", "count": 2, "max_orders": 2, "window_ms": 1000 } } ] }
  ]
}
```

A case is a sequence on one fresh stage (the rate rule has memory). Acceptance: for every case,
both languages produce the same decision, code and `context` (integers exact, floats within 1e-9);
at least one case per refusal variant, per boundary (rate window edge, band edges inclusive, freeze
max inclusive, lot tolerance) and per rule-order pair below.

| Test | Level | Acceptance criterion |
|---|---|---|
| `trading_halted_refused`, `reduce_only_refuses_position_increasing_order`, `reduce_only_flat_refuses_all`, `reduce_only_two_orders_in_flight` | unit (`honba-risk/src/tests/`) | expected variant and numbers; in-flight case is the 100/60/50 example |
| `instrument_unknown_refused`, `quantity_below_min`/`_over_freeze`/`lot_multiple_refused`, `tick_size_refused` (price and trigger, non-positive and off-tick), `price_band_from_profile` (test profile with a band) | unit | one refusal per variant, boundaries approved |
| `max_notional_refused`, `max_notional_uses_trigger_then_reference`, `max_notional_unpriceable_refused` | unit | price precedence; `context.reason == "unpriceable"` |
| `order_rate_refused_in_event_time`, `refused_orders_do_not_count`, `rate_window_boundary_half_open` | unit | window semantics of decision 4 |
| property `rule_order` | unit | 10 000 seeded requests (fixed seed); the refusal equals the lowest-numbered rule that refuses when each rule is evaluated alone |
| property `deterministic` / `idempotent_without_rate` | unit | two fresh stages agree on every sequence; with `order_rate = None`, `check` twice gives the same decision. With a rate limit `check` is **not** idempotent by design (an approval consumes a slot) |
| `validate_*` typed violations | unit (`honba-market`) | decision 4a |
| `trading_state_serde_snake_case` | unit (`honba-messages`) | round trip `"reducing"` |
| `config_without_risk_section_parses` | unit (`honba-config`) | every `configs/backtest/*.toml` and existing config fixture parses; `risk == RiskLimits::default()` |
| `risk_unknown_field_rejected`, `risk_invalid_limits_rejected` | unit (`honba-config`) | parse / validate errors |
| `config_types_include_risk_limits` + `committed_artifacts` drift | integration (`honba-codegen`) | `CONFIG_TYPES` lists `RiskLimits`; regenerated schema equals committed |
| `double_stage_rejected_at_build`, `live_run_without_limits_refused` | integration (`honba-engine/tests`, `honba-strategy/tests`) | `Engine::start` / assembler error |
| `strategy_engine_risk_fills_refusal_in_audit` | integration (`honba-strategy/tests`) | strategy -> runner -> stage -> sim: audit has `RiskRefused` then `OrderRejected`, strategy sees `order_rejected`, sim saw no submit |
| `halt_stops_hosted_runner` | integration (`honba-strategy/tests`) | `EngineOutput::StateChange(Halted)` then runner orders are refused `risk_trading_halted` |
| `order_refused_by_risk`, `post_order_flows_to_sim_only`, `cancel_and_close_allowed_when_halted` | integration (`honba-api-rest/tests`) | 422 envelope with code and context; gateway untouched; cancel/close succeed when halted |
| `risk_conformance` | integration (Rust and Python) | the golden vectors above |

**Existing assertions that change deliberately (R1).** `crates/honba-engine/tests/engine.rs:460`
(`reason: "trading halted"`) becomes `RiskRefused { refusal: TradingHalted }` followed by
`OrderRejected { reason: "risk_trading_halted" }`; `engine.rs:496` and `:501`
(`"no execution attached"`) become `"order_execution_unavailable"`. Reason, recorded in the
commit message: free-text pre-gate reasons are replaced by `ErrorCode` wire spellings so audit,
event and REST share one vocabulary (this ADR and ADR 0019 decision 5). The literal at
`crates/honba-engine/src/tests/audit.rs:92` is opaque test data for sequence numbering and may stay.

## Consequences

- New crate, new public types, no breaking wire change; ten new `ErrorCode` variants are additive
  and need `make codegen` plus a CHANGELOG migration note.
- `TradingState` changes home with re-exports and gains serde; Python gains its first exposure,
  completing E2-S1 together with reduce-only and the hosted-runner halt fix.
- `Handler` gains two default methods (`on_trading_state`, `holds_risk_stage`): additive.
- `InstrumentRules::validate_quantity`/`validate_price` get typed results and their first
  production callers; `price_band` its first caller.
- A risk refusal is a first-class, replayable fact: typed `RiskRefusal` in the audit, an
  `order_rejected` event with a `risk_*` reason, a 422 with the same code on REST.
- **Behaviour change for research runs** with a stage attached: orders with off-tick prices, lot
  violations or unresolved instruments that simulators accepted before are now refused.
- **Cross-repo consumers (confirm before touching; flagged, not edited here):**
  `honba-frontend` (generated `domain.ts`: `ErrorCode` union, `BacktestRunConfig.risk`, `RiskLimits`;
  `make schema-ts` is local only); `honba-docs` (risk stage page, error-code table,
  `order_reject_cancel.md` already flagged by ADR 0019); `honba-examples`
  (`ai_research/10_gated_order_flow.py`, `docs/ROADMAP.md:383`); `honba-strategies` (catalog
  backtests re-run under the stage; off-tick or non-lot orders surface as refusals);
  `honba-adapters` (no API change; broker RMS rejections keep venue text in `Rejected.reason`,
  mapping them to `risk_*` codes is optional follow-up).

## Out of scope

| Item | Disposition |
|---|---|
| Max position per instrument / account (`risk_max_position_exceeded`) | deferred to E3-S4 (margin model), code stays reserved |
| Cash / margin pre-check | deferred to E3-S4 |
| Drawdown limit, automatic kill switch (`risk_max_drawdown_exceeded`) | deferred to E4-S6 gates with E2-S11 durable state; rejected for this per-order stage, which sees no PnL |
| Market-order price collar / band check on market orders | deferred to E3-S1b (L2 fill model has the execution price) |
| Stale-feed breaker | deferred to E2-S10 (`stale_after_blocks_orders`) |
| Risk-limited sweeps | deferred until `honba-sweep` trials consume `BacktestRunConfig`; trials apply state rules only |
| HTTP request-flood limiting | E11-S8 (the rate rule is event-time, not a transport limiter) |

## Known limits

- `price_band` defaults to `None` and the India pack does not override it, so rule 8 refuses
  nothing in production until a pack supplies bands; `price_band_from_profile` uses a test profile.
- `position` is as good as its seed: live starting positions arrive with E2-S7 reconciliation;
  until then reduce-only in live mode is correct only for positions opened in the session.
- `reference_price` is the last observed price, not the execution price; for market orders the
  notional check is an estimate.
- Instrument resolution is per path until E2-S9's store implements `RulesSource`.
- Risk state (the rate window) is in-memory and per run; durable state is E2-S11.
- New `ErrorCode` variants break strict decoders until they regenerate (ADR 0012 rule 3).
- The live guard is a runtime check in assemblers; a live config type will make it a schema rule.

## Amendments

Accepted 2026-10-07 with these changes from the proposed text, resolving the critical review:

1. `RulesSource` port in `honba-risk` (adapter `ProfileRulesSource` lives there, not in
   `honba-market`, to avoid a cycle); unknown instruments refused (`risk_instrument_unknown`);
   per-path injection; build-time universe resolution; E2-S9 dependency.
2. `RiskLimits`/`OrderRateLimit` Rust definitions; `RiskStage::new(limits, currency, rules)` with
   explicit `AccountConfig` currency; `Engine::with_risk`/`with_positions`; orders-per-window rate,
   half-open, ns units; refused orders never count.
3. `reference_price` and notional price precedence; unpriceable refusal.
4. Reduce-only on position plus same-side working exposure (ADR 0019); in-flight test.
5. False tick-alignment claim replaced; `TickSize` rule; `is_on_tick` has no production caller.
6. `LotSize` split into three rules/codes; typed `QuantityViolation`/`PriceViolation` in `honba-market`.
7. `order_execution_unavailable` and `risk_trading_halted`; reason = `ErrorCode` spelling.
8. `RiskRefused` carries the typed refusal; then `OrderRejected` and a `Rejected` event (ADR 0019).
9. Dependency-graph edits corrected (`ALLOWED_DEV` entry, layer comment, `honba-cli` edge, no
   codegen/testing edges, `CORE_CRATES` rationale).
10. Python surface section. 11. Golden vectors, test plan with acceptance criteria, property
    definitions. 12. Construction guard and the discovery that `StrategyRunner` bypasses
    `Engine::submit` (one stage per submitter; `Handler::on_trading_state`).
13. `TradingState` serde, test move, `accepts_orders` kept, deliberate audit-test changes.
14. REST request sources; cancel/close when halted. 15. Out-of-scope dispositions; runtime live
    guard. 16. Cross-repo consumers; citation fixes (`engine.rs:259`/`:234`, `ROADMAP.md:424`/`:440`,
    `state.rs:10`, `audit.rs:24`/`:78`, `errors.rs:79`, `lib.rs:21`, `endpoints.rs:54`, `rules.rs:11`,
    `india/profile.rs:18`/`:82`).
