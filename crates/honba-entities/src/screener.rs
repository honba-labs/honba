//! Screener and metric definitions domain model.

use serde::{Deserialize, Serialize};
use std::collections::HashMap;

use crate::error::{EntitiesError, Result};

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

/// A metric used as the right-hand operand of a predicate.
///
/// Makes metric-to-metric comparisons expressible on the wire, e.g.
/// `SMA50 crosses_above {"key": "SMA200"}`. `key` is the wire key, never a UI id.
#[derive(Clone, Debug, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct MetricRef {
    /// Wire key of the referenced metric (e.g. `SMA200`).
    pub key: String,
    /// Optional period dimension.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub period: Option<MetricPeriod>,
    /// Optional timeframe dimension.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub timeframe: Option<Timeframe>,
}

impl MetricRef {
    /// A reference to `key` with no period or timeframe.
    pub fn new(key: impl Into<String>) -> Self {
        Self {
            key: key.into(),
            period: None,
            timeframe: None,
        }
    }
}

/// Individual filter predicate applied to a metric key.
///
/// Deserialization enforces the value contract of [`check_predicate_value`].
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
#[serde(try_from = "RawFilterPredicate")]
pub struct ScreenerFilterPredicate {
    /// Target metric key to filter by.
    pub key: String,
    /// Comparison operator.
    pub op: FilterOp,
    /// Target value, array of values, or a [`MetricRef`] object.
    pub value: serde_json::Value,
    /// Optional period dimension.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub period: Option<MetricPeriod>,
    /// Optional timeframe dimension.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub timeframe: Option<Timeframe>,
}

/// Unchecked wire form of [`ScreenerFilterPredicate`].
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct RawFilterPredicate {
    key: String,
    op: FilterOp,
    value: serde_json::Value,
    #[serde(default)]
    period: Option<MetricPeriod>,
    #[serde(default)]
    timeframe: Option<Timeframe>,
}

impl TryFrom<RawFilterPredicate> for ScreenerFilterPredicate {
    type Error = EntitiesError;

    fn try_from(raw: RawFilterPredicate) -> Result<Self> {
        check_predicate_value(raw.op, &raw.value)?;
        Ok(Self {
            key: raw.key,
            op: raw.op,
            value: raw.value,
            period: raw.period,
            timeframe: raw.timeframe,
        })
    }
}

impl ScreenerFilterPredicate {
    /// A predicate with no period or timeframe, checked by [`check_predicate_value`].
    pub fn new(key: impl Into<String>, op: FilterOp, value: serde_json::Value) -> Result<Self> {
        check_predicate_value(op, &value)?;
        Ok(Self {
            key: key.into(),
            op,
            value,
            period: None,
            timeframe: None,
        })
    }

    /// The metric operand, when `value` is a [`MetricRef`] for a comparison operator.
    pub fn metric_ref(&self) -> Option<MetricRef> {
        match self.op {
            FilterOp::Gt
            | FilterOp::Gte
            | FilterOp::Lt
            | FilterOp::Lte
            | FilterOp::CrossesAbove
            | FilterOp::CrossesBelow
                if self.value.is_object() =>
            {
                serde_json::from_value(self.value.clone()).ok()
            }
            _ => None,
        }
    }
}

fn invalid(msg: String) -> EntitiesError {
    EntitiesError::InvalidPredicate(msg)
}

fn as_metric_ref(op: FilterOp, value: &serde_json::Value) -> Result<()> {
    if value.is_object() {
        serde_json::from_value::<MetricRef>(value.clone())
            .map(|_| ())
            .map_err(|e| invalid(format!("{op:?}: invalid MetricRef: {e}")))
    } else {
        Err(invalid(format!(
            "{op:?}: value must be a finite number or a MetricRef object"
        )))
    }
}

/// Checks that `value` fits `op` (mirrors Python `honba.entities.screener._check_value`).
///
/// - `crosses_above` / `crosses_below`: a finite number or a [`MetricRef`] object.
/// - `gt` / `gte` / `lt` / `lte`: a finite number, a string, or a [`MetricRef`] object.
/// - `between`: an array of exactly two finite numbers.
/// - `in` / `not_in`: an array.
/// - other operators: unconstrained.
pub fn check_predicate_value(op: FilterOp, value: &serde_json::Value) -> Result<()> {
    match op {
        FilterOp::CrossesAbove | FilterOp::CrossesBelow => {
            if value.is_number() {
                Ok(())
            } else {
                as_metric_ref(op, value)
            }
        }
        FilterOp::Gt | FilterOp::Gte | FilterOp::Lt | FilterOp::Lte => {
            if value.is_number() || value.is_string() {
                Ok(())
            } else {
                as_metric_ref(op, value)
            }
        }
        FilterOp::Between => match value.as_array() {
            Some(bounds) if bounds.len() == 2 && bounds.iter().all(|b| b.is_number()) => Ok(()),
            _ => Err(invalid(
                "Between: value must be an array of two finite numbers".into(),
            )),
        },
        FilterOp::In | FilterOp::NotIn => {
            if value.is_array() {
                Ok(())
            } else {
                Err(invalid(format!("{op:?}: value must be an array")))
            }
        }
        FilterOp::Eq | FilterOp::Neq | FilterOp::Like | FilterOp::Has => Ok(()),
    }
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
