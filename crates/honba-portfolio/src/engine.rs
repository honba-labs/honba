//! The pure valuation engine (ADR 0032 §3).

use honba_entities::{
    Currency, EntitiesError, Money, PortfolioAccount, PortfolioHolding, PortfolioPosition,
    PortfolioSnapshot, PortfolioTotals,
};
use honba_messages::{Product, UnixNanos};

use crate::error::PortfolioError;
use crate::marks::Marks;
use crate::state::{AccountState, PortfolioState};

/// Values `state` against `marks` into a [`PortfolioSnapshot`] (ADR 0032 §3).
///
/// Pure: no clock, no I/O. `as_of` is the time the marks were taken, stamped by the caller, so
/// the same positions and marks always give a byte-identical snapshot. Accounts are ordered by
/// id and positions by instrument, so the input order does not reach the wire.
///
/// Realized PnL is taken from every position (including a flat one, whose realized PnL still
/// counts), while market value and unrealized PnL come only from the open positions. A non-flat
/// position with no mark fails with [`PortfolioError::MissingMark`].
pub fn value(
    state: &PortfolioState,
    marks: &Marks,
    owner: &str,
    revision: u64,
    as_of: UnixNanos,
) -> Result<PortfolioSnapshot, PortfolioError> {
    let mut accounts: Vec<PortfolioAccount> = Vec::with_capacity(state.accounts.len());
    for account in &state.accounts {
        accounts.push(value_account(account, marks)?);
    }
    accounts.sort_by(|a, b| a.id.cmp(&b.id));

    let totals = totals(&accounts, state.base_currency)?;
    Ok(PortfolioSnapshot {
        owner: owner.to_string(),
        revision,
        as_of,
        base_currency: state.base_currency,
        accounts,
        totals,
    })
}

/// Values one account: its positions into open positions and holdings, and its cash, equity and
/// PnL totals.
fn value_account(
    account: &AccountState,
    marks: &Marks,
) -> Result<PortfolioAccount, PortfolioError> {
    let currency = account.currency;
    let mut positions = Vec::new();
    let mut holdings = Vec::new();
    let mut market_value = Money::zero(currency);
    let mut realized = Money::zero(currency);
    let mut unrealized = Money::zero(currency);

    for held in &account.positions {
        let position = &held.position;
        // Realized PnL is a ledger entry: it counts even when the position is flat.
        realized = (realized + position.realized_pnl())?;
        if position.is_flat() {
            continue;
        }

        let instrument_id = position.instrument_id().clone();
        let mark = marks
            .get(&instrument_id)
            .ok_or_else(|| PortfolioError::MissingMark {
                instrument_id: instrument_id.clone(),
            })?;
        // A short's market value is a liability: the position's sign carries the direction.
        let signed_quantity = position.side().sign() * position.quantity();
        let leg_value =
            Money::mul_qty(signed_quantity, mark, currency).map_err(EntitiesError::from)?;
        let leg_unrealized = Money::from_major_f64(position.unrealized_pnl(mark), currency)
            .map_err(EntitiesError::from)?;

        market_value = (market_value + leg_value)?;
        unrealized = (unrealized + leg_unrealized)?;

        if held.product == Product::Delivery {
            holdings.push(PortfolioHolding {
                instrument_id,
                quantity: position.quantity(),
                avg_price: position.avg_price(),
                mark,
                market_value: leg_value,
                unrealized_pnl: leg_unrealized,
                realized_pnl: position.realized_pnl(),
                group_name: held.group_name.clone(),
                tags: held.tags.clone(),
            });
        } else {
            positions.push(PortfolioPosition {
                instrument_id,
                side: position.side(),
                quantity: position.quantity(),
                avg_price: position.avg_price(),
                mark,
                market_value: leg_value,
                unrealized_pnl: leg_unrealized,
                realized_pnl: position.realized_pnl(),
                product: held.product,
                group_name: held.group_name.clone(),
                tags: held.tags.clone(),
                strategy_id: held.strategy_id.clone(),
            });
        }
    }

    positions.sort_by(|a, b| a.instrument_id.cmp(&b.instrument_id));
    holdings.sort_by(|a, b| a.instrument_id.cmp(&b.instrument_id));

    let equity = (account.cash + market_value)?;

    Ok(PortfolioAccount {
        id: account.id.clone(),
        name: account.name.clone(),
        mode: account.mode,
        source: account.source,
        broker: account.broker.clone(),
        currency,
        cash: account.cash,
        equity,
        market_value,
        margin_used: account.margin_used,
        margin_available: account.margin_available,
        leverage: account.leverage,
        realized_pnl: realized,
        unrealized_pnl: unrealized,
        positions,
        holdings,
        greeks: account.greeks,
    })
}

/// Sums the accounts into portfolio totals in `base_currency`.
fn totals(
    accounts: &[PortfolioAccount],
    base_currency: Currency,
) -> Result<PortfolioTotals, PortfolioError> {
    let mut cash = Money::zero(base_currency);
    let mut equity = Money::zero(base_currency);
    let mut market_value = Money::zero(base_currency);
    let mut realized = Money::zero(base_currency);
    let mut unrealized = Money::zero(base_currency);
    for account in accounts {
        cash = (cash + account.cash)?;
        equity = (equity + account.equity)?;
        market_value = (market_value + account.market_value)?;
        realized = (realized + account.realized_pnl)?;
        unrealized = (unrealized + account.unrealized_pnl)?;
    }
    let total_pnl = (realized + unrealized)?;
    Ok(PortfolioTotals {
        cash,
        equity,
        market_value,
        realized_pnl: realized,
        unrealized_pnl: unrealized,
        total_pnl,
    })
}
