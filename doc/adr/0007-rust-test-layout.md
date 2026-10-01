# ADR 007: Rust Test Layout (Unit Tests in `src/tests/`, Integration Tests in `tests/`)

## Status
Accepted

## Context
Unit tests were spread across inline `#[cfg(test)] mod tests { ... }` blocks at the bottom of implementation files
(engine, entities, messages, strategy, py), while `honba-data` already kept them in `src/tests/`. Six crates (sim,
cli, testing, indicators, analytics, market) had no unit tests, and sim, py and cli had no integration tests. Test
data (`InstrumentId::new("X", Venue::new("NSE"))`, ad-hoc bar builders) was copy-pasted across files.

## Decisions
1. **Unit tests live in `src/tests/`.** Each crate declares `#[cfg(test)] mod tests;` once in `lib.rs` (or `main.rs`
   for a binary) and keeps `src/tests/mod.rs` plus one file per area (`src/tests/<area>.rs`, e.g. `tests::queue`,
   `tests::bar_fill`). Implementation files contain no inline test modules. Doctests stay on the items they document.
2. **Integration tests live in `<crate>/tests/`** beside `Cargo.toml` and use only the crate's public API (or, for the
   CLI, the built binary via `CARGO_BIN_EXE_honba`).
3. **Visibility for tests.** Tests that need crate internals (for example building deliberately invalid values with
   struct-update syntax) get `pub(crate)` access; public APIs are never widened for tests. Current cases: the fields
   of `Bar`, `QuoteTick`, `TradeTick`, `Order` and `SchemaVersion` (messages), `Position` and `Trade` (entities), and
   the backtest helpers in `honba-cli`.
4. **Shared test data.** Each crate's `src/tests/mod.rs` holds small named helpers (`any_instrument()`, bar/order
   builders) for values that are irrelevant to the test. `honba_testing::fixtures` (`instrument`, `any_instrument`,
   `minute_bar_type`, `flat_bar`, `TEST_VENUE`) serves integration tests in crates whose dev-dependencies may include
   `honba-testing` per `scripts/dependency_graph.py` (currently `honba-strategy`); lower layers keep local helpers.
   Literals stay where the value is the point of the test (golden files, symbol-specific fixtures).
5. **Known gaps are recorded, not cemented.** A test that documents missing behaviour is `#[ignore = "known gap: ..."]`
   with the reason (e.g. the `BarFillEngine` stop/limit gap from ADR 006) and fails when run with `--ignored`.

## Consequences
- `cargo test -p <crate> tests::<area>` selects one area's unit tests; test paths are `tests::<area>::<name>`.
- Every workspace crate now has unit tests, and sim, py and cli have integration tests that run without Python or
  network access.
- New code follows the same layout; reviewers should reject new inline `mod tests { ... }` blocks.
