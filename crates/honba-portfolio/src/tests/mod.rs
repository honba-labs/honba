//! Unit tests for the valuation engine.

use honba_entities::{Currency, Money, PortfolioMode, PortfolioSource, Position, PositionSide};
use honba_messages::{Exchange, InstrumentId, Product, UnixNanos};

use crate::error::PortfolioError;
use crate::{value, AccountState, HeldPosition, Marks, PortfolioState};

/// An INR instrument on the NSE.
fn instrument(symbol: &str) -> InstrumentId {
    InstrumentId::new(symbol, Exchange::new("NSE"))
}

/// A position of `qty` at `price` on the given side.
fn position(symbol: &str, side: PositionSide, qty: f64, price: f64) -> Position {
    let mut position = Position::flat(instrument(symbol), Currency::Inr);
    position.apply_fill(side, qty, price);
    position
}

/// Values a single-account portfolio and returns the account.
fn valued_account(account: AccountState, marks: &Marks) -> honba_entities::PortfolioAccount {
    let state = PortfolioState::new(Currency::Inr).with_account(account);
    let snapshot = value(&state, marks, "default", 0, UnixNanos::from_u64(1)).unwrap();
    snapshot.accounts.into_iter().next().unwrap()
}

/// An account named `name` with zero cash and no positions.
fn account(name: &str) -> AccountState {
    AccountState::new(name, name, Currency::Inr, Money::new(0, Currency::Inr))
}

#[test]
fn a_long_position_is_valued_at_the_mark() {
    let marked = valued_account(
        account("MAIN").with_position(
            position("ACME", PositionSide::Long, 10.0, 100.0),
            Product::Intraday,
        ),
        &Marks::new().with(instrument("ACME"), 110.0),
    );

    let leg = &marked.positions[0];
    assert_eq!(leg.quantity, 10.0);
    assert_eq!(leg.avg_price, 100.0);
    assert_eq!(leg.mark, 110.0);
    assert_eq!(leg.market_value.minor(), 110_000); // 10 * 110
    assert_eq!(leg.unrealized_pnl.minor(), 10_000); // 10 * (110 - 100)
    assert_eq!(leg.product, Product::Intraday);
    assert_eq!(marked.market_value.minor(), 110_000);
    assert_eq!(marked.equity.minor(), 110_000); // cash 0 + market value
    assert_eq!(marked.unrealized_pnl.minor(), 10_000);
}

#[test]
fn a_short_position_has_negative_market_value() {
    let marked = valued_account(
        account("MAIN").with_position(
            position("ACME", PositionSide::Short, 10.0, 100.0),
            Product::Intraday,
        ),
        &Marks::new().with(instrument("ACME"), 90.0),
    );

    let leg = &marked.positions[0];
    assert_eq!(leg.market_value.minor(), -90_000); // -10 * 90, a liability
    assert_eq!(leg.unrealized_pnl.minor(), 10_000); // -10 * (90 - 100)
    assert_eq!(marked.market_value.minor(), -90_000);
}

#[test]
fn a_delivery_position_becomes_a_holding() {
    let marked = valued_account(
        account("MAIN").with_position(
            position("ACME", PositionSide::Long, 5.0, 100.0),
            Product::Delivery,
        ),
        &Marks::new().with(instrument("ACME"), 120.0),
    );

    assert!(marked.positions.is_empty());
    let holding = &marked.holdings[0];
    assert_eq!(holding.quantity, 5.0);
    assert_eq!(holding.market_value.minor(), 60_000); // 5 * 120
    assert_eq!(holding.unrealized_pnl.minor(), 10_000); // 5 * (120 - 100)
}

