//! Portfolio and account aggregation.

use std::collections::HashMap;

use honba_messages::InstrumentId;

use crate::error::{EntitiesError, Result};
use crate::instrument::{Currency, Money};
use crate::position::Position;

/// A named account holding cash and positions.
///
/// ```
/// use honba_entities::{Account, Currency, Money};
/// use honba_messages::{InstrumentId, Venue};
///
/// let mut acct = Account::new("MAIN", Money::new(1_000_000.0, Currency::Inr));
/// assert_eq!(acct.cash().amount(), 1_000_000.0);
///
/// acct.debit(Money::new(250_000.0, Currency::Inr)).unwrap();
/// assert_eq!(acct.cash().amount(), 750_000.0);
/// ```
#[derive(Clone, Debug)]
pub struct Account {
    name: String,
    cash: Money,
    positions: HashMap<InstrumentId, Position>,
}

impl Account {
    /// Creates an account with the given starting cash.
    pub fn new(name: impl Into<String>, cash: Money) -> Self {
        Self { name: name.into(), cash, positions: HashMap::new() }
    }

    /// Returns the account name.
    pub fn name(&self) -> &str { &self.name }

    /// Returns the current cash balance.
    pub fn cash(&self) -> Money { self.cash }

    /// Adds cash to the account.
    pub fn credit(&mut self, amount: Money) -> Result<()> {
        self.cash = (self.cash + amount)?;
        Ok(())
    }

    /// Removes cash from the account.
    pub fn debit(&mut self, amount: Money) -> Result<()> {
        self.cash = (self.cash - amount)?;
        Ok(())
    }

    /// Returns a reference to a position if it exists.
    pub fn position(&self, instrument_id: &InstrumentId) -> Option<&Position> {
        self.positions.get(instrument_id)
    }

    /// Returns a mutable reference to a position if it exists.
    pub fn position_mut(&mut self, instrument_id: &InstrumentId) -> Option<&mut Position> {
        self.positions.get_mut(instrument_id)
    }

    /// Inserts or replaces a position.
    pub fn upsert_position(&mut self, position: Position) {
        self.positions.insert(position.instrument_id().clone(), position);
    }

    /// Returns an error if a position for the given instrument is not present.
    pub fn require_position(&mut self, id: &InstrumentId) -> Result<&mut Position> {
        self.positions.get_mut(id).ok_or_else(|| {
            EntitiesError::PositionNotFound(id.to_string())
        })
    }

    /// Iterates over all positions.
    pub fn positions(&self) -> impl Iterator<Item = &Position> {
        self.positions.values()
    }
}

/// A collection of accounts.
///
/// ```
/// use honba_entities::{Account, Currency, Money, Portfolio};
///
/// let mut p = Portfolio::new();
/// p.add_account(Account::new("MAIN", Money::new(500_000.0, Currency::Inr)));
/// assert!(p.account("MAIN").is_some());
/// ```
#[derive(Clone, Debug, Default)]
pub struct Portfolio {
    accounts: HashMap<String, Account>,
}

impl Portfolio {
    /// Creates an empty portfolio.
    pub fn new() -> Self {
        Self::default()
    }

    /// Adds or replaces an account.
    pub fn add_account(&mut self, account: Account) {
        self.accounts.insert(account.name().to_string(), account);
    }

    /// Returns a reference to an account by name.
    pub fn account(&self, name: &str) -> Option<&Account> {
        self.accounts.get(name)
    }

    /// Returns a mutable reference to an account by name.
    pub fn account_mut(&mut self, name: &str) -> Option<&mut Account> {
        self.accounts.get_mut(name)
    }

    /// Returns an error if the account is not present.
    pub fn require_account(&mut self, name: &str) -> Result<&mut Account> {
        self.accounts
            .get_mut(name)
            .ok_or_else(|| EntitiesError::AccountNotFound(name.to_string()))
    }

    /// Returns the currency used by the first account, if any.
    pub fn base_currency(&self) -> Option<Currency> {
        self.accounts.values().next().map(|a| a.cash().currency())
    }
}
