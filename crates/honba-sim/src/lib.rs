#![deny(missing_docs)]
#![deny(rustdoc::broken_intra_doc_links)]
#![deny(unsafe_code)]

//! Simulation and paper execution engines for the Honba platform.

pub mod bar_fill;
pub mod cost;
pub mod fill_model;
pub mod latency;
pub mod margin;
pub mod next_open;
pub mod paper;
pub mod scripted;

pub use bar_fill::{BarFillEngine, FillCosts, FillCostsError};
pub use cost::ScheduleFillCost;
pub use fill_model::{FillModel, NoSlippage, SlippageKind, SlippageModel};
pub use latency::{depth_survival_discount, LatencyModel, NoLatency, SeededLatency};
pub use margin::{
    default_session_close_ts, is_intraday, square_off_order, MarginQuery, MisPositions,
};
pub use next_open::{FillCostFn, NextOpenSim};
pub use paper::{OrderLedger, PaperExecution};
pub use scripted::{Behavior, ScriptedExecution, VenueAction};

#[cfg(test)]
mod tests;
