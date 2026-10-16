//! Golden replay for the portfolio valuation engine (ADR 0032, E13-S1).
//!
//! A fixture portfolio is valued against fixed marks and the snapshot is compared, byte for
//! byte, against a committed golden. The fixture exercises both sources (`broker`/`live` and
//! `ledger`/`sandbox`), a long, a short, a delivery holding, a flat-but-realized position and
//! pass-through margin. Run `regenerate_golden` (`--ignored`) to refresh the golden.

use honba_entities::{Currency, Money, PortfolioMode, PortfolioSource, Position, PositionSide};
use honba_messages::{Exchange, InstrumentId, Product, UnixNanos};
use honba_portfolio::{value, AccountState, Marks, PortfolioState};

/// The committed golden, refreshed by `regenerate_golden`.
const GOLDEN: &str = include_str!("golden/portfolio_snapshot.json");

/// The path of the golden on disk.
fn golden_path() -> std::path::PathBuf {
    std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("tests/golden/portfolio_snapshot.json")
}

fn instrument(symbol: &str) -> InstrumentId {
    InstrumentId::new(symbol, Exchange::new("NSE"))
}

fn filled(symbol: &str, currency: Currency, qty: f64, price: f64) -> Position {
    let mut position = Position::flat(instrument(symbol), currency);
    position.apply_fill(PositionSide::Long, qty, price);
    position
}

/// A two-account, multi-position fixture valued at a fixed `as_of`.
fn fixture() -> (PortfolioState, Marks) {
    // A live, broker-synced account: a long, a short, and a delivery holding.
    let mut long = filled("ACME", Currency::Inr, 10.0, 100.0);
    long.apply_fill(PositionSide::Short, 4.0, 108.0); // takes 4 off, realizes 32 INR
    let delivery = filled("WIDGET", Currency::Inr, 50.0, 20.0);

    let mut short = Position::flat(instrument("BETA"), Currency::Inr);
    short.apply_fill(PositionSide::Short, 25.0, 200.0);

    let mut broker = AccountState::new(
        "BROKER",
        "Zerodha",
        Currency::Inr,
        Money::new(25_000_000, Currency::Inr),
    );
    broker.mode = PortfolioMode::Live;
    broker.source = PortfolioSource::Broker;
    broker.broker = Some("zerodha".to_string());
    broker.leverage = 5.0;
    broker.margin_used = Money::new(15_000_000, Currency::Inr);
    broker.margin_available = Money::new(60_000_000, Currency::Inr);
    broker.add_position(long, Product::Intraday);
    broker.add_position(short, Product::Intraday);
    broker.add_position(delivery, Product::Delivery);

    // A sandbox ledger account with one already-closed position (flat, realized only).
    let mut closed = filled("ACME", Currency::Inr, 5.0, 90.0);
    closed.apply_fill(PositionSide::Short, 5.0, 95.0); // flat, realizes 25 INR

    let mut sandbox = AccountState::new(
        "PAPER",
        "Paper",
        Currency::Inr,
        Money::new(100_000_000, Currency::Inr),
    );
    sandbox.add_position(closed, Product::Intraday);

    let state = PortfolioState::new(Currency::Inr)
        .with_account(sandbox)
        .with_account(broker);
    let marks = Marks::new()
        .with(instrument("ACME"), 110.0)
        .with(instrument("BETA"), 190.0)
        .with(instrument("WIDGET"), 21.0);
    (state, marks)
}

fn snapshot_json() -> String {
    let (state, marks) = fixture();
    let snapshot = value(
        &state,
        &marks,
        "alice",
        0,
        UnixNanos::from_u64(1_700_000_000_000_000_000),
    )
    .expect("valued");
    serde_json::to_string_pretty(&snapshot).expect("serialized")
}

#[test]
fn the_fixture_matches_the_golden() {
    assert_eq!(
        snapshot_json().trim_end(),
        GOLDEN.trim_end(),
        "portfolio valuation drifted from tests/golden/portfolio_snapshot.json"
    );
}

#[test]
fn the_snapshot_is_byte_identical_across_runs() {
    assert_eq!(snapshot_json(), snapshot_json());
}

#[test]
#[ignore = "writes the golden; run with --ignored regenerate_golden"]
fn regenerate_golden() {
    let json = format!("{}\n", snapshot_json());
    std::fs::write(golden_path(), json).expect("wrote golden");
}
