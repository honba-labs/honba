#![allow(missing_docs)]
use schemars::JsonSchema;
use serde::{Deserialize, Serialize};

use honba_messages::InstrumentId;

/// Instruments query parameters.
#[derive(Clone, Debug, Default, PartialEq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct InstrumentsQuery {
    /// Exchange filter.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub exchange: Option<String>,
    /// Symbol filter.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub symbol: Option<String>,
}

/// Quotes query parameters.
#[derive(Clone, Debug, Default, PartialEq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct QuotesQuery {
    /// Comma-separated symbols.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub symbols: Option<String>,
    /// Venue/exchange.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub venue: Option<String>,
}

/// Bars query parameters.
#[derive(Clone, Debug, Default, PartialEq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct BarsQuery {
    /// Timeframe/bar specification (e.g. "1m", "1d").
    #[serde(skip_serializing_if = "Option::is_none")]
    pub tf: Option<String>,
    /// Inclusive start time, RFC3339 or ISO-8601 date.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub from: Option<String>,
    /// Exclusive end time, RFC3339 or ISO-8601 date.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub to: Option<String>,
}

/// Depth (order book) query parameters.
#[derive(Clone, Debug, Default, PartialEq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct DepthQuery {
    /// Number of levels per side.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub depth: Option<u32>,
}

/// Strategies request: submit source or a manifest to verify and compile.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct StrategiesRequest {
    /// Strategy name.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub name: Option<String>,
    /// Strategy source code to verify and compile to a manifest.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub code: Option<String>,
}

/// Backtest request.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct BacktestRequest {
    /// Strategy name or manifest id, as returned by `POST /strategies`.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub strategy: Option<String>,
    /// Universe of instruments.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub universe: Option<String>,
    /// Inclusive start date/time.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub start: Option<String>,
    /// Exclusive end date/time.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub end: Option<String>,
    /// Bar specification as a string (e.g. "1d").
    #[serde(skip_serializing_if = "Option::is_none")]
    pub bar_spec: Option<String>,
    /// Initial capital.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub initial_capital: Option<f64>,
    /// Seed, for reproducibility. Same seed and data give a byte-identical journal.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub seed: Option<u64>,
}

/// Sweep request.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct SweepRequest {
    /// Strategy name or manifest id.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub strategy: Option<String>,
    /// Parameter ranges, as a JSON object of name to [low, high, step].
    #[serde(skip_serializing_if = "Option::is_none")]
    pub params: Option<serde_json::Value>,
    /// Number of trials.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub trials: Option<u64>,
    /// Seed. Required for a reproducible sweep.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub seed: Option<u64>,
}

/// Orders request.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct OrdersRequest {
    /// Instrument to trade.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub instrument_id: Option<InstrumentId>,
    /// Side: buy or sell.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub side: Option<String>,
    /// Order type: market or limit.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub order_type: Option<String>,
    /// Quantity.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub qty: Option<f64>,
    /// Limit price.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub price: Option<f64>,
    /// Time in force: day, gtc, ioc, fok.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub tif: Option<String>,
}
