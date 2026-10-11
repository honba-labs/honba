#![deny(missing_docs)]
#![deny(rustdoc::broken_intra_doc_links)]
#![deny(unsafe_code)]

//! Performance analytics for the Honba platform.
//!
//! Everything here takes injected data: round trips for trade statistics,
//! return or equity series for risk metrics, and explicit annualization
//! parameters. Nothing is hardcoded.
//!
//! # Example
//!
//! ```
//! use honba_analytics::{EquityStats, TradeStats};
//!
//! let returns = [0.01, -0.005, 0.02, -0.003, 0.015];
//! let stats = EquityStats::from_returns(&returns, 252.0, 0.0).unwrap();
//! assert!(stats.total_return > 0.0);
//!
//! // Trade stats need at least one round trip; see `RoundTrip`.
//! let _ = TradeStats::from_round_trips(&[]).is_err();
//! ```

pub mod cost_stress;
pub mod dsr;
pub mod equity_stats;
pub mod error;
pub mod holdout;
pub mod math;
pub mod monte_carlo;
pub mod pbo;
pub mod report;
pub mod round_trip;
pub mod trade_stats;
pub mod validation;
pub mod walk_forward;

pub use cost_stress::{compute_cost_stress, CostStressConfig, CostStressResult};
pub use dsr::{compute_dsr, expected_max_sharpe, DsrConfig, DsrResult};
pub use equity_stats::EquityStats;
pub use error::{AnalyticsError, Result};
pub use holdout::{compute_holdout, HoldoutConfig, HoldoutResult};
pub use monte_carlo::{run_block_bootstrap, MonteCarloConfig, MonteCarloResult};
pub use pbo::{compute_pbo, PboConfig, PboResult};
pub use report::PerformanceReport;
pub use round_trip::RoundTrip;
pub use trade_stats::TradeStats;
pub use validation::ValidationBlock;
pub use walk_forward::{compute_walk_forward, WalkForwardConfig, WalkForwardResult};

#[cfg(test)]
mod tests;
