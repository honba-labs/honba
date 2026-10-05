//! Integration: conservative rounding where money leaves the model (ADR 0011).
//!
//! An `Account`, an `Instrument` and a `Position` wired together through the
//! public API only: stakes round up to lots and minor units, payouts floor,
//! off-tick prices are refused at settlement, and per-fill rounding keeps the
//! ledger in integers across many fills.

use honba_entities::{
    Account, Currency, Instrument, InstrumentKind, Money, MoneyError, Position, PositionSide,
};
use honba_messages::{Exchange, InstrumentId};

fn nifty_future() -> Instrument {
    Instrument::new(
        InstrumentId::new("NIFTY", Exchange::new("NSE")),
        InstrumentKind::Future,
        Currency::Inr,
        75.0,
        0.05,
    )
}

#[test]
fn a_sized_trade_debits_a_lot_rounded_stake_and_credits_a_floored_payout() {
    let inst = nifty_future();
    let mut acct = Account::new("MAIN", Money::new(1_000_000_000, Currency::Inr));

    // Want 100 units: a stake rounds up to 2 lots, never down to 1.
    let qty = inst.stake_quantity(100.0).unwrap();
    assert_eq!(qty, 150.0);
    let stake = inst.settle_notional(qty, 22_000.05).unwrap();
    acct.debit(stake).unwrap();
    assert_eq!(acct.cash().minor(), 1_000_000_000 - 330_000_750);

    // A payout with a sub-paisa tail (say, a pro-rata dividend) floors.
    let payout = Money::payout_from_major_f64(150.0 * 0.333_333, Currency::Inr).unwrap();
    assert_eq!(payout.minor(), 4999, "49.99995 floors to 49.99");
    acct.credit(payout).unwrap();
    assert_eq!(acct.cash().minor(), 1_000_000_000 - 330_000_750 + 4999);
}

#[test]
fn an_off_tick_price_cannot_settle_into_the_account() {
    let inst = nifty_future();
    let acct = Account::new("MAIN", Money::new(1_000_000, Currency::Inr));
    assert_eq!(
        inst.settle_notional(75.0, 22_000.03),
        Err(MoneyError::OffTick)
    );
    // Nothing was debited: the refusal happens before the ledger moves.
    assert_eq!(acct.cash().minor(), 1_000_000);
}

#[test]
fn per_fill_rounding_keeps_account_and_position_in_lockstep() {
    // Every fill settles notional in integer paise; realized PnL is booked per
    // fill in integer paise; the account's cash change equals the realized PnL
    // exactly once the position is flat again.
    let inst = nifty_future();
    let start = Money::new(100_000_000, Currency::Inr);
    let mut acct = Account::new("MAIN", start);
    let mut pos = Position::flat(inst.id().clone(), Currency::Inr);

    for i in 0..500 {
        let buy_px = 100.0 + 0.05 * f64::from(i % 7);
        let sell_px = buy_px + 0.1 + 0.05;
        acct.debit(inst.settle_notional(75.0, buy_px).unwrap())
            .unwrap();
        pos.apply_fill(PositionSide::Long, 75.0, buy_px);
        acct.credit(inst.settle_notional(75.0, sell_px).unwrap())
            .unwrap();
        pos.apply_fill(PositionSide::Short, 75.0, sell_px);
    }

    assert!(pos.is_flat());
    // 500 round trips of 75 * 0.15 = 11.25 each.
    assert_eq!(pos.realized_pnl(), Money::new(500 * 1125, Currency::Inr));
    assert_eq!((acct.cash() - start).unwrap(), pos.realized_pnl());
}

#[test]
fn a_mixed_currency_ledger_entry_is_refused_not_dropped() {
    let mut acct = Account::new("MAIN", Money::new(1_000, Currency::Inr));
    assert!(acct.debit(Money::new(100, Currency::Usd)).is_err());
    assert_eq!(acct.cash(), Money::new(1_000, Currency::Inr));
}
