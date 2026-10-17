//! The valuation input: a user's accounts, cash and positions (ADR 0032 §3).

use honba_entities::{Currency, Money, PortfolioGreeks, PortfolioMode, PortfolioSource, Position};
use honba_messages::Product;

/// A user's portfolio as the ledger holds it: accounts with cash and positions.
///
/// This is the *input* to valuation, not a wire type. The report contract it produces
/// ([`honba_entities::PortfolioSnapshot`]) is the serializable projection (ADR 0032 §2).
#[derive(Clone, Debug)]
pub struct PortfolioState {
    /// The settlement currency the totals are reported in.
    ///
    /// v1 assumes every account settles in this currency; a mismatch surfaces as a
    /// currency-mismatch error rather than a silent conversion (ADR 0032, open item).
    pub base_currency: Currency,
    /// The accounts.
    pub accounts: Vec<AccountState>,
}

impl PortfolioState {
    /// Returns an empty portfolio in `base_currency`.
    pub fn new(base_currency: Currency) -> Self {
        Self {
            base_currency,
            accounts: Vec::new(),
        }
    }

    /// Appends an account.
    pub fn add_account(&mut self, account: AccountState) {
        self.accounts.push(account);
    }

    /// Returns a copy with one more account, for chaining.
    pub fn with_account(mut self, account: AccountState) -> Self {
        self.add_account(account);
        self
    }
}

/// One account's cash and positions, with the metadata the report carries (ADR 0032 §2).
#[derive(Clone, Debug)]
pub struct AccountState {
    /// Stable account id within the portfolio.
    pub id: String,
    /// Display name.
    pub name: String,
    /// Live or sandbox money (ADR 0032 §4).
    pub mode: PortfolioMode,
    /// Where the positions and funds come from (ADR 0032 §6).
    pub source: PortfolioSource,
    /// The broker this account is synced from, when `source` is
    /// [`PortfolioSource::Broker`].
    pub broker: Option<String>,
    /// The settlement currency of the account's amounts.
    pub currency: Currency,
    /// Free cash.
    pub cash: Money,
    /// Account leverage, `1.0` when unlevered.
    pub leverage: f64,
    /// Margin currently committed (passed through, not derived).
    pub margin_used: Money,
    /// Margin still available to trade (passed through, not derived).
    pub margin_available: Money,
    /// The positions the account holds, each with the product it is held under.
    pub positions: Vec<HeldPosition>,
    /// Net aggregate option Greeks across positions, when options are present (OpenAlgo design).
    pub greeks: Option<PortfolioGreeks>,
}

impl AccountState {
    /// Returns an account with no positions and conservative defaults: `sandbox` mode, a `ledger`
    /// source, no broker, `1.0` leverage and zero margins.
    pub fn new(
        id: impl Into<String>,
        name: impl Into<String>,
        currency: Currency,
        cash: Money,
    ) -> Self {
        Self {
            id: id.into(),
            name: name.into(),
            mode: PortfolioMode::Sandbox,
            source: PortfolioSource::Ledger,
            broker: None,
            currency,
            cash,
            leverage: 1.0,
            margin_used: Money::zero(currency),
            margin_available: Money::zero(currency),
            positions: Vec::new(),
            greeks: None,
        }
    }

    /// Appends a position held under `product`.
    pub fn add_position(&mut self, position: Position, product: Product) {
        self.positions.push(HeldPosition::new(position, product));
    }

    /// Returns a copy with one more position, for chaining.
    pub fn with_position(mut self, position: Position, product: Product) -> Self {
        self.add_position(position, product);
        self
    }

    /// Sets the aggregate option Greeks for this account.
    pub fn with_greeks(mut self, greeks: PortfolioGreeks) -> Self {
        self.greeks = Some(greeks);
        self
    }
}

/// A position together with the product it is held under (ADR 0032 §2).
///
/// [`Position`] carries the quantity, average price and realized PnL; `product` decides whether
/// the position is reported as an open position or a delivery [`honba_entities::PortfolioHolding`].
#[derive(Clone, Debug)]
pub struct HeldPosition {
    /// The position.
    pub position: Position,
    /// The product: `Delivery` becomes a holding, anything else an open position.
    pub product: Product,
    /// Sub-basket / logical group (from QuantDinger design: e.g. "Core", "Hedge").
    pub group_name: Option<String>,
    /// Categorization tags (from QuantDinger design).
    pub tags: Vec<String>,
    /// Associated strategy tag (from OpenAlgo design).
    pub strategy_id: Option<String>,
}

impl HeldPosition {
    /// Pairs a position with its product.
    pub fn new(position: Position, product: Product) -> Self {
        Self {
            position,
            product,
            group_name: None,
            tags: Vec::new(),
            strategy_id: None,
        }
    }

    /// Sets the sub-basket / group name for this position.
    pub fn with_group(mut self, group_name: impl Into<String>) -> Self {
        self.group_name = Some(group_name.into());
        self
    }

    /// Sets the tags for this position.
    pub fn with_tags(mut self, tags: Vec<String>) -> Self {
        self.tags = tags;
        self
    }

    /// Sets the strategy id for this position.
    pub fn with_strategy(mut self, strategy_id: impl Into<String>) -> Self {
        self.strategy_id = Some(strategy_id.into());
        self
    }
}