#[test]
fn a_flat_position_is_dropped_but_its_realized_pnl_still_counts() {
    // 10 long at 100, then sell 10 at 110: flat, +100 INR realized.
    let mut closed = Position::flat(instrument("ACME"), Currency::Inr);
    closed.apply_fill(PositionSide::Long, 10.0, 100.0);
    closed.apply_fill(PositionSide::Short, 10.0, 110.0);
    assert!(closed.is_flat());
    assert_eq!(closed.realized_pnl().minor(), 10_000);

    let marked = valued_account(
        account("MAIN").with_position(closed, Product::Intraday),
        &Marks::new(),
    );

    assert!(marked.positions.is_empty());
    assert!(marked.holdings.is_empty());
    assert_eq!(marked.market_value.minor(), 0);
    assert_eq!(marked.realized_pnl.minor(), 10_000);
}

#[test]
fn a_missing_mark_for_an_open_position_is_an_error() {
    let state = PortfolioState::new(Currency::Inr).with_account(account("MAIN").with_position(
        position("ACME", PositionSide::Long, 10.0, 100.0),
        Product::Intraday,
    ));
    let error = value(&state, &Marks::new(), "default", 0, UnixNanos::from_u64(1)).unwrap_err();
    assert_eq!(
        error,
        PortfolioError::MissingMark {
            instrument_id: instrument("ACME")
        }
    );
}

#[test]
fn totals_sum_every_account() {
    let state = PortfolioState::new(Currency::Inr)
        .with_account(account("A").with_position(
            position("ACME", PositionSide::Long, 10.0, 100.0),
            Product::Intraday,
        ))
        .with_account(account("B").with_position(
            position("ACME", PositionSide::Long, 4.0, 100.0),
            Product::Intraday,
        ));
    let marks = Marks::new().with(instrument("ACME"), 110.0);

    let snapshot = value(&state, &marks, "default", 7, UnixNanos::from_u64(5)).unwrap();

    assert_eq!(snapshot.revision, 7);
    assert_eq!(snapshot.owner, "default");
    assert_eq!(snapshot.accounts.len(), 2);
    assert_eq!(snapshot.totals.market_value.minor(), 14 * 110 * 100); // 14 * 110 INR
    assert_eq!(snapshot.totals.unrealized_pnl.minor(), 14 * 10 * 100); // 14 * (110 - 100)
    assert_eq!(
        snapshot.totals.total_pnl.minor(),
        snapshot.totals.unrealized_pnl.minor()
    );
}

#[test]
fn accounts_and_positions_are_ordered_regardless_of_input() {
    let state = PortfolioState::new(Currency::Inr)
        .with_account(account("ZED").with_position(
            position("ZZZ", PositionSide::Long, 1.0, 10.0),
            Product::Intraday,
        ))
        .with_account(
            account("AAA")
                .with_position(
                    position("ZZZ", PositionSide::Long, 1.0, 10.0),
                    Product::Intraday,
                )
                .with_position(
                    position("AAA", PositionSide::Long, 1.0, 10.0),
                    Product::Intraday,
                ),
        );
    let marks = Marks::new()
        .with(instrument("AAA"), 11.0)
        .with(instrument("ZZZ"), 11.0);

    let snapshot = value(&state, &marks, "default", 0, UnixNanos::from_u64(1)).unwrap();

    assert_eq!(snapshot.accounts[0].id, "AAA");
    assert_eq!(snapshot.accounts[1].id, "ZED");
    let ids: Vec<_> = snapshot.accounts[0]
        .positions
        .iter()
        .map(|p| p.instrument_id.symbol().to_string())
        .collect();
    assert_eq!(ids, ["AAA", "ZZZ"]);
}

#[test]
fn ordering_does_not_change_the_snapshot_bytes() {
    let marks = |m: &mut Marks| {
        m.set(instrument("AAA"), 11.0);
        m.set(instrument("ZZZ"), 13.0);
    };
    let build = |reversed: bool| {
        let mut first = account("A")
            .with_position(
                position("AAA", PositionSide::Long, 1.0, 10.0),
                Product::Intraday,
            )
            .with_position(
                position("ZZZ", PositionSide::Long, 2.0, 12.0),
                Product::Intraday,
            );
        if reversed {
            first.positions.reverse();
        }
        let state = PortfolioState::new(Currency::Inr).with_account(first);
        let mut m = Marks::new();
        marks(&mut m);
        serde_json::to_string(&value(&state, &m, "default", 0, UnixNanos::from_u64(1)).unwrap())
            .unwrap()
    };
    assert_eq!(build(false), build(true));
}

