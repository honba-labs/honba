//! Portfolio report contract: a valued, per-user portfolio snapshot (ADR 0032, ROADMAP E13).
//!
//! Where [`crate::portfolio`] holds the *engine* types (an in-memory `Account`/`Portfolio` that a
//! simulation mutates), this module holds the *report* contract: a serializable, valued snapshot a
//! user reads back from `GET /portfolio`, carrying the mark-to-market figures the chart and the
//! workbench show. Like the screener and signal contracts, these types live here in
//! `honba-entities` (L1) so `honba-codegen` and `honba-api` can name them without depending on the
//! valuation engine (`honba-portfolio`, L4).
//!
//! The snapshot is a **projection**, not a decision: the engine constructs it from positions and a
//! mark source, and this module deliberately carries no valuation logic.

use honba_messages::{InstrumentId, Product, UnixNanos};
use schemars::JsonSchema;
use serde::{Deserialize, Serialize};

use crate::instrument::{Currency, Money};
use crate::position::PositionSide;

/// Net aggregate option Greeks across open positions in an account or portfolio (OpenAlgo design).
#[derive(Clone, Copy, Debug, Default, PartialEq, Serialize, Deserialize, JsonSchema)]
pub struct PortfolioGreeks {
    /// Delta: sensitivity of position value to underlying price changes.
    pub delta: f64,
    /// Gamma: sensitivity of Delta to underlying price changes.
    pub gamma: f64,
    /// Theta: time decay per day.
    pub theta: f64,
    /// Vega: sensitivity to a 1% change in implied volatility.
    pub vega: f64,
}

/// Where an account's positions and funds come from (ADR 0032 §2, §6).
///
/// An `Ledger` account is honba's own book: positions accrue from the execution path and the
/// simulator. A `Broker` account is synced: positions and funds are read from a broker adapter and
/// name the broker. Both project into the same [`PortfolioAccount`], so one contract serves both.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash, Serialize, Deserialize, JsonSchema)]
#[serde(rename_all = "snake_case")]
pub enum PortfolioSource {
    /// honba's own ledger (fills and the simulator).
    Ledger,
    /// A broker account, read from an adapter.
    Broker,
}

/// Whether an account holds real or simulated money (ADR 0032 §4).
///
/// Sandbox money must never mix with live money: totals are per mode and an order names its
/// account. `Sandbox` is the default while no live venue exists.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash, Serialize, Deserialize, JsonSchema)]
#[serde(rename_all = "snake_case")]
pub enum PortfolioMode {
    /// Real money, sourced from a live broker.
    Live,
    /// Simulated money (paper trading, backtest or the simulator).
    Sandbox,
}

/// A valued position held for a user (ADR 0032 §2).
///
/// `quantity` and the prices are observations (`f64`); the amounts are `Money` in the account's
/// currency. `realized_pnl` is the ledger entry accrued per fill (ADR 0011); `unrealized_pnl` is
/// `(mark - avg_price) * sign * quantity`, computed against the last mark.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize, JsonSchema)]
pub struct PortfolioPosition {
    /// The instrument held.
    pub instrument_id: InstrumentId,
    /// Long or short. The sign of `quantity` is carried here, not in the number.
    pub side: PositionSide,
    /// The absolute quantity held.
    pub quantity: f64,
    /// Volume-weighted average entry price.
    pub avg_price: f64,
    /// The last mark used to value the position.
    pub mark: f64,
    /// `quantity * mark`, in the account's currency.
    pub market_value: Money,
    /// Mark-to-market PnL, in the account's currency.
    pub unrealized_pnl: Money,
    /// Realized PnL accrued on fills, in the account's currency.
    pub realized_pnl: Money,
    /// The product the position is held under (intraday/delivery/carry).
    pub product: Product,
    /// Logical group or sub-basket (from QuantDinger design: e.g. "Core", "Hedge").
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub group_name: Option<String>,
    /// User or strategy tags (from QuantDinger design: e.g. ["tech", "dividend"]).
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub tags: Vec<String>,
    /// Strategy tag attributing this position to an automated strategy (from OpenAlgo design).
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub strategy_id: Option<String>,
}

