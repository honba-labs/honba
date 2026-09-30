//! End-to-end construction, arithmetic, and lifecycle tests.

use honba_entities::{
    Account, Currency, EntitiesError, Instrument, InstrumentKind, Money, Portfolio, Position,
    PositionSide, Trade,
};
use honba_messages::{InstrumentId, OrderId, OrderSide, UnixNanos, Venue};

fn nse(sym: &str) -> InstrumentId {
    InstrumentId::new(sym, Venue::new("NSE"))
}

#[test]
fn instrument_metadata() {
    let inst = Instrument::new(
        nse("BANKNIFTY"),
        InstrumentKind::Index,
        Currency::Inr,
        1.0,
        0.05,
    );
    assert_eq!(inst.kind(), InstrumentKind::Index);
    assert_eq!(inst.currency(), Currency::Inr);
    assert_eq!(inst.id().symbol(), "BANKNIFTY");
}

#[test]
fn money_arithmetic() {
    let a = Money::new(100.0, Currency::Inr);
    let b = Money::new(40.0, Currency::Inr);
    assert_eq!((a + b).unwrap().amount(), 140.0);
    assert_eq!((a - b).unwrap().amount(), 60.0);
    assert_eq!(b.neg().amount(), -40.0);
}

#[test]
fn money_currency_mismatch_errors() {
    let inr = Money::new(100.0, Currency::Inr);
    let usd = Money::new(100.0, Currency::Usd);
    match inr + usd {
        Err(EntitiesError::CurrencyMismatch { left, right }) => {
            assert_eq!(left, "INR");
            assert_eq!(right, "USD");
        }
        other => panic!("expected CurrencyMismatch, got {other:?}"),
    }
}

#[test]
fn position_average_price() {
    let mut pos = Position::flat(nse("NIFTY50"), Currency::Inr);
    pos.apply_fill(PositionSide::Long, 75.0, 22_000.0);
    pos.apply_fill(PositionSide::Long, 25.0, 22_100.0);
    assert_eq!(pos.quantity(), 100.0);
    assert_eq!(pos.avg_price(), 22_025.0);
    assert!(!pos.is_flat());
}

#[test]
fn position_realizes_pnl_on_reduce() {
    let mut pos = Position::flat(nse("X"), Currency::Inr);
    pos.apply_fill(PositionSide::Long, 100.0, 10.0);
    pos.apply_fill(PositionSide::Short, 40.0, 12.0);
    assert_eq!(pos.quantity(), 60.0);
    assert_eq!(pos.realized_pnl(), 80.0);
    assert_eq!(pos.side(), PositionSide::Long);
}

#[test]
fn position_reverses_on_large_opposite_fill() {
    let mut pos = Position::flat(nse("X"), Currency::Inr);
    pos.apply_fill(PositionSide::Long, 50.0, 10.0);
    pos.apply_fill(PositionSide::Short, 80.0, 12.0);
    assert_eq!(pos.side(), PositionSide::Short);
    assert_eq!(pos.quantity(), 30.0);
    assert_eq!(pos.avg_price(), 12.0);
    assert_eq!(pos.realized_pnl(), 100.0); // 50 * (12 - 10)
}

#[test]
fn position_unrealized_pnl() {
    let mut pos = Position::flat(nse("X"), Currency::Inr);
    pos.apply_fill(PositionSide::Long, 100.0, 10.0);
    assert_eq!(pos.unrealized_pnl(11.0), 100.0);
    assert_eq!(pos.unrealized_pnl(9.0), -100.0);
}

#[test]
fn account_cash_movements() {
    let mut acct = Account::new("MAIN", Money::new(1_000_000.0, Currency::Inr));
    acct.debit(Money::new(250_000.0, Currency::Inr)).unwrap();
    acct.credit(Money::new(50_000.0, Currency::Inr)).unwrap();
    assert_eq!(acct.cash().amount(), 800_000.0);
}

#[test]
fn account_position_lifecycle() {
    let mut acct = Account::new("MAIN", Money::new(1_000_000.0, Currency::Inr));
    let id = nse("NIFTY50");
    acct.upsert_position(Position::flat(id.clone(), Currency::Inr));

    let pos = acct.require_position(&id).unwrap();
    pos.apply_fill(PositionSide::Long, 75.0, 22_000.0);
    assert_eq!(acct.position(&id).unwrap().quantity(), 75.0);
}

#[test]
fn account_missing_position_errors() {
    let mut acct = Account::new("MAIN", Money::new(0.0, Currency::Inr));
    match acct.require_position(&nse("MISSING")) {
        Err(EntitiesError::PositionNotFound(_)) => {}
        other => panic!("expected PositionNotFound, got {other:?}"),
    }
}

#[test]
fn portfolio_manages_accounts() {
    let mut p = Portfolio::new();
    p.add_account(Account::new("MAIN", Money::new(500_000.0, Currency::Inr)));
    assert_eq!(p.base_currency(), Some(Currency::Inr));
    p.require_account("MAIN").unwrap();
    assert!(p.require_account("MISSING").is_err());
}

#[test]
fn trade_notional() {
    let t = Trade::new(
        OrderId::new("O-1"),
        nse("NIFTY50"),
        OrderSide::Buy,
        75.0,
        22_000.0,
        UnixNanos::from_u64(1),
        UnixNanos::from_u64(2),
    );
    assert_eq!(t.notional(), 75.0 * 22_000.0);
    assert_eq!(t.side(), OrderSide::Buy);
}
