//! Screener and metric definitions domain model.

use serde::{Deserialize, Serialize};
use std::collections::HashMap;

/// Type of metric value.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(rename_all = "SCREAMING_SNAKE_CASE")]
pub enum ValueType {
    /// Floating point or integer numeric value.
    Number,
    /// String / text value.
    String,
    /// Categorical enum value.
    Enum,
    /// Boolean flag.
    Bool,
    /// Date or timestamp string.
    Date,
    /// Monetary quantity.
    Money,
}

/// Unit of measurement for a metric.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(rename_all = "SCREAMING_SNAKE_CASE")]
pub enum UnitType {
    /// Percentage.
    Pct,
    /// Absolute price.
    Price,
    /// Financial or technical ratio.
    Ratio,
    /// Share count.
    Shares,
    /// Currency amount.
    Currency,
}

/// Evaluation period dimension for fundamental or snapshot metrics.
#[derive(Clone, Debug, Default, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(rename_all = "UPPERCASE")]
pub enum MetricPeriod {
    /// Latest snapshot.
    #[default]
    Snapshot,
    /// Trailing twelve months.
    Ttm,
    /// Fiscal year.
    Fy,
    /// Fiscal quarter.
    Fq,
    /// Half year.
    H1,
    /// Current point-in-time value.
    Current,
    /// Custom period identifier.
    #[serde(untagged)]
    Custom(String),
}

/// Bar or indicator timeframe dimension for technical metrics.
#[derive(Clone, Debug, Default, PartialEq, Eq, Hash, Serialize, Deserialize)]
pub enum Timeframe {
    /// 1 minute
    #[serde(rename = "1")]
    M1,
    /// 5 minutes
    #[serde(rename = "5")]
    M5,
    /// 15 minutes
    #[serde(rename = "15")]
    M15,
    /// 30 minutes
    #[serde(rename = "30")]
    M30,
    /// 1 hour / 60 minutes
    #[serde(rename = "60")]
    H1,
    /// 2 hours / 120 minutes
    #[serde(rename = "120")]
    H2,
    /// 4 hours / 240 minutes
    #[serde(rename = "240")]
    H4,
    /// 1 day
    #[serde(rename = "1D")]
    #[default]
    D1,
    /// 1 week
    #[serde(rename = "1W")]
    W1,
    /// 1 month
    #[serde(rename = "1M")]
    Month1,
    /// Custom timeframe string
    #[serde(untagged)]
    Custom(String),
}

/// Specification of a requested metric with its evaluation dimensions.
#[derive(Clone, Debug, PartialEq, Eq, Hash, Serialize, Deserialize)]
pub struct MetricKeySpec {
    /// Unique metric key (e.g. `price_earnings_ttm`, `RSI`, `close`).
    pub key: String,
    /// Optional period dimension (e.g. `TTM`, `FY`, `SNAPSHOT`).
    #[serde(skip_serializing_if = "Option::is_none")]
    pub period: Option<MetricPeriod>,
    /// Optional timeframe dimension (e.g. `1D`, `1W`, `60`).
    #[serde(skip_serializing_if = "Option::is_none")]
    pub timeframe: Option<Timeframe>,
}

/// Dynamic value of a metric in a screener row or symbol fact table.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
#[serde(untagged)]
pub enum MetricValue {
    /// Numeric value.
    Num(f64),
    /// Text or categorical string.
    Text(String),
    /// Boolean flag.
    Bool(bool),
    /// Null or missing value.
    Null,
}

/// Comparison operators for screener filter expressions.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum FilterOp {
    /// Equal
    Eq,
    /// Not equal
    Neq,
    /// Greater than
    Gt,
    /// Greater than or equal
    Gte,
    /// Less than
    Lt,
    /// Less than or equal
    Lte,
    /// Between lower and upper bounds
    Between,
    /// In set
    In,
    /// Not in set
    NotIn,
    /// Pattern match / substring
    Like,
    /// Collection contains
    Has,
    /// Crosses above threshold or second series
    CrossesAbove,
    /// Crosses below threshold or second series
    CrossesBelow,
}

/// Individual filter predicate applied to a metric key.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct ScreenerFilterPredicate {
    /// Target metric key to filter by.
    pub key: String,
    /// Comparison operator.
    pub op: FilterOp,
    /// Target value or array of values.
    pub value: serde_json::Value,
    /// Optional period dimension.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub period: Option<MetricPeriod>,
    /// Optional timeframe dimension.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub timeframe: Option<Timeframe>,
}

/// Logical grouping of filter predicates (AND / OR).
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct ScreenerFilterGroup {
    /// Operator: "AND" or "OR".
    pub operator: String,
    /// Predicates or nested groups.
    pub items: Vec<serde_json::Value>,
}

/// Sort direction and dimension for screener results.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct SortSpec {
    /// Metric key to sort on.
    pub key: String,
    /// Sort direction: "asc" or "desc".
    pub dir: String,
    /// Optional period dimension.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub period: Option<MetricPeriod>,
    /// Optional timeframe dimension.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub timeframe: Option<Timeframe>,
}

/// Single scanned instrument row returned in screener responses.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct ScreenerRow {
    /// Canonical full symbol (e.g. `NSE:RELIANCE`).
    pub full_symbol: String,
    /// Internal instrument identifier.
    pub instrument_id: String,
    /// Instrument display name.
    pub name: String,
    /// Metric values keyed by metric spec string.
    pub values: HashMap<String, MetricValue>,
}

/// Full screener scan response.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct ScreenerScanResponse {
    /// Total count of matching instruments.
    pub total: usize,
    /// Range [offset, limit].
    pub range: (usize, usize),
    /// Column keys requested.
    pub columns: Vec<String>,
    /// Rows of instrument data.
    pub rows: Vec<ScreenerRow>,
}