/// A delivery holding: a carried position, without an intraday side (ADR 0032 §2).
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize, JsonSchema)]
pub struct PortfolioHolding {
    /// The instrument held.
    pub instrument_id: InstrumentId,
    /// The absolute quantity held.
    pub quantity: f64,
    /// Volume-weighted average entry price.
    pub avg_price: f64,
    /// The last mark used to value the holding.
    pub mark: f64,
    /// `quantity * mark`, in the account's currency.
    pub market_value: Money,
    /// Mark-to-market PnL, in the account's currency.
    pub unrealized_pnl: Money,
    /// Realized PnL accrued on fills, in the account's currency.
    pub realized_pnl: Money,
    /// Logical group or sub-basket (from QuantDinger design: e.g. "Core", "Growth").
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub group_name: Option<String>,
    /// User or strategy tags (from QuantDinger design: e.g. ["largecap", "esg"]).
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub tags: Vec<String>,
}

/// One account of a portfolio: cash, equity, margins and the positions it holds (ADR 0032 §2).
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize, JsonSchema)]
pub struct PortfolioAccount {
    /// Stable account id within the portfolio.
    pub id: String,
    /// Display name.
    pub name: String,
    /// Live or sandbox money (ADR 0032 §4).
    pub mode: PortfolioMode,
    /// Where the positions and funds come from (ADR 0032 §6).
    pub source: PortfolioSource,
    /// The broker this account is synced from, when `source` is [`PortfolioSource::Broker`].
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub broker: Option<String>,
    /// The settlement currency of the account's amounts.
    pub currency: Currency,
    /// Free cash.
    pub cash: Money,
    /// Cash plus mark-to-market value of the open positions (and holdings).
    pub equity: Money,
    /// Mark-to-market value of the open positions (and holdings).
    pub market_value: Money,
    /// Margin currently committed.
    pub margin_used: Money,
    /// Margin still available to trade.
    pub margin_available: Money,
    /// Account leverage, `1.0` when unlevered.
    pub leverage: f64,
    /// Realized PnL summed over the account's positions.
    pub realized_pnl: Money,
    /// Unrealized PnL summed over the account's positions.
    pub unrealized_pnl: Money,
    /// Open / intraday positions.
    pub positions: Vec<PortfolioPosition>,
    /// Carried delivery holdings.
    pub holdings: Vec<PortfolioHolding>,
    /// Net aggregate option Greeks across positions, when options are present (OpenAlgo design).
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub greeks: Option<PortfolioGreeks>,
}

/// Totals across every account of a portfolio (ADR 0032 §2).
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize, JsonSchema)]
pub struct PortfolioTotals {
    /// Summed cash.
    pub cash: Money,
    /// Summed equity.
    pub equity: Money,
    /// Summed market value.
    pub market_value: Money,
    /// Summed realized PnL.
    pub realized_pnl: Money,
    /// Summed unrealized PnL.
    pub unrealized_pnl: Money,
    /// Realized plus unrealized PnL.
    pub total_pnl: Money,
}

/// A valued, per-user portfolio: the response body of `GET /portfolio` (ADR 0032 §2).
///
/// `owner` is the request's `Caller` (`token_id` when auth is on, else `default`): one snapshot
/// belongs to one user. `as_of` is stamped by the caller, never read from a clock here.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize, JsonSchema)]
pub struct PortfolioSnapshot {
    /// The user this portfolio belongs to (`token_id` or `default`).
    pub owner: String,
    /// Monotonic revision of the stored portfolio, bumped on every write.
    pub revision: u64,
    /// The time the marks were taken (`UnixNanos`).
    pub as_of: UnixNanos,
    /// The base currency of the totals.
    pub base_currency: Currency,
    /// The accounts, in a stable order.
    pub accounts: Vec<PortfolioAccount>,
    /// Totals across every account.
    pub totals: PortfolioTotals,
}
