# ADR 006: Canonical Domain Types and the JSON Wire Contract (E0-S2)

## Status
Accepted

## Context
Per [ROADMAP-borrowed-ideas.md](../../ROADMAP-borrowed-ideas.md) pillar P2 ("domain types (Rust) -> serde JSON ->
Python models -> JSON Schema") and story E0-S2, the core domain types need one serialized form shared by Rust and
Python. Before this ADR:

- Only `Venue`, `InstrumentId`, `OrderId`, `TradeId`, `UnixNanos`, `Currency` and `PositionSide` derived serde;
  `Event`, `Message`, `Order`, `Bar`, `Trade`, `Position` and `OrderIntent` had no serialized form.
- `Event::Order*` variants carried `order_id: String` instead of `OrderId`.
- `OrderIntent` (Rust and Python) could only express market and limit orders; a stop-limit order needs two prices.
- `Trade` had no transaction costs.
- Python `honba.entities` dataclasses are a lighter, strategy-facing shape (`Bar.ts`, no `BarType`) and are used by
  `honba.strategies`; they are not a wire format.

## Decisions
1. **Rust serde is the single source of truth.** The Rust types in `honba-messages` (L0), `honba-entities` (L1) and
   `honba-strategy` (L4, `OrderIntent`) define the wire contract through `serde` derives. No `schemars` dependency is
   added: the roadmap places JSON Schema *downstream* of the Python models, so a later story can export JSON Schema
   from the pydantic models (`model_json_schema()`) and diff it in CI.
2. **Python wire models are verified, not hand-trusted.** `honba.entities.wire` holds pydantic v2 models that mirror
   the serde representation exactly (`extra="forbid"`, frozen, no NaN/inf). They are verified against the Rust schema
   in two ways:
   - shared **golden vectors** in `schema/golden/*.json`, read by Rust integration tests (`crates/*/tests/golden.rs`)
     and Python tests (`python/tests/unit/test_wire_golden.py`);
   - a cross-language round trip through the extension (`honba._honba.canonical_json(kind, json)` parses with Rust
     serde and re-serializes): golden -> Rust -> JSON -> Python -> JSON -> Rust must be lossless
     (`python/tests/integration/test_wire_roundtrip.py`).
   Golden files hold `cases` (must round-trip), `invalid` (JSON values both sides must reject) and `invalid_text`
   (raw JSON text both sides must reject, for faults a parsed value cannot carry, such as duplicate keys).
   - **Duplicate keys are invalid.** serde rejects a duplicated field; pydantic's `validate_json` silently keeps the
     last one. Payloads from other processes must therefore be parsed with `honba.entities.wire.loads(kind, text)`,
     which rejects duplicate keys at any depth (and the non-standard `NaN`/`Infinity` literals) before validating.
   The existing dataclasses stay as the strategy-facing API; `wire` models offer `to_domain()` / `from_domain()` where
   the mapping is lossless.
3. **Representation rules.**
   - Structs are JSON objects with snake_case field names equal to the Rust field names; `Option` is always emitted
     (`null` when absent).
   - Unit enums are lower snake_case strings (`"buy"`, `"stop_limit"`, `"partially_filled"`); `Currency` is its ISO
     code (`"INR"`). Every wire enum is declared through `honba_messages::enum_with_all!`, which generates an `ALL`
     list from the definition; `honba._honba.wire_enum_values()` exposes those lists and a cross-language test
     requires them to equal `honba.entities.wire.ENUMS` and sends every Python value through `canonical_json`, so a
     variant added on one side only fails CI.
   - Identifier newtypes (`Venue`, `OrderId`, `TradeId`) are plain strings; `InstrumentId` is
     `{"symbol", "venue"}`.
   - `UnixNanos` is a JSON integer (u64). Values above 2^53 are exact in Rust and Python but not in JavaScript;
     JS/HTTP consumers must use a big-integer-aware parser (revisit in the OpenAPI/MCP stories).
   - Prices and quantities are finite `f64`. serde_json is built with `float_roundtrip` so parsing is exact.
     NaN/inf are not part of the contract: serializing a non-finite field fails
     (`honba_messages::validation::serialize_finite`) instead of serde_json's default of writing `null`, so a NaN
     can never be read back as an absent optional.
   - **Value invariants are part of the contract.** Each type has a `validate()` returning a typed
     `honba_messages::InvariantError`; constructors `debug_assert` it and deserialization (`#[serde(try_from)]` over a
     private raw struct) enforces it, so an invalid payload is rejected rather than producing a broken value. The
     Python wire models enforce the same rules, and every `invalid` golden case is run by both test suites:
     - `BarSpecification`: `step >= 1`.
     - `Bar`: finite OHLCV, `low <= open, close <= high`, `volume >= 0`.
     - `QuoteTick`: finite, `bid_price <= ask_price`, sizes `>= 0`. `TradeTick`: finite, `size >= 0`.
     - `Order`: `quantity > 0`, prices finite; `side` may be `no_order_side` (an order is a record).
     - `Event::OrderFilled`: `last_qty > 0`, `last_px` finite.
     - `Trade`: `side` is buy or sell, `quantity > 0`, `price > 0`, `costs` finite.
     - `Position`: `quantity >= 0` and `avg_price >= 0` (direction is `side`), `realized_pnl` finite.
   - `Event` is internally tagged: `{"type": "<variant>", ...fields}` with variants `quote`, `trade`, `bar`, `order`,
     `order_accepted`, `order_rejected`, `order_filled`, `order_cancelled`. Order lifecycle events use `OrderId`.
   - `Message` is the versioned envelope: `{"schema_version": 1, "event": {...}, "ts_init": n}`. A reader rejects any
     other `schema_version`. Stand-alone types carry no version; golden files record the version they belong to.
4. **Domain additions (additive where possible).**
   - `OrderIntent` gains `trigger_price` and `stop_*` / `stop_limit_*` constructors. `price` is the limit price only.
     Invariants (both languages): market has neither price; limit needs `price`; stop-market needs `trigger_price`
     and no `price`; stop-limit needs both; side is buy or sell; quantity > 0; prices finite. The fields stay public
     and the constructors are infallible, so an `OrderIntent` *value* may be invalid; what is guaranteed is that an
     invalid intent never becomes an `Order`: deserialization rejects it, and `OrderIntent::into_order` (the only
     intent -> order path) validates and returns `Result<Order, IntentError>`. `StrategyRunner` does not submit a
     rejected intent; it records an `IntentRejection { intent, error, ts_init }` (`StrategyRunner::rejections`),
     calls `Strategy::on_intent_rejected` (default no-op) and continues the run. In Python the `OrderIntent`
     dataclass and the wire model validate on construction.
   - `Order` gains an optional `trigger_price` (`with_trigger_price`), so a stop-limit intent survives conversion.
   - `Trade` gains `costs` (total transaction costs in settlement currency, default 0, `with_costs`).
   - Python `OrderType.STOP` becomes an alias of `STOP_MARKET` (`"stop"` still parses); `TimeInForce` gains `FOK`,
     `GTD`; `OrderSide` gains `NO_ORDER_SIDE` (wire-only; intents reject it).

## Consequences
- Breaking (Rust): `OrderIntent::into_order` returns `Result<Order, IntentError>`.
- Breaking (Rust): `Event::Order*::order_id` is now `OrderId` (`"O-1".into()` still compiles via `From<&str>`);
  `OrderIntent` has a new public field (struct literals must add `trigger_price`); `PositionSide` and `Currency`
  serialize as `"long"` / `"INR"` (affects `honba-analytics` `RoundTrip` JSON output).
- Breaking (Python): `OrderType.STOP.value` is now `"stop_market"`; an intent with `OrderType.STOP` must set
  `trigger_price` instead of `price`.
- Adding or changing a wire field requires updating the golden vectors in the same commit; a breaking change bumps
  `SCHEMA_VERSION` in both `honba_messages` and `honba.entities.wire`.
- Money stays `f64` here; the integer-money decision is E0-S6.

## Known gaps
- `honba-sim`'s `BarFillEngine` fills every order at the last bar close, ignoring `order_type`, `price` and
  `trigger_price`: stop and stop-limit orders (and limit orders) are filled immediately as if they were market
  orders. The intent and order types carry stop prices correctly, but no simulator honours them yet. Tracked as a
  follow-up ticket; it is not part of E0-S2.