#[test]
fn account_metadata_is_carried_through() {
    let mut account = account("BROKER");
    account.mode = PortfolioMode::Live;
    account.source = PortfolioSource::Broker;
    account.broker = Some("zerodha".to_string());
    account.leverage = 5.0;
    account.cash = Money::new(1_000_000, Currency::Inr);
    account.margin_used = Money::new(275_000, Currency::Inr);
    account.margin_available = Money::new(725_000, Currency::Inr);

    let marked = valued_account(account, &Marks::new());

    assert_eq!(marked.mode, PortfolioMode::Live);
    assert_eq!(marked.source, PortfolioSource::Broker);
    assert_eq!(marked.broker.as_deref(), Some("zerodha"));
    assert_eq!(marked.leverage, 5.0);
    assert_eq!(marked.margin_used.minor(), 275_000);
    assert_eq!(marked.margin_available.minor(), 725_000);
    assert_eq!(marked.equity.minor(), 1_000_000); // cash, no positions
}

#[test]
fn a_position_currency_that_differs_from_the_account_is_an_error() {
    let mut usd = Position::flat(instrument("ACME"), Currency::Usd);
    usd.apply_fill(PositionSide::Long, 1.0, 100.0);
    let state = PortfolioState::new(Currency::Inr)
        .with_account(account("MAIN").with_position(usd, Product::Intraday));
    let marks = Marks::new().with(instrument("ACME"), 100.0);

    let error = value(&state, &marks, "default", 0, UnixNanos::from_u64(1)).unwrap_err();
    assert!(matches!(error, PortfolioError::Money(_)));
}

#[test]
fn sub_baskets_tags_strategy_and_greeks_are_carried_through() {
    use honba_entities::PortfolioGreeks;

    let held_intraday = HeldPosition::new(
        position("NIFTY_FUT", PositionSide::Long, 50.0, 24_000.0),
        Product::Intraday,
    )
    .with_group("Hedge")
    .with_tags(vec!["futures".to_string(), "index".to_string()])
    .with_strategy("strat_alpha");

    let held_delivery = HeldPosition::new(
        position("RELIANCE", PositionSide::Long, 10.0, 2_800.0),
        Product::Delivery,
    )
    .with_group("Core")
    .with_tags(vec!["energy".to_string(), "largecap".to_string()]);

    let greeks = PortfolioGreeks {
        delta: 0.55,
        gamma: 0.02,
        theta: -15.0,
        vega: 8.5,
    };

    let mut acct = account("MAIN").with_greeks(greeks);
    acct.positions.push(held_intraday);
    acct.positions.push(held_delivery);

    let marks = Marks::new()
        .with(instrument("NIFTY_FUT"), 24_100.0)
        .with(instrument("RELIANCE"), 2_850.0);

    let marked = valued_account(acct, &marks);

    assert_eq!(marked.greeks, Some(greeks));
    assert_eq!(marked.positions.len(), 1);
    assert_eq!(marked.positions[0].group_name.as_deref(), Some("Hedge"));
    assert_eq!(marked.positions[0].tags, vec!["futures", "index"]);
    assert_eq!(
        marked.positions[0].strategy_id.as_deref(),
        Some("strat_alpha")
    );

    assert_eq!(marked.holdings.len(), 1);
    assert_eq!(marked.holdings[0].group_name.as_deref(), Some("Core"));
    assert_eq!(marked.holdings[0].tags, vec!["energy", "largecap"]);
}
