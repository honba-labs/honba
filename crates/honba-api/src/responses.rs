#![allow(missing_docs)]
//! Response DTOs. These are *records* (ADR 0012): unknown fields are ignored
//! on parse so an older client reads a newer server's response.
use schemars::JsonSchema;
use serde::{Deserialize, Serialize};

use honba_entities::{Position, Trade};
use honba_messages::{Bar, Order, QuoteTick};
use honba_strategy::StrategyIr;

use crate::capabilities::Capabilities;

/// Capabilities response.
pub type CapabilitiesResponse = Capabilities;

/// Instruments response.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize, JsonSchema)]
pub struct InstrumentsResponse {
    /// Matching instruments, as of the requested snapshot.
    pub instruments: Vec<serde_json::Value>,
}

/// Quotes response.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize, JsonSchema)]
pub struct QuotesResponse {
    /// Latest top-of-book per requested symbol.
    pub quotes: Vec<QuoteTick>,
}

/// Bars response.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize, JsonSchema)]
pub struct BarsResponse {
    /// Bars in ascending `ts_event` order.
    pub bars: Vec<Bar>,
}

/// One side of the order book.
#[derive(Clone, Debug, Default, PartialEq, Serialize, Deserialize, JsonSchema)]
pub struct DepthLevel {
    /// Price.
    pub price: f64,
    /// Quantity resting at this price.
    pub qty: f64,
}

/// Depth (order book) response.
#[derive(Clone, Debug, Default, PartialEq, Serialize, Deserialize, JsonSchema)]
pub struct DepthResponse {
    /// Bid levels, best (highest) first.
    pub bids: Vec<DepthLevel>,
    /// Ask levels, best (lowest) first.
    pub asks: Vec<DepthLevel>,
}

/// A strategy that verified, with the id the server keeps it under.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize, JsonSchema)]
pub struct CompiledStrategy {
    /// Content id: `sha256:` plus the hex digest of the canonical manifest JSON.
    /// The same manifest always has the same id; a backtest names it as `strategy`.
    pub id: String,
    /// The verified IR the manifest compiled to.
    pub ir: StrategyIr,
}

/// Strategies response: the compiled strategies of this process, ordered by id.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize, JsonSchema)]
pub struct StrategiesResponse {
    /// Compiled strategies.
    pub strategies: Vec<CompiledStrategy>,
}

/// Lifecycle of an asynchronous job.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(rename_all = "snake_case")]
#[non_exhaustive]
pub enum RunStatus {
    /// Accepted, not started.
    Pending,
    /// In progress.
    Running,
    /// Finished successfully.
    Completed,
    /// Finished with a failure; see the envelope error.
    Failed,
}

/// Lifecycle of a backtest run or sweep job.
///
/// Named `RunStatus` and re-exported under the two names the plan's endpoint
/// list uses, so both spellings refer to one enum.
pub type BacktestStatus = RunStatus;

/// Lifecycle of a sweep job.
pub type SweepStatus = RunStatus;

/// Headline performance numbers for a completed backtest.
///
/// A fixed, documented set rather than a free-form object, so a dashboard can
/// bind to fields that are guaranteed present.
#[derive(Clone, Debug, Default, PartialEq, Serialize, Deserialize, JsonSchema)]
pub struct BacktestMetrics {
    /// Number of completed round trips.
    pub trades: u64,
    /// Net profit across the run.
    pub net_pnl: f64,
    /// Annualized Sharpe ratio.
    pub sharpe: f64,
    /// Maximum peak-to-trough drawdown, as a positive fraction.
    pub max_drawdown: f64,
    /// Total return as a fraction of initial capital.
    pub total_return: f64,
}

/// Backtest response.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize, JsonSchema)]
pub struct BacktestResponse {
    /// Server-assigned run id.
    pub run_id: String,
    /// Current lifecycle state.
    pub status: RunStatus,
    /// Present once `status` is `completed`.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub metrics: Option<BacktestMetrics>,
    /// What the run did *not* model, stated explicitly.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub assumptions: Option<serde_json::Value>,
}

/// Sweep response.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize, JsonSchema)]
pub struct SweepResponse {
    /// Server-assigned job id.
    pub job_id: String,
    /// Current lifecycle state.
    pub status: RunStatus,
    /// Present once `status` is `completed`.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub report: Option<SweepReportResponse>,
}

/// Sweep results: best trials plus the ranking, in a deterministic order.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize, JsonSchema)]
pub struct SweepReportResponse {
    /// Trials ranked best-first by the fitness function.
    pub ranked: Vec<serde_json::Value>,
    /// Best trial's parameter set.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub best: Option<serde_json::Value>,
}

/// Orders response.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize, JsonSchema)]
pub struct OrdersResponse {
    /// Orders matching the query.
    pub orders: Vec<Order>,
}

/// Positions response.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize, JsonSchema)]
pub struct PositionsResponse {
    /// Open positions.
    pub positions: Vec<Position>,
}

/// Trades response.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize, JsonSchema)]
pub struct TradesResponse {
    /// Realized trades.
    pub trades: Vec<Trade>,
}
