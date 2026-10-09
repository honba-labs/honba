# Crate Workspace Handoff (updated 2026-10-09)

Current status:
- `honba-market` owns market contracts, calendar data (NSE/BSE, 2026 circular verification), cost models, and universe rules.
- `honba-ports` owns adapter traits and abstractions (`DataClient`, `ExecutionClient`, `MarketDataPort`, `ExecutionPort`).
- M4 planned: pure-Rust Zerodha connector in `honba-ports` / native connector crate wrapping `kiteconnect-rs` ticker decoder and REST client.
- Test suites across all crates green (`cargo test --workspace`).
