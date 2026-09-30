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

pub mod equity_stats;
pub mod error;
pub mod report;
pub mod round_trip;
pub mod trade_stats;

pub use equity_stats::EquityStats;
pub use error::{AnalyticsError, Result};
pub use report::PerformanceReport;
pub use round_trip::RoundTrip;
pub use trade_stats::TradeStats;
